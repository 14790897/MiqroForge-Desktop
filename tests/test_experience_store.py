"""Phase 4: ExperienceStore aggregates facts, rules, and traces."""
from pathlib import Path

from miqi.agent.memory.experience_store import ExperienceStore
from miqi.agent.memory.store import MemoryStore
from miqi.agent.trace.store import TraceStore


def make_stores(tmp_path: Path):
    ms = MemoryStore(workspace=tmp_path, lessons_legacy_inject_enabled=True)
    ts = TraceStore(workspace=tmp_path, enabled=True)
    es = ExperienceStore(memory_store=ms, trace_store=ts)
    return ms, ts, es


def test_list_rules_returns_lessons(tmp_path):
    ms, ts, es = make_stores(tmp_path)
    ms._lesson_store.learn("shell:error", "rm -rf /", "use trash", scope="global", session_key="s")
    rules = es.list_entries(type="rule")
    assert len(rules) >= 1
    assert rules[0]["type"] == "rule"
    assert "use trash" in rules[0]["content"]


def test_list_traces_returns_trace_records(tmp_path):
    ms, ts, es = make_stores(tmp_path)
    ts.begin_task("sess1", "fetch-data", "download csv from server")
    ts.record_step("sess1", "web_fetch", "url=http://x.com/data.csv", "200 OK")
    ts.end_task("sess1", "success", "downloaded", tool_calls=[])
    traces = es.list_entries(type="trace")
    assert len(traces) >= 1
    assert traces[0]["type"] == "trace"
    assert "fetch-data" in traces[0]["title"] or "download csv" in traces[0]["content"]


def test_delete_rule(tmp_path):
    ms, ts, es = make_stores(tmp_path)
    ms._lesson_store.learn("test:err", "bad", "good", scope="global", session_key="s")
    rules = es.list_entries(type="rule")
    assert len(rules) >= 1
    entry_id = rules[0]["id"]
    result = es.delete_entry("rule", entry_id)
    assert result is True
    rules_after = es.list_entries(type="rule")
    assert all(r["id"] != entry_id for r in rules_after)


def test_toggle_rule(tmp_path):
    ms, ts, es = make_stores(tmp_path)
    ms._lesson_store.learn("test:toggle", "bad", "better", scope="global", session_key="s")
    rules = es.list_entries(type="rule")
    entry_id = rules[0]["id"]
    assert rules[0]["enabled"] is True

    ok = es.toggle_entry("rule", entry_id, False)
    assert ok is True
    rules2 = es.list_entries(type="rule")
    toggled = next((r for r in rules2 if r["id"] == entry_id), None)
    assert toggled is not None
    assert toggled["enabled"] is False


def test_list_all_types(tmp_path):
    ms, ts, es = make_stores(tmp_path)
    ms._lesson_store.learn("t", "b", "g", scope="global", session_key="s")
    ts.begin_task("s2", "task", "goal")
    ts.end_task("s2", "success", "done", tool_calls=[])
    all_entries = es.list_entries()
    types = {e["type"] for e in all_entries}
    assert "rule" in types
    assert "trace" in types
