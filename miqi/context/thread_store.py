"""Thread metadata persistence using aiosqlite."""

from __future__ import annotations

import time
from pathlib import Path

import aiosqlite


class ThreadStore:
    """SQLite-based thread metadata store.

    Stores thread_id, agent_id, agent_type, status, plan_json,
    and timestamps. Used for persistence alongside JSONL sessions.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(self.db_path), timeout=30) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS threads (
                    thread_id TEXT PRIMARY KEY,
                    agent_id TEXT,
                    agent_type TEXT,
                    status TEXT DEFAULT 'active',
                    plan_json TEXT,
                    created_at REAL,
                    updated_at REAL
                )
            """)
            await db.commit()

    async def save_thread(
        self, thread_id: str, agent_id: str, agent_type: str
    ) -> None:
        """Save or update a thread record."""
        now = time.time()
        async with aiosqlite.connect(str(self.db_path), timeout=30) as db:
            await db.execute(
                """INSERT OR REPLACE INTO threads
                   (thread_id, agent_id, agent_type, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'active', ?, ?)""",
                (thread_id, agent_id, agent_type, now, now),
            )
            await db.commit()

    async def get_thread(self, thread_id: str) -> dict | None:
        """Get a thread record by ID."""
        async with aiosqlite.connect(str(self.db_path), timeout=30) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM threads WHERE thread_id = ?",
                (thread_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return dict(row)

    async def update_status(self, thread_id: str, status: str) -> None:
        """Update thread status."""
        async with aiosqlite.connect(str(self.db_path), timeout=30) as db:
            await db.execute(
                "UPDATE threads SET status = ?, updated_at = ? WHERE thread_id = ?",
                (status, time.time(), thread_id),
            )
            await db.commit()

    async def save_plan(self, thread_id: str, plan_json: str) -> None:
        """Save plan JSON for a thread."""
        async with aiosqlite.connect(str(self.db_path), timeout=30) as db:
            await db.execute(
                "UPDATE threads SET plan_json = ?, updated_at = ? WHERE thread_id = ?",
                (plan_json, time.time(), thread_id),
            )
            await db.commit()

    async def list_threads(self) -> list[dict]:
        """List all threads."""
        async with aiosqlite.connect(str(self.db_path), timeout=30) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM threads ORDER BY updated_at DESC"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
