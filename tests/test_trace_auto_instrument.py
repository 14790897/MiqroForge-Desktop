"""Tests for TraceStore lifecycle — independent of AgentLoop (Phase 48)."""
from pathlib import Path

from miqi.agent.trace.store import TraceStore


def make_store(tmp_path: Path) -> TraceStore:
    return TraceStore(workspace=tmp_path, enabled=True)


def test_record_step_no_op_without_begin(tmp_path):
    """Calling record_step when no task is open must not raise and not create a task."""
    store = make_store(tmp_path)
    store.record_step("ghost", "some_tool", "args", "result")
    assert store.get_current_task("ghost") is None


def test_auto_begin_then_record_then_end(tmp_path):
    """Simulate what the loop does: auto-begin → record two steps → end."""
    store = make_store(tmp_path)
    session_key = "test:auto"

    # Simulate auto-begin in _process_message
    assert store.get_current_task(session_key) is None
    store.begin_task(session_key, "do-something", "user said: do something")
    assert store.get_current_task(session_key) is not None

    # Simulate two tool calls
    store.record_step(session_key, "web_search", '{"query":"x"}', "10 results")
    store.record_step(session_key, "write_file",  '{"path":"out.md"}', "ok")

    # end_task called at turn boundary (simulates auto-end in finally)
    trace_hash = store.end_task(session_key, "partial", "session reset", tool_calls=[])
    assert trace_hash is not None

    trace = store.get_trace(trace_hash)
    assert trace is not None
    assert trace.goal == "user said: do something"
    assert trace.task_name == "do-something"
    assert len(trace.tool_calls) == 2


def test_begin_task_resets_steps(tmp_path):
    """A second begin_task on same session (auto-close then re-open) starts fresh steps."""
    store = make_store(tmp_path)
    store.begin_task("s", "task1", "first goal")
    store.record_step("s", "tool_a", "a", "ra")

    # Second begin_task should auto-close previous and start fresh
    store.begin_task("s", "task2", "second goal")
    store.record_step("s", "tool_b", "b", "rb")

    task = store.get_current_task("s")
    assert task is not None
    assert task["goal"] == "second goal"
    steps = task.get("steps", [])
    assert len(steps) == 1
    assert steps[0]["tool_name"] == "tool_b"


def test_per_turn_two_turns(tmp_path):
    """Each user turn creates one completed trace; second auto-chains to first via parent_hash."""
    store = make_store(tmp_path)
    session = "test:two-turns"

    # Turn 1: begin → record steps → end (simulates auto-begin + finally auto-end)
    store.begin_task(session, "turn-one", "first user message")
    store.record_step(session, "tool_a", "args1", "result1")
    hash1 = store.end_task(session, "success", "done", tool_calls=[])
    assert hash1 is not None
    assert store.get_current_task(session) is None  # fully closed

    trace1 = store.get_trace(hash1)
    assert trace1.task_name == "turn-one"
    assert len(trace1.tool_calls) == 1

    # Turn 2: begin → record steps → end
    store.begin_task(session, "turn-two", "second user message")
    store.record_step(session, "tool_b", "args2", "result2")
    hash2 = store.end_task(session, "success", "done", tool_calls=[])
    assert hash2 is not None
    assert hash2 != hash1

    trace2 = store.get_trace(hash2)
    assert trace2.parent_hash == hash1  # lineage auto-chained via _latest_trace_hash_for_session
    assert len(trace2.tool_calls) == 1
