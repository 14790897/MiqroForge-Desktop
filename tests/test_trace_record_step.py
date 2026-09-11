"""Phase 1: TraceStore.record_step() accumulates steps that end_task persists."""
import time
from pathlib import Path

from miqi.agent.trace.store import TraceStore


def make_store(tmp_path: Path) -> TraceStore:
    return TraceStore(workspace=tmp_path, enabled=True)


def test_record_step_empty_before_begin(tmp_path):
    """record_step with no open task must be a no-op."""
    store = make_store(tmp_path)
    store.record_step("s1", "read_file", "path=x.txt", "content: hello")
    # No exception, nothing stored
    assert store.get_current_task("s1") is None


def test_record_step_accumulates(tmp_path):
    store = make_store(tmp_path)
    store.begin_task("s1", "fetch-paper", "Download arxiv paper")
    store.record_step("s1", "web_search", 'query="arxiv 2401"', "found 5 results")
    store.record_step("s1", "read_file",  "path=paper.pdf",     "abstract: ...")
    task = store.get_current_task("s1")
    assert task is not None
    assert len(task.get("steps", [])) == 2
    assert task["steps"][0]["tool_name"] == "web_search"
    assert task["steps"][1]["tool_name"] == "read_file"


def test_end_task_uses_accumulated_steps(tmp_path):
    store = make_store(tmp_path)
    store.begin_task("s1", "write-report", "Write summary report")
    store.record_step("s1", "read_file",  "path=data.csv",  "rows: 100")
    store.record_step("s1", "write_file", "path=report.md", "ok: written")
    # Pass empty tool_calls — must fall back to accumulated steps
    trace_hash = store.end_task("s1", "success", "done", tool_calls=[])
    assert trace_hash is not None
    trace = store.get_trace(trace_hash)
    assert trace is not None
    assert len(trace.tool_calls) == 2
    names = [tc.tool_name for tc in trace.tool_calls]
    assert "read_file" in names
    assert "write_file" in names


def test_end_task_explicit_steps_override_accumulated(tmp_path):
    """Explicit tool_calls param must win over auto-accumulated steps."""
    from miqi.agent.trace.model import TaskStep
    store = make_store(tmp_path)
    store.begin_task("s1", "t1", "goal")
    store.record_step("s1", "web_search", "q=x", "res")
    explicit = [TaskStep("explicit_tool", "args", "result", time.time())]
    trace_hash = store.end_task("s1", "success", "used explicit", tool_calls=explicit)
    trace = store.get_trace(trace_hash)
    assert len(trace.tool_calls) == 1
    assert trace.tool_calls[0].tool_name == "explicit_tool"
