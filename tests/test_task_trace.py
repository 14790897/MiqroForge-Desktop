import importlib.util
import json
import time
from pathlib import Path

import pytest

from miqi.agent.context import ContextBuilder
from miqi.agent.trace import store as trace_store_module
from miqi.agent.trace.migrate import migrate_lessons_to_traces
from miqi.agent.trace.model import TaskStep
from miqi.agent.trace.store import TraceStore


class _UnavailableEmbedder:
    def encode_one(self, text: str) -> bytes | None:
        return None


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TraceStore:
    monkeypatch.setattr(
        trace_store_module.Embedder,
        "get",
        staticmethod(lambda _: _UnavailableEmbedder()),
    )
    return TraceStore(workspace=tmp_path, enabled=True)


def test_task_trace_crud(store: TraceStore):
    sid = "sess-1"
    tid = store.begin_task(sid, "test_task", "Do something useful")
    assert tid

    h = store.end_task(
        sid,
        outcome="success",
        outcome_notes="Worked perfectly",
        tool_calls=[TaskStep("read_file", "config.json", "ok", time.time())],
    )
    assert h

    trace = store.get_trace(h)
    assert trace is not None
    assert trace.outcome == "success"
    assert trace.tool_calls[0].tool_name == "read_file"


def test_double_begin_auto_closes_previous(store: TraceStore):
    sid = "sess-2"
    store.begin_task(sid, "first", "first goal")
    store.begin_task(sid, "second", "second goal")
    recents = store.list_recent(n=10)
    firsts = [t for t in recents if t.task_name == "first"]
    assert firsts and firsts[0].outcome == "partial"


def test_search_fallback_when_embedding_unavailable(store: TraceStore):
    sid = "sess-3"
    store.begin_task(sid, "fetch", "fetch arxiv papers")
    store.end_task(sid, "success", "all good", [])
    results = store.search_traces("download papers", limit=3)
    assert isinstance(results, list)


@pytest.mark.skipif(
    not importlib.util.find_spec("fastembed"),
    reason="fastembed not installed",
)
def test_embedding_semantic_search(tmp_path: Path):
    store = TraceStore(workspace=tmp_path, enabled=True)
    sid = "sess-4"
    for name, goal in [
        ("gh-issues", "fetch github issues"),
        ("arxiv", "get paper pdfs from arxiv"),
        ("build", "compile the rust code"),
    ]:
        store.begin_task(sid, name, goal)
        store.end_task(sid, "success", goal, [])
    results = store.search_traces("download papers from arxiv", limit=2)
    assert results
    assert results[0].task_name == "arxiv"
    assert results[0].similarity_score > 0.5


def test_context_injection(store: TraceStore, tmp_path: Path):
    sid = "sess-6"
    store.begin_task(sid, "paper-download", "download arxiv papers")
    store.end_task(
        sid,
        "success",
        "Use paper_search before downloading PDFs.",
        [TaskStep("paper_search", "arxiv", "ok", time.time())],
    )

    builder = ContextBuilder(workspace=tmp_path, trace_store=store)
    prompt = builder.build_system_prompt(
        session_key=sid,
        current_message="download papers from arxiv",
    )

    assert "## Similar Task History" in prompt
    assert "paper-download" in prompt
    assert "paper_search" in prompt


def test_lesson_migration(store: TraceStore, tmp_path: Path):
    lessons_file = tmp_path / "LESSONS.jsonl"
    lesson = {
        "id": "abc123",
        "actor_key": "cli:user",
        "trigger": "response:length",
        "bad_action": "answered too long",
        "better_action": "answer concisely",
        "enabled": True,
        "created_at": 123.0,
        "updated_at": 456.0,
    }
    lessons_file.write_text(json.dumps(lesson, ensure_ascii=False) + "\n", encoding="utf-8")

    assert migrate_lessons_to_traces(lessons_file, store) == 1
    assert migrate_lessons_to_traces(lessons_file, store) == 0

    trace = store.get_trace("lesson:abc123")
    assert trace is not None
    assert trace.session_id == "cli:user"
    assert trace.task_name == "lesson-response:length"
    assert trace.outcome == "success"
    assert trace.outcome_notes == "answer concisely"
    assert trace.metadata["source"] == "legacy_lesson"
