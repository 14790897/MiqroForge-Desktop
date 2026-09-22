"""Regression tests for the runtime.db write-lock failure (#1012).

The CI flake was: a turn task cancelled while aiosqlite had already produced a
cursor but had not yet handed it to ``async with db.execute(...)``.  The cursor
was never closed, so the connection kept a stale WAL read snapshot and every
later write on it failed with ``SQLITE_BUSY_SNAPSHOT`` ("database is locked")
*instantly* — ``timeout=30`` is bypassed for a stale snapshot.

These tests pin both halves of the fix:

* prevention — cancelling the caller must not cancel (or strand) the operation;
* recovery — a connection that is already poisoned must be recycled instead of
  keeping the store write-dead forever.
"""

from __future__ import annotations

import asyncio
import sqlite3

import aiosqlite
import pytest

from miqi.runtime.db_util import (
    RuntimeDb,
    execute_dml,
    fetchall,
    fetchone,
    format_sqlite_error,
    is_stale_snapshot_error,
)
from miqi.runtime.ledger_runtime import LedgerRuntime


async def _prepare_items(conn: aiosqlite.Connection) -> None:
    await execute_dml(conn, "CREATE TABLE IF NOT EXISTS t (v TEXT)")
    await conn.commit()


async def _poison_connection(db: aiosqlite.Connection, db_path):
    """Reproduce the CI strand on ``db``'s connection.

    A multi-row SELECT whose cursor is never closed leaves the statement
    active, holding a WAL read snapshot.  The cursor is returned so the caller
    keeps it referenced — that is what the real failure looks like: the
    cancelled turn task's traceback (and the aiosqlite future it holds) keeps
    the cursor alive for the rest of the process, so nothing ever resets the
    statement and no later write clears the staleness.

    A commit from another connection then makes the snapshot stale; the next
    write on ``db`` fails instantly with SQLITE_BUSY_SNAPSHOT.
    """
    stranded = await db.execute("SELECT item_id, content FROM runtime_ledger_items")
    other = await aiosqlite.connect(str(db_path), timeout=2, isolation_level=None)
    try:
        await other.execute(
            """INSERT INTO runtime_ledger_items
               (item_id, session_id, thread_id, turn_id, seq, item_type,
                role, content, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("foreign-1", "c1:s1", "t-foreign", None, 9999, "message", "user", "x", "{}", 0.0),
        )
        await other.commit()
    finally:
        await other.close()
    return stranded


async def test_stale_snapshot_write_is_recycled_and_lands(tmp_path):
    """A poisoned connection must not leave the ledger write-dead.

    Without the recovery path this fails with
    ``sqlite3.OperationalError: database is locked`` — the exact CI failure.
    """
    db_path = tmp_path / "runtime.db"
    ledger = LedgerRuntime(db_path, session_id="c1:s1")
    await ledger.initialize()
    try:
        first = await ledger.append_item(
            thread_id="t1", item_type="message", role="user", content="before"
        )
        assert ledger._dbx is not None

        _stranded = await _poison_connection(ledger._dbx.conn, db_path)

        item = await ledger.append_item(
            thread_id="t1", item_type="message", role="user", content="after"
        )
        assert item.seq > first.seq
        assert ledger._dbx.recycle_count == 1
        # Keep the stranded cursor referenced for the whole test: dropping it
        # would let CPython finalize the statement and hide the regression.
        assert _stranded is not None

        # The store keeps working on the recycled connection.
        items = await ledger.load_items("t1")
        assert [i.content for i in items] == ["before", "after"]
    finally:
        await ledger.close()


async def test_cancelled_caller_does_not_cancel_the_operation(tmp_path):
    """Cancelling the caller must not abandon an in-flight operation.

    The operation finishes on its own connection (closing its cursors), so no
    stale snapshot is ever created; the caller still sees CancelledError.
    """
    db = RuntimeDb(tmp_path / "runtime.db", name="test")
    await db.open()
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def operation(conn: aiosqlite.Connection) -> str:
        started.set()
        await release.wait()
        finished.set()
        return "done"

    try:
        task = asyncio.create_task(db.run(operation))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        release.set()
        await asyncio.wait_for(finished.wait(), timeout=5)
        assert db.orphaned_ops == 1
        assert db.recycle_count == 0

        # The connection is still healthy.
        assert await db.run(lambda conn: fetchone(conn, "SELECT 1")) == (1,)
    finally:
        await db.close()


async def test_failed_orphan_quarantines_the_connection(tmp_path):
    """An orphaned operation that FAILED must force a recycle on the next run.

    ``_report_orphan`` is the only observer left once the caller has been
    cancelled, so a failure there has to quarantine the connection — the
    failure may have left it unusable and nothing else will notice.

    The failure raised here is deliberately *not* a lock error: a
    stale-snapshot lock error is already recovered inside ``_run_locked``, so
    it would not exercise the quarantine path at all.
    """
    db = RuntimeDb(tmp_path / "runtime.db", name="test")
    await db.open()
    started = asyncio.Event()
    release = asyncio.Event()

    async def failing_operation(conn: aiosqlite.Connection) -> str:
        started.set()
        await release.wait()
        raise sqlite3.OperationalError("no such table: nope")

    try:
        task = asyncio.create_task(db.run(failing_operation))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert db.orphaned_ops == 1

        release.set()
        # Wait for the orphaned operation to finish so its done-callback runs.
        for _ in range(250):
            if db.health()["dirty"]:
                break
            await asyncio.sleep(0.02)
        assert db.health()["dirty"] is True, (
            "a failed orphan left the connection unquarantined"
        )
        assert db.recycle_count == 0

        # The next operation recycles before issuing anything.
        assert await db.run(lambda conn: fetchone(conn, "SELECT 1")) == (1,)
        assert db.recycle_count == 1
        assert db.health()["dirty"] is False
    finally:
        await db.close()


async def test_successful_orphan_does_not_recycle(tmp_path):
    """The quarantine must not fire for an orphan that completed cleanly.

    Recycling a healthy connection is pure churn, and the orphaned operation
    is the common case on every user stop — so the success path has to stay
    exactly as cheap as it was.
    """
    db = RuntimeDb(tmp_path / "runtime.db", name="test")
    await db.open()
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def operation(conn: aiosqlite.Connection) -> str:
        started.set()
        await release.wait()
        finished.set()
        return "done"

    try:
        task = asyncio.create_task(db.run(operation))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        release.set()
        await asyncio.wait_for(finished.wait(), timeout=5)
        # Give the orphaned task a few event-loop turns to settle so its
        # done-callback has run.  (The flag it would set is False either way,
        # so this bounds the wait rather than racing it — the real assertion
        # is that the next run does not recycle.)
        for _ in range(10):
            await asyncio.sleep(0.01)
        assert db.health()["dirty"] is False

        assert await db.run(lambda conn: fetchone(conn, "SELECT 1")) == (1,)
        assert db.recycle_count == 0
    finally:
        await db.close()


async def test_append_item_survives_caller_cancellation(tmp_path):
    """Same story through a real store method (the CI call site)."""
    db_path = tmp_path / "runtime.db"
    ledger = LedgerRuntime(db_path, session_id="c1:s1")
    await ledger.initialize()
    try:
        task = asyncio.create_task(
            ledger.append_item(
                thread_id="t1", item_type="message", role="assistant", content="streamed"
            )
        )
        await asyncio.sleep(0)  # let the operation start
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert ledger._dbx is not None
        assert ledger._dbx.recycle_count == 0

        item = await ledger.append_item(
            thread_id="t1", item_type="message", role="user", content="retry"
        )
        assert item.seq >= 1
        contents = [i.content for i in await ledger.load_items("t1")]
        assert "retry" in contents
    finally:
        await ledger.close()


def test_lock_classification_does_not_mask_real_contention():
    """Only stale-snapshot failures may trigger a recycle.

    An ordinary write-lock conflict waits for the busy timeout and then
    propagates; treating it as a stale snapshot would hide real contention.
    """
    # No extended code available (mocked/older interpreters): fall back to timing.
    busy = sqlite3.OperationalError("database is locked")
    assert is_stale_snapshot_error(busy, 0.001) is True
    assert is_stale_snapshot_error(busy, 29.0) is False

    # Non-lock failures are never reclassified.
    assert is_stale_snapshot_error(sqlite3.OperationalError("no such table: t"), 0.001) is False

    # When SQLite reports a code, it is authoritative — a plain SQLITE_BUSY that
    # returns fast is still ordinary contention, not a stale snapshot.
    class BusyError(sqlite3.OperationalError):
        sqlite_errorcode = 5
        sqlite_errorname = "SQLITE_BUSY"

    assert is_stale_snapshot_error(BusyError("database is locked"), 0.001) is False

    class SnapshotError(sqlite3.OperationalError):
        sqlite_errorcode = 517
        sqlite_errorname = "SQLITE_BUSY_SNAPSHOT"

    assert is_stale_snapshot_error(SnapshotError("database is locked"), 29.0) is True


async def test_concurrent_opens_share_a_single_connection(tmp_path):
    """Concurrent open() calls must not each create their own connection."""
    opened: list[object] = []

    async def counting_prepare(conn) -> None:
        opened.append(conn)

    db = RuntimeDb(tmp_path / "runtime.db", name="test", prepare=counting_prepare)
    await asyncio.gather(db.open(), db.open(), db.open())
    try:
        assert len(opened) == 1
        assert db.conn is opened[0]
    finally:
        await db.close()


async def test_close_waits_for_an_in_flight_open(tmp_path):
    """A cancelled initialize() must not leak the connection open() publishes.

    Without the recorded opening task, close() sees no connection, returns, and
    the still-running open() then publishes a connection (and its worker thread)
    that nobody owns.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_prepare(conn) -> None:
        started.set()
        await release.wait()

    db = RuntimeDb(tmp_path / "runtime.db", name="test", prepare=slow_prepare)
    opener = asyncio.create_task(db.open())
    await asyncio.wait_for(started.wait(), timeout=5)
    opener.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opener

    closer = asyncio.create_task(db.close())
    await asyncio.sleep(0)
    # close() must not have returned while the open is still in flight.
    assert not closer.done()

    release.set()
    await asyncio.wait_for(closer, timeout=5)
    assert db.is_open is False


async def test_wal_transition_retries_lock_contention(tmp_path, monkeypatch):
    """A concurrent opener can make the WAL transition fail once; retry it."""
    real_execute = aiosqlite.core.Connection.execute
    calls = {"n": 0}

    async def flaky_execute(self, sql, parameters=None):
        if "journal_mode" in sql:
            calls["n"] += 1
            if calls["n"] == 1:
                raise sqlite3.OperationalError("database is locked")
        return await real_execute(self, sql, parameters)

    monkeypatch.setattr(aiosqlite.core.Connection, "execute", flaky_execute)
    db = RuntimeDb(tmp_path / "runtime.db", name="test")
    await db.open()
    try:
        assert calls["n"] == 2
        assert db.wal_mode is True
        assert db.health()["wal_mode"] is True
    finally:
        await db.close()


def test_sqlite_errors_are_reported_with_their_extended_code():
    """'database is locked' alone cannot be triaged; the code must be logged."""
    assert format_sqlite_error(sqlite3.OperationalError("database is locked")) == (
        "database is locked"
    )

    class CodedError(sqlite3.OperationalError):
        sqlite_errorcode = 517
        sqlite_errorname = "SQLITE_BUSY_SNAPSHOT"

    rendered = format_sqlite_error(CodedError("database is locked"))
    assert "SQLITE_BUSY_SNAPSHOT" in rendered
    assert "517" in rendered


class _SnapshotError(sqlite3.OperationalError):
    """A stale-snapshot failure, as SQLite reports it."""

    sqlite_errorcode = 517
    sqlite_errorname = "SQLITE_BUSY_SNAPSHOT"


async def test_close_does_not_pull_the_connection_from_a_shielded_task(tmp_path):
    """cancel -> close -> let the background operation finish.

    The operation outlives its cancelled caller, so ``close()`` must wait for it
    rather than detaching the connection underneath it: otherwise the fix for
    ``database is locked`` would just trade it for a use-after-close race.
    """
    db = RuntimeDb(tmp_path / "runtime.db", name="test", prepare=_prepare_items)
    await db.open()
    started = asyncio.Event()
    release = asyncio.Event()
    outcome: dict[str, object] = {}

    async def operation(conn: aiosqlite.Connection) -> str:
        started.set()
        await release.wait()
        # close() must not have taken this connection away in the meantime.
        await execute_dml(conn, "INSERT INTO t (v) VALUES ('late')")
        await conn.commit()
        outcome["wrote"] = True
        return "done"

    try:
        task = asyncio.create_task(db.run(operation))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        closer = asyncio.create_task(db.close())
        await asyncio.sleep(0)
        # The operation still holds the store lock, so close() cannot be done.
        assert not closer.done()

        release.set()
        await asyncio.wait_for(closer, timeout=5)

        assert outcome.get("wrote") is True, "the in-flight operation was cut off"
        assert db.is_open is False
        assert db.orphaned_ops == 1
        # No background task left running.
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert pending == []
    finally:
        if db.is_open:
            await db.close()


async def test_total_changes_counts_writes_a_rollback_undid(tmp_path):
    """The replay guard's core assumption, asserted directly.

    ``_run_locked`` only replays when the connection modified nothing.  That is
    sound only if ``total_changes`` also counts writes that a later ROLLBACK
    undid — otherwise a partially applied transaction would look untouched.
    """
    db = RuntimeDb(tmp_path / "runtime.db", name="test", prepare=_prepare_items)
    await db.open()
    try:
        await db.run(lambda conn: execute_dml(conn, "INSERT INTO t (v) VALUES ('kept')"))
        before = db.conn.total_changes

        async def rolled_back(conn: aiosqlite.Connection) -> None:
            await conn.execute("BEGIN")
            await execute_dml(conn, "INSERT INTO t (v) VALUES ('undone')")
            await conn.execute("ROLLBACK")

        await db.run(rolled_back)

        assert db.conn.total_changes > before
        rows = await db.run(lambda conn: fetchall(conn, "SELECT v FROM t"))
        assert [r[0] for r in rows] == ["kept"]
    finally:
        await db.close()


async def test_partially_written_operation_is_not_replayed(tmp_path):
    """A stale snapshot must not replay an operation that already wrote rows.

    ``total_changes`` cannot prove a multi-statement operation is safe to
    replay once part of it landed, so the guard refuses instead of risking
    duplicate writes — the retry boundary is the whole operation, and it stays
    transaction-atomic by only ever replaying untouched ones.
    """
    db = RuntimeDb(tmp_path / "runtime.db", name="test", prepare=_prepare_items)
    await db.open()
    attempts: list[int] = []

    async def partially_written(conn: aiosqlite.Connection) -> None:
        attempts.append(1)
        await execute_dml(conn, "INSERT INTO t (v) VALUES ('first')")
        # Fail after a write has already been applied.
        raise _SnapshotError("database is locked")

    try:
        with pytest.raises(sqlite3.OperationalError):
            await db.run(partially_written)

        # Recycled so later operations are healthy, but not replayed.
        assert len(attempts) == 1
        assert db.recycle_count == 1

        rows = await db.run(lambda conn: fetchall(conn, "SELECT v FROM t"))
        assert [r[0] for r in rows] == ["first"], "the operation was replayed"
    finally:
        await db.close()


async def test_non_wal_fallback_still_reads_and_writes(tmp_path, monkeypatch):
    """A database that cannot use WAL keeps working as a degraded mode.

    ``SQLITE_BUSY_SNAPSHOT`` is a WAL concept, so the snapshot recovery path
    does not apply here; the store must still serve reads and writes rather
    than failing to start.
    """
    async def refuse_wal(self, db: aiosqlite.Connection) -> str:
        return "delete"

    monkeypatch.setattr(RuntimeDb, "_ensure_wal", refuse_wal)
    db = RuntimeDb(tmp_path / "runtime.db", name="test", prepare=_prepare_items)
    await db.open()
    try:
        assert db.wal_mode is False
        assert db.health()["wal_mode"] is False

        await db.run(lambda conn: execute_dml(conn, "INSERT INTO t (v) VALUES ('x')"))
        rows = await db.run(lambda conn: fetchall(conn, "SELECT v FROM t"))
        assert [r[0] for r in rows] == ["x"]
    finally:
        await db.close()


async def test_queued_successor_never_sees_a_poisoned_connection(tmp_path):
    """A successor already parked on the lock must see the quarantine.

    The failure has to be recorded while ``_run_locked`` still holds the lock.
    Left to the task's done callback instead, the waiter resumed by
    ``Lock.release`` runs *before* that callback — both are scheduled with
    ``call_soon`` during the same step and the waiter goes first — so it would
    read ``_dirty == False`` and issue its statement against the very
    connection the failure just called into question.
    """
    db = RuntimeDb(tmp_path / "runtime.db", name="test")
    await db.open()
    in_flight = asyncio.Event()
    fail = asyncio.Event()

    async def failing(conn: aiosqlite.Connection) -> str:
        in_flight.set()
        await fail.wait()
        raise sqlite3.OperationalError("no such table: nope")

    try:
        first = asyncio.create_task(db.run(failing))
        await asyncio.wait_for(in_flight.wait(), timeout=5)

        # Park a successor behind it on the store's lock.
        successor = asyncio.create_task(
            db.run(lambda conn: fetchone(conn, "SELECT 1"))
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        fail.set()
        with pytest.raises(sqlite3.OperationalError):
            await first

        assert await successor == (1,)
        assert db.recycle_count == 1, (
            "the queued successor reused the connection the failed operation "
            "left behind — the quarantine was applied too late"
        )
    finally:
        await db.close()
