"""Tests for BridgeRuntimeLoop persistent event loop (Phase 27.1)."""

import asyncio

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────


class _CaptureSend:
    """Capture _send() calls for verification."""
    def __init__(self):
        self.messages: list[dict] = []

    def send(self, data: dict) -> None:
        self.messages.append(data)

    def last(self) -> dict | None:
        return self.messages[-1] if self.messages else None


def _dispatch_legacy(_req_id: str, _method: str, _params: dict) -> None:
    """Simulated legacy dispatch that simply echoes."""
    # This would call _send directly in production, but in tests
    # we capture via the send_func
    pass  # Legacy handlers use module-level _send, not capturable


# ── BridgeRuntimeLoop creation ───────────────────────────────────────────


def test_bridge_runtime_loop_creates_with_send_and_dispatch():
    """BridgeRuntimeLoop accepts send_func and dispatch_legacy_func."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()

    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=_dispatch_legacy,
        dev_mode=False,
    )
    assert loop._send is not None
    assert callable(loop._send)
    assert loop._dispatch_legacy is _dispatch_legacy
    assert loop._dev_mode is False
    assert loop.app_server is None  # not started yet
    assert loop.loop is None  # not started yet


def test_bridge_runtime_loop_dev_mode():
    """dev_mode=True enables dev- prefixed client_id generation."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
        dev_mode=True,
    )
    assert loop._dev_mode is True

    # In dev mode, missing client_id generates a dev- prefix
    client_id = loop._resolve_client_id({})
    assert client_id.startswith("dev-")


@pytest.mark.asyncio
async def test_bridge_runtime_loop_resolve_client_id_with_explicit():
    """Explicit client_id is returned as-is."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
    )

    assert loop._resolve_client_id({"client_id": "my-client"}) == "my-client"
    assert loop._resolve_client_id({"caller_id": "caller-1"}) == "caller-1"
    assert loop._resolve_client_id({"user_id": "user-1"}) == "user-1"
    # client_id takes precedence over caller_id
    assert loop._resolve_client_id({"client_id": "c1", "caller_id": "c2"}) == "c1"


def test_bridge_runtime_loop_missing_client_id_raises_error():
    """Missing client_id raises AppServerError in production mode (Phase 27.5)."""
    from miqi.bridge.loop import BridgeRuntimeLoop
    from miqi.runtime.app_server import AppServerError

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
        dev_mode=False,
    )

    with pytest.raises(AppServerError, match="client_id is required"):
        loop._resolve_client_id({})


def test_bridge_runtime_loop_dev_mode_allows_missing_client_id():
    """In dev mode, missing client_id generates a dev- prefix."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
        dev_mode=True,
    )

    client_id = loop._resolve_client_id({})
    assert client_id.startswith("dev-")


# ── AppServer method registration ────────────────────────────────────────


@pytest.mark.asyncio
async def test_bridge_init_app_server_registers_handlers():
    """After _init_app_server, AppServer has status, replay, and command handlers."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()

    methods = loop.app_server._methods
    assert "status" in methods
    assert "replay.turns" in methods
    assert "replay.timeline" in methods
    assert "replay.messages" in methods
    assert "thread.create" in methods
    assert "thread.list" in methods
    assert "chat.abort" in methods

    await loop._shutdown()


# ── Event sink translation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_event_sink_translates_appserver_to_bridge_format():
    """AppServer event envelope is translated to legacy bridge wire format."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()
    BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=_dispatch_legacy,
    )

    # Simulate what _setup_event_sink does
    async def _desktop_sink(envelope):
        capturer.send({
            "id": envelope.get("request_id"),
            "type": envelope["event"],
            "data": envelope["data"],
        })

    await _desktop_sink({
        "request_id": "req-123",
        "event": "progress",
        "data": {"msg": "hello"},
    })

    assert capturer.last() == {
        "id": "req-123",
        "type": "progress",
        "data": {"msg": "hello"},
    }

    # Push events (no request_id)
    await _desktop_sink({
        "request_id": None,
        "event": "session_expiring",
        "data": {"session_id": "s1"},
    })

    assert capturer.last() == {
        "id": None,
        "type": "session_expiring",
        "data": {"session_id": "s1"},
    }


# ── Shutdown ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shutdown_stops_app_server():
    """_shutdown stops the AppServer."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()
    assert loop.app_server._running is True

    await loop._shutdown()
    assert loop.app_server._running is False


# ── Phase 27 acceptance tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_send_handler_registered_on_app_server():
    """chat.send is registered as an AppServer method."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()

    methods = loop.app_server._methods
    assert "chat.send" in methods
    assert "chat.abort" in methods
    assert "agent.spawn" in methods
    assert "agent.kill" in methods

    await loop._shutdown()


def test_missing_client_id_rejected_in_production():
    """Missing client_id raises AppServerError (no legacy shim)."""
    from miqi.bridge.loop import BridgeRuntimeLoop
    from miqi.runtime.app_server import AppServerError

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
        dev_mode=False,
    )
    with pytest.raises(AppServerError, match="client_id is required"):
        loop._resolve_client_id({})


def test_no_runtime_warning_in_production_path():
    """BridgeRuntimeLoop in production mode raises no UserWarning."""
    import warnings

    from miqi.bridge.loop import BridgeRuntimeLoop
    from miqi.runtime.app_server import AppServerError

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
        dev_mode=False,
    )

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        try:
            loop._resolve_client_id({})
        except AppServerError:
            pass  # Expected

    # No UserWarning should have been emitted
    user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
    assert len(user_warnings) == 0, (
        f"Unexpected UserWarning(s): {[str(x.message) for x in user_warnings]}"
    )


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_tasks():
    """After shutdown, pending tasks are cancelled and resources cleaned up."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()

    # Manually add a task to simulate an active chat turn
    async def _forever():
        while True:
            await asyncio.sleep(1)

    task = asyncio.create_task(_forever())
    loop._active_chat_tasks["test-task"] = task

    await loop._shutdown()

    # Task should be cancelled
    assert task.done()
    assert task.cancelled() or task.exception() is not None
    # Active chat tasks should be cleared
    assert len(loop._active_chat_tasks) == 0
    # AppServer should be stopped
    assert loop.app_server._running is False


# ── Phase 41: Codex turn handler registration ────────────────────────────

@pytest.mark.asyncio
async def test_drain_chat_events_converts_tool_begin_to_tool_hint_progress():
    from miqi.bridge.loop import BridgeRuntimeLoop
    from miqi.protocol.events import ToolCallBeginEvent, TurnCompleteEvent

    class FakeAppServer:
        def __init__(self):
            self.events = []

        async def emit_event(self, session_id, event_type, data, request_id=None):
            self.events.append({
                "session_id": session_id,
                "event_type": event_type,
                "data": data,
                "request_id": request_id,
            })

    class FakeRuntime:
        def __init__(self):
            self.events = [
                ToolCallBeginEvent(
                    turn_id="turn-asset",
                    tool_call_id="tc-asset",
                    tool_name="write_file",
                    tool_display='write_file("/tmp/asset.txt")',
                    arguments={"path": "/tmp/asset.txt"},
                ),
                TurnCompleteEvent(
                    turn_id="turn-asset",
                    thread_id="thread-asset",
                    outcome="success",
                    tools_used=["write_file"],
                    token_usage={},
                ),
            ]

        async def next_event(self, timeout=None):
            return self.events.pop(0)

    loop = BridgeRuntimeLoop(send_func=_CaptureSend().send)
    loop._app_server = FakeAppServer()

    await loop._drain_chat_events(
        request_id="req-asset",
        runtime=FakeRuntime(),
        thread_id="thread-asset",
        session_id="session-asset",
        client_id="client-asset",
        session_key="session-asset",
    )

    progress = loop._app_server.events[0]
    assert progress == {
        "session_id": "session-asset",
        "event_type": "progress",
        "data": {
            "text": 'write_file("/tmp/asset.txt")',
            "tool_hint": True,
            "tool_call_id": "tc-asset",
            "tool_args": {"path": "/tmp/asset.txt"},
            "session_key": "session-asset",
        },
        "request_id": "req-asset",
    }


@pytest.mark.asyncio
async def test_drain_chat_events_forwards_reasoning_and_final_reasoning():
    """AgentReasoningEvent streams live; AgentMessageEvent carries final reasoning (#539)."""
    from miqi.bridge.loop import BridgeRuntimeLoop
    from miqi.protocol.events import AgentMessageEvent, AgentReasoningEvent, TurnCompleteEvent

    class FakeAppServer:
        def __init__(self):
            self.events = []

        async def emit_event(self, session_id, event_type, data, request_id=None):
            self.events.append({"event_type": event_type, "data": data, "request_id": request_id})

    class FakeRuntime:
        def __init__(self):
            self.events = [
                AgentReasoningEvent(turn_id="t1", content="thinking "),
                AgentReasoningEvent(turn_id="t1", content="step"),
                AgentMessageEvent(turn_id="t1", content="answer", reasoning="thinking step"),
                TurnCompleteEvent(turn_id="t1", thread_id="h1", outcome="success", tools_used=[], token_usage={}),
            ]

        async def next_event(self, timeout=None):
            return self.events.pop(0)

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(send_func=capturer.send)
    loop._app_server = FakeAppServer()

    await loop._drain_chat_events(
        request_id="req-r",
        runtime=FakeRuntime(),
        thread_id="h1",
        session_id="sess-r",
        client_id="client-r",
        session_key="sess-r",
    )

    # Two live reasoning deltas forwarded as streaming progress.
    reasoning_progress = [e for e in loop._app_server.events if e["event_type"] == "progress"]
    assert [p["data"]["delta"] for p in reasoning_progress] == ["thinking ", "step"]
    assert all(p["data"]["stream"] == "reasoning" for p in reasoning_progress)

    # Final terminal event (sent via _send, not the app server fanout).
    final = capturer.last()
    assert final is not None
    assert final["type"] == "final"
    assert final["data"]["reasoning"] == "thinking step"
    assert final["data"]["content"] == "answer"


@pytest.mark.asyncio
async def test_drain_chat_events_sends_backend_timeout_error_directly():
    from miqi.bridge.loop import CHAT_DRAIN_IDLE_TIMEOUT_SECONDS, BridgeRuntimeLoop

    class DroppingAppServer:
        async def emit_event(self, session_id, event_type, data, request_id=None):
            return None

    class TimeoutRuntime:
        async def next_event(self, timeout=None):
            return None

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(send_func=capturer.send)
    loop._app_server = DroppingAppServer()

    await loop._drain_chat_events(
        request_id="req-timeout",
        runtime=TimeoutRuntime(),
        thread_id="thread-timeout",
        session_id="session-timeout",
        client_id="client-timeout",
        session_key="session-timeout",
    )

    assert capturer.messages[0] == {
        "id": "req-timeout",
        "type": "error",
        "data": {
            "code": "TIMEOUT",
            "message": f"Turn 超时（{CHAT_DRAIN_IDLE_TIMEOUT_SECONDS}s）",
            "session_key": "session-timeout",
        },
    }


@pytest.mark.asyncio
async def test_phase41_turn_handlers_registered_on_bridge_app_server():
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()

    methods = loop.app_server._methods
    for method in [
        "turn/start",
        "turn/interrupt",
        "turn/steer",
        "thread/compact/start",
        "thread/inject_items",
    ]:
        assert method in methods

    await loop._shutdown()


# ── Concurrent dispatch (regression test) ───────────────────────────────
# Issue: when chat.send took long during first-time sandbox install
# (~minutes), _drain_loop used to block every subsequent stdin request
# on the same serial await.  The user's first thread/start (and any other
# IPC call) would time out at 30s in the Electron layer even though the
# bridge handler itself was fast.  _drain_loop now dispatches each line
# as an independent task.  See _dispatch_one_line in miqi/bridge/loop.py.


async def test_drain_loop_dispatches_concurrently() -> None:
    """A slow chat.send must not block a fast thread/start on the same
    stdin queue.  Regression test for the 30s timeouts seen on the
    user's first conversation message."""
    import json as _json

    from miqi.bridge.loop import BridgeRuntimeLoop
    from miqi.runtime.initialize_protocol import ConnectionState

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(send_func=capturer.send, dispatch_legacy_func=None)
    await loop._init_app_server()

    # Configure the connection as already initialized (skip the handshake)
    cs = ConnectionState()
    cs.client_id = "miqi-desktop"
    cs.initialized = True
    loop._connection_state = cs
    loop._stdin_queue = asyncio.Queue()

    # Replace chat.send with a slow handler (25s) and thread/start with a
    # fast one.  We need an actual registered slot for chat.send so the
    # dispatch path matches production — but since we want full control
    # we just register a test method alongside.
    async def slow_handler(request_id, params, client_id, session_id, registry):
        await asyncio.sleep(25)
        return {"result": {"slow": True}}

    async def fast_handler(request_id, params, client_id, session_id, registry):
        return {"result": {"fast": True}}

    loop.app_server._methods["test.slow"] = slow_handler
    loop.app_server._methods["test.fast"] = fast_handler

    # Push slow first
    t0 = asyncio.get_event_loop().time()
    await loop._stdin_queue.put(_json.dumps({
        "id": "req-slow", "method": "test.slow", "params": {},
    }))
    # Push fast 0.5s later
    await asyncio.sleep(0.5)
    await loop._stdin_queue.put(_json.dumps({
        "id": "req-fast", "method": "test.fast", "params": {},
    }))

    # Start _drain_loop as a background task (do NOT await it — that
    # would re-introduce the bug we are fixing because the gather on EOF
    # would wait for the slow handler).
    drain_task = asyncio.create_task(loop._drain_loop())

    # Give the fast handler a moment to complete while the slow one
    # is still sleeping.
    deadline = t0 + 2.0
    while asyncio.get_event_loop().time() < deadline:
        by_id = {m.get("id") or m.get("request_id"): m for m in capturer.messages}
        if "req-fast" in by_id:
            break
        await asyncio.sleep(0.05)

    elapsed = asyncio.get_event_loop().time() - t0
    by_id = {m.get("id") or m.get("request_id"): m for m in capturer.messages}
    assert "req-fast" in by_id, (
        f"fast request must complete while slow is still in flight "
        f"(after {elapsed:.2f}s). Captured: {capturer.messages}"
    )
    assert "req-slow" not in by_id, (
        f"slow request should still be in flight after {elapsed:.2f}s. "
        f"Captured: {capturer.messages}"
    )
    assert elapsed < 2.0, (
        f"fast request took {elapsed:.2f}s — slow handler blocked the loop. "
        f"Captured: {capturer.messages}"
    )

    # Cleanup: cancel the drain task and the still-sleeping slow handler.
    drain_task.cancel()
    try:
        await drain_task
    except asyncio.CancelledError:
        pass
    if loop._shutdown_event is not None:
        loop._shutdown_event.set()
