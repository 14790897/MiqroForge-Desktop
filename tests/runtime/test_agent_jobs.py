"""Tests for AgentJobRuntime (Phase 13.2)."""

import asyncio

import pytest


@pytest.fixture
def fake_services_for_jobs(fake_services):
    """Services fixture with a working turn_runner.run_agent_job mock."""
    from miqi.runtime.turn_runner import TurnResult

    async def _fake_run_agent_job(job):
        return TurnResult(
            final_content=f"Completed: {job.task}",
            messages=[{"role": "assistant", "content": f"Completed: {job.task}"}],
            tools_used=[],
        )

    fake_services.turn_runner.run_agent_job = _fake_run_agent_job
    return fake_services


@pytest.mark.asyncio
async def test_agent_job_runtime_starts_job(fake_services_for_jobs):
    from miqi.runtime.agent_jobs import AgentJobRuntime

    runtime = AgentJobRuntime(services=fake_services_for_jobs)

    job = await runtime.start(
        agent_type="code-agent",
        task="inspect repo",
        parent_thread_id="main",
    )

    assert job.job_id
    assert job.thread_id
    assert job.thread_id == f"{fake_services_for_jobs.session_id}:{job.job_id}"
    assert job.status in {"queued", "running"}


@pytest.mark.asyncio
async def test_agent_job_runtime_lists_jobs(fake_services_for_jobs):
    from miqi.runtime.agent_jobs import AgentJobRuntime

    runtime = AgentJobRuntime(services=fake_services_for_jobs)
    job = await runtime.start(
        agent_type="code-agent", task="inspect repo", parent_thread_id="main",
    )

    jobs = runtime.list()
    assert any(item["job_id"] == job.job_id for item in jobs)


@pytest.mark.asyncio
async def test_agent_job_completes_and_persists_result(fake_services_for_jobs):
    from miqi.runtime.agent_jobs import AgentJobRuntime

    runtime = AgentJobRuntime(services=fake_services_for_jobs)
    job = await runtime.start(
        agent_type="code-agent", task="analyze me", parent_thread_id="main",
    )

    # Let the background task run
    await asyncio.sleep(0.1)

    # Job should have completed
    jobs = runtime.list()
    found = next(item for item in jobs if item["job_id"] == job.job_id)
    assert found["status"] == "completed"
    assert found["result_preview"]


@pytest.mark.asyncio
async def test_agent_job_get_raises_for_unknown():
    from miqi.runtime.agent_jobs import AgentJobRuntime

    runtime = AgentJobRuntime(services=None)
    with pytest.raises(KeyError):
        runtime.get("nonexistent")


@pytest.mark.asyncio
async def test_agent_job_kill_aborts_running_job(fake_services_for_jobs):
    from miqi.runtime.agent_jobs import AgentJobRuntime

    # Use a blocking turn runner so the job stays "running"
    async def _blocking(_job):
        await asyncio.sleep(10)
        from miqi.runtime.turn_runner import TurnResult
        return TurnResult(final_content="done", messages=[], tools_used=[])

    fake_services_for_jobs.turn_runner.run_agent_job = _blocking

    runtime = AgentJobRuntime(services=fake_services_for_jobs)
    job = await runtime.start(
        agent_type="code-agent", task="long task", parent_thread_id="main",
    )

    # Give the task a tick to start
    await asyncio.sleep(0.02)

    # Kill it
    await runtime.kill(job.job_id)

    # Give cancellation a tick
    await asyncio.sleep(0.05)

    jobs = runtime.list()
    found = next(item for item in jobs if item["job_id"] == job.job_id)
    assert found["status"] == "aborted"


def test_agent_job_dataclass_defaults():
    from miqi.runtime.agent_jobs import AgentJob

    job = AgentJob(
        job_id="j1",
        agent_type="code-agent",
        task="test",
        thread_id="t1",
        parent_thread_id="main",
    )
    assert job.status == "queued"
    assert job.result is None
    assert job.error is None
    assert job.completed_at is None
    assert job.created_at > 0


# ---------------------------------------------------------------------------
# Phase 52: persistence and resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persisted_job_survives_restart(tmp_path, fake_services_for_jobs):
    """A completed job persisted to the store must be visible in a fresh runtime."""
    import asyncio as _asyncio

    from miqi.runtime.agent_graph_store import AgentGraphStore
    from miqi.runtime.agent_jobs import AgentJobRuntime
    from miqi.runtime.turn_runner import TurnResult

    db_path = tmp_path / "agent_graph.db"
    store = AgentGraphStore(db_path)

    async def _fake_run_agent_job(job):
        return TurnResult(final_content="done", messages=[], tools_used=[])

    fake_services_for_jobs.turn_runner.run_agent_job = _fake_run_agent_job

    runtime = AgentJobRuntime(services=fake_services_for_jobs, store=store)
    job = await runtime.start(
        agent_type="code-agent", task="persist me", parent_thread_id="main",
    )

    # Wait for the background task to finish
    await _asyncio.sleep(0.1)

    # Create a brand-new runtime backed by the same store
    resumed = AgentJobRuntime(services=fake_services_for_jobs, store=store)
    jobs = resumed.list()
    found = next((j for j in jobs if j["job_id"] == job.job_id), None)
    assert found is not None, "Persisted job should be loaded into new runtime"
    assert found["status"] == "completed"
    assert found["result_preview"] == "done"


@pytest.mark.asyncio
async def test_running_job_resumed_as_interrupted(tmp_path):
    """A 'running' job row must be loaded as 'interrupted' on restart."""
    import time

    from miqi.runtime.agent_graph_store import AgentGraphStore
    from miqi.runtime.agent_jobs import AgentJobRuntime

    store = AgentGraphStore(tmp_path / "agent_graph.db")
    store.save_job(
        job_id="orphan-job",
        agent_type="code-agent",
        task="was running",
        thread_id="thread-1",
        parent_thread_id="main",
        status="running",
        result=None,
        error=None,
        created_at=time.time(),
        completed_at=None,
    )

    runtime = AgentJobRuntime(services=None, store=store)
    jobs = runtime.list()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_without_store_behavior_unchanged(fake_services_for_jobs):
    """Constructing AgentJobRuntime without a store must preserve existing behaviour."""
    import asyncio as _asyncio

    from miqi.runtime.agent_jobs import AgentJobRuntime

    runtime = AgentJobRuntime(services=fake_services_for_jobs)
    job = await runtime.start(
        agent_type="code-agent", task="no store", parent_thread_id="main",
    )

    await _asyncio.sleep(0.1)

    jobs = runtime.list()
    found = next((j for j in jobs if j["job_id"] == job.job_id), None)
    assert found is not None
    assert found["status"] == "completed"
    assert found["result_preview"].startswith("Completed: no store")
