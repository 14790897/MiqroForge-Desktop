"""SQLite-backed task trace storage."""

from __future__ import annotations

import json
import random
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from loguru import logger

from miqi.agent.trace.embedder import Embedder
from miqi.agent.trace.model import (
    TaskStep,
    TaskTrace,
    compute_trace_hash,
    deserialize_tool_calls,
    serialize_tool_calls,
)

T = TypeVar("T")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS task_traces (
    trace_hash TEXT PRIMARY KEY,
    parent_hash TEXT,
    session_id TEXT NOT NULL,
    task_name TEXT NOT NULL,
    goal TEXT NOT NULL,
    tool_calls TEXT NOT NULL,
    outcome TEXT NOT NULL,
    outcome_notes TEXT,
    embedding BLOB,
    created_at REAL NOT NULL,
    ended_at REAL,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_traces_session ON task_traces(session_id);
CREATE INDEX IF NOT EXISTS idx_traces_outcome ON task_traces(outcome);
CREATE INDEX IF NOT EXISTS idx_traces_created ON task_traces(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_parent ON task_traces(parent_hash);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS task_traces_fts
USING fts5(
    goal, outcome_notes,
    content=task_traces,
    content_rowid=rowid
);

CREATE TRIGGER IF NOT EXISTS task_traces_fts_insert AFTER INSERT ON task_traces BEGIN
    INSERT INTO task_traces_fts(rowid, goal, outcome_notes)
    VALUES (new.rowid, new.goal, new.outcome_notes);
END;

CREATE TRIGGER IF NOT EXISTS task_traces_fts_delete AFTER DELETE ON task_traces BEGIN
    INSERT INTO task_traces_fts(task_traces_fts, rowid, goal, outcome_notes)
    VALUES('delete', old.rowid, old.goal, old.outcome_notes);
END;

CREATE TRIGGER IF NOT EXISTS task_traces_fts_update AFTER UPDATE ON task_traces BEGIN
    INSERT INTO task_traces_fts(task_traces_fts, rowid, goal, outcome_notes)
    VALUES('delete', old.rowid, old.goal, old.outcome_notes);
    INSERT INTO task_traces_fts(rowid, goal, outcome_notes)
    VALUES (new.rowid, new.goal, new.outcome_notes);
END;
"""


class TraceStore:
    """SQLite-backed task trace storage with WAL and jitter retry."""

    _WRITE_MAX_RETRIES = 15
    _WRITE_RETRY_MIN_S = 0.020
    _WRITE_RETRY_MAX_S = 0.150
    _CHECKPOINT_EVERY_N_WRITES = 50

    def __init__(
        self,
        workspace: Path,
        *,
        enabled: bool = True,
        embedding_model: str = "intfloat/multilingual-e5-small",
        recover: bool = True,
    ):
        self.workspace = workspace
        self.enabled = enabled
        self.embedding_model = embedding_model
        self.db_path = workspace / "traces" / "TRACES.sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._write_count = 0
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            # CI 实测（ubuntu 并行负载）：1.0s 超时会在短暂写锁竞争下直接抛
            # sqlite3.OperationalError: database is locked（打断 886 中断重试
            # 流程）。放宽到 10s：等得过抖动，又不至于让追踪写入长时间阻塞。
            timeout=10.0,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()
        self._open_tasks: dict[str, dict[str, Any]] = {}
        self._open_tasks_file = self.db_path.parent / "OPEN_TASKS.json"
        if recover:
            self._recover_open_tasks()

    def _recover_open_tasks(self) -> None:
        """Auto-close any tasks that were open when the process last crashed."""
        if not self._open_tasks_file.exists():
            return
        try:
            data = json.loads(self._open_tasks_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
        except (json.JSONDecodeError, OSError):
            return
        # Pre-populate ALL entries before processing any to ensure mid-recovery
        # crash safety — _persist_open_tasks() only removes the sidecar when
        # self._open_tasks is empty.
        self._open_tasks.update(data)
        for session_id in list(data):
            self.end_task(
                session_id=session_id,
                outcome="partial",
                outcome_notes="Auto-closed on restart: process crashed before task_end.",
                tool_calls=[],
            )

    def _persist_open_tasks(self) -> None:
        """Write current open tasks to sidecar for crash recovery."""
        if not self._open_tasks:
            try:
                self._open_tasks_file.unlink(missing_ok=True)
            except OSError:
                pass
            return
        try:
            self._open_tasks_file.write_text(
                json.dumps(self._open_tasks, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    # --- Lifecycle ---

    def begin_task(
        self,
        session_id: str,
        task_name: str,
        goal: str,
        parent_hash: str | None = None,
    ) -> str:
        """Open a task for this session, auto-closing any previous open task."""
        if not self.enabled:
            return ""

        if self.get_current_task(session_id) is not None:
            self.end_task(
                session_id=session_id,
                outcome="partial",
                outcome_notes="Superseded by new task_begin",
                tool_calls=[],
            )

        if parent_hash is None:
            parent_hash = self._latest_trace_hash_for_session(session_id)

        task_id = uuid.uuid4().hex
        self._open_tasks[session_id] = {
            "id": task_id,
            "task_name": task_name,
            "goal": goal,
            "started_at": time.time(),
            "parent_hash": parent_hash,
            "steps": [],
        }
        self._persist_open_tasks()
        return task_id

    def record_step(
        self,
        session_id: str,
        tool_name: str,
        args_summary: str,
        result_summary: str,
    ) -> None:
        """Append one tool-call step to the currently open task. No-op if no open task."""
        if not self.enabled:
            return
        task = self._open_tasks.get(session_id)
        if task is None:
            return
        task.setdefault("steps", []).append(
            {
                "tool_name": tool_name,
                "args_summary": args_summary[:300],
                "result_summary": result_summary[:300],
                "timestamp": time.time(),
            }
        )

    def end_task(
        self,
        session_id: str,
        outcome: str,
        outcome_notes: str,
        tool_calls: list[TaskStep],
    ) -> str | None:
        """Close the currently open task for this session."""
        if not self.enabled:
            return None
        if outcome not in {"success", "partial", "failure"}:
            raise ValueError("outcome must be one of: success, partial, failure")

        open_task = self._open_tasks.pop(session_id, None)
        self._persist_open_tasks()
        if open_task is None:
            return None

        if not tool_calls:
            raw = open_task.get("steps") or []
            tool_calls = [
                TaskStep(
                    tool_name=str(s.get("tool_name", "")),
                    args_summary=str(s.get("args_summary", "")),
                    result_summary=str(s.get("result_summary", "")),
                    timestamp=float(s.get("timestamp", 0.0)),
                )
                for s in raw
                if isinstance(s, dict)
            ]

        tool_names = [step.tool_name for step in tool_calls]
        trace_hash = compute_trace_hash(str(open_task["goal"]), tool_names)
        embedding = self._encode_trace_text(str(open_task["goal"]), outcome_notes)
        trace = TaskTrace(
            trace_hash=trace_hash,
            parent_hash=open_task.get("parent_hash"),
            session_id=session_id,
            task_name=str(open_task["task_name"]),
            goal=str(open_task["goal"]),
            tool_calls=tool_calls,
            outcome=outcome,
            outcome_notes=outcome_notes,
            embedding=embedding,
            created_at=float(open_task["started_at"]),
            ended_at=time.time(),
            metadata={"task_id": open_task.get("id")},
        )
        self.upsert_trace(trace)
        return trace_hash

    def get_current_task(self, session_id: str) -> dict | None:
        return self._open_tasks.get(session_id)

    # --- Graph traversal ---

    def get_children(self, trace_hash: str) -> list[TaskTrace]:
        """Return all direct child traces (where parent_hash == trace_hash)."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM task_traces WHERE parent_hash = ? ORDER BY created_at ASC",
                (trace_hash,),
            )
            rows = cursor.fetchall()
        return [self._row_to_trace(row) for row in rows]

    def get_lineage(
        self, trace_hash: str, max_depth: int = 20
    ) -> list[TaskTrace]:
        """Return the ancestor chain for a trace, oldest-first.

        Uses a recursive CTE to walk parent_hash pointers up the tree.
        The trace identified by *trace_hash* itself is the last element.
        Returns an empty list if the trace does not exist.
        """
        sql = """
        WITH RECURSIVE ancestors(trace_hash, parent_hash, depth) AS (
            SELECT trace_hash, parent_hash, 0
            FROM task_traces
            WHERE trace_hash = :start
            UNION ALL
            SELECT t.trace_hash, t.parent_hash, a.depth + 1
            FROM task_traces t
            JOIN ancestors a ON t.trace_hash = a.parent_hash
            WHERE a.depth < :max_depth
        )
        SELECT tt.*
        FROM task_traces tt
        JOIN ancestors a ON tt.trace_hash = a.trace_hash
        ORDER BY a.depth DESC
        """
        with self._lock:
            cursor = self._conn.execute(sql, {"start": trace_hash, "max_depth": max_depth})
            rows = cursor.fetchall()
        return [self._row_to_trace(row) for row in rows]

    # --- Query ---

    def search_traces(
        self,
        query: str,
        limit: int = 5,
        threshold: float = 0.0,
    ) -> list[TaskTrace]:
        """Semantic search when embeddings are available, otherwise FTS5."""
        if not self.enabled or not query.strip() or limit <= 0:
            return []

        query_embedding = self._encode_query(query)
        if query_embedding is not None:
            semantic = self._search_semantic(query_embedding, limit, threshold)
            if semantic:
                return semantic

        return self._search_fts(query, limit, threshold)

    def get_trace(self, trace_hash: str) -> TaskTrace | None:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM task_traces WHERE trace_hash = ?", (trace_hash,)
            )
            row = cursor.fetchone()
        return self._row_to_trace(row) if row else None

    def list_recent(self, n: int = 20, outcome: str | None = None) -> list[TaskTrace]:
        if outcome:
            sql = "SELECT * FROM task_traces WHERE outcome = ? ORDER BY created_at DESC LIMIT ?"
            params: tuple[Any, ...] = (outcome, n)
        else:
            sql = "SELECT * FROM task_traces ORDER BY created_at DESC LIMIT ?"
            params = (n,)
        with self._lock:
            cursor = self._conn.execute(sql, params)
            rows = cursor.fetchall()
        return [self._row_to_trace(row) for row in rows]

    def upsert_trace(self, trace: TaskTrace) -> None:
        """Insert a trace, skipping an existing trace_hash."""
        if not self.enabled:
            return

        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(
                """INSERT OR IGNORE INTO task_traces
                   (trace_hash, parent_hash, session_id, task_name, goal, tool_calls,
                    outcome, outcome_notes, embedding, created_at, ended_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trace.trace_hash,
                    trace.parent_hash,
                    trace.session_id,
                    trace.task_name,
                    trace.goal,
                    serialize_tool_calls(trace.tool_calls),
                    trace.outcome,
                    trace.outcome_notes,
                    trace.embedding,
                    trace.created_at,
                    trace.ended_at,
                    json.dumps(trace.metadata or {}, ensure_ascii=False),
                ),
            )

        self._execute_write(_do)

    def close(self) -> None:
        with self._lock:
            if self._conn:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except Exception:
                    pass
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    # --- Internals ---

    def _init_schema(self) -> None:
        cursor = self._conn.cursor()
        cursor.executescript(SCHEMA_SQL)
        try:
            cursor.execute("SELECT * FROM task_traces_fts LIMIT 0")
        except sqlite3.OperationalError:
            cursor.executescript(FTS_SQL)
        self._conn.commit()

    def _execute_write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        last_err: Optional[Exception] = None
        for attempt in range(self._WRITE_MAX_RETRIES):
            try:
                with self._lock:
                    self._conn.execute("BEGIN IMMEDIATE")
                    try:
                        result = fn(self._conn)
                        self._conn.commit()
                    except BaseException:
                        try:
                            self._conn.rollback()
                        except Exception:
                            pass
                        raise
                self._write_count += 1
                if self._write_count % self._CHECKPOINT_EVERY_N_WRITES == 0:
                    self._try_wal_checkpoint()
                return result
            except sqlite3.OperationalError as exc:
                err_msg = str(exc).lower()
                if "locked" in err_msg or "busy" in err_msg:
                    last_err = exc
                    if attempt < self._WRITE_MAX_RETRIES - 1:
                        time.sleep(random.uniform(self._WRITE_RETRY_MIN_S, self._WRITE_RETRY_MAX_S))
                        continue
                raise
        raise last_err or sqlite3.OperationalError("database is locked after max retries")

    def _try_wal_checkpoint(self) -> None:
        try:
            with self._lock:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass

    def _encode_trace_text(self, goal: str, outcome_notes: str) -> bytes | None:
        try:
            return Embedder.get(self.embedding_model).encode_one(goal + "\n" + outcome_notes)
        except Exception as exc:
            logger.warning("trace embedding failed: {}", exc)
            return None

    def _encode_query(self, query: str) -> bytes | None:
        try:
            return Embedder.get(self.embedding_model).encode_one(query)
        except Exception as exc:
            logger.warning("trace query embedding failed: {}", exc)
            return None

    def _search_semantic(self, query_embedding: bytes, limit: int, threshold: float) -> list[TaskTrace]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM task_traces WHERE embedding IS NOT NULL ORDER BY created_at DESC"
            )
            rows = cursor.fetchall()
        scored: list[TaskTrace] = []
        for row in rows:
            trace = self._row_to_trace(row)
            if not trace.embedding:
                continue
            trace.similarity_score = max(0.0, Embedder.cosine(query_embedding, trace.embedding))
            if trace.similarity_score >= threshold:
                scored.append(trace)
        scored.sort(key=lambda t: t.similarity_score, reverse=True)
        return scored[:limit]

    def _search_fts(self, query: str, limit: int, threshold: float) -> list[TaskTrace]:
        fts_query = self._sanitize_fts5_query(query)
        if not fts_query:
            return []
        rows = self._fetch_fts_rows(fts_query, limit)
        if not rows:
            relaxed_query = self._relaxed_fts5_query(fts_query)
            if relaxed_query != fts_query:
                rows = self._fetch_fts_rows(relaxed_query, limit)

        traces = [self._row_to_trace(row) for row in rows]
        for idx, trace in enumerate(traces):
            trace.similarity_score = 1.0 / (idx + 1)
        if threshold > 0:
            traces = [trace for trace in traces if trace.similarity_score >= threshold]
        return traces

    def _fetch_fts_rows(self, fts_query: str, limit: int) -> list[sqlite3.Row]:
        sql = """
            SELECT t.*
            FROM task_traces_fts
            JOIN task_traces t ON t.rowid = task_traces_fts.rowid
            WHERE task_traces_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        with self._lock:
            try:
                cursor = self._conn.execute(sql, (fts_query, limit))
                rows = cursor.fetchall()
            except sqlite3.OperationalError:
                rows = []
        return list(rows)

    @staticmethod
    def _sanitize_fts5_query(query: str) -> str:
        sanitized = re.sub(r'[+{}()"^]', " ", query)
        sanitized = re.sub(r"\*+", "*", sanitized)
        sanitized = re.sub(r"(^|\s)\*", r"\1", sanitized)
        sanitized = re.sub(r"(?i)^(AND|OR|NOT)\b\s*", "", sanitized.strip())
        sanitized = re.sub(r"(?i)\s+(AND|OR|NOT)\s*$", "", sanitized.strip())
        return sanitized.strip()

    @staticmethod
    def _relaxed_fts5_query(query: str) -> str:
        terms = [term for term in query.split() if term.upper() not in {"AND", "OR", "NOT"}]
        return " OR ".join(terms) if len(terms) > 1 else query

    def _latest_trace_hash_for_session(self, session_id: str) -> str | None:
        with self._lock:
            cursor = self._conn.execute(
                """SELECT trace_hash FROM task_traces
                   WHERE session_id = ? ORDER BY created_at DESC LIMIT 1""",
                (session_id,),
            )
            row = cursor.fetchone()
        return str(row["trace_hash"]) if row else None

    @staticmethod
    def _row_to_trace(row: sqlite3.Row) -> TaskTrace:
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        return TaskTrace(
            trace_hash=row["trace_hash"],
            parent_hash=row["parent_hash"],
            session_id=row["session_id"],
            task_name=row["task_name"],
            goal=row["goal"],
            tool_calls=deserialize_tool_calls(row["tool_calls"] or "[]"),
            outcome=row["outcome"],
            outcome_notes=row["outcome_notes"] or "",
            embedding=row["embedding"],
            created_at=float(row["created_at"]),
            ended_at=float(row["ended_at"]) if row["ended_at"] is not None else None,
            metadata=metadata,
        )
