"""End-to-end tests for turn/start event projection through RuntimeSession."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from miqi.runtime.app_server import AppServer, ClientSessionRegistry
from miqi.runtime.turn_app_handlers import register_codex_turn_handlers


@pytest.mark.asyncio
async def test_turn_start_streams_codex_item_events(tmp_path, fake_config):
    from miqi.providers.base import LLMResponse, LLMStreamEvent
    from miqi.runtime.session import RuntimeSession

    class Provider:
        def get_default_model(self):
            return "test-model"

        async def stream_chat(self, **kwargs):
            yield LLMStreamEvent(kind="content_delta", delta="hel")
            yield LLMStreamEvent(kind="content_delta", delta="lo")
            yield LLMStreamEvent(kind="completed", response=LLMResponse(
                content="hello",
                finish_reason="stop",
                usage={"total_tokens": 3},
            ))

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=Provider(),
        session_id="client-1:default",
        workspace=fake_config.workspace_path,
    )
    await runtime.start()

    # Create the target thread so turn/start passes thread existence validation
    thread_runtime = runtime.services.thread_runtime
    await thread_runtime.create_thread(thread_id="thread-e2e", title="E2E Thread")

    registry = ClientSessionRegistry()
    registry._sessions[runtime.session_id] = runtime
    registry._client_sessions.setdefault("client-1", set()).add(runtime.session_id)
    registry._session_clients.setdefault(runtime.session_id, set()).add("client-1")
    registry._last_activity[runtime.session_id] = 0

    server = AppServer(registry)
    register_codex_turn_handlers(server)

    captured: list[dict] = []

    async def sink(envelope):
        captured.append(envelope)

    server.set_event_sink("client-1", sink)
    server.subscribe("client-1", runtime.session_id)

    response = await server.dispatch(
        "req-1",
        "turn/start",
        {
            "threadId": "thread-e2e",
            "clientUserMessageId": "client-msg-1",
            "input": [{"type": "text", "text": "hello"}],
        },
        "client-1",
        runtime.session_id,
    )

    assert response["result"]["turn"]["status"] == "inProgress"

    for _ in range(100):
        if any(e["event"] == "turn/completed" for e in captured):
            break
        await asyncio.sleep(0.01)

    events = [e["event"] for e in captured]
    assert "turn/started" in events
    assert "item/started" in events
    assert "item/agentMessage/delta" in events
    assert "item/completed" in events
    assert "thread/tokenUsage/updated" in events
    assert "turn/completed" in events

    completed = [e for e in captured if e["event"] == "turn/completed"][-1]
    assert completed["data"]["turn"]["status"] == "completed"

    await server.stop()


@pytest.mark.asyncio
async def test_turn_interrupt_streams_interrupted_completion(tmp_path, fake_config):
    import asyncio as _asyncio

    from miqi.providers.base import LLMResponse, LLMStreamEvent
    from miqi.runtime.session import RuntimeSession

    class Provider:
        def get_default_model(self):
            return "test-model"

        async def stream_chat(self, **kwargs):
            await _asyncio.sleep(30)
            yield LLMStreamEvent(kind="completed", response=LLMResponse(
                content="late",
                finish_reason="stop",
            ))

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=Provider(),
        session_id="client-1:default",
        workspace=fake_config.workspace_path,
    )
    await runtime.start()

    # Create the target thread so turn/start passes thread existence validation
    thread_runtime = runtime.services.thread_runtime
    await thread_runtime.create_thread(thread_id="thread-interrupt", title="Interrupt Thread")

    registry = ClientSessionRegistry()
    registry._sessions[runtime.session_id] = runtime
    registry._client_sessions.setdefault("client-1", set()).add(runtime.session_id)
    registry._session_clients.setdefault(runtime.session_id, set()).add("client-1")
    registry._last_activity[runtime.session_id] = 0

    server = AppServer(registry)
    register_codex_turn_handlers(server)
    captured: list[dict] = []

    async def sink(envelope):
        captured.append(envelope)

    server.set_event_sink("client-1", sink)
    server.subscribe("client-1", runtime.session_id)

    response = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-interrupt", "input": [{"type": "text", "text": "wait"}]},
        "client-1",
        runtime.session_id,
    )
    turn_id = response["result"]["turn"]["id"]

    for _ in range(100):
        if runtime.active_turn_id("thread-interrupt") == turn_id:
            break
        await _asyncio.sleep(0.01)

    interrupt = await server.dispatch(
        "req-2",
        "turn/interrupt",
        {"threadId": "thread-interrupt", "turnId": turn_id},
        "client-1",
        runtime.session_id,
    )
    assert interrupt["result"] == {}

    for _ in range(100):
        if any(e["event"] == "turn/completed" for e in captured):
            break
        await _asyncio.sleep(0.01)

    completed = [e for e in captured if e["event"] == "turn/completed"][-1]
    assert completed["data"]["turn"]["status"] == "interrupted"

    await server.stop()


# ── Phase 41 hardening: recoverable ErrorEvent in drain ───────────────────


@pytest.mark.asyncio
async def test_recoverable_error_does_not_terminate_drain(tmp_path, fake_config):
    """Fix 3: recoverable ErrorEvent should not stop the drain loop."""
    import asyncio as _asyncio

    from miqi.protocol.events import ErrorEvent, EventSeverity
    from miqi.providers.base import LLMResponse, LLMStreamEvent
    from miqi.runtime.session import RuntimeSession

    class Provider:
        def get_default_model(self):
            return "test-model"

        async def stream_chat(self, **kwargs):
            yield LLMStreamEvent(kind="content_delta", delta="before-error")
            await _asyncio.sleep(0.3)
            yield LLMStreamEvent(kind="content_delta", delta="after-error")
            yield LLMStreamEvent(kind="completed", response=LLMResponse(
                content="before-error after-error",
                finish_reason="stop",
                usage={"total_tokens": 5},
            ))

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=Provider(),
        session_id="client-1:default",
        workspace=fake_config.workspace_path,
    )
    await runtime.start()

    # Create the target thread so turn/start passes thread existence validation
    thread_runtime = runtime.services.thread_runtime
    await thread_runtime.create_thread(thread_id="thread-recoverable", title="Recoverable Thread")

    registry = ClientSessionRegistry()
    registry._sessions[runtime.session_id] = runtime
    registry._client_sessions.setdefault("client-1", set()).add(runtime.session_id)
    registry._session_clients.setdefault(runtime.session_id, set()).add("client-1")
    registry._last_activity[runtime.session_id] = 0

    server = AppServer(registry)
    register_codex_turn_handlers(server)
    captured: list[dict] = []

    async def sink(envelope):
        captured.append(envelope)

    server.set_event_sink("client-1", sink)
    server.subscribe("client-1", runtime.session_id)

    response = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-recoverable",
         "input": [{"type": "text", "text": "hello"}]},
        "client-1",
        runtime.session_id,
    )
    turn_id = response["result"]["turn"]["id"]

    # Inject a recoverable ErrorEvent into the stream
    await _asyncio.sleep(0.05)
    await runtime._events.put(ErrorEvent(
        turn_id=turn_id,
        severity=EventSeverity.WARNING,
        message="Recoverable hiccup",
        recoverable=True,
    ))

    # Wait for drain to finish
    for _ in range(100):
        if any(e["event"] == "turn/completed" for e in captured):
            break
        await _asyncio.sleep(0.01)

    events = [e["event"] for e in captured]
    assert "error" in events or "error/warning" in events
    assert "turn/completed" in events

    # There should be exactly one turn/completed and it should be "completed"
    completed_events = [e for e in captured if e["event"] == "turn/completed"]
    assert len(completed_events) == 1
    assert completed_events[0]["data"]["turn"]["status"] == "completed"

    await server.stop()


# ── Phase 41 hardening: approval cancellation on interrupt ────────────────


@pytest.mark.asyncio
async def test_turn_interrupt_calls_cancel_approvals_for_thread(tmp_path, fake_config):
    """Fix 4: turn/interrupt active turn must call cancel_approvals_for_thread."""
    import asyncio as _asyncio
    from unittest.mock import AsyncMock

    from miqi.providers.base import LLMResponse, LLMStreamEvent
    from miqi.runtime.session import RuntimeSession

    class Provider:
        def get_default_model(self):
            return "test-model"

        async def stream_chat(self, **kwargs):
            await _asyncio.sleep(30)
            yield LLMStreamEvent(kind="completed", response=LLMResponse(
                content="late", finish_reason="stop",
            ))

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=Provider(),
        session_id="client-1:default",
        workspace=fake_config.workspace_path,
    )
    await runtime.start()

    # Create the target thread so turn/start passes thread existence validation
    thread_runtime = runtime.services.thread_runtime
    await thread_runtime.create_thread(thread_id="thread-approval", title="Approval Thread")

    # Replace orchestrator with mock to observe cancel_approvals_for_thread
    mock_cancel = AsyncMock()
    mock_orchestrator = MagicMock()
    mock_orchestrator.cancel_approvals_for_thread = mock_cancel
    runtime.services.orchestrator = mock_orchestrator

    registry = ClientSessionRegistry()
    registry._sessions[runtime.session_id] = runtime
    registry._client_sessions.setdefault("client-1", set()).add(runtime.session_id)
    registry._session_clients.setdefault(runtime.session_id, set()).add("client-1")
    registry._last_activity[runtime.session_id] = 0

    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-approval",
         "input": [{"type": "text", "text": "approval test"}]},
        "client-1",
        runtime.session_id,
    )
    turn_id = response["result"]["turn"]["id"]

    # Wait for active turn to be registered
    for _ in range(100):
        if runtime.active_turn_id("thread-approval") == turn_id:
            break
        await _asyncio.sleep(0.01)

    assert runtime.active_turn_id("thread-approval") == turn_id

    interrupt = await server.dispatch(
        "req-2",
        "turn/interrupt",
        {"threadId": "thread-approval", "turnId": turn_id},
        "client-1",
        runtime.session_id,
    )
    assert interrupt["result"] == {}

    # Wait for the turn task to be cleaned up
    for _ in range(50):
        if runtime.active_turn_id("thread-approval") is None:
            break
        await _asyncio.sleep(0.01)

    # cancel_approvals_for_thread must have been called
    mock_cancel.assert_awaited_once_with(
        "thread-approval", reason="Turn aborted by user."
    )

    await server.stop()


# ── Phase 41 hardening v2: per-thread reservation race-condition fix ─────


@pytest.mark.asyncio
async def test_concurrent_turn_start_same_thread_only_one_succeeds(tmp_path, fake_config):
    """Two concurrent turn/start for the same thread — exactly one wins."""
    import asyncio as _asyncio

    from miqi.providers.base import LLMResponse, LLMStreamEvent
    from miqi.runtime.session import RuntimeSession

    class Provider:
        def get_default_model(self):
            return "test-model"

        async def stream_chat(self, **kwargs):
            await _asyncio.sleep(30)
            yield LLMStreamEvent(kind="completed", response=LLMResponse(
                content="slow", finish_reason="stop",
            ))

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=Provider(),
        session_id="client-1:default",
        workspace=fake_config.workspace_path,
    )
    await runtime.start()

    thread_runtime = runtime.services.thread_runtime
    await thread_runtime.create_thread(thread_id="thread-concurrent", title="Concurrent Thread")

    registry = ClientSessionRegistry()
    registry._sessions[runtime.session_id] = runtime
    registry._client_sessions.setdefault("client-1", set()).add(runtime.session_id)
    registry._session_clients.setdefault(runtime.session_id, set()).add("client-1")
    registry._last_activity[runtime.session_id] = 0

    server = AppServer(registry)
    register_codex_turn_handlers(server)

    async def do_turn_start(req_id: str) -> dict:
        return await server.dispatch(
            req_id,
            "turn/start",
            {"threadId": "thread-concurrent",
             "input": [{"type": "text", "text": "concurrent"}]},
            "client-1",
            runtime.session_id,
        )

    r1, r2 = await _asyncio.gather(do_turn_start("req-1"), do_turn_start("req-2"))

    # Exactly one success
    success = [r for r in (r1, r2) if "result" in r]
    rejected = [r for r in (r1, r2) if r.get("code") == "INVALID_REQUEST"]
    assert len(success) == 1, f"Expected 1 success, got {len(success)}: {r1}, {r2}"
    assert len(rejected) == 1, f"Expected 1 INVALID_REQUEST, got {len(rejected)}: {r1}, {r2}"

    # Exactly one drain background task
    assert len(server._background_tasks) == 1, (
        f"Expected 1 drain task, got {len(server._background_tasks)}"
    )

    # Wait for the drain to finish (clean up)
    for task in list(server._background_tasks):
        task.cancel()
    for task in list(server._background_tasks):
        try:
            await task
        except (_asyncio.CancelledError, Exception):
            pass

    await server.stop()


@pytest.mark.asyncio
async def test_sequential_immediate_second_turn_start_rejected(tmp_path, fake_config):
    """Second sequential turn/start (without waiting for active turn) is rejected."""
    import asyncio as _asyncio

    from miqi.providers.base import LLMResponse, LLMStreamEvent
    from miqi.runtime.session import RuntimeSession

    class Provider:
        def get_default_model(self):
            return "test-model"

        async def stream_chat(self, **kwargs):
            await _asyncio.sleep(30)
            yield LLMStreamEvent(kind="completed", response=LLMResponse(
                content="slow", finish_reason="stop",
            ))

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=Provider(),
        session_id="client-1:default",
        workspace=fake_config.workspace_path,
    )
    await runtime.start()

    thread_runtime = runtime.services.thread_runtime
    await thread_runtime.create_thread(thread_id="thread-seq", title="Sequential Thread")

    registry = ClientSessionRegistry()
    registry._sessions[runtime.session_id] = runtime
    registry._client_sessions.setdefault("client-1", set()).add(runtime.session_id)
    registry._session_clients.setdefault(runtime.session_id, set()).add("client-1")
    registry._last_activity[runtime.session_id] = 0

    server = AppServer(registry)
    register_codex_turn_handlers(server)

    # First turn/start — succeeds and reserves the thread
    r1 = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-seq",
         "input": [{"type": "text", "text": "first"}]},
        "client-1",
        runtime.session_id,
    )
    assert "result" in r1
    assert r1["result"]["turn"]["status"] == "inProgress"

    # Verify the reservation is held (active_turn_id returns reservation before
    # TaskRunner picks up the UserMessage)
    reserved_id = runtime.active_turn_id("thread-seq")
    assert reserved_id is not None, "Reservation should be held immediately"

    # Second turn/start immediately — must be rejected by the reservation
    r2 = await server.dispatch(
        "req-2",
        "turn/start",
        {"threadId": "thread-seq",
         "input": [{"type": "text", "text": "second"}]},
        "client-1",
        runtime.session_id,
    )
    assert r2.get("code") == "INVALID_REQUEST", f"Expected INVALID_REQUEST, got {r2}"

    # Clean up
    for task in list(server._background_tasks):
        task.cancel()
    for task in list(server._background_tasks):
        try:
            await task
        except (_asyncio.CancelledError, Exception):
            pass

    await server.stop()


@pytest.mark.asyncio
async def test_turn_start_succeeds_after_turn_completes(tmp_path, fake_config):
    """After a turn completes (drain releases reservation), a new turn succeeds."""
    import asyncio as _asyncio

    from miqi.providers.base import LLMResponse, LLMStreamEvent
    from miqi.runtime.session import RuntimeSession

    class Provider:
        def get_default_model(self):
            return "test-model"

        async def stream_chat(self, **kwargs):
            yield LLMStreamEvent(kind="content_delta", delta="quick")
            yield LLMStreamEvent(kind="completed", response=LLMResponse(
                content="quick", finish_reason="stop", usage={"total_tokens": 3},
            ))

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=Provider(),
        session_id="client-1:default",
        workspace=fake_config.workspace_path,
    )
    await runtime.start()

    thread_runtime = runtime.services.thread_runtime
    await thread_runtime.create_thread(thread_id="thread-release", title="Release Thread")

    registry = ClientSessionRegistry()
    registry._sessions[runtime.session_id] = runtime
    registry._client_sessions.setdefault("client-1", set()).add(runtime.session_id)
    registry._session_clients.setdefault(runtime.session_id, set()).add("client-1")
    registry._last_activity[runtime.session_id] = 0

    server = AppServer(registry)
    register_codex_turn_handlers(server)
    captured: list[dict] = []

    async def sink(envelope):
        captured.append(envelope)

    server.set_event_sink("client-1", sink)
    server.subscribe("client-1", runtime.session_id)

    # First turn/start
    r1 = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-release",
         "input": [{"type": "text", "text": "first"}]},
        "client-1",
        runtime.session_id,
    )
    assert "result" in r1

    # Wait for the turn to complete (drain releases reservation)
    for _ in range(100):
        if any(e["event"] == "turn/completed" for e in captured):
            break
        await _asyncio.sleep(0.01)
    assert any(e["event"] == "turn/completed" for e in captured)

    # Reservation should be released
    assert runtime.active_turn_id("thread-release") is None
    assert "thread-release" not in runtime._turn_reservations

    # Second turn/start — must succeed now
    r2 = await server.dispatch(
        "req-2",
        "turn/start",
        {"threadId": "thread-release",
         "input": [{"type": "text", "text": "second"}]},
        "client-1",
        runtime.session_id,
    )
    assert "result" in r2, f"Expected success, got {r2}"
    assert r2["result"]["turn"]["status"] == "inProgress"

    await server.stop()
