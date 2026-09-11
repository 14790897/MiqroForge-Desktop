"""Thread runtime — persistent thread lifecycle manager for RuntimeSession.

Owns thread CRUD operations on a SQLite store: create, rename, archive,
delete, fork, list. One instance per workspace.

Phase 22 hardening: uses a single persistent aiosqlite connection
instead of per-method connect() to prevent background-thread leaks
when the event loop shuts down.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite


@dataclass(frozen=True)
class RuntimeThread:
    """A runtime thread record."""

    thread_id: str
    session_id: str
    title: str
    status: str
    parent_thread_id: str | None
    created_at: float
    updated_at: float
    # Phase 36: Codex-style metadata fields
    forked_from_id: str | None = None
    ephemeral: bool = False
    cwd: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class ThreadRuntime:
    """Persistent thread lifecycle manager for RuntimeSession.

    Manages thread records in a SQLite database. One instance per
    workspace, shared across sessions via the common runtime DB.

    Uses a single persistent aiosqlite connection (opened in initialize(),
    closed in close()) to avoid background-thread leaks on event-loop
    shutdown.
    """

    def __init__(self, db_path: Path, *, session_id: str):
        self.db_path = db_path
        self.session_id = session_id
        self._db: aiosqlite.Connection | None = None

    # ── lifecycle ──────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Open persistent connection and create tables."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Same busy timeout as HistoryRuntime/StoredRuntime — see
        # LedgerRuntime.initialize for the cross-connection contention note.
        # isolation_level=None (autocommit) also prevents a cancelled turn
        # task from stranding an open write transaction on the shared DB.
        self._db = await aiosqlite.connect(
            str(self.db_path), timeout=30, isolation_level=None
        )
        await self._db.execute("PRAGMA journal_mode=WAL")
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS runtime_threads (
                thread_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                parent_thread_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                forked_from_id TEXT,
                ephemeral INTEGER NOT NULL DEFAULT 0,
                cwd TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (session_id, thread_id)
            )
        """)
        # Phase 36: migrate existing tables to add new columns
        for statement in [
            "ALTER TABLE runtime_threads ADD COLUMN forked_from_id TEXT",
            "ALTER TABLE runtime_threads ADD COLUMN ephemeral INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE runtime_threads ADD COLUMN cwd TEXT",
            "ALTER TABLE runtime_threads ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
        ]:
            try:
                await self._db.execute(statement)
            except aiosqlite.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
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
                "ThreadRuntime.initialize() must be called before use"
            )
        return self._db

    # ── thread CRUD ────────────────────────────────────────────────────

    async def create_thread(
        self,
        *,
        title: str,
        thread_id: str | None = None,
        parent_thread_id: str | None = None,
        # Phase 36: Codex-style metadata fields
        forked_from_id: str | None = None,
        ephemeral: bool = False,
        cwd: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RuntimeThread:
        now = time.time()
        tid = thread_id or f"thread-{str(uuid.uuid4())[:12]}"
        metadata_json = json.dumps(metadata or {})
        db = self._conn
        await db.execute(
            """INSERT INTO runtime_threads
               (thread_id, session_id, title, status, parent_thread_id,
                created_at, updated_at,
                forked_from_id, ephemeral, cwd, metadata_json)
               VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)""",
            (tid, self.session_id, title, parent_thread_id, now, now,
             forked_from_id, int(ephemeral), cwd, metadata_json),
        )
        await db.commit()
        thread = await self.get_thread(tid)
        assert thread is not None
        return thread

    async def get_thread(self, thread_id: str) -> RuntimeThread | None:
        db = self._conn
        cursor = await db.execute(
            """SELECT * FROM runtime_threads
               WHERE thread_id = ? AND session_id = ?""",
            (thread_id, self.session_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        metadata = {}
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
        return RuntimeThread(
            thread_id=row["thread_id"],
            session_id=row["session_id"],
            title=row["title"],
            status=row["status"],
            parent_thread_id=row["parent_thread_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            forked_from_id=row["forked_from_id"] if "forked_from_id" in row.keys() else None,
            ephemeral=bool(row["ephemeral"]) if "ephemeral" in row.keys() else False,
            cwd=row["cwd"] if "cwd" in row.keys() else None,
            metadata=metadata,
        )

    async def rename_thread(self, thread_id: str, title: str) -> RuntimeThread:
        await self._update(thread_id, title=title)
        thread = await self.get_thread(thread_id)
        if thread is None:
            raise KeyError(thread_id)
        return thread

    async def archive_thread(self, thread_id: str) -> RuntimeThread:
        await self._update(thread_id, status="archived")
        thread = await self.get_thread(thread_id)
        if thread is None:
            raise KeyError(thread_id)
        return thread

    async def delete_thread(self, thread_id: str) -> None:
        db = self._conn
        await db.execute(
            """DELETE FROM runtime_threads
               WHERE thread_id = ? AND session_id = ?""",
            (thread_id, self.session_id),
        )
        await db.commit()

    async def fork_thread(
        self, parent_thread_id: str, *, title: str
    ) -> RuntimeThread:
        parent = await self.get_thread(parent_thread_id)
        if parent is None:
            raise KeyError(parent_thread_id)
        return await self.create_thread(
            title=title,
            parent_thread_id=parent_thread_id,
            forked_from_id=parent_thread_id,
            cwd=parent.cwd,
            metadata=dict(parent.metadata),
        )

    async def list_threads(
        self, *, include_archived: bool = False,
    ) -> list[RuntimeThread]:
        query = "SELECT * FROM runtime_threads WHERE session_id = ?"
        params: tuple[object, ...] = (self.session_id,)
        if not include_archived:
            query += " AND status = 'active'"
        query += " ORDER BY updated_at DESC"
        db = self._conn
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [
            RuntimeThread(
                thread_id=row["thread_id"],
                session_id=row["session_id"],
                title=row["title"],
                status=row["status"],
                parent_thread_id=row["parent_thread_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                forked_from_id=row["forked_from_id"] if "forked_from_id" in row.keys() else None,
                ephemeral=bool(row["ephemeral"]) if "ephemeral" in row.keys() else False,
                cwd=row["cwd"] if "cwd" in row.keys() else None,
                metadata=ThreadRuntime._safe_json_load(row["metadata_json"] if "metadata_json" in row.keys() else "{}"),
            )
            for row in rows
        ]

    @staticmethod
    def _safe_json_load(raw: str) -> dict[str, object]:
        try:
            return json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

    async def _update(
        self,
        thread_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
    ) -> None:
        thread = await self.get_thread(thread_id)
        if thread is None:
            raise KeyError(thread_id)
        next_title = title if title is not None else thread.title
        next_status = status if status is not None else thread.status
        db = self._conn
        await db.execute(
            """UPDATE runtime_threads
               SET title = ?, status = ?, updated_at = ?
               WHERE thread_id = ? AND session_id = ?""",
            (next_title, next_status, time.time(), thread_id, self.session_id),
        )
        await db.commit()

    async def update_metadata(
        self, thread_id: str, metadata: dict[str, object],
    ) -> RuntimeThread:
        """Merge metadata into an existing thread (issue #680 跟进: 模式落盘)。

        ThreadRuntime (SQLite) has no generic upsert; this is the narrow
        metadata-write used by the bridge to persist reasoning_mode per
        thread.  Raises KeyError when the thread does not exist.
        """
        thread = await self.get_thread(thread_id)
        if thread is None:
            raise KeyError(thread_id)
        merged = dict(thread.metadata)
        merged.update(metadata)
        db = self._conn
        await db.execute(
            """UPDATE runtime_threads
               SET metadata_json = ?, updated_at = ?
               WHERE thread_id = ? AND session_id = ?""",
            (json.dumps(merged, ensure_ascii=False), time.time(), thread_id, self.session_id),
        )
        await db.commit()
        return RuntimeThread(
            thread_id=thread.thread_id,
            session_id=thread.session_id,
            title=thread.title,
            status=thread.status,
            parent_thread_id=thread.parent_thread_id,
            created_at=thread.created_at,
            updated_at=time.time(),
            forked_from_id=thread.forked_from_id,
            ephemeral=thread.ephemeral,
            cwd=thread.cwd,
            metadata=merged,
        )
