"""Runtime ledger - append-only item log for replay and reconstruction.

Phase 24: Records every typed runtime event as immutable rows in a
session-scoped SQLite table with monotonically increasing sequence
numbers. Coexists with HistoryRuntime; designed to become the source
of truth for provider-message reconstruction and debug replay.
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


@dataclass(frozen=True)
class LedgerItem:
    """A single immutable item in the runtime ledger."""

    item_id: str
    session_id: str
    thread_id: str
    turn_id: str | None
    seq: int
    item_type: str
    role: str | None
    content: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0


class LedgerRuntime:
    """Append-only runtime item ledger scoped to one session.

    Uses a single persistent aiosqlite connection (opened in initialize(),
    closed in close()) to avoid background-thread leaks on event-loop
    shutdown. All queries are filtered by session_id.
    """

    def __init__(self, db_path: Path, *, session_id: str):
        self.db_path = db_path
        self.session_id = session_id
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Same busy timeout as HistoryRuntime/StoredRuntime: all runtime
        # stores open independent connections on the same DB file, and a
        # cancelled turn's in-flight write can hold the file lock while a
        # retry turn's mirror write waits. The default 5s is too short and
        # surfaces as "database is locked" (flaky test_issue_886).
        #
        # isolation_level=None (autocommit) closes the OTHER lock-leak:
        # with implicit transactions, a turn task cancelled between the
        # INSERT await and the commit await leaves an open transaction
        # holding the write lock indefinitely — the busy timeout only
        # bounds the wait, it does not release the stranded lock.
        self._db = await aiosqlite.connect(
            str(self.db_path), timeout=30, isolation_level=None
        )
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS runtime_ledger_items (
                item_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                turn_id TEXT,
                seq INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                role TEXT,
                content TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_runtime_ledger_thread_seq
            ON runtime_ledger_items(session_id, thread_id, seq)
        """)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("LedgerRuntime.initialize() must be called before use")
        return self._db

    async def append_item(
        self,
        *,
        thread_id: str,
        item_type: str,
        turn_id: str | None = None,
        role: str | None = None,
        content: str = "",
        payload: dict[str, Any] | None = None,
    ) -> LedgerItem:
        db = self._conn
        payload_dict = dict(payload or {})
        async with self._write_lock:
            async with db.execute(
                """SELECT COALESCE(MAX(seq), 0) + 1
                   FROM runtime_ledger_items
                   WHERE session_id = ? AND thread_id = ?""",
                (self.session_id, thread_id),
            ) as cursor:
                row = await cursor.fetchone()
            seq = int(row[0])
            item = LedgerItem(
                item_id=str(uuid.uuid4()),
                session_id=self.session_id,
                thread_id=thread_id,
                turn_id=turn_id,
                seq=seq,
                item_type=item_type,
                role=role,
                content=content,
                payload=payload_dict,
                created_at=time.time(),
            )
            # Phase 25: sanitize payload so non-JSON types don't crash
            safe_payload = self._sanitize_payload(item.payload)
            await db.execute(
                """INSERT INTO runtime_ledger_items
                   (item_id, session_id, thread_id, turn_id, seq, item_type,
                    role, content, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.item_id,
                    item.session_id,
                    item.thread_id,
                    item.turn_id,
                    item.seq,
                    item.item_type,
                    item.role,
                    item.content,
                    json.dumps(safe_payload),
                    item.created_at,
                ),
            )
            await db.commit()
        return item

    @staticmethod
    def _sanitize_payload(obj: Any) -> Any:
        """Recursively convert non-JSON-serializable values to safe types.

        bytes → base64-encoded string (with b64: prefix)
        set → sorted list
        Enum → .value
        Other non-serializable types → repr() string (with str: prefix)
        """
        if isinstance(obj, dict):
            return {k: LedgerRuntime._sanitize_payload(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [LedgerRuntime._sanitize_payload(v) for v in obj]
        if isinstance(obj, bytes):
            import base64
            return "b64:" + base64.b64encode(obj).decode("ascii")
        if isinstance(obj, set):
            return sorted(LedgerRuntime._sanitize_payload(v) for v in obj)
        if isinstance(obj, str):
            return obj
        if isinstance(obj, (int, float, bool)):
            return obj
        if obj is None:
            return obj
        # Handle Enum — use .value attribute
        if hasattr(obj, "value"):
            return obj.value
        # Fallback: convert to string with marker
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return f"str:{obj!r}"

    async def load_items(self, thread_id: str) -> list[LedgerItem]:
        db = self._conn
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM runtime_ledger_items
               WHERE session_id = ? AND thread_id = ?
               ORDER BY seq ASC""",
            (self.session_id, thread_id),
        ) as cursor:
            rows = await cursor.fetchall()
        items: list[LedgerItem] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError) as exc:
                from loguru import logger
                logger.warning(
                    "LedgerRuntime: skipping corrupt payload_json for item {}: {}",
                    row["item_id"], exc,
                )
                payload = {}
            items.append(LedgerItem(
                item_id=row["item_id"],
                session_id=row["session_id"],
                thread_id=row["thread_id"],
                turn_id=row["turn_id"],
                seq=row["seq"],
                item_type=row["item_type"],
                role=row["role"],
                content=row["content"],
                payload=payload,
                created_at=row["created_at"],
            ))
        return items

    async def load_provider_messages(self, thread_id: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for item in await self.load_items(thread_id):
            if item.item_type != "message" or item.role is None:
                continue
            message: dict[str, Any] = {"role": item.role, "content": item.content}
            message.update(item.payload.get("message_fields", {}))
            messages.append(message)
        return messages

    # ── Phase 36: turn listing, fork copy, rollback ───────────────────────

    async def list_turn_ids(self, thread_id: str) -> list[str]:
        """Return turn_ids in ledger sequence order for a thread."""
        seen: dict[str, int] = {}
        for item in await self.load_effective_items(thread_id):
            if item.turn_id and item.turn_id not in seen:
                seen[item.turn_id] = item.seq
        return sorted(seen.keys(), key=lambda turn_id: seen[turn_id])

    async def load_turn_items(self, thread_id: str, turn_id: str) -> list[LedgerItem]:
        """Return ledger items for a specific turn in a thread."""
        return [
            item for item in await self.load_effective_items(thread_id)
            if item.turn_id == turn_id
        ]

    async def copy_thread_items(self, source_thread_id: str, dest_thread_id: str) -> int:
        """Copy all effective ledger items from source to destination thread."""
        copied = 0
        for item in await self.load_effective_items(source_thread_id):
            payload = dict(item.payload)
            payload["copied_from_thread_id"] = source_thread_id
            payload["copied_from_item_id"] = item.item_id
            await self.append_item(
                thread_id=dest_thread_id,
                turn_id=item.turn_id,
                item_type=item.item_type,
                role=item.role,
                content=item.content,
                payload=payload,
            )
            copied += 1
        return copied

    async def append_rollback_marker(
        self, thread_id: str, *, drop_last_turns: int
    ) -> LedgerItem:
        """Append a rollback marker removing the last N turns."""
        turn_ids = await self.list_turn_ids(thread_id)
        removed = turn_ids[-drop_last_turns:] if drop_last_turns > 0 else []
        return await self.append_item(
            thread_id=thread_id,
            item_type="thread_rollback",
            content="",
            payload={
                "drop_last_turns": drop_last_turns,
                "removed_turn_ids": removed,
            },
        )

    async def load_effective_items(self, thread_id: str) -> list[LedgerItem]:
        """Load ledger items filtered through rollback markers.

        Items from turns removed by rollback markers are excluded.
        Rollback marker items themselves are also excluded.
        """
        items = await self.load_items(thread_id)
        removed: set[str] = set()
        for item in items:
            if item.item_type == "thread_rollback":
                removed.update(item.payload.get("removed_turn_ids", []))
        return [
            item for item in items
            if item.item_type != "thread_rollback"
            and (item.turn_id is None or item.turn_id not in removed)
        ]
