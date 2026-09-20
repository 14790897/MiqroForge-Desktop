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
    fetchone,
    format_sqlite_error,
    is_stale_snapshot_error,
)
from miqi.runtime.ledger_runtime import LedgerRuntime


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
