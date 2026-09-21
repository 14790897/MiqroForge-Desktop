"""Runtime ledger - append-only item log for replay and reconstruction.

Phase 24: Records every typed runtime event as immutable rows in a
session-scoped SQLite table with monotonically increasing sequence
numbers. Coexists with HistoryRuntime; designed to become the source
of truth for provider-message reconstruction and debug replay.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from miqi.runtime.db_util import (
    RuntimeDb,
    execute_dml,
    fetchall,
    fetchone,
    run_script,
)

_MAX_SEQ_SQL = """SELECT COALESCE(MAX(seq), 0) + 1
                  FROM runtime_ledger_items
                  WHERE session_id = ? AND thread_id = ?"""

_INSERT_SQL = """INSERT INTO runtime_ledger_items
                 (item_id, session_id, thread_id, turn_id, seq, item_type,
                  role, content, payload_json, created_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


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
        self._dbx: RuntimeDb | None = None

    async def initialize(self) -> None:
        # All statements go through RuntimeDb (#1012): it keeps one persistent
        # aiosqlite connection with a 30s busy timeout and autocommit, and runs
        # each operation in a shielded task so a cancelled turn task can no
        # longer orphan a cursor and pin a stale WAL read snapshot on it.
        self._dbx = RuntimeDb(self.db_path, name="ledger", prepare=self._prepare)
        await self._dbx.open()

    @staticmethod
    async def _prepare(db: aiosqlite.Connection) -> None:
        db.row_factory = aiosqlite.Row
        await run_script(db, """
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
        await run_script(db, """
            CREATE INDEX IF NOT EXISTS idx_runtime_ledger_thread_seq
            ON runtime_ledger_items(session_id, thread_id, seq)
        """)
        await db.commit()

    async def close(self) -> None:
        if self._dbx is not None:
            await self._dbx.close()
            self._dbx = None

    @property
    def _db(self) -> aiosqlite.Connection | None:
        """The live connection, or None once closed (diagnostics/tests)."""
        if self._dbx is None or not self._dbx.is_open:
            return None
        return self._dbx.conn

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._dbx is None:
            raise RuntimeError("LedgerRuntime.initialize() must be called before use")
        return self._dbx.conn

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
        payload_dict = dict(payload or {})
        # Phase 25: sanitize payload so non-JSON types don't crash
        safe_payload = json.dumps(self._sanitize_payload(payload_dict))
        item_id = str(uuid.uuid4())
        created_at = time.time()

        async def _append(db: aiosqlite.Connection) -> int:
            row = await fetchone(db, _MAX_SEQ_SQL, (self.session_id, thread_id))
            seq = int(row[0]) if row is not None else 1
            await execute_dml(
                db,
                _INSERT_SQL,
                (
                    item_id,
                    self.session_id,
                    thread_id,
                    turn_id,
                    seq,
                    item_type,
                    role,
                    content,
                    safe_payload,
                    created_at,
                ),
            )
            await db.commit()
            return seq

        seq = await self._require_db().run(_append)
        return LedgerItem(
            item_id=item_id,
            session_id=self.session_id,
            thread_id=thread_id,
            turn_id=turn_id,
            seq=seq,
            item_type=item_type,
            role=role,
            content=content,
            payload=payload_dict,
            created_at=created_at,
        )

    def _require_db(self) -> RuntimeDb:
        if self._dbx is None:
            raise RuntimeError("LedgerRuntime.initialize() must be called before use")
        return self._dbx

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
        rows = await self._require_db().run(
            lambda db: fetchall(
                db,
                """SELECT * FROM runtime_ledger_items
                   WHERE session_id = ? AND thread_id = ?
                   ORDER BY seq ASC""",
                (self.session_id, thread_id),
            )
        )
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
        self,
        thread_id: str,
        *,
        drop_last_turns: int = 0,
        drop_from_turn_id: str | None = None,
    ) -> LedgerItem:
        """Append a rollback marker removing turns.

        Either the last ``drop_last_turns`` turns, or — when
        ``drop_from_turn_id`` is given — that turn and every later turn.
        ``drop_from_turn_id`` wins when both are provided.
        """
        turn_ids = await self.list_turn_ids(thread_id)
        if drop_from_turn_id is not None and drop_from_turn_id in turn_ids:
            removed = turn_ids[turn_ids.index(drop_from_turn_id):]
        elif drop_last_turns > 0:
            removed = turn_ids[-drop_last_turns:]
        else:
            removed = []
        return await self.append_item(
            thread_id=thread_id,
            item_type="thread_rollback",
            content="",
            payload={
                "drop_last_turns": drop_last_turns,
                "drop_from_turn_id": drop_from_turn_id,
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
