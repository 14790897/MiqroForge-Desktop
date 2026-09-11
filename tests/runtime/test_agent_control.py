"""Tests for miqi.runtime.agent_control."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.protocol.events import AgentStatus
from miqi.runtime.agent_control import AgentControl
from miqi.runtime.agent_registry import AgentRegistry


@pytest.fixture
def event_emitter():
    emitter = MagicMock()
    emitter.emit = AsyncMock()
    return emitter


@pytest.fixture
def agent_control(tmp_path, event_emitter):
    registry = AgentRegistry()
    return AgentControl(
        session_id="test-session",
        registry=registry,
        event_emitter=event_emitter,
        workspace=tmp_path,
    )


@pytest.mark.asyncio
async def test_spawn_agent(agent_control, event_emitter):
    agent = await agent_control.spawn(
        agent_type="code-agent",
        task="Fix the lint errors",
        label="lint-fix",
    )
    assert agent.agent_id
    assert agent.metadata.name == "code-agent"
    assert agent.state.current == AgentStatus.IDLE
    event_emitter.emit.assert_called()


@pytest.mark.asyncio
async def test_max_concurrent_blocks_4th_spawn(tmp_path, event_emitter):
    """Issue #246: no more than max_concurrent (3) subagents at once."""
    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=event_emitter,
        workspace=tmp_path,
        max_concurrent=3,
    )
    for i in range(3):
        await control.spawn("code-agent", f"task {i}", label=f"a{i}")
    with pytest.raises(RuntimeError, match="already running"):
        await control.spawn("code-agent", "task 3", label="a3")


@pytest.mark.asyncio
async def test_max_concurrent_releases_terminal_agents(tmp_path, event_emitter):
    """A completed subagent frees a slot for a new spawn."""
    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=event_emitter,
        workspace=tmp_path,
        max_concurrent=3,
    )
    agents = [
        await control.spawn("code-agent", f"task {i}", label=f"a{i}")
        for i in range(3)
    ]
    # Two agents finish → only one slot is occupied → a new spawn fits.
    for a in agents[:2]:
        a.state.transition(AgentStatus.THINKING)
        a.state.transition(AgentStatus.COMPLETED)
    replacement = await control.spawn("code-agent", "task 3", label="a3")
    assert replacement.agent_id
    # Third still running + replacement = 2 < 3, so one more is allowed.
    await control.spawn("code-agent", "task 4", label="a4")


@pytest.mark.asyncio
async def test_list_agents(agent_control):
    await agent_control.spawn("code-agent", "task 1", label="a")
    await agent_control.spawn("doc-agent", "task 2", label="b")
    agents = agent_control.list_agents()
    assert len(agents) == 2
    types = {a["type"] for a in agents}
    assert types == {"Code Agent", "Document Agent"}


@pytest.mark.asyncio
async def test_kill_agent(agent_control):
    agent = await agent_control.spawn("code-agent", "task", label="test")
    agent_id = agent.agent_id
    await agent_control.kill(agent_id)
    with pytest.raises(KeyError):
        await agent_control.get_status(agent_id)


@pytest.mark.asyncio
async def test_completion_delivered_once(tmp_path):
    """Issue #246 review: kill() emits aborted, and the cancelled _run_agent
    task's finally would emit again — the completion must reach the frontend
    exactly once (idempotent delivery)."""
    from unittest.mock import AsyncMock, MagicMock

    calls: list[dict] = []
    async def on_complete(data: dict) -> None:
        calls.append(data)

    emitter = MagicMock()
    emitter.emit = AsyncMock()
    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=emitter,
        workspace=tmp_path,
        completion_callback=on_complete,
    )
    agent = await control.spawn("code-agent", "task", label="test")

    # First emission (kill → aborted) is delivered…
    await control._emit_completed(agent, status="aborted")
    assert len(calls) == 1
    assert calls[0]["status"] == "aborted"

    # …the second (cancelled _run_agent finally) is suppressed.
    await control._emit_completed(agent)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_notify_completed_advances_state_without_callback(tmp_path):
    """Issue #246 review: notify_completed must sync the job result and
    advance the state machine to terminal even when no completion callback is
    wired — otherwise a finished agent stays THINKING and permanently occupies
    a max_concurrent slot (CLI/TUI/gateway transports pass no callback)."""
    from unittest.mock import AsyncMock, MagicMock

    emitter = MagicMock()
    emitter.emit = AsyncMock()
    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=emitter,
        workspace=tmp_path,
        completion_callback=None,  # no frontend transport
    )
    agent = await control.spawn("code-agent", "task", label="test")

    # Simulate the AgentJobRuntime path: the agent is THINKING while the
    # job runs (spawn with a job runtime transitions immediately).
    agent.state.transition(AgentStatus.THINKING)

    # Simulate AgentJobRuntime finishing the job.
    fake_job = MagicMock()
    fake_job.result = "done"
    fake_job.error = None
    fake_jobs = MagicMock()
    fake_jobs.get = MagicMock(return_value=fake_job)
    control._agent_jobs = fake_jobs

    await control.notify_completed(agent.agent_id, "completed")

    assert agent.state.current == AgentStatus.COMPLETED
    assert agent.result == "done"

    # The terminal state frees the concurrency slot for a new spawn.  Restore
    # _agent_jobs so the replacement spawn takes the legacy path.
    control._agent_jobs = None
    replacement = await control.spawn("code-agent", "task 2", label="b")
    assert replacement.agent_id


@pytest.mark.asyncio
async def test_get_status(agent_control):
    agent = await agent_control.spawn("code-agent", "task", label="test")
    status = await agent_control.get_status(agent.agent_id)
    assert status == AgentStatus.IDLE


@pytest.mark.asyncio
async def test_spawn_unknown_type_raises(agent_control):
    with pytest.raises(KeyError, match="Unknown agent type"):
        await agent_control.spawn("nonexistent", "task")


@pytest.mark.asyncio
async def test_spawn_emits_event(agent_control, event_emitter):
    await agent_control.spawn("research-agent", "Research X", label="research-x")
    # Should emit SubAgentSpawnedEvent
    call_args = event_emitter.emit.call_args
    assert call_args is not None


@pytest.mark.asyncio
async def test_kill_updates_status(agent_control):
    agent = await agent_control.spawn("code-agent", "task", label="test")
    await agent_control.kill(agent.agent_id)
    # agent should be removed and status unavailable
    with pytest.raises(KeyError):
        await agent_control.get_status(agent.agent_id)


@pytest.mark.asyncio
async def test_spawn_main_agent(agent_control, event_emitter):
    agent = await agent_control.spawn("main", "General task", label="main-task")
    assert agent.metadata.name == "main"
    assert agent.metadata.max_iterations == 40


@pytest.mark.asyncio
async def test_fork_creates_new_agent(agent_control, event_emitter):
    parent = await agent_control.spawn("code-agent", "test task", label="parent")
    child = await agent_control.fork(parent.thread_id)
    assert child.agent_id != parent.agent_id
    assert child.thread_id != parent.thread_id


@pytest.mark.asyncio
async def test_fork_unknown_thread_raises(agent_control):
    with pytest.raises(ValueError, match="Unknown thread"):
        await agent_control.fork("nonexistent-thread")


# ---------------------------------------------------------------------------
# Phase 10: Tool definitions, orchestrator enforcement, result persistence
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_tool_registry():
    """Create a minimal ToolRegistry mock with get_definitions for testing."""
    from unittest.mock import MagicMock

    registry = MagicMock()
    registry.get_definitions.return_value = [
        {
            "type": "function",
            "function": {"name": "read_file", "description": "Read a file", "parameters": {}},
        },
        {
            "type": "function",
            "function": {"name": "exec", "description": "Execute command", "parameters": {}},
        },
        {
            "type": "function",
            "function": {"name": "docx_read", "description": "Read docx", "parameters": {}},
        },
    ]
    return registry


@pytest.fixture
def fake_provider():
    """Create a provider that records its calls and returns responses."""

    class FakeProvider:
        def __init__(self):
            self.chat_calls: list[dict] = []
            self.response_sequence: list = []

        def get_default_model(self) -> str:
            return "gpt-4o"

        async def chat(self, **kwargs):
            self.chat_calls.append(kwargs)
            if self.response_sequence:
                return self.response_sequence.pop(0)
            # Default: final answer
            return _FakeResponse(content="Done.", tool_calls=[])

        async def stream_chat(self, **kwargs):
            """Phase 20: streaming fallback wrapping chat()."""
            from miqi.providers.base import LLMStreamEvent

            response = await self.chat(**kwargs)
            yield LLMStreamEvent(kind="completed", response=response)

    return FakeProvider()


class _FakeToolCall:
    """Minimal fake for response.tool_calls entries."""

    def __init__(self, name, args, tc_id="tcid"):
        self.name = name
        self.arguments = args
        self.id = tc_id


class _FakeResponse:
    """Minimal fake response object."""

    def __init__(self, content="", tool_calls=None, finish_reason="stop"):
        self.content = content
        self.tool_calls = tool_calls or []
        self._has_tool_calls = bool(tool_calls)
        self.finish_reason = finish_reason

    @property
    def has_tool_calls(self):
        return self._has_tool_calls


# Task 10.3: Sub-agents receive role-filtered tool definitions
@pytest.mark.asyncio
async def test_sub_agent_receives_role_filtered_tools(
    tmp_path,
    event_emitter,
    fake_provider,
    fake_tool_registry,
):
    """code-agent should only see code tools, not doc tools."""
    from miqi.runtime.agent_control import AgentControl
    from miqi.runtime.agent_registry import AgentRegistry

    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=event_emitter,
        workspace=tmp_path,
        provider=None,  # Don't auto-start background task
        tool_registry=fake_tool_registry,
        orchestrator="placeholder",  # Won't be used if agent has no tool calls
    )

    # Spawn agent (no auto-start since provider is None)
    agent = await control.spawn("code-agent", "test", label="test-role-tools")

    # Set provider after spawn, then call _run_agent directly
    control._provider = fake_provider
    fake_provider.response_sequence = [
        _FakeResponse(content="Done.", tool_calls=[]),
    ]

    await control._run_agent(agent, "test task")

    # Provider should have been called with role-filtered tools
    assert len(fake_provider.chat_calls) >= 1
    tools_arg = fake_provider.chat_calls[0].get("tools")
    assert tools_arg is not None, "Sub-agent must receive tool definitions (was None)"

    tool_names = {t["function"]["name"] for t in tools_arg}
    # code-agent should have read_file and exec (code tools)
    assert "read_file" in tool_names
    assert "exec" in tool_names
    # code-agent should NOT have doc tools
    assert "docx_read" not in tool_names


# Task 10.4: Orchestrator required before sub-agent tool execution
@pytest.mark.asyncio
async def test_sub_agent_raises_when_no_orchestrator_for_tools(
    tmp_path,
    event_emitter,
    fake_provider,
    fake_tool_registry,
):
    """Sub-agent must raise RuntimeError when tools are called but no orchestrator."""
    from miqi.runtime.agent_control import AgentControl
    from miqi.runtime.agent_registry import AgentRegistry

    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=event_emitter,
        workspace=tmp_path,
        provider=None,  # Don't auto-start
        tool_registry=fake_tool_registry,
        orchestrator=None,  # No orchestrator!
    )

    agent = await control.spawn("code-agent", "test", label="test-no-orch")

    # Set provider after spawn to avoid auto-start
    control._provider = fake_provider
    fake_provider.response_sequence = [
        _FakeResponse(
            tool_calls=[_FakeToolCall("read_file", {"path": "/tmp/x"}, "tc-1")],
        ),
    ]

    # _run_agent should set error state
    await control._run_agent(agent, "test task")

    # Agent should have transitioned to ERROR with a clear error
    assert agent.error is not None, "Agent should have recorded an error"
    assert "ToolOrchestrator must be configured" in agent.error
    assert agent.state.current.value in ("error", "aborted")


# Task 10.5: Sub-agent results are persisted and exposed via list/detail
@pytest.mark.asyncio
async def test_sub_agent_result_persisted(
    tmp_path,
    event_emitter,
    fake_provider,
    fake_tool_registry,
):
    """After completion, sub-agent result/error/messages are stored on LiveAgent."""
    from miqi.runtime.agent_control import AgentControl
    from miqi.runtime.agent_registry import AgentRegistry

    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=event_emitter,
        workspace=tmp_path,
        provider=None,  # Don't auto-start
        tool_registry=fake_tool_registry,
        orchestrator="placeholder",
    )

    agent = await control.spawn("code-agent", "Write a test", label="test-persist")

    # Set provider after spawn
    control._provider = fake_provider
    fake_provider.response_sequence = [
        _FakeResponse(content="Task complete. Here is the code.", tool_calls=[]),
    ]

    await control._run_agent(agent, "Write a test")

    # Result persisted
    assert agent.result is not None
    assert "Task complete" in agent.result
    assert agent.completed_at is not None
    # Messages recorded
    assert len(agent.messages) >= 2  # system + user + assistant
    roles = {m["role"] for m in agent.messages}
    assert "assistant" in roles

    # list_agents includes result_preview
    agents_list = control.list_agents()
    assert len(agents_list) == 1
    assert "result_preview" in agents_list[0]
    assert len(agents_list[0]["result_preview"]) <= 200
    assert "Task complete" in agents_list[0]["result_preview"]

    # get_agent_detail returns full info
    detail = control.get_agent_detail(agent.agent_id)
    assert detail["result"] == agent.result
    assert detail["messages"] is agent.messages
    assert detail["completed_at"] == agent.completed_at


@pytest.mark.asyncio
async def test_subagent_end_hook_error_does_not_fail_successful_agent(
    tmp_path,
    event_emitter,
    fake_provider,
    fake_tool_registry,
):
    from miqi.execution.hook_runtime import HookPoint
    from miqi.runtime.agent_registry import AgentRegistry

    hooks = MagicMock()

    async def run_hook(point, ctx):
        if point == HookPoint.SUBAGENT_END:
            raise RuntimeError("hook exploded")

    hooks.run = AsyncMock(side_effect=run_hook)
    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=event_emitter,
        workspace=tmp_path,
        provider=None,
        tool_registry=fake_tool_registry,
        orchestrator="placeholder",
        hooks=hooks,
    )
    agent = await control.spawn("code-agent", "task", label="hook-success")
    control._provider = fake_provider
    fake_provider.response_sequence = [
        _FakeResponse(content="Task complete.", tool_calls=[]),
    ]

    await control._run_agent(agent, "task")

    assert agent.state.current == AgentStatus.COMPLETED
    assert agent.result == "Task complete."
    assert agent.error is None


@pytest.mark.asyncio
async def test_subagent_end_hook_error_does_not_mask_agent_error(
    tmp_path,
    event_emitter,
    fake_provider,
    fake_tool_registry,
):
    from miqi.execution.hook_runtime import HookPoint
    from miqi.runtime.agent_registry import AgentRegistry

    hooks = MagicMock()

    async def run_hook(point, ctx):
        if point == HookPoint.SUBAGENT_END:
            raise RuntimeError("hook exploded")

    hooks.run = AsyncMock(side_effect=run_hook)
    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=event_emitter,
        workspace=tmp_path,
        provider=None,
        tool_registry=fake_tool_registry,
        orchestrator=None,
        hooks=hooks,
    )
    agent = await control.spawn("code-agent", "task", label="hook-failure")
    control._provider = fake_provider
    fake_provider.response_sequence = [
        _FakeResponse(
            tool_calls=[_FakeToolCall("read_file", {"path": "/tmp/x"}, "tc-1")],
        ),
    ]

    await control._run_agent(agent, "task")

    assert agent.state.current == AgentStatus.ERROR
    assert agent.error is not None
    assert "ToolOrchestrator must be configured" in agent.error
    assert "hook exploded" not in agent.error


@pytest.mark.asyncio
async def test_get_agent_detail_unknown_raises(tmp_path, event_emitter):
    """get_agent_detail should raise KeyError for unknown agent IDs."""
    from miqi.runtime.agent_control import AgentControl
    from miqi.runtime.agent_registry import AgentRegistry

    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=event_emitter,
        workspace=tmp_path,
    )

    with pytest.raises(KeyError, match="Unknown agent"):
        control.get_agent_detail("nonexistent")


# ---------------------------------------------------------------------------
# Phase 10 post-audit: kill cancels background task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_cancels_running_task(tmp_path, event_emitter, fake_tool_registry):
    """kill() must cancel the background asyncio Task so the sub-agent stops."""
    import asyncio as _asyncio

    from miqi.runtime.agent_control import AgentControl
    from miqi.runtime.agent_registry import AgentRegistry

    # Blocking provider — never returns, so task stays running until cancelled
    class BlockingProvider:
        def get_default_model(self) -> str:
            return "gpt-4o"

        async def chat(self, **kwargs):
            await _asyncio.sleep(10)
            return _FakeResponse(content="done")

    blocking = BlockingProvider()

    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=event_emitter,
        workspace=tmp_path,
        provider=blocking,
        tool_registry=fake_tool_registry,
        orchestrator="placeholder",
    )

    # Spawn starts the background task
    agent = await control.spawn("code-agent", "long task", label="long-task")

    # Agent should have a running task
    assert agent.agent_id in control._running_tasks
    task = control._running_tasks[agent.agent_id]
    assert not task.done(), "Task should be running"

    # Kill should cancel the task
    await control.kill(agent.agent_id)

    # Task should be removed from running tasks
    assert agent.agent_id not in control._running_tasks

    # Agent should be removed from _agents
    assert agent.agent_id not in control._agents

    # Allow pending cancellation to propagate
    await _asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_killed_agent_not_marked_completed(tmp_path, event_emitter):
    """A killed agent should not be marked COMPLETED after kill()."""
    import asyncio as _asyncio
    from unittest.mock import AsyncMock, MagicMock

    from miqi.runtime.agent_control import AgentControl
    from miqi.runtime.agent_registry import AgentRegistry

    # Fake provider that blocks until cancelled
    class BlockingProvider:
        def get_default_model(self) -> str:
            return "gpt-4o"

        async def chat(self, **kwargs):
            await _asyncio.sleep(10)  # long-running
            return _FakeResponse(content="done")

    blocking = BlockingProvider()

    # Tool registry that returns empty definitions
    tool_reg = MagicMock()
    tool_reg.get_definitions.return_value = []

    ev = MagicMock()
    ev.emit = AsyncMock()
    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=ev,
        workspace=tmp_path,
        provider=blocking,
        tool_registry=tool_reg,
        orchestrator="placeholder",
    )

    agent = await control.spawn("code-agent", "blocking task", label="blocking")
    task_ref = control._running_tasks.get(agent.agent_id)
    assert task_ref is not None, "Task must be started by spawn"

    # Give the task a tick to start
    await _asyncio.sleep(0.01)

    # Kill the agent
    await control.kill(agent.agent_id)

    # Wait for cancellation to propagate
    try:
        await _asyncio.wait_for(task_ref, timeout=1.0)
    except (_asyncio.CancelledError, _asyncio.TimeoutError):
        pass

    # Agent state should be ABORTED, not COMPLETED
    assert agent.state.current.value != "completed"
    # Agent error should be "Cancelled"
    assert agent.error == "Cancelled"
    # Result should not be set (killed before completion)
    assert agent.result is None


# ---------------------------------------------------------------------------
# Phase 52: spawn edge persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_records_edge_when_store_present(tmp_path, event_emitter):
    """AgentControl.spawn must persist a parent→child edge when a store is given."""
    from miqi.runtime.agent_control import AgentControl
    from miqi.runtime.agent_graph_store import AgentGraphStore
    from miqi.runtime.agent_registry import AgentRegistry

    store = AgentGraphStore(tmp_path / "agent_graph.db")
    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=event_emitter,
        workspace=tmp_path,
        store=store,
    )

    agent = await control.spawn("code-agent", "task", parent_agent_id="root")

    children = store.get_children("root")
    assert len(children) == 1
    assert children[0].child_agent_id == agent.agent_id
    assert children[0].child_thread_id == agent.thread_id


@pytest.mark.asyncio
async def test_spawn_without_store_no_edge(tmp_path, event_emitter):
    """Spawning with no store must still work and record no edges."""
    from miqi.runtime.agent_control import AgentControl
    from miqi.runtime.agent_registry import AgentRegistry

    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=event_emitter,
        workspace=tmp_path,
    )

    agent = await control.spawn("code-agent", "task", parent_agent_id="root")
    assert agent.agent_id
    assert agent.parent_agent_id == "root"


# ---------------------------------------------------------------------------
# Phase 13 integration: spawn ordering (job-first), kill delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_emits_event_with_job_ids(tmp_path, event_emitter):
    """When AgentJobRuntime is wired, SubAgentSpawnedEvent carries job IDs
    (not temporary IDs), and returned LiveAgent matches them."""
    import asyncio as _asyncio
    from unittest.mock import MagicMock

    from miqi.runtime.agent_control import AgentControl
    from miqi.runtime.agent_jobs import AgentJobRuntime
    from miqi.runtime.agent_registry import AgentRegistry

    # Simple services mock for AgentJobRuntime
    services = MagicMock()
    services.session_id = "test-session"
    services.turn_runner = MagicMock()

    async def _fake_run_agent_job(job):
        from miqi.runtime.turn_runner import TurnResult
        return TurnResult(final_content="done", messages=[], tools_used=[])

    services.turn_runner.run_agent_job = _fake_run_agent_job

    agent_jobs = AgentJobRuntime(services=services)

    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=event_emitter,
        workspace=tmp_path,
        agent_jobs=agent_jobs,
    )

    # Clear the spawn event so we can capture the fresh one
    event_emitter.emit.reset_mock()

    agent = await control.spawn("code-agent", "test task", label="test-job-ids")

    # The returned agent should use job-allocated IDs
    assert agent.agent_id
    assert agent.thread_id

    # Verify the emitted event carries the SAME IDs
    event_emitter.emit.assert_called()
    call_args = event_emitter.emit.call_args
    event = call_args[0][0] if call_args[0] else None
    assert event is not None, "No SubAgentSpawnedEvent emitted"

    from miqi.protocol.events import SubAgentSpawnedEvent
    assert isinstance(event, SubAgentSpawnedEvent)

    # event.sub_agent_id must match returned agent.agent_id (job.job_id)
    assert event.sub_agent_id == agent.agent_id, (
        f"Event sub_agent_id {event.sub_agent_id} != agent.agent_id {agent.agent_id}"
    )
    assert event.sub_thread_id == agent.thread_id, (
        f"Event sub_thread_id {event.sub_thread_id} != agent.thread_id {agent.thread_id}"
    )

    # Give the job task time to complete
    await _asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_kill_with_agent_jobs_cancels_job(tmp_path, event_emitter):
    """kill() must delegate to AgentJobRuntime.kill() when agent is job-backed,
    and the job status must be aborted."""
    import asyncio as _asyncio
    from unittest.mock import MagicMock

    from miqi.runtime.agent_control import AgentControl
    from miqi.runtime.agent_jobs import AgentJobRuntime
    from miqi.runtime.agent_registry import AgentRegistry

    services = MagicMock()
    services.session_id = "test-session"

    # Blocking turn runner so the job stays running
    async def _blocking(_job):
        await _asyncio.sleep(10)
        from miqi.runtime.turn_runner import TurnResult
        return TurnResult(final_content="done", messages=[], tools_used=[])

    services.turn_runner.run_agent_job = _blocking

    agent_jobs = AgentJobRuntime(services=services)

    control = AgentControl(
        session_id="test-session",
        registry=AgentRegistry(),
        event_emitter=event_emitter,
        workspace=tmp_path,
        agent_jobs=agent_jobs,
    )

    event_emitter.emit.reset_mock()
    agent = await control.spawn("code-agent", "blocking task", label="blocking-job")

    # Give the job a tick to start
    await _asyncio.sleep(0.02)

    # Kill through AgentControl
    await control.kill(agent.agent_id)

    # Agent should be removed from _agents
    assert agent.agent_id not in control._agents

    # Job should be aborted
    jobs = agent_jobs.list()
    found = next((j for j in jobs if j["job_id"] == agent.agent_id), None)
    assert found is not None, "Job should still exist"
    assert found["status"] == "aborted", f"Expected aborted, got {found['status']}"

    await _asyncio.sleep(0.02)
