"""Tests for ReplayRuntime — ledger-backed turn/message reconstruction (Phase 25)."""

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────


async def _populate_ledger_with_complete_turn(ledger, *, thread_id="thread-1", turn_id="turn-1"):
    """Populate a ledger with a complete turn: start, user msg, deltas, tool, exec, end."""

    await ledger.append_item(
        thread_id=thread_id, turn_id=turn_id,
        item_type="turn_started", payload={"agent_name": "main"},
    )
    await ledger.append_item(
        thread_id=thread_id, turn_id=turn_id,
        item_type="message", role="user", content="read file",
        payload={"message_fields": {}},
    )
    await ledger.append_item(
        thread_id=thread_id, turn_id=turn_id,
        item_type="assistant_delta", content="Let ",
        payload={"index": 0},
    )
    await ledger.append_item(
        thread_id=thread_id, turn_id=turn_id,
        item_type="assistant_delta", content="me check.",
        payload={"index": 1},
    )
    await ledger.append_item(
        thread_id=thread_id, turn_id=turn_id,
        item_type="tool_call_started",
        payload={"tool_call_id": "tc-1", "name": "read_file", "arguments": {"path": "x.txt"}},
    )
    await ledger.append_item(
        thread_id=thread_id, turn_id=turn_id,
        item_type="exec_started",
        payload={"tool_call_id": "tc-1", "command": "cat x.txt", "cwd": "/tmp", "sandbox_type": "none"},
    )
    await ledger.append_item(
        thread_id=thread_id, turn_id=turn_id,
        item_type="exec_output_delta", content="hello world",
        payload={"tool_call_id": "tc-1", "stream": "stdout"},
    )
    await ledger.append_item(
        thread_id=thread_id, turn_id=turn_id,
        item_type="exec_completed",
        payload={"tool_call_id": "tc-1", "exit_code": 0, "duration_ms": 42, "output_size": 11},
    )
    await ledger.append_item(
        thread_id=thread_id, turn_id=turn_id,
        item_type="tool_call_completed",
        payload={
            "tool_call_id": "tc-1", "result": "hello world", "duration_ms": 42,
            "retry_count": 0, "permission_verdict": "allow", "sandbox_type": "none",
        },
    )
    await ledger.append_item(
        thread_id=thread_id, turn_id=turn_id,
        item_type="message", role="assistant", content="Let me check.",
        payload={"message_fields": {}},
    )
    await ledger.append_item(
        thread_id=thread_id, turn_id=turn_id,
        item_type="turn_completed",
        payload={"final_content": "Let me check.", "token_usage": {"input": 10, "output": 5}},
    )


# ── Provider Message Reconstruction ──────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_reconstructs_provider_messages(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await _populate_ledger_with_complete_turn(ledger)

        replay = ReplayRuntime(ledger)
        messages = await replay.get_provider_messages("thread-1")

        roles = [m["role"] for m in messages]
        assert roles == ["user", "assistant"]
        assert messages[0]["content"] == "read file"
        assert messages[1]["content"] == "Let me check."
    finally:
        await ledger.close()


# ── Turn Timeline Reconstruction ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_reconstructs_turn_timeline(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await _populate_ledger_with_complete_turn(ledger)

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-1")

        assert timeline is not None
        assert timeline.turn_id == "turn-1"
        assert timeline.thread_id == "thread-1"
        assert timeline.status == "completed"
        assert timeline.user_input == "read file"
        assert timeline.assistant_text == "Let me check."
        assert timeline.assistant_deltas == ["Let ", "me check."]

        # Tool calls
        assert len(timeline.tool_calls) == 1
        tc = timeline.tool_calls[0]
        assert tc.tool_call_id == "tc-1"
        assert tc.name == "read_file"
        assert tc.arguments == {"path": "x.txt"}
        assert tc.status == "completed"
        assert tc.result == "hello world"
        assert tc.duration_ms == 42
        assert tc.permission_verdict == "allow"

        # Exec commands
        assert len(timeline.exec_commands) == 1
        ex = timeline.exec_commands[0]
        assert ex.tool_call_id == "tc-1"
        assert ex.command == "cat x.txt"
        assert ex.cwd == "/tmp"
        assert ex.output == "hello world"
        assert ex.exit_code == 0
        assert ex.status == "completed"

        assert timeline.started_at is not None
        assert timeline.completed_at is not None
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_replay_lists_turns(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await _populate_ledger_with_complete_turn(ledger, turn_id="turn-a")
        await _populate_ledger_with_complete_turn(ledger, turn_id="turn-b")

        replay = ReplayRuntime(ledger)
        turns = await replay.list_turns("thread-1")

        assert turns == ["turn-a", "turn-b"]
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_replay_get_thread_timeline(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await _populate_ledger_with_complete_turn(ledger, turn_id="turn-a")
        await _populate_ledger_with_complete_turn(ledger, turn_id="turn-b")

        replay = ReplayRuntime(ledger)
        timelines = await replay.get_thread_timeline("thread-1")

        assert len(timelines) == 2
        assert [t.turn_id for t in timelines] == ["turn-a", "turn-b"]
        for t in timelines:
            assert t.status == "completed"
    finally:
        await ledger.close()


# ── Session Isolation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_session_isolation(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    db_path = tmp_path / "runtime.db"
    ledger_a = LedgerRuntime(db_path, session_id="sess-a")
    ledger_b = LedgerRuntime(db_path, session_id="sess-b")
    await ledger_a.initialize()
    await ledger_b.initialize()
    try:
        await ledger_a.append_item(
            thread_id="shared", turn_id="t1",
            item_type="turn_started", payload={},
        )
        await ledger_b.append_item(
            thread_id="shared", turn_id="t2",
            item_type="turn_started", payload={},
        )

        replay_a = ReplayRuntime(ledger_a)
        replay_b = ReplayRuntime(ledger_b)

        turns_a = await replay_a.list_turns("shared")
        turns_b = await replay_b.list_turns("shared")

        assert turns_a == ["t1"]
        assert turns_b == ["t2"]
    finally:
        await ledger_a.close()
        await ledger_b.close()


# ── Recovery: Aborted Turn ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_aborted_turn_timeline(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-aborted",
            item_type="turn_started", payload={"agent_name": "main"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-aborted",
            item_type="message", role="user", content="long task",
            payload={"message_fields": {}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-aborted",
            item_type="tool_call_started",
            payload={"tool_call_id": "tc-1", "name": "slow_tool", "arguments": {}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-aborted",
            item_type="turn_aborted",
            payload={"reason": "User requested abort."},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-aborted")

        assert timeline.status == "aborted"
        assert timeline.user_input == "long task"
        assert len(timeline.tool_calls) == 1
        assert timeline.tool_calls[0].status == "pending"
    finally:
        await ledger.close()


# ── Recovery: Errored Turn ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_errored_turn_timeline(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-err",
            item_type="turn_started", payload={"agent_name": "main"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-err",
            item_type="message", role="user", content="bad request",
            payload={"message_fields": {}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-err",
            item_type="error",
            content="Internal error occurred.",
            payload={"recoverable": False, "source": "task_runner"},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-err")

        assert timeline.status == "incomplete"  # No turn_completed/aborted marker
        assert len(timeline.errors) == 1
        assert timeline.errors[0]["source"] == "task_runner"
    finally:
        await ledger.close()


# ── Recovery: Dangling Tool Start ────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_dangling_tool_start_is_pending(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-dangle",
            item_type="turn_started", payload={"agent_name": "main"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-dangle",
            item_type="tool_call_started",
            payload={"tool_call_id": "tc-dangle", "name": "unfinished_tool", "arguments": {}},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-dangle")

        assert len(timeline.tool_calls) == 1
        assert timeline.tool_calls[0].status == "pending"
        assert timeline.tool_calls[0].name == "unfinished_tool"
    finally:
        await ledger.close()


# ── Recovery: Dangling Exec Start ────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_dangling_exec_start_is_pending(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-exec-dangle",
            item_type="turn_started", payload={"agent_name": "main"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-exec-dangle",
            item_type="exec_started",
            payload={"tool_call_id": "tc-ex", "command": "sleep 999", "cwd": "/", "sandbox_type": "none"},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-exec-dangle")

        assert len(timeline.exec_commands) == 1
        assert timeline.exec_commands[0].status == "pending"
        assert timeline.exec_commands[0].command == "sleep 999"
    finally:
        await ledger.close()


# ── Recovery: Streaming Deltas Merge ─────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_streaming_deltas_merge_to_assistant_text(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-deltas",
            item_type="turn_started", payload={"agent_name": "main"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-deltas",
            item_type="message", role="user", content="hi",
            payload={"message_fields": {}},
        )
        for i, part in enumerate(["Hello", ", ", "world", "!"]):
            await ledger.append_item(
                thread_id="thread-1", turn_id="turn-deltas",
                item_type="assistant_delta", content=part,
                payload={"index": i},
            )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-deltas")

        assert timeline.assistant_text == "Hello, world!"
        assert timeline.assistant_deltas == ["Hello", ", ", "world", "!"]
    finally:
        await ledger.close()


# ── Recovery: No Final Message, Deltas Only ──────────────────────────────


@pytest.mark.asyncio
async def test_replay_deltas_without_final_message(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-deltas-only",
            item_type="turn_started", payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-deltas-only",
            item_type="message", role="user", content="q", payload={"message_fields": {}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-deltas-only",
            item_type="assistant_delta", content="streaming only, ",
            payload={"index": 0},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-deltas-only",
            item_type="assistant_delta", content="no final message.",
            payload={"index": 1},
        )
        # No final message item, no turn_completed

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-deltas-only")

        assert timeline.assistant_text == "streaming only, no final message."
        assert timeline.status == "incomplete"
    finally:
        await ledger.close()


# ── Recovery: Corrupt Payload ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_corrupt_payload_skipped_gracefully(tmp_path):
    """ReplayRuntime must not crash when a payload_json is invalid JSON."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        # Insert a valid item first
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-c",
            item_type="turn_started", payload={"agent_name": "main"},
        )
        # Manually corrupt one row's payload_json in the DB
        db = ledger._conn
        await db.execute(
            """INSERT INTO runtime_ledger_items
               (item_id, session_id, thread_id, turn_id, seq, item_type,
                role, content, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "corrupt-item-id", "sess-1", "thread-1", "turn-c", 2,
                "tool_call_started", None, "",
                "NOT VALID JSON {{{", 1234567890.0,
            ),
        )
        await db.commit()

        replay = ReplayRuntime(ledger)
        # Must not raise — just skip the corrupt row
        timeline = await replay.get_turn_timeline("thread-1", "turn-c")
        assert timeline is not None
        assert timeline.turn_id == "turn-c"
        # The corrupt tool_call_started should be skipped
        assert len(timeline.tool_calls) == 0
    finally:
        await ledger.close()


# ── Recovery: Empty Thread ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_empty_thread_returns_empty(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        replay = ReplayRuntime(ledger)

        turns = await replay.list_turns("nonexistent-thread")
        assert turns == []

        timelines = await replay.get_thread_timeline("nonexistent-thread")
        assert timelines == []

        timeline = await replay.get_turn_timeline("nonexistent-thread", "any-turn")
        assert timeline is None

        messages = await replay.get_provider_messages("nonexistent-thread")
        assert messages == []
    finally:
        await ledger.close()


# ── Reasoning Deltas ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_captures_reasoning_deltas(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-reason",
            item_type="turn_started", payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-reason",
            item_type="reasoning_delta", content="Let me think...",
            payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-reason",
            item_type="reasoning_delta", content="The answer is 42.",
            payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-reason",
            item_type="turn_completed", payload={},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-reason")

        assert timeline.reasoning_deltas == ["Let me think...", "The answer is 42."]
    finally:
        await ledger.close()


# ── Multi-Turn Thread ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_multi_turn_thread(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        # Turn 1
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-1",
            item_type="turn_started", payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-1",
            item_type="message", role="user", content="q1", payload={"message_fields": {}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-1",
            item_type="message", role="assistant", content="a1", payload={"message_fields": {}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-1",
            item_type="turn_completed", payload={},
        )
        # Turn 2
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-2",
            item_type="turn_started", payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-2",
            item_type="message", role="user", content="q2", payload={"message_fields": {}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-2",
            item_type="message", role="assistant", content="a2", payload={"message_fields": {}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-2",
            item_type="turn_completed", payload={},
        )

        replay = ReplayRuntime(ledger)

        # list_turns
        assert await replay.list_turns("thread-1") == ["turn-1", "turn-2"]

        # get_thread_timeline
        timelines = await replay.get_thread_timeline("thread-1")
        assert len(timelines) == 2
        assert timelines[0].user_input == "q1"
        assert timelines[1].user_input == "q2"

        # get_provider_messages should include all messages across turns
        messages = await replay.get_provider_messages("thread-1")
        assert len(messages) == 4
        assert [m["content"] for m in messages] == ["q1", "a1", "q2", "a2"]
    finally:
        await ledger.close()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 31.8: Exec lifecycle ledger → replay round-trip
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_exec_streaming_delta_recorded_and_replayable(tmp_path):
    """Exec output deltas written to ledger must be merged back during replay."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-exec",
            item_type="turn_started", payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-exec",
            item_type="exec_started",
            payload={"tool_call_id": "tc-ex", "command": "echo hello",
                     "cwd": "/tmp", "sandbox_type": "none"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-exec",
            item_type="exec_output_delta", content="hello",
            payload={"tool_call_id": "tc-ex", "stream": "stdout"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-exec",
            item_type="exec_output_delta", content=" world",
            payload={"tool_call_id": "tc-ex", "stream": "stdout"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-exec",
            item_type="exec_output_delta", content="\n",
            payload={"tool_call_id": "tc-ex", "stream": "stderr"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-exec",
            item_type="exec_completed",
            payload={"tool_call_id": "tc-ex", "exit_code": 0,
                     "duration_ms": 100, "output_size": 12,
                     "cancelled": False, "timed_out": False},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-exec")

        assert len(timeline.exec_commands) == 1
        ex = timeline.exec_commands[0]
        assert ex.command == "echo hello"
        assert ex.output == "hello world\n"  # merged from deltas
        assert ex.exit_code == 0
        assert ex.status == "completed"
        assert ex.cancelled is False
        assert ex.timed_out is False
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_exec_timeout_recorded_as_timed_out(tmp_path):
    """Exec timeout must record timed_out=True and replay as timed_out status."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-timeout",
            item_type="exec_started",
            payload={"tool_call_id": "tc-to", "command": "sleep 999",
                     "cwd": "/", "sandbox_type": "none"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-timeout",
            item_type="exec_completed",
            payload={"tool_call_id": "tc-to", "exit_code": -1,
                     "duration_ms": 30000, "output_size": 0,
                     "cancelled": False, "timed_out": True},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-timeout")

        assert len(timeline.exec_commands) == 1
        ex = timeline.exec_commands[0]
        assert ex.status == "timed_out"
        assert ex.timed_out is True
        assert ex.cancelled is False
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_exec_cancelled_recorded_as_cancelled(tmp_path):
    """Exec cancellation must record cancelled=True and replay as cancelled."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-cancel",
            item_type="exec_started",
            payload={"tool_call_id": "tc-cx", "command": "long-running",
                     "cwd": "/", "sandbox_type": "none"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-cancel",
            item_type="exec_completed",
            payload={"tool_call_id": "tc-cx", "exit_code": -1,
                     "duration_ms": 500, "output_size": 0,
                     "cancelled": True, "timed_out": False},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-cancel")

        assert len(timeline.exec_commands) == 1
        ex = timeline.exec_commands[0]
        assert ex.status == "cancelled"
        assert ex.cancelled is True
        assert ex.timed_out is False
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_exec_nonzero_exit_recorded_as_error(tmp_path):
    """Non-zero exit code must replay as status=error."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-err",
            item_type="exec_started",
            payload={"tool_call_id": "tc-err", "command": "false",
                     "cwd": "/", "sandbox_type": "none"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-err",
            item_type="exec_completed",
            payload={"tool_call_id": "tc-err", "exit_code": 1,
                     "duration_ms": 50, "output_size": 0,
                     "cancelled": False, "timed_out": False},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-err")

        assert len(timeline.exec_commands) == 1
        ex = timeline.exec_commands[0]
        assert ex.exit_code == 1
        assert ex.status == "error"
    finally:
        await ledger.close()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 31.8: Approval lifecycle ledger → replay round-trip
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_approval_requested_and_allowed_recorded(tmp_path):
    """Approval request + allow resolution must be replayable."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-ap",
            item_type="turn_started", payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-ap",
            item_type="approval_requested",
            payload={
                "approval_id": "turn-ap:call-1",
                "tool_call_id": "call-1",
                "tool_name": "exec",
                "category": "exec",
                "description": "Run: rm file",
                "allow_permanent": True,
            },
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-ap",
            item_type="approval_resolved",
            payload={
                "approval_id": "turn-ap:call-1",
                "tool_call_id": "call-1",
                "decision": "once",
                "tool_name": "exec",
                "category": "exec",
            },
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-ap",
            item_type="tool_call_started",
            payload={"tool_call_id": "call-1", "name": "exec",
                     "arguments": {"command": "rm file"}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-ap",
            item_type="tool_call_completed",
            payload={"tool_call_id": "call-1", "result": "ok"},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-ap")

        assert len(timeline.approval_events) == 1
        ap = timeline.approval_events[0]
        assert ap.approval_id == "turn-ap:call-1"
        assert ap.tool_name == "exec"
        assert ap.category == "exec"
        assert ap.description == "Run: rm file"
        assert ap.allow_permanent is True
        assert ap.decision == "once"
        assert ap.resolved_seq is not None

        # Tool call still reconstructed
        assert len(timeline.tool_calls) == 1
        assert timeline.tool_calls[0].tool_call_id == "call-1"
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_approval_denied_recorded(tmp_path):
    """Approval denial must be replayable with decision=deny."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-deny",
            item_type="approval_requested",
            payload={
                "approval_id": "turn-deny:call-1",
                "tool_call_id": "call-1",
                "tool_name": "write_file",
                "category": "file_write",
                "description": "write_file: /tmp/secret",
                "allow_permanent": False,
            },
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-deny",
            item_type="approval_resolved",
            payload={
                "approval_id": "turn-deny:call-1",
                "tool_call_id": "call-1",
                "decision": "deny",
                "tool_name": "write_file",
                "category": "file_write",
            },
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-deny")

        assert len(timeline.approval_events) == 1
        ap = timeline.approval_events[0]
        assert ap.approval_id == "turn-deny:call-1"
        assert ap.tool_name == "write_file"
        assert ap.category == "file_write"
        assert ap.decision == "deny"

        # No tool execution should be present (denied before execution)
        assert len(timeline.tool_calls) == 0
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_approval_timeout_recorded(tmp_path):
    """Approval timeout must replay as decision=timeout."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-timeout",
            item_type="approval_requested",
            payload={
                "approval_id": "turn-timeout:call-1",
                "tool_call_id": "call-1",
                "tool_name": "exec",
                "category": "exec",
                "description": "Run: cmd",
            },
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-timeout",
            item_type="approval_resolved",
            payload={
                "approval_id": "turn-timeout:call-1",
                "tool_call_id": "call-1",
                "decision": "timeout",
                "tool_name": "exec",
                "category": "exec",
            },
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-timeout")

        assert len(timeline.approval_events) == 1
        ap = timeline.approval_events[0]
        assert ap.decision == "timeout"
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_approval_abort_recorded(tmp_path):
    """Approval abort must replay as decision=abort."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-abort",
            item_type="approval_requested",
            payload={
                "approval_id": "turn-abort:call-1",
                "tool_call_id": "call-1",
                "tool_name": "exec",
                "category": "exec",
                "description": "Run: cmd",
            },
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-abort",
            item_type="approval_resolved",
            payload={
                "approval_id": "turn-abort:call-1",
                "tool_call_id": "call-1",
                "decision": "abort",
                "tool_name": "exec",
                "category": "exec",
            },
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-abort")

        assert len(timeline.approval_events) == 1
        ap = timeline.approval_events[0]
        assert ap.decision == "abort"
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_approval_always_permanent_recorded(tmp_path):
    """Permanent approval (always) must replay as decision=always."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-perm",
            item_type="approval_requested",
            payload={
                "approval_id": "turn-perm:call-1",
                "tool_call_id": "call-1",
                "tool_name": "exec",
                "category": "exec",
                "description": "Run: safe-cmd",
            },
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-perm",
            item_type="approval_resolved",
            payload={
                "approval_id": "turn-perm:call-1",
                "tool_call_id": "call-1",
                "decision": "always",
                "tool_name": "exec",
                "category": "exec",
            },
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-perm")

        assert len(timeline.approval_events) == 1
        ap = timeline.approval_events[0]
        assert ap.decision == "always"
    finally:
        await ledger.close()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 31.8: File mutation approval in ledger/replay
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_file_write_approval_denied_replay_shows_deny(tmp_path):
    """File mutation denial: replay must show approval denied + no tool result."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-fw",
            item_type="turn_started", payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-fw",
            item_type="message", role="user", content="write file",
            payload={"message_fields": {}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-fw",
            item_type="tool_call_started",
            payload={"tool_call_id": "tc-fw", "name": "write_file",
                     "arguments": {"path": "/tmp/secret", "content": "evil"}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-fw",
            item_type="approval_requested",
            payload={
                "approval_id": "turn-fw:tc-fw",
                "tool_call_id": "tc-fw",
                "tool_name": "write_file",
                "category": "file_write",
                "description": "write_file: /tmp/secret",
            },
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-fw",
            item_type="approval_resolved",
            payload={
                "approval_id": "turn-fw:tc-fw",
                "tool_call_id": "tc-fw",
                "decision": "deny",
                "tool_name": "write_file",
                "category": "file_write",
            },
        )
        # No tool_call_completed — denied before execution

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-fw")

        # Approval denial recorded
        assert len(timeline.approval_events) == 1
        ap = timeline.approval_events[0]
        assert ap.decision == "deny"
        assert ap.tool_name == "write_file"

        # Tool start exists but no completion → pending
        assert len(timeline.tool_calls) == 1
        assert timeline.tool_calls[0].status == "pending"
        assert timeline.tool_calls[0].result is None
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_file_write_approval_allowed_replay_shows_tool_result(tmp_path):
    """File mutation allow: replay must show approval resolved + tool result."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-fwa",
            item_type="turn_started", payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-fwa",
            item_type="message", role="user", content="write safe file",
            payload={"message_fields": {}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-fwa",
            item_type="tool_call_started",
            payload={"tool_call_id": "tc-fwa", "name": "write_file",
                     "arguments": {"path": "/tmp/safe"}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-fwa",
            item_type="approval_requested",
            payload={
                "approval_id": "turn-fwa:tc-fwa",
                "tool_call_id": "tc-fwa",
                "tool_name": "write_file",
                "category": "file_write",
                "description": "write_file: /tmp/safe",
            },
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-fwa",
            item_type="approval_resolved",
            payload={
                "approval_id": "turn-fwa:tc-fwa",
                "tool_call_id": "tc-fwa",
                "decision": "once",
                "tool_name": "write_file",
                "category": "file_write",
            },
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-fwa",
            item_type="tool_call_completed",
            payload={
                "tool_call_id": "tc-fwa",
                "result": "Successfully wrote 5 bytes",
                "duration_ms": 10,
                "permission_verdict": "allow",
            },
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-fwa")

        # Approval resolved
        assert len(timeline.approval_events) == 1
        ap = timeline.approval_events[0]
        assert ap.decision == "once"
        assert ap.tool_name == "write_file"

        # Tool result present
        assert len(timeline.tool_calls) == 1
        tc = timeline.tool_calls[0]
        assert tc.status == "completed"
        assert tc.result == "Successfully wrote 5 bytes"
        assert tc.permission_verdict == "allow"
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_office_doc_write_approval_in_replay(tmp_path):
    """Office doc write (docx_write) approval lifecycle must be replayable."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-docx",
            item_type="tool_call_started",
            payload={"tool_call_id": "tc-docx", "name": "docx_write",
                     "arguments": {"file_path": "/tmp/out.docx"}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-docx",
            item_type="approval_requested",
            payload={
                "approval_id": "turn-docx:tc-docx",
                "tool_call_id": "tc-docx",
                "tool_name": "docx_write",
                "category": "file_write",
                "description": "docx_write: /tmp/out.docx",
                "allow_permanent": True,
            },
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-docx",
            item_type="approval_resolved",
            payload={
                "approval_id": "turn-docx:tc-docx",
                "tool_call_id": "tc-docx",
                "decision": "always",
                "tool_name": "docx_write",
                "category": "file_write",
            },
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-docx",
            item_type="tool_call_completed",
            payload={"tool_call_id": "tc-docx", "result": "docx created"},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-docx")

        assert len(timeline.approval_events) == 1
        ap = timeline.approval_events[0]
        assert ap.tool_name == "docx_write"
        assert ap.category == "file_write"
        assert ap.decision == "always"

        assert len(timeline.tool_calls) == 1
        assert timeline.tool_calls[0].result == "docx created"
    finally:
        await ledger.close()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 31.8: Corrupt payload resilience
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_corrupt_approval_payload_does_not_break_replay(tmp_path):
    """Corrupt approval_resolved payload_json must not crash replay.
    The item should be silently skipped (payload parsing falls back to {}).
    """
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-corr",
            item_type="turn_started", payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-corr",
            item_type="approval_requested",
            payload={
                "approval_id": "turn-corr:call-1",
                "tool_call_id": "call-1",
                "tool_name": "exec",
                "category": "exec",
            },
        )
        # Manually insert corrupt row
        db = ledger._conn
        await db.execute(
            """INSERT INTO runtime_ledger_items
               (item_id, session_id, thread_id, turn_id, seq, item_type,
                role, content, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("corrupt-ap-id", "sess-1", "thread-1", "turn-corr", 3,
             "approval_resolved", None, "",
             "{{{broken json", 1234567890.0),
        )
        await db.commit()

        replay = ReplayRuntime(ledger)
        # Must not raise
        timeline = await replay.get_turn_timeline("thread-1", "turn-corr")
        assert timeline is not None

        # The approval_requested should still be present (dangling, no resolution)
        assert len(timeline.approval_events) == 1
        assert timeline.approval_events[0].decision == "pending"
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_unknown_ledger_item_type_does_not_break_replay(tmp_path):
    """Unknown item_type values must be silently skipped by replay."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-unk",
            item_type="turn_started", payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-unk",
            item_type="message", role="user", content="hi",
            payload={"message_fields": {}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-unk",
            item_type="unknown_future_item_type",
            payload={"some": "data"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-unk",
            item_type="turn_completed", payload={},
        )

        replay = ReplayRuntime(ledger)
        # Must not raise
        timeline = await replay.get_turn_timeline("thread-1", "turn-unk")
        assert timeline is not None
        assert timeline.status == "completed"
        assert timeline.user_input == "hi"
    finally:
        await ledger.close()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 31.8: Full turn timeline reconstruction with all event types
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_full_turn_timeline_with_approval_and_exec(tmp_path):
    """Reconstruct a complete turn with: user → approval request → approval
    resolved → exec start → output deltas → exec end → tool completed →
    assistant."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        # User input
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-full",
            item_type="turn_started", payload={"agent_name": "main"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-full",
            item_type="message", role="user", content="run ls",
            payload={"message_fields": {}},
        )
        # Tool call started
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-full",
            item_type="tool_call_started",
            payload={"tool_call_id": "tc-full", "name": "exec",
                     "arguments": {"command": "ls"}},
        )
        # Approval requested
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-full",
            item_type="approval_requested",
            payload={
                "approval_id": "turn-full:tc-full",
                "tool_call_id": "tc-full",
                "tool_name": "exec",
                "category": "exec",
                "description": "Run: ls",
                "allow_permanent": True,
            },
        )
        # Approval resolved
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-full",
            item_type="approval_resolved",
            payload={
                "approval_id": "turn-full:tc-full",
                "tool_call_id": "tc-full",
                "decision": "once",
                "tool_name": "exec",
                "category": "exec",
            },
        )
        # Exec started
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-full",
            item_type="exec_started",
            payload={"tool_call_id": "tc-full", "command": "ls",
                     "cwd": "/tmp", "sandbox_type": "none"},
        )
        # Output deltas
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-full",
            item_type="exec_output_delta", content="file1.txt\n",
            payload={"tool_call_id": "tc-full", "stream": "stdout"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-full",
            item_type="exec_output_delta", content="file2.txt",
            payload={"tool_call_id": "tc-full", "stream": "stdout"},
        )
        # Exec completed
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-full",
            item_type="exec_completed",
            payload={"tool_call_id": "tc-full", "exit_code": 0,
                     "duration_ms": 42, "output_size": 22,
                     "cancelled": False, "timed_out": False},
        )
        # Tool completed
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-full",
            item_type="tool_call_completed",
            payload={"tool_call_id": "tc-full",
                     "result": "file1.txt\nfile2.txt", "duration_ms": 42,
                     "permission_verdict": "allow", "sandbox_type": "none"},
        )
        # Assistant response
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-full",
            item_type="assistant_delta", content="Done.",
            payload={"index": 0},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-full",
            item_type="message", role="assistant", content="Done.",
            payload={"message_fields": {}},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-full",
            item_type="turn_completed",
            payload={"final_content": "Done."},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-full")

        # Verify complete timeline reconstruction
        assert timeline.status == "completed"
        assert timeline.user_input == "run ls"
        assert timeline.assistant_text == "Done."

        # Approval event
        assert len(timeline.approval_events) == 1
        ap = timeline.approval_events[0]
        assert ap.approval_id == "turn-full:tc-full"
        assert ap.tool_name == "exec"
        assert ap.decision == "once"
        assert ap.category == "exec"

        # Tool call
        assert len(timeline.tool_calls) == 1
        tc = timeline.tool_calls[0]
        assert tc.tool_call_id == "tc-full"
        assert tc.name == "exec"
        assert tc.status == "completed"
        assert tc.result == "file1.txt\nfile2.txt"
        assert tc.permission_verdict == "allow"

        # Exec command
        assert len(timeline.exec_commands) == 1
        ex = timeline.exec_commands[0]
        assert ex.tool_call_id == "tc-full"
        assert ex.command == "ls"
        assert ex.output == "file1.txt\nfile2.txt"
        assert ex.exit_code == 0
        assert ex.status == "completed"

        # Time ordering: request seq < resolved seq
        assert ap.request_seq < ap.resolved_seq
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_dangling_approval_request_without_resolution(tmp_path):
    """Approval requested but never resolved → replay as pending."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-dangle",
            item_type="approval_requested",
            payload={
                "approval_id": "turn-dangle:call-1",
                "tool_call_id": "call-1",
                "tool_name": "exec",
                "category": "exec",
                "description": "Run: cmd",
            },
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-dangle")

        assert len(timeline.approval_events) == 1
        ap = timeline.approval_events[0]
        assert ap.decision == "pending"
        assert ap.resolved_seq is None
    finally:
        await ledger.close()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 31.8 fix: No-duplicate replay guarantees
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_replay_produces_exactly_one_exec_command_per_id(tmp_path):
    """When the ledger has exactly 1 exec_started → 1 exec_completed,
    ReplayRuntime must produce exactly 1 ExecCommandReplay — no duplicates."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-one-exec",
            item_type="turn_started", payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-one-exec",
            item_type="exec_started",
            payload={"tool_call_id": "tc-only", "command": "echo one",
                     "cwd": "/tmp", "sandbox_type": "none"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-one-exec",
            item_type="exec_output_delta", content="one",
            payload={"tool_call_id": "tc-only", "stream": "stdout"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-one-exec",
            item_type="exec_completed",
            payload={"tool_call_id": "tc-only", "exit_code": 0,
                     "duration_ms": 10, "output_size": 3,
                     "cancelled": False, "timed_out": False},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-one-exec")

        assert len(timeline.exec_commands) == 1, (
            f"Expected exactly 1 ExecCommandReplay, "
            f"got {len(timeline.exec_commands)}"
        )
        ex = timeline.exec_commands[0]
        assert ex.tool_call_id == "tc-only"
        assert ex.command == "echo one"
        assert ex.output == "one"
        assert ex.status == "completed"
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_replay_produces_exactly_one_approval_event_per_id(tmp_path):
    """When the ledger has exactly 1 approval_requested → 1 approval_resolved,
    ReplayRuntime must produce exactly 1 ApprovalEventReplay — no duplicates."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-one-ap",
            item_type="turn_started", payload={},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-one-ap",
            item_type="approval_requested",
            payload={
                "approval_id": "turn-one-ap:call-only",
                "tool_call_id": "call-only",
                "tool_name": "exec",
                "category": "exec",
                "description": "Run: only",
            },
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-one-ap",
            item_type="approval_resolved",
            payload={
                "approval_id": "turn-one-ap:call-only",
                "tool_call_id": "call-only",
                "decision": "once",
                "tool_name": "exec",
                "category": "exec",
            },
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-one-ap")

        assert len(timeline.approval_events) == 1, (
            f"Expected exactly 1 ApprovalEventReplay, "
            f"got {len(timeline.approval_events)}"
        )
        ap = timeline.approval_events[0]
        assert ap.approval_id == "turn-one-ap:call-only"
        assert ap.decision == "once"
        assert ap.tool_name == "exec"
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_exec_output_deltas_not_duplicated_in_replay(tmp_path):
    """When the ledger has N exec_output_delta items from a single execution,
    ReplayRuntime must merge them exactly once into the output — no
    duplicate content or duplicate ExecCommandReplay."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await ledger.initialize()
    try:
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-delta-once",
            item_type="exec_started",
            payload={"tool_call_id": "tc-delta", "command": "seq 3",
                     "cwd": "/", "sandbox_type": "none"},
        )
        # Write 3 deltas (one per output line)
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-delta-once",
            item_type="exec_output_delta", content="1\n",
            payload={"tool_call_id": "tc-delta", "stream": "stdout"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-delta-once",
            item_type="exec_output_delta", content="2\n",
            payload={"tool_call_id": "tc-delta", "stream": "stdout"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-delta-once",
            item_type="exec_output_delta", content="3",
            payload={"tool_call_id": "tc-delta", "stream": "stdout"},
        )
        await ledger.append_item(
            thread_id="thread-1", turn_id="turn-delta-once",
            item_type="exec_completed",
            payload={"tool_call_id": "tc-delta", "exit_code": 0,
                     "duration_ms": 10, "output_size": 6,
                     "cancelled": False, "timed_out": False},
        )

        replay = ReplayRuntime(ledger)
        timeline = await replay.get_turn_timeline("thread-1", "turn-delta-once")

        # Exactly 1 ExecCommandReplay
        assert len(timeline.exec_commands) == 1
        ex = timeline.exec_commands[0]
        # All 3 deltas merged exactly once
        assert ex.output == "1\n2\n3", (
            f"Deltas should be merged once, got {ex.output!r}"
        )
        assert ex.tool_call_id == "tc-delta"
        assert ex.status == "completed"
    finally:
        await ledger.close()
