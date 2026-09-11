"""Tests for HistoryRuntime — persistent turn and message history."""

from types import SimpleNamespace

import pytest

from miqi.runtime import history_runtime
from miqi.runtime.history_runtime import HistoryItem, HistoryRuntime


@pytest.mark.asyncio
async def test_history_runtime_appends_and_loads_messages(tmp_path):
    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
    await runtime.initialize()

    await runtime.append_item(HistoryItem(
        item_id="i1",
        thread_id="thread-1",
        turn_id="turn-1",
        role="user",
        content="hello",
    ))
    await runtime.append_item(HistoryItem(
        item_id="i2",
        thread_id="thread-1",
        turn_id="turn-1",
        role="assistant",
        content="hi",
    ))

    items = await runtime.load_items("thread-1")

    assert [item.role for item in items] == ["user", "assistant"]
    assert [item.content for item in items] == ["hello", "hi"]
    await runtime.close()


@pytest.mark.asyncio
async def test_history_runtime_records_turn_lifecycle(tmp_path):
    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
    await runtime.initialize()

    await runtime.start_turn("turn-1", thread_id="thread-1")
    await runtime.complete_turn(
        "turn-1",
        status="completed",
        tools_used=["read_file"],
        token_usage={"prompt_tokens": 10, "completion_tokens": 5},
    )

    turn = await runtime.get_turn("turn-1")

    assert turn is not None
    assert turn.status == "completed"
    assert turn.tools_used == ["read_file"]
    assert turn.token_usage["prompt_tokens"] == 10
    assert turn.completed_at is not None
    await runtime.close()


@pytest.mark.asyncio
async def test_history_runtime_append_message_convenience(tmp_path):
    """append_message() creates an item with auto-generated id."""
    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
    await runtime.initialize()

    item = await runtime.append_message(
        thread_id="thread-1",
        turn_id="turn-1",
        role="user",
        content="convenience test",
    )

    assert item.item_id
    assert item.role == "user"
    assert item.content == "convenience test"

    items = await runtime.load_items("thread-1")
    assert len(items) == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_history_runtime_load_messages_formats_for_provider(tmp_path):
    """load_messages() returns provider-friendly dicts."""
    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
    await runtime.initialize()

    await runtime.append_message(
        thread_id="t1", turn_id="turn-1", role="user", content="hello",
    )
    await runtime.append_message(
        thread_id="t1", turn_id="turn-1", role="assistant", content="hi there",
    )

    messages = await runtime.load_messages("t1")
    assert len(messages) == 2
    assert messages[0] == {"role": "user", "content": "hello"}
    assert messages[1] == {"role": "assistant", "content": "hi there"}
    await runtime.close()


@pytest.mark.asyncio
async def test_append_item_rejects_invalid_role(tmp_path):
    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
    await runtime.initialize()
    try:
        with pytest.raises(ValueError, match="Invalid history role"):
            await runtime.append_item(HistoryItem(
                item_id="bad-role",
                thread_id="thread-1",
                turn_id="turn-1",
                role="admin",
                content="should not persist",
            ))

        items = await runtime.load_items("thread-1")
        assert items == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_append_item_truncates_large_content_and_payload(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        history_runtime,
        "MAX_HISTORY_CONTENT_CHARS",
        32,
        raising=False,
    )
    monkeypatch.setattr(
        history_runtime,
        "MAX_HISTORY_PAYLOAD_JSON_CHARS",
        64,
        raising=False,
    )

    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
    await runtime.initialize()
    try:
        await runtime.append_item(HistoryItem(
            item_id="large-item",
            thread_id="thread-1",
            turn_id="turn-1",
            role="tool",
            content="x" * 128,
            payload={"result": "y" * 256},
        ))

        items = await runtime.load_items("thread-1")
        assert len(items) == 1
        item = items[0]
        assert len(item.content) <= history_runtime.MAX_HISTORY_CONTENT_CHARS
        assert item.content.endswith("<truncated>")
        assert item.payload["truncated"] is True
        assert item.payload["original_size_chars"] > (
            history_runtime.MAX_HISTORY_PAYLOAD_JSON_CHARS
        )
        assert len(item.payload["preview"]) <= (
            history_runtime.MAX_HISTORY_PAYLOAD_JSON_CHARS
        )
    finally:
        await runtime.close()


# ---------------------------------------------------------------------------
# Phase 19: compaction persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_messages_with_compaction_replaces_visible_history(tmp_path):
    """replace_messages_with_compaction() clears old items and inserts replacement."""
    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
    await runtime.initialize()
    await runtime.append_message(thread_id="t1", turn_id="a", role="user", content="old")
    await runtime.append_message(thread_id="t1", turn_id="a", role="assistant", content="old answer")

    await runtime.replace_messages_with_compaction(
        "t1",
        "compact-1",
        [{"role": "system", "content": "[summary]"}],
    )

    messages = await runtime.load_messages("t1")
    assert messages == [{"role": "system", "content": "[summary]"}]
    await runtime.close()


@pytest.mark.asyncio
async def test_compaction_record_stores_audit_metadata(tmp_path):
    """The runtime_compactions row must store messages_before/after and
    tokens_saved, not just replacement_json."""
    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
    await runtime.initialize()
    await runtime.append_message(thread_id="t1", turn_id="a", role="user", content="old")
    await runtime.append_message(thread_id="t1", turn_id="a", role="assistant", content="old answer")

    await runtime.replace_messages_with_compaction(
        "t1",
        "compact-1",
        [{"role": "system", "content": "[summary]"}],
        messages_before=2,
        messages_after=1,
        tokens_saved=50,
    )

    # Query the compaction record directly
    import json

    import aiosqlite
    async with aiosqlite.connect(str(tmp_path / "runtime.db")) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM runtime_compactions WHERE thread_id = ? AND session_id = ?",
            ("t1", "test-session"),
        )
        row = await cursor.fetchone()

    assert row is not None, "Compaction record should exist"
    assert row["messages_before"] == 2
    assert row["messages_after"] == 1
    assert row["tokens_saved"] == 50
    assert json.loads(row["replacement_json"]) == [{"role": "system", "content": "[summary]"}]
    await runtime.close()


@pytest.mark.asyncio
async def test_compaction_replacement_order_does_not_fall_back_to_item_id(
    tmp_path,
    monkeypatch,
):
    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
    await runtime.initialize()
    try:
        await runtime.append_message(
            thread_id="t1", turn_id="a", role="user", content="old"
        )

        uuids = iter(["compaction-id", "b-system", "a-user"])
        monkeypatch.setattr(
            history_runtime,
            "time",
            SimpleNamespace(time=lambda: 1000.0),
        )
        monkeypatch.setattr(history_runtime.uuid, "uuid4", lambda: next(uuids))

        await runtime.replace_messages_with_compaction(
            "t1",
            "compact-1",
            [
                {"role": "system", "content": "[summary]"},
                {"role": "user", "content": "recent message"},
            ],
        )

        messages = await runtime.load_messages("t1")
        assert [message["role"] for message in messages] == ["system", "user"]
        assert messages[0]["content"] == "[summary]"
    finally:
        await runtime.close()


# ── Phase 36: history deletion for rollback ──────────────────────────────


@pytest.mark.asyncio
async def test_history_delete_turn_messages(tmp_path):
    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="s1")
    await runtime.initialize()
    try:
        await runtime.append_message(
            thread_id="t1", turn_id="turn-1", role="user", content="one"
        )
        await runtime.append_message(
            thread_id="t1", turn_id="turn-2", role="user", content="two"
        )
        removed = await runtime.delete_turn_items("t1", ["turn-2"])
        assert removed == 1
        messages = await runtime.load_messages("t1")
        assert [m["content"] for m in messages] == ["one"]
    finally:
        await runtime.close()


# ── Issue #84: get_turn must degrade on corrupted JSON columns ────────────


@pytest.mark.asyncio
async def test_get_turn_degrades_on_corrupted_tools_used_json(tmp_path):
    """A corrupted tools_used_json must degrade to [], not crash get_turn.

    Parity with load_items, which already skips corrupted payload_json with a
    warning. Without the fix, get_turn raises json.JSONDecodeError and the whole
    turn record fails to load.
    """
    import aiosqlite

    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
    await runtime.initialize()
    try:
        await runtime.start_turn("turn-1", thread_id="thread-1")
        await runtime.complete_turn(
            "turn-1", status="completed",
            tools_used=["read_file"],
            token_usage={"prompt_tokens": 10},
        )

        # Corrupt the tools_used_json column directly in the DB.
        async with aiosqlite.connect(tmp_path / "runtime.db") as conn:
            await conn.execute(
                "UPDATE runtime_turns SET tools_used_json = ? WHERE turn_id = ?",
                ("{not valid json", "turn-1"),
            )
            await conn.commit()

        # The DB connection caches state; reopen on a fresh runtime so the
        # corruption is read back.
        await runtime.close()
        runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
        await runtime.initialize()

        turn = await runtime.get_turn("turn-1")

        # Degrades instead of crashing: turn still loads, tools_used falls back.
        assert turn is not None
        assert turn.status == "completed"
        assert turn.tools_used == []
        # Untouched column still loads.
        assert turn.token_usage == {"prompt_tokens": 10}
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_get_turn_degrades_on_corrupted_token_usage_json(tmp_path):
    """A corrupted token_usage_json must degrade to {}, not crash get_turn."""
    import aiosqlite

    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
    await runtime.initialize()
    try:
        await runtime.start_turn("turn-1", thread_id="thread-1")
        await runtime.complete_turn(
            "turn-1", status="completed",
            tools_used=["read_file"],
            token_usage={"prompt_tokens": 10},
        )

        async with aiosqlite.connect(tmp_path / "runtime.db") as conn:
            await conn.execute(
                "UPDATE runtime_turns SET token_usage_json = ? WHERE turn_id = ?",
                ("<<broken>>", "turn-1"),
            )
            await conn.commit()

        await runtime.close()
        runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
        await runtime.initialize()

        turn = await runtime.get_turn("turn-1")

        assert turn is not None
        assert turn.token_usage == {}
        # Untouched column still loads.
        assert turn.tools_used == ["read_file"]
    finally:
        await runtime.close()


# ── Issue #85: compaction must not crash on a message missing 'role' ──────


@pytest.mark.asyncio
async def test_replace_messages_with_compaction_skips_missing_role(tmp_path):
    """A replacement message missing the 'role' key must not crash compaction.

    Without the fix, msg["role"] raises KeyError, the compaction transaction
    rolls back, and the user sees no compaction effect at all. The same loop
    already used msg.get("content") — role should be accessed the same way.
    """
    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
    await runtime.initialize()
    await runtime.append_message(thread_id="t1", turn_id="a", role="user", content="old")

    # A replacement batch where one message is missing 'role' (e.g. legacy /
    # abnormally-written data). The other message is well-formed.
    await runtime.replace_messages_with_compaction(
        "t1",
        "compact-1",
        [
            {"content": "no role here"},          # missing role
            {"role": "system", "content": "[summary]"},
        ],
    )
    try:
        messages = await runtime.load_messages("t1")
        # Compaction completes: the well-formed message survives; the missing-role
        # one gets a default role rather than crashing the whole transaction.
        assert {"role": "system", "content": "[summary]"} in messages
        assert len(messages) == 2
        roles = [m["role"] for m in messages]
        assert "unknown" in roles, f"missing-role msg should default to 'unknown', got {roles}"
    finally:
        await runtime.close()


# ── Issue #490: thread-scoped history isolation + restart recovery ──────
#
# The fix for #490 relies on two guarantees of the data layer that must
# NOT regress (both prior PRs violated them):
#
#   1. Isolation: load_messages(thread_A) returns only A's rows, never
#      messages written under thread_B. The #500 silent-injection path
#      leaked sibling content across threads; the #505 approach dropped
#      the session_id filter entirely. Both caused the "串" (crosstalk)
#      the user rejected. The (thread_id, session_id) filter is the
#      boundary and must stay intact.
#
#   2. Restart recovery: because session_id is a stable string
#      (`{client_id}:{session_key}`, persisted client-side) and the DB
#      file lives for the whole workspace, reopening the same session
#      (close() + new HistoryRuntime bound to the SAME session_id) must
#      see the prior rows. The frontend bug was regenerating thread_id;
#      session_id-qualified reads must remain stable across reopen.


@pytest.mark.asyncio
async def test_load_messages_isolates_threads_within_session(tmp_path):
    """load_messages(A) returns only A — sibling thread B/C content never leaks in.

    Mirrors issue #490's user scenario at the data layer: one session,
    three threads, each with distinct content. Switching back to A and
    loading its history must yield exactly A's exchange.
    """
    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="miqi-desktop:desktop:1")
    await runtime.initialize()
    try:
        # Thread A — the one we'll revisit.
        await runtime.append_message(
            thread_id="thread-a", turn_id="ta-1", role="user", content="my favorite number is 7",
        )
        await runtime.append_message(
            thread_id="thread-a", turn_id="ta-1", role="assistant", content="got it, 7",
        )
        # Thread B — unrelated topic.
        await runtime.append_message(
            thread_id="thread-b", turn_id="tb-1", role="user", content="explain CSS flexbox",
        )
        await runtime.append_message(
            thread_id="thread-b", turn_id="tb-1", role="assistant", content="flexbox is...",
        )
        # Thread C — another unrelated topic.
        await runtime.append_message(
            thread_id="thread-c", turn_id="tc-1", role="user", content="how to bake bread",
        )

        a_messages = await runtime.load_messages("thread-a")

        # A returns exactly its own user+assistant pair, in order.
        assert [m["content"] for m in a_messages] == ["my favorite number is 7", "got it, 7"]
        # B and C content must never appear in A's history → no crosstalk.
        a_blob = " ".join(m["content"] for m in a_messages)
        assert "CSS" not in a_blob
        assert "bread" not in a_blob

        # Sanity: each thread loads only its own content.
        assert [m["content"] for m in await runtime.load_messages("thread-b")] == [
            "explain CSS flexbox", "flexbox is...",
        ]
        assert [m["content"] for m in await runtime.load_messages("thread-c")] == [
            "how to bake bread",
        ]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_history_survives_reopen_with_stable_session_id(tmp_path):
    """Reopening the same session (same session_id) recovers prior history.

    This is the persistence guarantee the #490 frontend fix relies on:
    after MiQi restart, a new HistoryRuntime bound to the identical
    session_id string must see the rows the previous process wrote — no
    data loss, no need to drop any filter.
    """
    db = tmp_path / "runtime.db"
    session_id = "miqi-desktop:desktop:1700000000"

    # First process: write a conversation under thread-a.
    runtime1 = HistoryRuntime(db, session_id=session_id)
    await runtime1.initialize()
    try:
        await runtime1.append_message(
            thread_id="thread-a", turn_id="ta-1", role="user", content="we refactored the parser",
        )
        await runtime1.append_message(
            thread_id="thread-a", turn_id="ta-1", role="assistant", content="noted, parser refactor",
        )
    finally:
        await runtime1.close()

    # Simulate restart: brand-new HistoryRuntime, SAME session_id, SAME db.
    runtime2 = HistoryRuntime(db, session_id=session_id)
    await runtime2.initialize()
    try:
        messages = await runtime2.load_messages("thread-a")
        assert [m["content"] for m in messages] == [
            "we refactored the parser", "noted, parser refactor",
        ], "history must be recoverable across reopen with a stable session_id"
    finally:
        await runtime2.close()


@pytest.mark.asyncio
async def test_load_messages_isolates_different_sessions_same_thread_id(tmp_path):
    """thread_id is scoped by session_id — the load filter must use both.

    Guards against #505's regression: it removed the session_id filter on
    the assumption that thread_id is globally unique. It is not — two
    sessions can both have a 'thread-a'. The session_id filter is what
    keeps session B from reading session A's rows.
    """
    db = tmp_path / "runtime.db"

    # Session A writes to 'thread-a'.
    runtime_a = HistoryRuntime(db, session_id="miqi-desktop:desktop:A")
    await runtime_a.initialize()
    try:
        await runtime_a.append_message(
            thread_id="thread-a", turn_id="1", role="user", content="secret of session A",
        )
    finally:
        await runtime_a.close()

    # Session B opens a DIFFERENT session but happens to use the same
    # thread_id value 'thread-a'. It must see nothing — 'thread-a' here
    # belongs to A, and B has its own session_id namespace.
    runtime_b = HistoryRuntime(db, session_id="miqi-desktop:desktop:B")
    await runtime_b.initialize()
    try:
        b_messages = await runtime_b.load_messages("thread-a")
        assert b_messages == [], (
            "session B must not read session A's rows even with a "
            "colliding thread_id — session_id filter is the boundary"
        )
    finally:
        await runtime_b.close()


# ── #834: reasoning_elapsed_s persisted on execution snapshots ──────────


@pytest.mark.asyncio
async def test_snapshot_persists_reasoning_elapsed_s(tmp_path):
    """#834: reasoning_elapsed_s round-trips through upsert_snapshot and
    get_interrupted_snapshots (中断恢复卡不再固定 1 秒)."""
    runtime = HistoryRuntime(tmp_path / "runtime.db", session_id="test-session")
    try:
        await runtime.initialize()

        await runtime.upsert_snapshot(
            "turn-1", "thread-1",
            status="interrupted",
            assistant_content="half",
            reasoning_content="deep thought",
            reasoning_elapsed_s=612.5,
        )

        snapshots = await runtime.get_interrupted_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0]["reasoning_elapsed_s"] == 612.5
        assert snapshots[0]["reasoning_content"] == "deep thought"

        # Snapshot without a measurement stays None (old-buffer behavior).
        await runtime.upsert_snapshot(
            "turn-2", "thread-2",
            status="interrupted",
            assistant_content="x",
        )
        snapshots = await runtime.get_interrupted_snapshots()
        by_turn = {s["turn_id"]: s for s in snapshots}
        assert by_turn["turn-2"]["reasoning_elapsed_s"] is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_snapshot_table_migrates_old_db_without_column(tmp_path):
    """#834: a pre-existing DB without reasoning_elapsed_s still opens and
    snapshots work (ALTER TABLE migration adds the column)."""
    import sqlite3

    db_path = tmp_path / "runtime.db"
    # Simulate an old-schema DB: create the table WITHOUT the new column.
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE execution_snapshots (
            turn_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            assistant_content TEXT NOT NULL DEFAULT '',
            reasoning_content TEXT NOT NULL DEFAULT '',
            tool_state_json TEXT NOT NULL DEFAULT '[]',
            version INTEGER NOT NULL DEFAULT 1,
            updated_at REAL NOT NULL
        )"""
    )
    conn.commit()
    conn.close()

    runtime = HistoryRuntime(db_path, session_id="test-session")
    try:
        await runtime.initialize()  # must migrate without error

        await runtime.upsert_snapshot(
            "turn-1", "thread-1",
            status="interrupted",
            assistant_content="half",
            reasoning_elapsed_s=42.0,
        )
        snapshots = await runtime.get_interrupted_snapshots()
        assert snapshots[0]["reasoning_elapsed_s"] == 42.0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_compaction_cancellation_rolls_back_and_releases_lock(tmp_path):
    """#992: 回合任务在 replace_messages_with_compaction 事务中途被取消时，
    CancelledError（不是 Exception 子类）必须触发 ROLLBACK——否则写锁滞留、
    其它连接写入等待 30s 后 database is locked（与 test_issue_886 同源），
    且部分 DELETE 会泄漏成半压缩状态。"""
    import asyncio
    import time

    import aiosqlite

    db_path = tmp_path / "runtime.db"
    runtime = HistoryRuntime(db_path, session_id="test-session")
    await runtime.initialize()
    await runtime.append_message(thread_id="t1", turn_id="a", role="user", content="old-1")
    await runtime.append_message(thread_id="t1", turn_id="a", role="assistant", content="old-2")

    # 确定性注入：第 3 次 execute（BEGIN → DELETE 之后、第一条 INSERT 之前）
    # 抛 CancelledError，等价于「首个写入已应用、回合任务被取消」。
    real_execute = runtime._db.execute
    state = {"calls": 0}

    def sabotaged_execute(sql, *args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 3:
            raise asyncio.CancelledError("simulated turn cancellation")
        return real_execute(sql, *args, **kwargs)

    runtime._db.execute = sabotaged_execute  # type: ignore[method-assign]
    try:
        with pytest.raises(asyncio.CancelledError):
            await runtime.replace_messages_with_compaction(
                "t1",
                "compact-cancelled",
                [{"role": "system", "content": "[summary]"}] * 5,
            )
    finally:
        runtime._db.execute = real_execute  # type: ignore[method-assign]

    # 1) 锁已释放：另一连接用 timeout=1 立即写入（滞留锁会让它 1s 后抛
    #    OperationalError: database is locked）。
    async with aiosqlite.connect(str(db_path), timeout=1) as probe:
        await probe.execute(
            "INSERT INTO runtime_history_items"
            " (item_id, thread_id, session_id, turn_id, role, content,"
            "  payload_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("probe-1", "t1", "test-session", "a", "user", "probe", "{}", time.time()),
        )
        await probe.commit()

    # 2) 无半压缩状态：原消息完整保留，无 compaction 记录。
    items = await runtime.load_items("t1")
    contents = sorted(i.content for i in items)
    assert "old-1" in contents and "old-2" in contents, contents
    assert all(not c.startswith("[summary]") for c in contents), contents
    async with aiosqlite.connect(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM runtime_compactions WHERE thread_id = ?", ("t1",)
        )
        row = await cursor.fetchone()
    assert row[0] == 0

    await runtime.close()
