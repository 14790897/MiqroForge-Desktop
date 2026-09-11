"""Tests for miqi.runtime.agent_graph_store."""

import time

from miqi.runtime.agent_graph_store import AgentGraphStore


def test_save_and_load_job(tmp_path):
    store = AgentGraphStore(tmp_path / "agent_graph.db")
    store.save_job(
        job_id="job-1",
        agent_type="code-agent",
        task="fix imports",
        thread_id="thread-1",
        parent_thread_id="main",
        status="completed",
        result="done",
        error=None,
        created_at=time.time(),
        completed_at=time.time(),
    )

    jobs = store.load_jobs()
    assert len(jobs) == 1
    assert jobs[0].job_id == "job-1"
    assert jobs[0].status == "completed"


def test_update_job_status(tmp_path):
    store = AgentGraphStore(tmp_path / "agent_graph.db")
    store.save_job(
        job_id="job-1",
        agent_type="code-agent",
        task="fix imports",
        thread_id="thread-1",
        parent_thread_id="main",
        status="running",
        result=None,
        error=None,
        created_at=time.time(),
        completed_at=None,
    )

    store.update_job_status(
        job_id="job-1",
        status="completed",
        result="all good",
        error=None,
        completed_at=time.time(),
    )

    job = store.get_job("job-1")
    assert job is not None
    assert job.status == "completed"
    assert job.result == "all good"
    assert job.completed_at is not None


def test_get_job(tmp_path):
    store = AgentGraphStore(tmp_path / "agent_graph.db")
    assert store.get_job("missing") is None

    store.save_job(
        job_id="job-1",
        agent_type="code-agent",
        task="fix imports",
        thread_id="thread-1",
        parent_thread_id="main",
        status="queued",
        result=None,
        error=None,
        created_at=time.time(),
        completed_at=None,
    )

    job = store.get_job("job-1")
    assert job is not None
    assert job.agent_type == "code-agent"


def test_spawn_edges_form_a_tree(tmp_path):
    store = AgentGraphStore(tmp_path / "agent_graph.db")
    store.add_edge(parent_agent_id="root", child_agent_id="a", child_thread_id="t:a")
    store.add_edge(parent_agent_id="a", child_agent_id="b", child_thread_id="t:b")
    store.add_edge(parent_agent_id="a", child_agent_id="c", child_thread_id="t:c")

    tree = store.get_tree("root")
    assert set(tree) == {"root", "a", "b", "c"}
    assert tree[0] == "root"

    children = store.get_children("a")
    assert len(children) == 2
    child_ids = {edge.child_agent_id for edge in children}
    assert child_ids == {"b", "c"}


def test_add_edge_is_idempotent(tmp_path):
    store = AgentGraphStore(tmp_path / "agent_graph.db")
    store.add_edge(parent_agent_id="root", child_agent_id="child", child_thread_id="t:child")
    store.add_edge(parent_agent_id="root", child_agent_id="child", child_thread_id="t:child")

    children = store.get_children("root")
    assert len(children) == 1
    assert children[0].child_agent_id == "child"


def test_empty_db_load_jobs_returns_empty(tmp_path):
    store = AgentGraphStore(tmp_path / "agent_graph.db")
    assert store.load_jobs() == []
