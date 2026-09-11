"""Tests for LedgerRuntime — append-only item log (Phase 24 + hardening)."""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_ledger_appends_and_loads_items_in_sequence(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime

    runtime = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await runtime.initialize()
    try:
        first = await runtime.append_item(
            thread_id="thread-1",
            turn_id="turn-1",
            item_type="turn_started",
            payload={"agent": "main"},
        )
        second = await runtime.append_item(
            thread_id="thread-1",
            turn_id="turn-1",
            item_type="message",
            role="user",
            content="hello",
            payload={"message_fields": {}},
        )

        items = await runtime.load_items("thread-1")

        assert [item.item_id for item in items] == [first.item_id, second.item_id]
        assert [item.seq for item in items] == [1, 2]
        assert items[0].item_type == "turn_started"
        assert items[1].role == "user"
        assert items[1].content == "hello"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_ledger_is_session_scoped(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime

    db_path = tmp_path / "runtime.db"
    one = LedgerRuntime(db_path, session_id="sess-1")
    two = LedgerRuntime(db_path, session_id="sess-2")
    await one.initialize()
    await two.initialize()
    try:
        await one.append_item(
            thread_id="shared-thread",
            turn_id="turn-1",
            item_type="message",
            role="user",
            content="from one",
            payload={},
        )
        await two.append_item(
            thread_id="shared-thread",
            turn_id="turn-2",
            item_type="message",
            role="user",
            content="from two",
            payload={},
        )

        assert [item.content for item in await one.load_items("shared-thread")] == ["from one"]
        assert [item.content for item in await two.load_items("shared-thread")] == ["from two"]
    finally:
        await one.close()
        await two.close()


@pytest.mark.asyncio
async def test_ledger_reconstructs_provider_messages(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime

    runtime = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await runtime.initialize()
    try:
        await runtime.append_item(
            thread_id="thread-1",
            turn_id="turn-1",
            item_type="message",
            role="user",
            content="hello",
            payload={"message_fields": {}},
        )
        await runtime.append_item(
            thread_id="thread-1",
            turn_id="turn-1",
            item_type="message",
            role="assistant",
            content="hi",
            payload={"message_fields": {"tool_calls": [{"id": "call-1"}]}},
        )

        messages = await runtime.load_provider_messages("thread-1")

        assert messages == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi", "tool_calls": [{"id": "call-1"}]},
        ]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_ledger_concurrent_appends_sequential_no_duplicate_seqs(tmp_path):
    """Bug fix: concurrent append_item() calls to the same thread must
    produce monotonically increasing seq values with no duplicates."""
    from miqi.runtime.ledger_runtime import LedgerRuntime

    runtime = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await runtime.initialize()
    try:
        count = 20

        async def append_one(i: int):
            return await runtime.append_item(
                thread_id="thread-1",
                turn_id="turn-1",
                item_type="message",
                role="user",
                content=f"msg-{i}",
                payload={},
            )

        # Launch count concurrent appends
        items = await asyncio.gather(*(append_one(i) for i in range(count)))

        # All items must have unique seq values 1..count (in any order
        # since concurrent appends don't guarantee submission order,
        # but they MUST guarantee uniqueness).
        seqs = sorted(item.seq for item in items)
        assert seqs == list(range(1, count + 1)), (
            f"Expected seq 1..{count}, got {seqs}. "
            f"Duplicates: {[s for s in seqs if seqs.count(s) > 1]}"
        )
        assert len(set(seqs)) == count, f"Duplicate seq values found: {seqs}"

        # Also verify all items are loadable and correctly ordered
        loaded = await runtime.load_items("thread-1")
        assert len(loaded) == count
        assert [item.seq for item in loaded] == list(range(1, count + 1))
    finally:
        await runtime.close()


# ── Phase 36: turn listing, fork copy, rollback ──────────────────────────


@pytest.mark.asyncio
async def test_ledger_lists_turns_in_sequence(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="s1")
    await ledger.initialize()
    try:
        await ledger.append_item(thread_id="t1", turn_id="turn-1", item_type="turn_started")
        await ledger.append_item(thread_id="t1", turn_id="turn-1", item_type="message", role="user", content="a")
        await ledger.append_item(thread_id="t1", turn_id="turn-2", item_type="turn_started")
        assert await ledger.list_turn_ids("t1") == ["turn-1", "turn-2"]
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_ledger_copy_thread_items_to_fork(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="s1")
    await ledger.initialize()
    try:
        await ledger.append_item(thread_id="source", turn_id="turn-1", item_type="message", role="user", content="hi")
        copied = await ledger.copy_thread_items("source", "fork")
        assert copied == 1
        fork_items = await ledger.load_items("fork")
        assert len(fork_items) == 1
        assert fork_items[0].content == "hi"
        assert fork_items[0].payload["copied_from_thread_id"] == "source"
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_ledger_rollback_marker_hides_last_turn(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="s1")
    await ledger.initialize()
    try:
        await ledger.append_item(thread_id="t1", turn_id="turn-1", item_type="message", role="user", content="one")
        await ledger.append_item(thread_id="t1", turn_id="turn-2", item_type="message", role="user", content="two")
        marker = await ledger.append_rollback_marker("t1", drop_last_turns=1)
        assert marker.payload["removed_turn_ids"] == ["turn-2"]
        visible = await ledger.load_effective_items("t1")
        assert [item.turn_id for item in visible if item.turn_id] == ["turn-1"]
    finally:
        await ledger.close()
