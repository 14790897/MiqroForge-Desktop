"""Tests for AgentJobRuntime (Phase 13.2)."""

import asyncio

import pytest


@pytest.fixture
def fake_services_for_jobs(fake_services):
    """Services fixture with a working turn_runner.run_agent_job mock."""
    from miqi.runtime.turn_runner import TurnResult

    async def _fake_run_agent_job(job, **_kwargs):
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
async def test_job_run_inherits_session_workspace_and_identity(fake_services):
    """回归(2026-10-15):run_agent_job 曾只收到 job——作业 turn 的 workspace 落到
    默认 "."，工具 ctx 继承它、SURE 事件桥接再把它当 project_root 发出，被 SURE
    按设计拒收(非绝对路径、什么也不记录)，子代理的工具事件整段静默丢失
    (本地与 CI 实测)。会话的 workspace/session_id 必须随任务下传。"""
    from types import SimpleNamespace

    from miqi.runtime.agent_jobs import AgentJobRuntime

    captured: dict = {}

    async def _capture(job, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(final_content="ok")

    fake_services.turn_runner.run_agent_job = _capture
    runtime = AgentJobRuntime(services=fake_services)
    await runtime.start(agent_type="code-agent", task="t", parent_thread_id="main")
    await asyncio.sleep(0.1)
    assert captured["workspace"] == fake_services.workspace
    assert captured["session_id"] == fake_services.session_id


def test_the_stall_threshold_outlasts_the_measured_worst_case_wait():
    """看门狗阈值必须长于实测最坏的合法单次等待——采样点在这些等待里不动,
    合法与卡死对它没有区别。

    实测最坏:一次网关非流式回退可达 600s(2026-10-13,e2e 注释同款记录);
    2026-10-14 首战:修复子代理在网关等待里被 5 分钟阈值误杀(sure-e2e 完整
    修复闭环,本机 12:06 的 stall dump)。上界是该 E2E 的 30 分钟等待窗口:
    真卡死交给看门狗兜底,不该由测试超时收场。
    """
    import inspect

    from miqi.runtime.agent_jobs import AgentJobRuntime

    params = inspect.signature(AgentJobRuntime.__init__).parameters
    seconds = params["stall_interval"].default * params["stall_samples"].default
    assert seconds > 600.0, (
        f"the stall threshold ({seconds:.0f}s) is not longer than the measured worst "
        "legal single wait (600s gateway non-stream fallback): a working-but-slow job "
        "would be killed"
    )
    assert seconds < 1800.0, (
        f"the stall threshold ({seconds:.0f}s) does not fit inside the full-repair E2E's "
        "30-minute window: a genuinely stuck job would be ended by the test's deadline "
        "instead of by the watchdog"
    )


@pytest.mark.asyncio
async def test_a_job_initializes_a_history_runtime_the_session_never_started(tmp_path):
    """作业回合经 HistoryRuntime 落 turn——落在「建了没 start」的会话上时,首个
    落库会 `HistoryRuntime.initialize() must be called before use`(2026-10-14
    CI 实测:修复子代理派发 4 秒即 failed,完整闭环用例 attempt 1 因此必红,且
    错误只此一行、无堆栈)。作业运行器保证依赖就绪,不赌会话启动的时序。
    """
    from types import SimpleNamespace

    from miqi.runtime.agent_jobs import AgentJobRuntime
    from miqi.runtime.history_runtime import HistoryRuntime

    history = HistoryRuntime(tmp_path / "runtime.db", session_id="s-unstarted")
    seen: dict[str, bool] = {}

    async def _run(job, **_kwargs):
        # 生产路径里回合的首个落库动作正是读这条连接;未 initialize 时抛
        # RuntimeError——就是 CI 里那行失败的原文。
        seen["conn"] = history._conn is not None
        return SimpleNamespace(final_content="done")

    services = SimpleNamespace(
        session_id="s-unstarted",
        workspace=tmp_path,
        history_runtime=history,
        turn_runner=SimpleNamespace(run_agent_job=_run),
        agent_control=None,
    )
    runtime = AgentJobRuntime(services=services, stall_interval=0.05, stall_samples=3)
    job = await runtime.start(
        agent_type="code-agent", task="t", parent_thread_id="main",
    )

    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        if job.status not in ("queued", "running"):
            break
        await asyncio.sleep(0.02)

    assert job.status == "completed", f"作业未完成: {job.error}"
    assert seen.get("conn") is True, "回合开始前 history 仍未 initialize"
    await history.close()


@pytest.mark.asyncio
async def test_stalled_job_is_failed_by_the_watchdog(fake_services):
    """2026-10-14:无声卡死的作业必须被看门狗如实收尾,而不是永远 running。

    复现形态:run_agent_job 停在一个永不触发的等待上(实测现场:事件循环
    空闲、无 socket、无 DB 活动、所有超时层都不触发)。看门狗以(可调小的)
    采样间隔判定"挂起点连续不动"后:日志 dump + 作业按 stalled 失败 +
    正常通知前端。
    """
    from miqi.runtime.agent_jobs import AgentJobRuntime

    never = asyncio.Event()

    async def _forever(job, **_kwargs):
        await never.wait()

    fake_services.turn_runner.run_agent_job = _forever
    notified: list[tuple[str, str]] = []

    class _AC:
        async def notify_completed(self, job_id, status):
            notified.append((job_id, status))

    fake_services.agent_control = _AC()

    runtime = AgentJobRuntime(
        services=fake_services, stall_interval=0.05, stall_samples=3,
    )
    job = await runtime.start(
        agent_type="code-agent", task="will stall", parent_thread_id="main",
    )

    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        if job.status not in ("queued", "running"):
            break
        await asyncio.sleep(0.05)

    assert job.status == "error"
    assert job.error and "stalled" in job.error
    assert notified and notified[-1] == (job.job_id, "error")
    never.set()


@pytest.mark.asyncio
async def test_await_chain_walks_to_the_innermost_await(fake_services):
    """2026-10-14 首战教训:get_stack() 只给最外一帧,卡点在 cr_await 链里。

    构造三层嵌套 await(最内层停在 Event.wait),断言 _await_chain 能把
    三层全部列出——失速 dump 的可用性就是这条链的完整度。
    """
    import asyncio as aio

    from miqi.runtime.agent_jobs import AgentJobRuntime

    never = aio.Event()

    async def inner():
        await never.wait()

    async def middle():
        await inner()

    async def outer():
        await middle()

    task = aio.create_task(outer())
    await aio.sleep(0.05)  # 让它停在 inner 里
    runtime = AgentJobRuntime(services=fake_services)
    chain = runtime._await_chain(task)
    task.cancel()
    try:
        await task
    except aio.CancelledError:
        pass
    assert len(chain) >= 3, chain
    assert any("test_agent_jobs.py" in entry for entry in chain), chain
    assert any("locks.py" in entry or "events" in entry for entry in chain), chain


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
    async def _blocking(_job, **_kwargs):
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

    async def _fake_run_agent_job(job, **_kwargs):
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
