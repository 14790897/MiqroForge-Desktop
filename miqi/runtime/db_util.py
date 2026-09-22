"""Shared SQLite access layer for the runtime stores.

Why this module exists (#1012)
-----------------------------
``LedgerRuntime``, ``HistoryRuntime`` and ``ThreadRuntime`` each keep one
long-lived aiosqlite connection to the same ``runtime.db``.  aiosqlite runs
``db.execute(...)`` like this::

    cursor = await self._execute(self._conn.execute, sql, parameters)  # core.py:223
    return Cursor(self, cursor)

``_execute`` hands the statement to a worker thread and then awaits a future
(``core.py:160``).  If the calling task is cancelled **after** the worker has
produced the cursor but **before** the task resumes, the future stays finished
with the raw ``sqlite3.Cursor`` as its result, while the task raises
``CancelledError`` at that await.  The ``async with db.execute(...)`` never
attaches the cursor, so it is never closed.

An unclosed cursor keeps its statement active, which keeps the connection's
WAL read snapshot pinned.  Once any other connection commits, every later
write on that connection fails with ``SQLITE_BUSY_SNAPSHOT`` — reported as
``database is locked`` — *instantly*, because SQLite deliberately skips the
busy handler for a stale snapshot.  ``timeout=30`` cannot help, and a failed
write does not clear the snapshot: the connection stays write-dead.

Two defences live here:

1. **Prevention (load bearing).** Every statement runs inside
   :meth:`RuntimeDb.run`, which executes the operation in its own task and
   shields it.  Cancelling the caller can no longer interrupt a statement in
   flight, so no cursor can be orphaned in the first place.
2. **Recovery (safety net).** If a statement still fails with a stale-snapshot
   lock error, :meth:`RuntimeDb.run` recycles the connection so that later
   operations are not poisoned, and replays the operation once — but only when
   the failed attempt had not already modified rows.
3. **Quarantine on failure, under the lock.** Any failure escaping
   :meth:`RuntimeDb._run_locked` marks the connection dirty
   (:meth:`RuntimeDb._quarantine`) and the next operation recycles before
   touching it.  The flag is set *while the lock is still held* — a successor
   waiting on that lock is resumed by ``Lock.release`` before this task's done
   callbacks run, so a quarantine applied from a callback would arrive too
   late for exactly the operation it is meant to protect.

Ordinary write-lock contention is *not* retried and *not* masked: it waits for
the busy timeout as before and then propagates.

Invariants worth relying on (#1012 review)
------------------------------------------
* **No use-after-close.** An operation holds ``_lock`` for its whole duration
  and :meth:`RuntimeDb.close` takes the same lock before detaching the
  connection, so an operation that outlived a cancelled caller always finishes
  against a live connection. An operation that has not started yet when
  ``close`` runs sees ``_db is None`` and fails loudly instead.
* **Replay is transaction-atomic.** A stale-snapshot failure is only replayed
  when the connection modified nothing, and ``total_changes`` counts writes
  that a later ``ROLLBACK`` undid — so a partially applied transaction can
  never look untouched and get replayed.

Journal mode
------------
``journal_mode=WAL`` is attempted on every (re)open and retried briefly, but a
database that cannot use WAL (some network filesystems) is not fatal: the
store continues in rollback-journal mode, which :attr:`RuntimeDb.health`
reports as ``wal_mode: false``.  That is a *degraded compatibility mode* —
``SQLITE_BUSY_SNAPSHOT`` is a WAL concept, so the stale-snapshot recovery path
cannot apply there.  Stranding is still prevented, because that comes from
shielding the operation, not from the journal mode.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aiosqlite
from loguru import logger

_db_logger = logger.bind(component="runtime_db")

# Extended result codes (SQLite ≥ 3.7.16; exposed by Python ≥ 3.11 as
# ``sqlite3.Error.sqlite_errorcode``).
SQLITE_BUSY = 5
SQLITE_LOCKED = 6
SQLITE_BUSY_SNAPSHOT = 517

# Busy timeout used by every runtime store connection.
BUSY_TIMEOUT_S = 30.0

# A lock error that comes back much faster than the busy timeout cannot have
# come from the busy handler — the handler always waits the full timeout before
# giving up.  A fast failure therefore means SQLite refused the write outright,
# which in WAL mode points at a stale read snapshot.  Only used when SQLite did
# not report an extended result code.
_FAST_FAIL_FRACTION = 0.5

# ``PRAGMA journal_mode=WAL`` briefly needs exclusive access, so a concurrent
# opener on the same file can make the first attempt fail with a lock error.
_WAL_RETRY_ATTEMPTS = 3
_WAL_RETRY_DELAY_S = 0.05

DbOperation = Callable[[aiosqlite.Connection], Awaitable[Any]]


def sqlite_error_code(exc: BaseException) -> Optional[int]:
    """Return the extended SQLite result code, when the runtime exposes it."""
    code = getattr(exc, "sqlite_errorcode", None)
    return code if isinstance(code, int) else None


def format_sqlite_error(exc: BaseException) -> str:
    """Render a SQLite error together with its extended code.

    ``database is locked`` alone cannot be triaged; ``SQLITE_BUSY_SNAPSHOT``
    can.  Include the code/name in every log line we emit about a DB failure.
    """
    code = getattr(exc, "sqlite_errorcode", None)
    name = getattr(exc, "sqlite_errorname", None)
    if code is None and name is None:
        return str(exc)
    return f"{exc} [{name or 'code'}={code}]"


def is_lock_error(exc: BaseException) -> bool:
    """True for the busy/locked family of SQLite failures."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    text = str(exc).lower()
    return "database is locked" in text or "database table is locked" in text or "is busy" in text


def is_stale_snapshot_error(
    exc: BaseException, elapsed_s: float, *, timeout_s: float = BUSY_TIMEOUT_S
) -> bool:
    """Classify a lock error as a stranded WAL read snapshot.

    When SQLite reports an extended result code it is authoritative: only
    ``SQLITE_BUSY_SNAPSHOT`` qualifies.  A plain ``SQLITE_BUSY`` (5) can also
    come back *without* the busy handler waiting, so reclassifying it by timing
    would recycle the connection and replay genuine contention.

    The elapsed-time heuristic is the fallback for when no code is available
    (interpreters without ``sqlite_errorcode``, or mocked errors): a lock error
    that returns far faster than the busy timeout cannot have come from the
    busy handler, which always waits the full timeout before giving up.
    """
    if not is_lock_error(exc):
        return False
    code = sqlite_error_code(exc)
    if code is not None:
        return code == SQLITE_BUSY_SNAPSHOT
    return elapsed_s < timeout_s * _FAST_FAIL_FRACTION


# ── cursor-safe statement helpers ─────────────────────────────────────────
#
# Every helper closes its cursor.  ``async with db.execute(...)`` is close
# enough on the happy path, but it leaves the cursor unattached when the
# caller is cancelled during ``__aenter__``; explicit close in a ``finally``
# keeps the discipline visible and centralised.


async def _close_quietly(cursor: Any) -> None:
    try:
        await cursor.close()
    except Exception:  # pragma: no cover - close failures must not mask the real error
        pass


async def fetchone(
    db: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()
) -> Any:
    cursor = await db.execute(sql, params)
    try:
        return await cursor.fetchone()
    finally:
        await _close_quietly(cursor)


async def fetchall(
    db: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()
) -> list[Any]:
    cursor = await db.execute(sql, params)
    try:
        return list(await cursor.fetchall())
    finally:
        await _close_quietly(cursor)


async def execute_dml(
    db: aiosqlite.Connection, sql: str, params: tuple[Any, ...] = ()
) -> int:
    """Run a write statement and return ``rowcount``."""
    cursor = await db.execute(sql, params)
    try:
        return int(cursor.rowcount or 0)
    finally:
        await _close_quietly(cursor)


async def run_script(db: aiosqlite.Connection, sql: str) -> None:
    """Run a DDL/PRAGMA statement that returns no rows of interest."""
    cursor = await db.execute(sql)
    try:
        await cursor.fetchone()
    finally:
        await _close_quietly(cursor)


# ── connection owner ──────────────────────────────────────────────────────


class RuntimeDb:
    """Owns one runtime-store connection and serialises every operation on it.

    ``run`` is the only entry point for statements.  Callers must not touch
    :attr:`conn` directly: a statement issued outside ``run`` is cancellable
    and can strand its cursor again (see the module docstring).

    The lock is deliberately **not re-entrant** — only leaf operations may call
    ``run``.  A store method that wraps another store method (for example
    ``HistoryRuntime.append_message`` calling ``append_item``) must not take
    the lock around the inner call.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        name: str,
        prepare: Optional[DbOperation] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.name = name
        # ``prepare`` runs after every (re)open: schema DDL plus connection
        # settings such as ``row_factory``.  It must be idempotent.
        self._prepare = prepare
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self._dirty = False
        self._opening: Optional["asyncio.Task[None]"] = None
        # None until the first open resolved the journal mode.
        self.wal_mode: Optional[bool] = None
        # Observability (#1012): a non-zero recycle/orphan count means the
        # safety net fired, which points at a caller issuing statements
        # outside ``run``.
        self.recycle_count = 0
        self.orphaned_ops = 0
        self.last_recycle_reason: Optional[str] = None

    # ── lifecycle ─────────────────────────────────────────────────────

    async def open(self) -> None:
        """Open the connection, sharing a single attempt between callers.

        The opening task is recorded so :meth:`close` can wait for it.  Without
        that, an ``initialize()`` cancelled mid-open would leave ``_open``
        running; a later ``close`` would see no connection and return, and the
        connection (plus its worker thread) published afterwards would leak.

        This deliberately does not take ``_lock``: :meth:`_recycle` calls
        ``open`` while ``_run_locked`` already holds it.
        """
        if self._db is not None:
            return
        opening = self._opening
        if opening is None or opening.done():
            opening = asyncio.ensure_future(self._open())
            self._opening = opening
        try:
            await asyncio.shield(opening)
        except asyncio.CancelledError:
            if not opening.done():
                self.orphaned_ops += 1
                opening.add_done_callback(self._report_orphan)
            raise

    async def _open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(
            str(self.db_path), timeout=BUSY_TIMEOUT_S, isolation_level=None
        )
        try:
            mode = await self._ensure_wal(db)
            self.wal_mode = mode == "wal"
            if not self.wal_mode:
                # Refusing to start would break workspaces on filesystems where
                # SQLite cannot use WAL at all (network shares), so surface the
                # degraded mode — on this store and in ``health()`` — instead of
                # failing or silently assuming the transition happened.
                _db_logger.warning(
                    "RuntimeDb({}): journal_mode is {!r}, expected 'wal'; "
                    "continuing in rollback-journal mode",
                    self.name,
                    mode or "<unknown>",
                )
            if self._prepare is not None:
                await self._prepare(db)
        except BaseException:
            await self._close_connection(db)
            raise
        self._db = db

    async def _ensure_wal(self, db: aiosqlite.Connection) -> str:
        """Switch the database to WAL, retrying transient lock contention.

        A concurrent opener on the same file can make the transition fail with a
        lock error; that case is retried a bounded number of times.  Any other
        failure propagates, and a transition that completes without the mode
        changing is not retried — retrying cannot help there.
        """
        mode = ""
        for attempt in range(_WAL_RETRY_ATTEMPTS):
            try:
                cursor = await db.execute("PRAGMA journal_mode=WAL")
                try:
                    row = await cursor.fetchone()
                finally:
                    await _close_quietly(cursor)
            except sqlite3.OperationalError as exc:
                if not is_lock_error(exc) or attempt + 1 >= _WAL_RETRY_ATTEMPTS:
                    raise
                _db_logger.debug(
                    "RuntimeDb({}): journal_mode=WAL contended ({}); retrying",
                    self.name,
                    format_sqlite_error(exc),
                )
                await asyncio.sleep(_WAL_RETRY_DELAY_S * (attempt + 1))
                continue
            mode = str(row[0]).lower() if row else ""
            if mode == "wal":
                return mode
            break
        return mode

    async def close(self) -> None:
        """Close the connection once in-flight work has drained.

        Ordering matters here: an operation that outlived a cancelled caller is
        still holding ``_lock``, so taking the lock below is what keeps this
        from pulling the connection out from under it.  An operation that has
        not started yet will fail loudly instead of touching a closed
        connection, because :meth:`_run_locked` re-checks ``_db`` once it has
        the lock.
        """
        opening = self._opening
        if opening is not None and not opening.done():
            try:
                await asyncio.shield(opening)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _db_logger.warning(
                    "RuntimeDb({}): the in-flight open failed while closing: {}",
                    self.name,
                    exc,
                )
        self._opening = None
        async with self._lock:
            db, self._db = self._db, None
        if db is not None:
            await self._close_connection(db)

    async def _close_connection(self, db: aiosqlite.Connection) -> None:
        try:
            await db.close()
        except Exception as exc:  # pragma: no cover - defensive
            _db_logger.warning(
                "RuntimeDb({}): closing the connection failed: {}", self.name, exc
            )

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError(f"{self.name}: RuntimeDb.open() must be called before use")
        return self._db

    @property
    def is_open(self) -> bool:
        return self._db is not None

    # ── execution ─────────────────────────────────────────────────────

    async def run(self, fn: DbOperation) -> Any:
        """Run ``fn(connection)`` so that cancelling the caller strands nothing.

        The operation runs to completion in its own task; the caller only
        awaits a shield around it.  A cancelled caller stops waiting (and
        reports ``CancelledError`` as usual), but the statements already in
        flight finish and their cursors are closed.
        """
        job = asyncio.ensure_future(self._run_locked(fn))
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            if not job.done():
                self.orphaned_ops += 1
                _db_logger.debug(
                    "RuntimeDb({}): caller cancelled; the in-flight operation finishes "
                    "in the background (orphaned_ops={})",
                    self.name,
                    self.orphaned_ops,
                )
            # Always attach the reporter: it retrieves the result either way, so
            # a failure that arrives after the caller gave up is logged rather
            # than dropped as "exception was never retrieved".
            job.add_done_callback(self._report_orphan)
            raise

    def _report_orphan(self, job: "asyncio.Task[Any]") -> None:
        if job.cancelled():
            return
        exc = job.exception()
        if exc is None:
            # The operation completed fine after its caller left; the
            # connection is healthy and must not be recycled.
            return
        # Nobody is left to observe this failure — the caller that awaited the
        # operation is gone — so say so loudly.  The quarantine itself is
        # *not* done here: this callback runs after ``_run_locked`` has already
        # released the lock, which is too late to keep a queued successor from
        # reusing the connection (see RuntimeDb._quarantine).
        _db_logger.warning(
            "RuntimeDb({}): operation that outlived its cancelled caller failed: {}",
            self.name,
            format_sqlite_error(exc),
        )

    async def _run_locked(self, fn: DbOperation) -> Any:
        async with self._lock:
            if self._db is None:
                raise RuntimeError(f"{self.name}: RuntimeDb is closed")
            if self._dirty:
                await self._recycle("quarantined by an earlier failure")

            connection = self.conn
            started = time.monotonic()
            before = connection.total_changes
            try:
                return await fn(connection)
            except sqlite3.OperationalError as exc:
                if not is_stale_snapshot_error(exc, time.monotonic() - started):
                    # Ordinary contention: the busy handler already waited, and
                    # masking it here would hide a real lock conflict.
                    self._quarantine()
                    raise
                # A stale snapshot predates this call, so the write that tripped
                # over it is this call's first write.  Recycle so every later
                # operation on this store is healthy again, and replay once —
                # but only when the failed attempt wrote nothing, which keeps
                # the replay from duplicating rows.
                modified = connection.total_changes != before
                await self._recycle(f"stale snapshot: {format_sqlite_error(exc)}")
                if modified:
                    raise
                return await fn(self.conn)
            except BaseException:
                self._quarantine()
                raise

    def _quarantine(self) -> None:
        """Mark the connection untrustworthy; the next ``run`` recycles it.

        Called from inside :meth:`_run_locked`, i.e. **while it still holds the
        lock** — and that is the whole point.  ``Lock.release`` wakes a waiting
        caller with ``call_soon``, and this task's done callbacks are scheduled
        the same way *later* in the same step, so a successor resumed by the
        release runs before any done callback would.  Recording the failure
        there instead would let that successor read ``_dirty == False`` and
        issue its statement against the very connection this failure just
        called into question (#1012 review).
        """
        self._dirty = True

    async def _recycle(self, reason: str) -> None:
        db, self._db = self._db, None
        self._dirty = False
        if db is not None:
            await self._close_connection(db)
        self.recycle_count += 1
        self.last_recycle_reason = reason
        _db_logger.warning(
            "RuntimeDb({}): recycled the connection ({}) — recycle_count={}",
            self.name,
            reason,
            self.recycle_count,
        )
        await self.open()

    def mark_dirty(self) -> None:
        """Quarantine the connection for the next ``run`` call."""
        self._dirty = True

    def health(self) -> dict[str, Any]:
        """Counters for diagnostics/tests."""
        return {
            "name": self.name,
            "open": self.is_open,
            "wal_mode": self.wal_mode,
            "dirty": self._dirty,
            "recycle_count": self.recycle_count,
            "orphaned_ops": self.orphaned_ops,
            "last_recycle_reason": self.last_recycle_reason,
        }
