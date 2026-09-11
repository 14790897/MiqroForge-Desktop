"""Tests for RuntimeServices and RuntimeSession (Phase 11)."""

import asyncio

import pytest

from miqi.protocol.commands import UserMessage
from miqi.runtime.services import RuntimeServices
from miqi.runtime.session import RuntimeSession

# ── Task 11.1: RuntimeServices ──────────────────────────────────────────

def test_runtime_services_builds_orchestrator(fake_config, fake_provider):
    """RuntimeServices.from_config creates all services with orchestrator wired."""
    services = RuntimeServices.from_config(
        config=fake_config,
        provider=fake_provider,
        session_id="test:session",
        workspace=fake_config.workspace_path,
    )

    assert services.provider is fake_provider
    assert services.tool_registry is not None
    assert services.orchestrator is not None
    assert services.orchestrator.tools is services.tool_registry
    assert services.event_emitter is not None
    assert services.model_settings is not None
    assert services.model_settings.model == fake_config.agents.defaults.model
    assert not hasattr(services, "agent_loop")
    assert services.agent_control is not None
    # AgentControl must have orchestrator wired
    assert services.agent_control._orchestrator is services.orchestrator


def test_runtime_services_wires_spawn_tool(fake_config, fake_provider):
    """SpawnTool._agent_control is wired by RuntimeServices."""
    services = RuntimeServices.from_config(
        config=fake_config,
        provider=fake_provider,
        session_id="test:session",
        workspace=fake_config.workspace_path,
    )

    spawn_tool = services.tool_registry.get("spawn")
    if spawn_tool is not None:
        assert hasattr(spawn_tool, "_agent_control")
        assert spawn_tool._agent_control is services.agent_control


# ── Task 11.2: RuntimeSession ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_runtime_session_accepts_user_message(fake_config, fake_provider):
    """RuntimeSession start → submit → next_event → stop flow."""
    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="cli:default",
        workspace=fake_config.workspace_path,
    )

    await runtime.start()
    await runtime.submit(UserMessage(content="hello", thread_id="cli:default"))

    # Phase 17: TurnStartedEvent arrives before AgentMessageEvent.
    # Drain events until we find one with 'content'.
    event: object | None = None
    while True:
        ev = await runtime.next_event(timeout=5)
        if ev is None:
            break
        if hasattr(ev, "content"):
            event = ev
            break

    await runtime.stop()

    assert event is not None, "Expected an AgentMessageEvent from runtime"
    assert hasattr(event, "content"), f"Got {type(event).__name__}"
    assert event.content == "done"


@pytest.mark.asyncio
async def test_runtime_session_emits_error_on_bad_provider(fake_config):
    """RuntimeSession emits ErrorEvent when provider fails."""

    class BadProvider:
        async def chat(self, **kwargs):
            raise RuntimeError("provider crash")

        async def stream_chat(self, **kwargs):
            from miqi.providers.base import LLMStreamEvent
            response = await self.chat(**kwargs)
            yield LLMStreamEvent(kind="completed", response=response)

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=BadProvider(),
        session_id="cli:default",
        workspace=fake_config.workspace_path,
    )

    await runtime.start()
    await runtime.submit(UserMessage(content="test", thread_id="cli:default"))

    found_error = False
    while True:
        event = await runtime.next_event(timeout=5)
        if event is None:
            break
        if event.__class__.__name__ == "ErrorEvent":
            found_error = True
            break

    await runtime.stop()
    assert found_error, "Expected an ErrorEvent when provider crashes"


@pytest.mark.asyncio
async def test_runtime_session_next_event_timeout(fake_config, fake_provider):
    """next_event(timeout=0.1) returns None when no events."""
    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="cli:default",
        workspace=fake_config.workspace_path,
    )

    await runtime.start()
    # Don't submit — no events should be available
    event = await runtime.next_event(timeout=0.01)
    await runtime.stop()

    assert event is None


@pytest.mark.asyncio
async def test_runtime_session_receives_service_events(fake_config, fake_provider):
    """Events emitted by services (not just TaskRunner) flow to next_event()."""
    from miqi.protocol.commands import UserMessage

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="cli:default",
        workspace=fake_config.workspace_path,
    )

    await runtime.start()

    # Inject an event through the services' event emitter directly,
    # simulating what the orchestrator or AgentControl would emit
    from miqi.protocol.events import ToolCallBeginEvent
    await runtime.services.event_emitter.emit(ToolCallBeginEvent(
        turn_id="test-turn",
        tool_call_id="tc-1",
        tool_name="read_file",
        tool_display="read_file /tmp/x",
        arguments={"path": "/tmp/x"},
    ))

    await runtime.submit(UserMessage(content="hello", thread_id="cli:default"))

    # The first event should be our manually-injected tool event
    first_event = await runtime.next_event(timeout=5)
    await runtime.stop()

    assert first_event is not None
    assert first_event.__class__.__name__ == "ToolCallBeginEvent"
    assert first_event.tool_name == "read_file"


# ---------------------------------------------------------------------------
# Phase 14 follow-up: abort cancellation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_runtime_session_abort_cancels_active_turn(fake_config):
    """Submitting AbortTurn after UserMessage must cancel the active turn
    and emit a cancellation event."""

    class BlockingProvider:
        async def chat(self, **kwargs):
            await asyncio.sleep(10)  # Blocks until cancelled
            return type("FakeResponse", (), {
                "content": "done", "tool_calls": [], "has_tool_calls": False,
            })()

        async def stream_chat(self, **kwargs):
            from miqi.providers.base import LLMStreamEvent
            response = await self.chat(**kwargs)
            yield LLMStreamEvent(kind="completed", response=response)

    from miqi.protocol.commands import AbortTurn

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=BlockingProvider(),
        session_id="cli:default",
        workspace=fake_config.workspace_path,
    )

    await runtime.start()

    # Submit UserMessage (will block on provider.chat)
    await runtime.submit(UserMessage(content="long task", thread_id="cli:default"))

    # Give the turn a tick to enter provider.chat
    await asyncio.sleep(0.05)

    # Submit abort
    await runtime.submit(AbortTurn(thread_id="cli:default"))

    # Collect events — should see the abort warning, not a completed response
    events_seen: list[str] = []
    try:
        while True:
            event = await asyncio.wait_for(runtime.next_event(), timeout=2)
            events_seen.append(event.__class__.__name__)
            if "ErrorEvent" in events_seen or "AgentMessage" in events_seen:
                break
    except asyncio.TimeoutError:
        pass

    await runtime.stop()

    # Must have seen the abort-related event (either ErrorEvent from abort,
    # or the runtime should have cancelled)
    assert any(
        "Error" in e or "abort" in e.lower()
        for e in events_seen
    ), f"No abort event seen. Events: {events_seen}"


@pytest.mark.asyncio
async def test_runtime_session_cancel_cleanup_error_stays_aborted(fake_config, fake_provider):
    """Cleanup errors after abort must not surface as chat.send failures."""
    import sqlite3

    from miqi.protocol.commands import AbortTurn
    from miqi.protocol.events import ErrorEvent, TurnAbortedEvent

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="cli:default",
        workspace=fake_config.workspace_path,
    )
    started = asyncio.Event()

    async def handle_spy(submission):
        if isinstance(submission, UserMessage):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError as exc:
                raise sqlite3.OperationalError("database is locked") from exc

    runtime._runner.handle = handle_spy

    await runtime.start()
    await runtime.submit(UserMessage(
        content="long task",
        thread_id="cli:default",
    ))
    await asyncio.wait_for(started.wait(), timeout=5)

    await runtime.submit(AbortTurn(thread_id="cli:default"))

    events = []
    try:
        while True:
            event = await asyncio.wait_for(runtime.next_event(), timeout=2)
            events.append(event)
            if isinstance(event, TurnAbortedEvent):
                break
    finally:
        await runtime.stop()

    assert any(isinstance(event, TurnAbortedEvent) for event in events)
    assert not any(
        isinstance(event, ErrorEvent)
        and "Cancelled turn task failed" in event.message
        for event in events
    )


# ---------------------------------------------------------------------------
# Phase 14 follow-up v2: queued submissions during active turn
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_runtime_session_queues_non_abort_during_active_turn(fake_config):
    """Non-AbortTurn submissions during an active turn MUST NOT be dropped.
    They are queued and processed after the current turn completes."""

    first_started = asyncio.Event()
    first_can_finish = asyncio.Event()
    call_count = 0

    class GatedProvider:
        """First call blocks until first_can_finish is set;
        second call returns immediately."""
        async def chat(self, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                first_started.set()
                await first_can_finish.wait()  # Block until test unblocks
            return type("FakeResponse", (), {
                "content": f"response-{call_count}",
                "tool_calls": [],
                "has_tool_calls": False,
                "finish_reason": "stop",
            })()

        async def stream_chat(self, **kwargs):
            """Phase 20: streaming fallback wrapping chat()."""
            from miqi.providers.base import LLMStreamEvent
            response = await self.chat(**kwargs)
            yield LLMStreamEvent(kind="completed", response=response)

    from miqi.protocol.commands import UserMessage

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=GatedProvider(),
        session_id="cli:default",
        workspace=fake_config.workspace_path,
    )

    await runtime.start()

    # Submit first message (will block provider)
    await runtime.submit(UserMessage(content="first", thread_id="cli:default"))
    # Wait for the first turn to enter provider.chat
    await asyncio.wait_for(first_started.wait(), timeout=5)

    # Submit second message while first is still blocked
    await runtime.submit(UserMessage(content="second", thread_id="cli:default"))
    # Give the dispatch loop a tick to process the second submission into _pending
    await asyncio.sleep(0.05)

    # Now unblock the first turn
    first_can_finish.set()

    # Collect events — should see two AgentMessageEvents
    responses: list[str] = []
    try:
        while len(responses) < 2:
            event = await asyncio.wait_for(runtime.next_event(), timeout=5)
            if hasattr(event, "content"):
                responses.append(event.content)
    except asyncio.TimeoutError:
        pass

    await runtime.stop()

    assert len(responses) == 2, (
        f"Expected 2 responses, got {len(responses)}: {responses}"
    )
    assert "response-1" in responses
    assert "response-2" in responses


# ---------------------------------------------------------------------------
# Phase 17: session / thread / history runtime wiring
# ---------------------------------------------------------------------------

def test_runtime_services_include_history_and_thread_runtime(
    fake_config, fake_provider, tmp_path,
):
    """RuntimeServices must carry HistoryRuntime, ThreadRuntime, SessionState."""
    from miqi.runtime.services import RuntimeServices

    services = RuntimeServices.from_config(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-history",
        workspace=tmp_path,
    )

    assert services.history_runtime is not None
    assert services.thread_runtime is not None
    assert services.session_state is not None
    assert services.session_state.session_id == "sess-history"
    assert services.session_state.workspace == tmp_path


@pytest.mark.asyncio
async def test_runtime_session_start_initializes_history_stores(
    fake_config, fake_provider, tmp_path,
):
    """RuntimeSession.start() must init history/thread stores and create default thread."""
    from miqi.runtime.session import RuntimeSession

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-init",
        workspace=tmp_path,
    )

    await runtime.start()
    try:
        db_path = tmp_path / ".miqi-runtime" / "runtime.db"
        assert db_path.exists(), f"runtime DB should exist at {db_path}"

        # Default thread must exist
        thread = await runtime.services.thread_runtime.get_thread(
            runtime.services.session_state.active_thread_id,
        )
        assert thread is not None
        assert thread.title == "Default"
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_session_handles_user_shell_command_during_active_turn(fake_config):
    """RunUserShellCommand arriving during an active turn must not be queued behind it."""
    import asyncio as _asyncio

    from miqi.protocol.commands import RunUserShellCommand, UserMessage
    from miqi.providers.base import LLMResponse, LLMStreamEvent
    from miqi.runtime.session import RuntimeSession

    shell_handled = _asyncio.Event()

    class Provider:
        def get_default_model(self):
            return "test-model"

        async def stream_chat(self, **kwargs):
            await _asyncio.sleep(0.3)
            yield LLMStreamEvent(kind="completed", response=LLMResponse(
                content="done",
                finish_reason="stop",
            ))

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=Provider(),
        session_id="client-1:default",
        workspace=fake_config.workspace_path,
    )
    await runtime.start()

    original_handle = runtime._runner.handle

    async def handle_spy(submission):
        if isinstance(submission, RunUserShellCommand):
            shell_handled.set()
        await original_handle(submission)

    runtime._runner.handle = handle_spy

    await runtime.submit(UserMessage(
        content="wait",
        thread_id="default",
        turn_id="turn-active",
    ))

    for _ in range(100):
        if runtime.active_turn_id("default") == "turn-active":
            break
        await _asyncio.sleep(0.01)

    await runtime.submit(RunUserShellCommand(
        command="echo inline",
        thread_id="default",
        turn_id="turn-active",
        standalone=False,
    ))

    await _asyncio.wait_for(shell_handled.wait(), timeout=5)
    await runtime.stop()


@pytest.mark.asyncio
async def test_shell_command_during_active_turn_does_not_block_abort(fake_config):
    """Phase 42 fix: shell command during active turn must not block the dispatch
    loop, so AbortTurn can still be dequeued and processed.

    Uses a blocking fake ToolRuntime to simulate a long-running exec.  Verifies:
      - the shell command is spawned as a background aux task
      - AbortTurn is dequeued without hanging
      - the aux task is cancelled and properly cleaned up
      - no aux tasks leak after stop
    """
    import asyncio as _asyncio

    from miqi.protocol.commands import AbortTurn, RunUserShellCommand, UserMessage
    from miqi.providers.base import LLMResponse, LLMStreamEvent
    from miqi.runtime.session import RuntimeSession

    exec_blocked = _asyncio.Event()
    exec_cancelled = _asyncio.Event()

    class BlockingExec:
        """Fake ToolRuntime whose execute_one blocks until cancelled."""

        def __init__(self):
            self.call_count = 0
            self.cancel_event_value = None

        async def execute_one(self, turn, tool_call):
            self.call_count += 1
            self.cancel_event_value = getattr(turn, "cancel_event", None)
            exec_blocked.set()
            # Block until the task is cancelled by the dispatch loop
            try:
                await _asyncio.sleep(30)
            except _asyncio.CancelledError:
                exec_cancelled.set()
                raise

    provider_entered = _asyncio.Event()

    class Provider:
        def get_default_model(self):
            return "test-model"

        async def stream_chat(self, **kwargs):
            provider_entered.set()
            await _asyncio.sleep(30)
            yield LLMStreamEvent(kind="completed", response=LLMResponse(
                content="done", finish_reason="stop",
            ))

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=Provider(),
        session_id="cli:default",
        workspace=fake_config.workspace_path,
    )

    await runtime.start()
    blocking_exec = BlockingExec()
    runtime.services.tool_runtime = blocking_exec

    # Start a blocking turn
    await runtime.submit(UserMessage(
        content="long task",
        thread_id="cli:default",
        turn_id="turn-block",
    ))
    await _asyncio.wait_for(provider_entered.wait(), timeout=5)

    # Submit a shell command during the active turn — execute_one will block
    await runtime.submit(RunUserShellCommand(
        command="sleep 999",
        thread_id="cli:default",
        turn_id="turn-block",
        standalone=False,
    ))

    # Wait until the shell command has entered execute_one
    await _asyncio.wait_for(exec_blocked.wait(), timeout=5)
    assert blocking_exec.call_count == 1

    # Verify the shell command was spawned as a background aux task
    assert len(runtime._pending_aux_tasks) == 1, (
        f"Expected 1 aux task, got {len(runtime._pending_aux_tasks)}"
    )

    # Submit abort — must be dequeued and processed, NOT hang
    await runtime.submit(AbortTurn(thread_id="cli:default"))

    # Wait for the exec to be cancelled (cleanup has run)
    await _asyncio.wait_for(exec_cancelled.wait(), timeout=5)

    await runtime.stop()

    # After stop(), no aux tasks should remain
    assert len(runtime._pending_aux_tasks) == 0, (
        f"Expected 0 pending aux tasks after stop, got {len(runtime._pending_aux_tasks)}"
    )

