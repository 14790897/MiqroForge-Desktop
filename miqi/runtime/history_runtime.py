"""History runtime — persistent turn, message, and compaction history.

Owns SQLite storage for turn records, history items (messages),
compaction records, and provides load/append/query/replace methods
used by TaskRunner and ContextRuntime. One instance per workspace,
scoped to a single session for cross-session isolation.

Phase 19: adds runtime_compactions table and
replace_messages_with_compaction() for persistent context compaction.

Phase 22 hardening: uses a single persistent aiosqlite connection
instead of per-method connect() to prevent background-thread leaks
when the event loop shuts down.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

VALID_HISTORY_ROLES = frozenset({
    "system",
    "user",
    "assistant",
    "tool",
    "developer",
    "unknown",
})
MAX_HISTORY_CONTENT_CHARS = 1_000_000
MAX_HISTORY_PAYLOAD_JSON_CHARS = 1_000_000
_TRUNCATED_SUFFIX = "<truncated>"
_HISTORY_CREATED_AT_STEP = 1e-6


@dataclass(frozen=True)
class HistoryItem:
    """A single message or event in thread history."""

    item_id: str
    thread_id: str
    turn_id: str
    role: str
    content: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class TurnRecord:
    """Record of a single turn's lifecycle."""

    turn_id: str
    thread_id: str
    status: str
    started_at: float
    completed_at: float | None = None
    tools_used: list[str] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)


class HistoryRuntime:
    """Persistent runtime history scoped to one session.

    All queries are filtered by session_id to prevent cross-session
    data access. Threads are implicitly scoped by the session that
    creates them.

    Uses a single persistent aiosqlite connection (opened in initialize(),
    closed in close()) to avoid background-thread leaks on event-loop
    shutdown.
    """

    def __init__(self, db_path: Path, *, session_id: str):
        self.db_path = db_path
        self.session_id = session_id
        self._db: aiosqlite.Connection | None = None
        self._last_history_created_at = 0.0

    # ── lifecycle ──────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Open persistent connection and create tables."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None (autocommit): see LedgerRuntime.initialize for
        # the stranded-lock rationale — a cancelled turn task must never leave
        # an open write transaction holding the shared DB file lock.
        self._db = await aiosqlite.connect(
            str(self.db_path), timeout=30, isolation_level=None
        )
        await self._db.execute("PRAGMA journal_mode=WAL")
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS runtime_turns (
                turn_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at REAL NOT NULL,
                completed_at REAL,
                tools_used_json TEXT NOT NULL,
                token_usage_json TEXT NOT NULL
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS runtime_history_items (
                item_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS runtime_compactions (
                compaction_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                messages_before INTEGER NOT NULL DEFAULT 0,
                messages_after INTEGER NOT NULL DEFAULT 0,
                tokens_saved INTEGER NOT NULL DEFAULT 0,
                replacement_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        # #740: execution snapshots — in-flight turn state persisted so an
        # interrupted turn (process exit / abort) can be resumed later.
        # Deleted when the turn completes normally (content is then persisted
        # as real messages); kept when interrupted (status=running/interrupted).
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS execution_snapshots (
                turn_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                assistant_content TEXT NOT NULL DEFAULT '',
                reasoning_content TEXT NOT NULL DEFAULT '',
                tool_state_json TEXT NOT NULL DEFAULT '[]',
                version INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL,
                reasoning_elapsed_s REAL,
                reasoning_mode TEXT
            )
        """)
        # #834: reasoning_elapsed_s was added after the original table schema,
        # so older DBs need a migration (SQLite has no ADD COLUMN IF NOT
        # EXISTS before 3.35).  Fresh DBs already have the column via the
        # CREATE above; the ALTER only fires for pre-existing tables.
        # Check-then-act is racy across concurrent connections, so the ALTER
        # itself is guarded (CR #856-4).  #905 review extends the same pattern
        # for reasoning_mode (fast/think label on restored interrupted cards).
        async with self._db.execute("PRAGMA table_info(execution_snapshots)") as cursor:
            cols = {row[1] for row in await cursor.fetchall()}
        if "reasoning_elapsed_s" not in cols:
            try:
                await self._db.execute(
                    "ALTER TABLE execution_snapshots ADD COLUMN reasoning_elapsed_s REAL"
                )
            except Exception:
                # Concurrent initializer may have won the race — verify the
                # column now exists before treating anything as an error.
                async with self._db.execute("PRAGMA table_info(execution_snapshots)") as cursor:
                    cols2 = {row[1] for row in await cursor.fetchall()}
                if "reasoning_elapsed_s" not in cols2:
                    raise
        if "reasoning_mode" not in cols:
            try:
                await self._db.execute(
                    "ALTER TABLE execution_snapshots ADD COLUMN reasoning_mode TEXT"
                )
            except Exception:
                async with self._db.execute("PRAGMA table_info(execution_snapshots)") as cursor:
                    cols2 = {row[1] for row in await cursor.fetchall()}
                if "reasoning_mode" not in cols2:
                    raise
        await self._db.commit()

    async def close(self) -> None:
        """Close the persistent database connection.

        Safe to call multiple times; no-op if already closed or never
        initialized.
        """
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        """Return the persistent connection, raising if not initialized."""
        if self._db is None:
            raise RuntimeError(
                "HistoryRuntime.initialize() must be called before use"
            )
        return self._db

    # ── turns ──────────────────────────────────────────────────────────

    async def start_turn(self, turn_id: str, *, thread_id: str) -> None:
        db = self._conn
        await db.execute(
            """INSERT OR REPLACE INTO runtime_turns
               (turn_id, thread_id, session_id, status, started_at,
                completed_at, tools_used_json, token_usage_json)
               VALUES (?, ?, ?, ?, ?, NULL, ?, ?)""",
            (
                turn_id, thread_id, self.session_id, "running",
                time.time(), "[]", "{}",
            ),
        )
        await db.commit()

    async def complete_turn(
        self,
        turn_id: str,
        *,
        status: str,
        tools_used: list[str],
        token_usage: dict[str, int],
    ) -> None:
        db = self._conn
        await db.execute(
            """UPDATE runtime_turns
               SET status = ?, completed_at = ?, tools_used_json = ?,
                   token_usage_json = ?
               WHERE turn_id = ? AND session_id = ?""",
            (
                status,
                time.time(),
                json.dumps(tools_used),
                json.dumps(token_usage),
                turn_id,
                self.session_id,
            ),
        )
        await db.commit()

    async def get_turn(self, turn_id: str) -> TurnRecord | None:
        db = self._conn
        cursor = await db.execute(
            "SELECT * FROM runtime_turns WHERE turn_id = ? AND session_id = ?",
            (turn_id, self.session_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        # Issue #84: degrade on corrupted JSON columns instead of crashing
        # the whole turn load (parity with load_items, which already skips
        # corrupted payload_json with a warning).
        try:
            tools_used = json.loads(row["tools_used_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "Corrupted tools_used_json for turn {}, degrading to []: {}",
                turn_id, exc,
            )
            tools_used = []
        if not isinstance(tools_used, list):
            tools_used = []
        try:
            token_usage = json.loads(row["token_usage_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "Corrupted token_usage_json for turn {}, degrading to {{}}: {}",
                turn_id, exc,
            )
            token_usage = {}
        if not isinstance(token_usage, dict):
            token_usage = {}

        return TurnRecord(
            turn_id=row["turn_id"],
            thread_id=row["thread_id"],
            status=row["status"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            tools_used=tools_used,
            token_usage=token_usage,
        )

    # ── history items ──────────────────────────────────────────────────

    async def append_item(self, item: HistoryItem) -> None:
        db = self._conn
        role = _validate_role(item.role)
        content = _sanitize_content(item.content)
        payload_json = _sanitize_payload(item.payload)
        await db.execute(
            """INSERT INTO runtime_history_items
               (item_id, thread_id, session_id, turn_id, role, content,
                payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.item_id,
                item.thread_id,
                self.session_id,
                item.turn_id,
                role,
                content,
                payload_json,
                self._next_history_created_at(item.created_at),
            ),
        )
        await db.commit()

    async def append_message(
        self,
        *,
        thread_id: str,
        turn_id: str,
        role: str,
        content: str,
        payload: dict[str, Any] | None = None,
    ) -> HistoryItem:
        item = HistoryItem(
            item_id=str(uuid.uuid4()),
            thread_id=thread_id,
            turn_id=turn_id,
            role=role,
            content=content,
            payload=payload or {},
        )
        await self.append_item(item)
        return item

    async def load_items(self, thread_id: str) -> list[HistoryItem]:
        db = self._conn
        cursor = await db.execute(
            """SELECT * FROM runtime_history_items
               WHERE thread_id = ? AND session_id = ?
               ORDER BY created_at ASC, rowid ASC""",
            (thread_id, self.session_id),
        )
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning(
                    "Skipping corrupted payload_json for item {} in thread {}: {}",
                    row["item_id"], thread_id, exc,
                )
                payload = {}
            results.append(HistoryItem(
                item_id=row["item_id"],
                thread_id=row["thread_id"],
                turn_id=row["turn_id"],
                role=row["role"],
                content=row["content"],
                payload=payload,
                created_at=row["created_at"],
            ))
        return results

    async def load_messages(self, thread_id: str) -> list[dict[str, Any]]:
        """Return provider-compatible message dicts for a thread."""
        items = await self.load_items(thread_id)
        messages: list[dict[str, Any]] = []
        for item in items:
            msg: dict[str, Any] = {"role": item.role, "content": item.content}
            msg.update(item.payload.get("message_fields", {}))
            messages.append(msg)
        return messages

    # ── Phase 36: delete turn items for rollback ───────────────────────

    async def delete_turn_items(self, thread_id: str, turn_ids: list[str]) -> int:
        """Delete all history items for given turn_ids in a thread.

        Returns the number of deleted rows.
        """
        if not turn_ids:
            return 0
        placeholders = ",".join("?" for _ in turn_ids)
        db = self._conn
        cursor = await db.execute(
            f"""DELETE FROM runtime_history_items
                WHERE session_id = ? AND thread_id = ?
                AND turn_id IN ({placeholders})""",
            (self.session_id, thread_id, *turn_ids),
        )
        await db.commit()
        return int(cursor.rowcount or 0)

    async def copy_thread_items(self, source_thread_id: str, dest_thread_id: str) -> int:
        """Copy all history items from source to destination thread.

        Returns the number of copied items.
        """
        source_items = await self.load_items(source_thread_id)
        copied = 0
        for item in source_items:
            await self.append_item(HistoryItem(
                item_id=str(uuid.uuid4()),
                thread_id=dest_thread_id,
                turn_id=item.turn_id,
                role=item.role,
                content=item.content,
                payload=dict(item.payload),
                created_at=item.created_at,
            ))
            copied += 1
        return copied

    # ── #740: execution snapshots (in-flight turn state) ───────────────

    async def upsert_snapshot(
        self,
        turn_id: str,
        thread_id: str,
        *,
        status: str = "running",
        assistant_content: str = "",
        reasoning_content: str = "",
        tool_state: list[dict] | None = None,
        version: int = 1,
        reasoning_elapsed_s: float | None = None,
        reasoning_mode: str | None = None,
    ) -> None:
        """Persist (or update) an in-flight turn's execution snapshot.

        Called by the turn loop on a throttle (time/bytes threshold), on
        interruption, and on completion (before the snapshot is deleted).
        """
        db = self._conn
        await db.execute(
            """INSERT INTO execution_snapshots
               (turn_id, thread_id, session_id, status, assistant_content,
                reasoning_content, tool_state_json, version, updated_at,
                reasoning_elapsed_s, reasoning_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(turn_id) DO UPDATE SET
                status = excluded.status,
                assistant_content = excluded.assistant_content,
                reasoning_content = excluded.reasoning_content,
                tool_state_json = excluded.tool_state_json,
                version = excluded.version,
                updated_at = excluded.updated_at,
                reasoning_elapsed_s = excluded.reasoning_elapsed_s,
                reasoning_mode = excluded.reasoning_mode""",
            (
                turn_id, thread_id, self.session_id, status,
                assistant_content, reasoning_content,
                json.dumps(tool_state or [], ensure_ascii=False),
                version, time.time(), reasoning_elapsed_s, reasoning_mode,
            ),
        )
        await db.commit()

    async def get_snapshot(self, turn_id: str) -> dict[str, Any] | None:
        """Return the snapshot for a turn (scoped to this session), or None."""
        db = self._conn
        async with db.execute(
            "SELECT * FROM execution_snapshots WHERE turn_id = ? AND session_id = ?",
            (turn_id, self.session_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._snapshot_row_to_dict(row)

    async def get_interrupted_snapshots(self) -> list[dict[str, Any]]:
        """Return recoverable snapshots for this runtime's session.

        Queried by session_id (not thread_id) — threads use UUID-style ids
        ("thread-…") while the load path historically derived "sid:default";
        session_id matches on both sides (#740).
        Used by the session-load path to render "任务被中断" cards with the
        half-generated content the user saw before the interruption.
        """
        db = self._conn
        async with db.execute(
            """SELECT * FROM execution_snapshots
               WHERE session_id = ? AND status IN ('running', 'interrupted')
               ORDER BY updated_at DESC""",
            (self.session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._snapshot_row_to_dict(r) for r in rows]

    async def delete_snapshot(self, turn_id: str) -> None:
        """Remove a turn's snapshot (scoped to this session)."""
        db = self._conn
        await db.execute(
            "DELETE FROM execution_snapshots WHERE turn_id = ? AND session_id = ?",
            (turn_id, self.session_id),
        )
        await db.commit()

    async def clear_snapshots(self, thread_id: str) -> int:
        """Remove all snapshots for a thread (session deleted/archived)."""
        db = self._conn
        async with db.execute(
            "DELETE FROM execution_snapshots WHERE thread_id = ?",
            (thread_id,),
        ) as cursor:
            await db.commit()
            return int(cursor.rowcount or 0)

    @staticmethod
    def _snapshot_row_to_dict(row: Any) -> dict[str, Any]:
        """Convert a snapshot row to the dict shape consumed by the load path."""
        return {
            "turn_id": row["turn_id"],
            "thread_id": row["thread_id"],
            "status": row["status"],
            "assistant_content": row["assistant_content"],
            "reasoning_content": row["reasoning_content"],
            "reasoning_elapsed_s": row["reasoning_elapsed_s"],
            "reasoning_mode": row["reasoning_mode"],
            "tool_state": json.loads(row["tool_state_json"] or "[]"),
            "version": row["version"],
            "updated_at": row["updated_at"],
        }

    # ── Phase 19: compaction persistence ───────────────────────────────

    async def replace_messages_with_compaction(
        self,
        thread_id: str,
        turn_id: str,
        replacement_messages: list[dict[str, Any]],
        *,
        messages_before: int = 0,
        messages_after: int = 0,
        tokens_saved: int = 0,
    ) -> None:
        """Replace all history items for a thread with compacted messages.

        Deletes existing items (scoped by session_id), inserts the
        replacement messages, and records a compaction row with full
        audit metadata.
        """
        db = self._conn
        compaction_id = str(uuid.uuid4())
        # Wrap in transaction so DELETE+INSERT+compaction record are atomic.
        # If the process crashes between DELETE and commit, the transaction
        # is rolled back and no history is lost.
        await db.execute("BEGIN")
        try:
            # Delete existing items for this thread (session-scoped)
            await db.execute(
                "DELETE FROM runtime_history_items WHERE thread_id = ? AND session_id = ?",
                (thread_id, self.session_id),
            )
            # Insert replacement messages
            for msg in replacement_messages:
                await db.execute(
                    """INSERT INTO runtime_history_items
                       (item_id, thread_id, session_id, turn_id, role, content,
                        payload_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        thread_id,
                        self.session_id,
                        turn_id,
                        msg.get("role", "unknown"),
                        msg.get("content") or "",
                        json.dumps(
                            {
                                "message_fields": {
                                    k: v
                                    for k, v in msg.items()
                                    if k not in {"role", "content"}
                                },
                            },
                        ),
                        self._next_history_created_at(time.time()),
                    ),
                )
            # Record the compaction with full audit metadata
            await db.execute(
                """INSERT INTO runtime_compactions
                   (compaction_id, thread_id, session_id, turn_id,
                    messages_before, messages_after, tokens_saved,
                    replacement_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    compaction_id,
                    thread_id,
                    self.session_id,
                    turn_id,
                    messages_before,
                    messages_after,
                    tokens_saved,
                    json.dumps(replacement_messages),
                    time.time(),
                ),
            )
            await db.commit()
        except asyncio.CancelledError:
            # CancelledError 不是 Exception 子类，只接 except Exception 会漏掉
            # 它：回合任务取消落在事务中途时，不回滚会把本连接的写锁一直
            # 滞留（与 test_issue_886 database is locked 同源）。ROLLBACK 用
            # shield——任务已处于取消态，普通 await 会被立即再次取消。
            await asyncio.shield(db.execute("ROLLBACK"))
            raise
        except Exception:
            await db.execute("ROLLBACK")
            raise

    def _next_history_created_at(self, preferred: float | None = None) -> float:
        created_at = time.time() if preferred is None else preferred
        if created_at <= self._last_history_created_at:
            created_at = self._last_history_created_at + _HISTORY_CREATED_AT_STEP
        self._last_history_created_at = created_at
        return created_at


def _validate_role(role: str) -> str:
    if role not in VALID_HISTORY_ROLES:
        raise ValueError(f"Invalid history role: {role!r}")
    return role


def _sanitize_content(content: str) -> str:
    if len(content) <= MAX_HISTORY_CONTENT_CHARS:
        return content
    logger.warning(
        "Truncating history content from {} to {} chars",
        len(content),
        MAX_HISTORY_CONTENT_CHARS,
    )
    return _truncate_text(content, MAX_HISTORY_CONTENT_CHARS)


def _sanitize_payload(payload: dict[str, Any]) -> str:
    """Sanitize and JSON-serialize payload for storage.

    Returns the final JSON string (not dict) so the caller avoids
    a second serialization pass.  Enforces MAX_HISTORY_PAYLOAD_JSON_CHARS
    against the *stored* string, including the truncation-marker framing.
    """
    safe_payload = _json_safe(payload)
    payload_json = json.dumps(safe_payload, ensure_ascii=False)
    if len(payload_json) <= MAX_HISTORY_PAYLOAD_JSON_CHARS:
        return payload_json
    logger.warning(
        "Truncating history payload from {} to {} chars",
        len(payload_json),
        MAX_HISTORY_PAYLOAD_JSON_CHARS,
    )
    # Build truncated payload with iterative shrinking to account for
    # JSON escaping expansion in the preview string.
    preview_limit = max(0, MAX_HISTORY_PAYLOAD_JSON_CHARS - 80)
    preview = _truncate_text(payload_json, preview_limit)
    while True:
        final_json = json.dumps(
            {
                "truncated": True,
                "original_size_chars": len(payload_json),
                "preview": preview,
            },
            ensure_ascii=False,
        )
        if len(final_json) <= MAX_HISTORY_PAYLOAD_JSON_CHARS or preview_limit == 0:
            return final_json
        excess = len(final_json) - MAX_HISTORY_PAYLOAD_JSON_CHARS
        preview_limit = max(0, preview_limit - max(1, excess))
        preview = _truncate_text(payload_json, preview_limit)


def _truncate_text(value: str, limit: int) -> str:
    if limit <= len(_TRUNCATED_SUFFIX):
        return _TRUNCATED_SUFFIX[:limit]
    return value[: limit - len(_TRUNCATED_SUFFIX)] + _TRUNCATED_SUFFIX


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if hasattr(value, "value"):
        return _json_safe(value.value)
    return repr(value)
