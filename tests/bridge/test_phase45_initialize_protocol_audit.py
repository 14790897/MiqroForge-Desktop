"""Phase 45 Initialize Protocol Audit Tests.

Tests for Codex-style initialize/initialized handshake through the bridge
transport layer. These tests verify the bridge-level initialization state
machine: NOT_INITIALIZED gate, ALREADY_INITIALIZED rejection, client_id
derivation, experimental API gate, and notification opt-out.

Tests that use BridgeRuntimeLoop push requests through the stdin queue
and capture responses. Tests that only need capability/initialize logic
use direct AppServer dispatch.
"""

import asyncio
from pathlib import Path

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────────


class _CaptureSend:
    """Capture send() calls for verification."""
    def __init__(self):
        self.messages: list[dict] = []

    def send(self, data: dict) -> None:
        self.messages.append(data)

    def last(self) -> dict | None:
        return self.messages[-1] if self.messages else None

    def clear(self) -> None:
        self.messages.clear()


def _make_server_with_caps():
    """Create an AppServer with capabilities support (for direct tests).

    These tests bypass the bridge initialize gate and use direct
    AppServer.dispatch() — necessary for unit-testing capability
    and initialize handler behavior without full bridge setup.
    """
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    server = AppServer(registry)
    return server, registry


async def _dispatch(server, registry, method, params, client_id="test-client", session_id=None, req_id="req-1"):
    return await server.dispatch(
        request_id=req_id,
        method=method,
        params=params,
        client_id=client_id,
        session_id=session_id,
    )


# ── 45.1.1: initialize method is registered or handled specially ────────────


@pytest.mark.asyncio
async def test_initialize_method_is_registered_on_server():
    """initialize method is registered and returns a valid response."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test-client", "title": "Test", "version": "1.0"},
    })

    assert "result" in resp, f"Expected result in initialize response, got: {resp}"
    result = resp["result"]
    assert "serverInfo" in result
    assert "userAgent" in result
    assert "capabilities" in result


# ── 45.1.2: initialize response contains required fields ────────────────────


@pytest.mark.asyncio
async def test_initialize_response_contains_server_info():
    """initialize response has serverInfo with name, title, version."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "miqi_desktop", "title": "MiQroForge Desktop", "version": "0.1.0"},
    })

    result = resp["result"]
    server_info = result["serverInfo"]
    assert server_info["name"] == "miqi"
    assert server_info["title"] == "MiQroForge"
    assert isinstance(server_info["version"], str)


@pytest.mark.asyncio
async def test_initialize_response_contains_user_agent():
    """initialize response includes userAgent string."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test", "title": "Test"},
    })

    assert "userAgent" in resp["result"]
    assert "miqi" in resp["result"]["userAgent"].lower()


@pytest.mark.asyncio
async def test_initialize_response_contains_home_paths():
    """initialize response includes miqiHome and codexHome paths."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test", "title": "Test"},
    })

    result = resp["result"]
    assert "miqiHome" in result
    assert "codexHome" in result
    assert isinstance(result["miqiHome"], str)
    assert isinstance(result["codexHome"], str)


@pytest.mark.asyncio
async def test_initialize_response_contains_platform():
    """initialize response includes platformFamily and platformOs."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test", "title": "Test"},
    })

    result = resp["result"]
    assert "platformFamily" in result
    assert "platformOs" in result


@pytest.mark.asyncio
async def test_initialize_response_contains_server_capabilities():
    """initialize response includes server capabilities flags."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test", "title": "Test"},
    })

    caps = resp["result"]["capabilities"]
    assert isinstance(caps["experimentalApi"], bool)
    assert isinstance(caps["supportsNotificationOptOut"], bool)
    assert isinstance(caps["supportsWorkbenchProcesses"], bool)
    assert "supportsPty" in caps


# ── 45.1.3: invalid clientInfo returns INVALID_PARAMS ───────────────────────


@pytest.mark.asyncio
async def test_initialize_missing_client_info_returns_invalid_params():
    """Missing clientInfo returns INVALID_PARAMS."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {})

    assert "error" in resp, f"Expected error for missing clientInfo, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_initialize_missing_client_name_returns_invalid_params():
    """Missing clientInfo.name returns INVALID_PARAMS."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"title": "No Name"},
    })

    assert "error" in resp, f"Expected error for missing name, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_initialize_empty_client_name_returns_invalid_params():
    """Empty clientInfo.name returns INVALID_PARAMS."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "", "title": "Empty"},
    })

    assert "error" in resp, f"Expected error for empty name, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS"


# ── 45.1.4: capabilities may be omitted (defaults) ──────────────────────────


@pytest.mark.asyncio
async def test_initialize_without_capabilities_uses_defaults():
    """Capabilities field can be omitted; defaults assume false/empty."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
    })

    assert "result" in resp, f"Expected result without capabilities, got: {resp}"


# ── 45.1.5: unknown capability fields are ignored ───────────────────────────


@pytest.mark.asyncio
async def test_initialize_unknown_capability_fields_ignored():
    """Unknown capability fields are silently ignored."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "capabilities": {
            "futureFeature": True,
            "unknownV2Field": {"nested": "value"},
        },
    })

    assert "result" in resp, f"Expected result with unknown caps, got: {resp}"


# ── 45.1.6: optOutNotificationMethods must be list of strings if present ────


@pytest.mark.asyncio
async def test_initialize_opt_out_must_be_list_of_strings():
    """optOutNotificationMethods must be a list of strings if present."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    # Valid: list of strings
    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "capabilities": {
            "optOutNotificationMethods": ["process/outputDelta"],
        },
    })
    assert "result" in resp, f"Expected result with valid opt-out list, got: {resp}"

    # Invalid: not a list
    resp2 = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "capabilities": {
            "optOutNotificationMethods": "process/outputDelta",
        },
    })
    assert "error" in resp2, f"Expected error for non-list opt-out, got: {resp2}"
    assert resp2.get("code") == "INVALID_PARAMS"


# ── 45.1.7: initialize stores capabilities on AppServer ─────────────────────


@pytest.mark.asyncio
async def test_initialize_stores_client_capabilities():
    """Successful initialize stores ClientCapabilities on AppServer."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "miqi_desktop", "title": "Desktop", "version": "0.1.0"},
        "capabilities": {
            "experimentalApi": True,
            "optOutNotificationMethods": ["process/outputDelta"],
        },
    })

    assert "result" in resp

    # The initialize handler should derive client_id from clientInfo
    # and store capabilities on AppServer
    client_id = resp["result"].get("clientId")
    assert client_id is not None

    caps = server.get_client_capabilities(client_id)
    assert caps is not None
    assert caps.experimental_api is True
    assert caps.opt_out_notification_methods == {"process/outputDelta"}


# ── 45.1.8: experimentalApi=true allows process/spawn without params flag ────


@pytest.mark.asyncio
async def test_experimental_api_from_initialize_grants_process_spawn():
    """process/spawn works after initialize with capabilities.experimentalApi=true."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    # Initialize with experimentalApi=true
    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "miqi_desktop"},
        "capabilities": {"experimentalApi": True},
    })
    assert "result" in resp
    client_id = resp["result"]["clientId"]

    # Set up WorkbenchProcessRuntime
    from unittest.mock import MagicMock

    from miqi.runtime.workbench_process_runtime import WorkbenchProcessRuntime
    registry.bridge_context["state"] = MagicMock()
    registry.bridge_context["workbench_process_runtime"] = WorkbenchProcessRuntime(workspace=Path.cwd())
    # Phase 45: expose AppServer so handlers can check client capabilities
    registry.bridge_context["app_server"] = server

    from miqi.runtime.workbench_process_app_handlers import register_workbench_process_handlers
    register_workbench_process_handlers(server)

    # process/spawn should succeed without params.experimentalApi
    # (using the capability from initialize)
    resp2 = await _dispatch(server, registry, "process/spawn", {
        "command": ["echo", "hello"],
        "cwd": str(Path.cwd()),
    }, client_id=client_id)

    # Should succeed (result, not error about EXPERIMENTAL_API_REQUIRED)
    if "error" in resp2:
        assert resp2.get("code") != "EXPERIMENTAL_API_REQUIRED", (
            f"Should not require experimentalApi in params when capability is set: {resp2}"
        )
    else:
        assert "result" in resp2


# ── 45.1.9: opt-out suppresses exact notification names ─────────────────────


@pytest.mark.asyncio
async def test_opt_out_notification_methods_suppress_exact_match():
    """optOutNotificationMethods suppresses exact notification names only."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    delivered: list[dict] = []

    async def _sink(envelope):
        delivered.append(envelope)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "miqi_desktop"},
        "capabilities": {
            "optOutNotificationMethods": ["process/outputDelta"],
        },
    })
    client_id = resp["result"]["clientId"]

    server.set_event_sink(client_id, _sink)

    # Opted-out event should be suppressed
    await server.emit_client_event(client_id, "process/outputDelta", {"pid": 1})
    assert len(delivered) == 0

    # Non-opted-out event should still be delivered
    await server.emit_client_event(client_id, "process/exited", {"pid": 1, "code": 0})
    assert len(delivered) == 1
    assert delivered[0]["event"] == "process/exited"


# ── 45.1.10: Non-initialize requests return NOT_INITIALIZED ─────────────────

# These tests require bridge-level integration. They verify the bridge
# transport enforces the initialize handshake. For direct AppServer.dispatch()
# tests the gate is not enforced (by design, to keep unit tests working).


async def _run_drain_serial(loop, requests: list[dict], capturer: _CaptureSend) -> None:
    """Drive _drain_loop with state-dependent requests serialized.

    _drain_loop dispatches each stdin line in a fire-and-forget task, so
    enqueueing a dependent batch lets later requests race past earlier
    state transitions (a post-initialize request can hit the
    NOT_INITIALIZED gate before initialize lands).  Start the drain task
    once, then enqueue each request only after the previous one's
    response (or, for the initialized notification, its state transition)
    has been observed.  Bounded waits: a production deadlock fails the
    test instead of hanging the suite.
    """
    import json as _json
    import time as _time

    async def _wait_for(expected_responses: int, what: str) -> None:
        deadline = _time.monotonic() + 30
        while len(capturer.messages) < expected_responses:
            if _time.monotonic() > deadline:
                raise AssertionError(
                    f"Timed out waiting for {what}; "
                    f"got {len(capturer.messages)} responses: {capturer.messages}"
                )
            await asyncio.sleep(0.01)

    async def _wait_for_ack() -> None:
        deadline = _time.monotonic() + 30
        while not (
            loop._connection_state and loop._connection_state.initialized_ack
        ):
            if _time.monotonic() > deadline:
                raise AssertionError("Timed out waiting for initialized ack")
            await asyncio.sleep(0.01)

    loop._stdin_queue = asyncio.Queue()
    drain_task = asyncio.create_task(loop._drain_loop())
    try:
        for req in requests:
            before = len(capturer.messages)
            await loop._stdin_queue.put(_json.dumps(req))
            if req.get("method") == "initialized":
                await _wait_for_ack()
            else:
                await _wait_for(before + 1, f"response to {req.get('method')}")
    finally:
        await loop._stdin_queue.put(None)
        await asyncio.wait_for(drain_task, timeout=30)


@pytest.mark.asyncio
async def test_drain_loop_not_initialized_gate():
    """Real _drain_loop: requests before initialize return NOT_INITIALIZED.

    Pushes JSON lines through BridgeRuntimeLoop's real stdin queue and
    captures send() output — no local helper gate simulation.
    """
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=None,
    )
    await loop._init_app_server()
    try:
        await _run_drain_serial(loop, [
            # Request before initialize → gate rejects it
            {"id": "req-1", "method": "no/such/method", "params": {}},
            # initialize is always allowed
            {
                "id": "req-2",
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "test", "title": "Test", "version": "1.0"},
                },
            },
            # Same request after initialize → passes the gate (UNKNOWN_METHOD
            # here, because the method is intentionally unregistered)
            {"id": "req-3", "method": "no/such/method", "params": {}},
        ], capturer)
    finally:
        await loop._shutdown()

    assert len(capturer.messages) == 3, (
        f"Expected 3 responses, got {len(capturer.messages)}: {capturer.messages}"
    )

    first = capturer.messages[0]
    assert first.get("code") == "NOT_INITIALIZED", (
        f"Pre-initialize request should be NOT_INITIALIZED, got: {first}"
    )
    assert first.get("error") == "Not initialized"
    assert first.get("recoverable") is False

    second = capturer.messages[1]
    assert "result" in second, f"initialize should succeed, got: {second}"
    assert "clientId" in second["result"]

    third = capturer.messages[2]
    assert third.get("code") == "UNKNOWN_METHOD", (
        f"Post-initialize request should pass the gate, got: {third}"
    )


@pytest.mark.asyncio
async def test_drain_loop_initialized_notification_no_response():
    """Real _drain_loop: initialized notification is silent and acks the handshake."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=None,
    )
    await loop._init_app_server()
    try:
        await _run_drain_serial(loop, [
            {
                "id": "req-1",
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "test", "title": "Test", "version": "1.0"},
                },
            },
            # Codex-style notification: no 'id' field
            {"method": "initialized", "params": {}},
        ], capturer)
    finally:
        await loop._shutdown()

    # Exactly one response — the initialize result. The notification must
    # produce no response and must advance the handshake ack.
    assert len(capturer.messages) == 1, (
        f"Notification must be silent, got {len(capturer.messages)}: {capturer.messages}"
    )
    assert "result" in capturer.messages[0]

    assert loop._connection_state is not None
    assert loop._connection_state.initialized_ack is True, (
        "initialized notification should set initialized_ack"
    )


# ── 45.1.11: Client ID derivation ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialize_derives_client_id_from_client_info():
    """Client ID is derived from clientInfo.name when no explicit clientId."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "miqi_desktop"},
    })

    client_id = resp["result"]["clientId"]
    assert client_id.startswith("client-")
    # Name segment should be sanitized
    assert "miqi_desktop" in client_id
    # Should include a short uuid
    parts = client_id.split("-")
    assert len(parts) >= 3  # client-NAME-UUID


@pytest.mark.asyncio
async def test_initialize_accepts_explicit_client_id():
    """Explicit clientId in params is used when provided."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "miqi_desktop"},
        "clientId": "my-custom-client-id",
    })

    client_id = resp["result"]["clientId"]
    assert client_id == "my-custom-client-id"


# ── 45.1.12: Per-request client_id conflict ─────────────────────────────────


@pytest.mark.asyncio
async def test_drain_loop_client_id_conflict_rejected():
    """Real _drain_loop: per-request client_id conflicting with the
    connection client_id is rejected with INVALID_PARAMS."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=None,
    )
    await loop._init_app_server()
    try:
        await _run_drain_serial(loop, [
            {
                "id": "req-1",
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "test", "title": "Test", "version": "1.0"},
                    "clientId": "client-A",
                },
            },
            # Mismatching per-request client_id → rejected before dispatch
            {
                "id": "req-2",
                "method": "no/such/method",
                "params": {"client_id": "other-client"},
            },
            # Matching client_id passes the conflict check (UNKNOWN_METHOD here
            # because the method is intentionally unregistered)
            {
                "id": "req-3",
                "method": "no/such/method",
                "params": {"client_id": "client-A"},
            },
        ], capturer)
    finally:
        await loop._shutdown()

    assert len(capturer.messages) == 3, (
        f"Expected 3 responses, got {len(capturer.messages)}: {capturer.messages}"
    )

    first = capturer.messages[0]
    assert "result" in first, f"initialize should succeed, got: {first}"
    assert first["result"]["clientId"] == "client-A"

    second = capturer.messages[1]
    assert second.get("code") == "INVALID_PARAMS", (
        f"Mismatching client_id should be rejected, got: {second}"
    )
    assert "client_id mismatch" in second.get("error", "")

    third = capturer.messages[2]
    assert third.get("code") == "UNKNOWN_METHOD", (
        f"Matching client_id should pass the conflict check, got: {third}"
    )

    # Connection state must still hold the initialize client_id
    assert loop._connection_state is not None
    assert loop._connection_state.client_id == "client-A"


# ── 45.1.13: Event sink is registered under initialized client_id ───────────


@pytest.mark.asyncio
async def test_initialize_registers_event_sink_under_client_id():
    """After initialize, the event sink is registered under the derived client_id."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "miqi_desktop"},
    })
    client_id = resp["result"]["clientId"]

    # At bridge level, the sink would be registered under this client_id.
    # For direct tests, we verify the client_id is usable for sink registration.
    delivered: list[dict] = []

    async def _sink(envelope):
        delivered.append(envelope)

    server.set_event_sink(client_id, _sink)
    await server.emit_client_event(client_id, "process/exited", {"code": 0})
    assert len(delivered) == 1
    assert delivered[0]["event"] == "process/exited"


# ══════════════════════════════════════════════════════════════════════════════
# Phase 45 Hardening: real _drain_loop integration tests
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_drain_loop_rejects_repeated_initialize_with_already_initialized():
    """Real _drain_loop: second initialize returns ALREADY_INITIALIZED.

    Pushes JSON lines through BridgeRuntimeLoop's real stdin queue and
    captures send() output — no local helper gate simulation.
    """
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=None,
    )
    await loop._init_app_server()
    try:
        await _run_drain_serial(loop, [
            {
                "id": "req-1",
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "miqi_desktop", "title": "Desktop", "version": "0.1.0"},
                },
            },
            # Second initialize (must be rejected by bridge, not AppServer)
            {
                "id": "req-2",
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "miqi_desktop", "title": "Desktop", "version": "0.1.0"},
                },
            },
        ], capturer)
    finally:
        await loop._shutdown()

    assert len(capturer.messages) >= 2, (
        f"Expected at least 2 messages, got {len(capturer.messages)}: {capturer.messages}"
    )

    # First message: initialize success
    first = capturer.messages[0]
    assert "result" in first, f"First message should be initialize success, got: {first}"
    assert "clientId" in first["result"]

    # Second message: ALREADY_INITIALIZED (rejected at bridge level)
    second = capturer.messages[1]
    assert second.get("code") == "ALREADY_INITIALIZED", (
        f"Second message should be ALREADY_INITIALIZED, got: {second}"
    )
    assert second.get("error") == "Already initialized"
    assert second.get("recoverable") is False


@pytest.mark.asyncio
async def test_drain_loop_preserves_client_id_after_repeated_initialize():
    """Real _drain_loop: client_id unchanged after repeated initialize rejection."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=None,
    )
    await loop._init_app_server()
    try:
        await _run_drain_serial(loop, [
            # First initialize with explicit clientId
            {
                "id": "req-1",
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "test"},
                    "clientId": "my-stable-client",
                },
            },
            # Second initialize tries to use a different clientId
            {
                "id": "req-2",
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "test"},
                    "clientId": "attacker-client",
                },
            },
        ], capturer)
    finally:
        await loop._shutdown()

    # Verify first initialize succeeded with original client_id
    first = capturer.messages[0]
    assert "result" in first
    assert first["result"]["clientId"] == "my-stable-client"

    # Verify second was rejected
    second = capturer.messages[1]
    assert second.get("code") == "ALREADY_INITIALIZED"

    # Connection state must still hold the first client_id
    assert loop._connection_state is not None
    assert loop._connection_state.client_id == "my-stable-client"


@pytest.mark.asyncio
async def test_drain_loop_preserves_capabilities_after_repeated_initialize():
    """Real _drain_loop: capabilities not overwritten by second initialize."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=None,
    )
    await loop._init_app_server()
    try:
        await _run_drain_serial(loop, [
            # First initialize with experimentalApi=true and some opt-out
            {
                "id": "req-1",
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "test"},
                    "clientId": "cap-test-client",
                    "capabilities": {
                        "experimentalApi": True,
                        "optOutNotificationMethods": ["process/outputDelta"],
                    },
                },
            },
            # Second initialize with experimentalApi=false (should be rejected)
            {
                "id": "req-2",
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "test"},
                    "clientId": "cap-test-client",
                    "capabilities": {
                        "experimentalApi": False,
                    },
                },
            },
        ], capturer)
    finally:
        await loop._shutdown()

    first = capturer.messages[0]
    assert "result" in first
    cid = first["result"]["clientId"]

    second = capturer.messages[1]
    assert second.get("code") == "ALREADY_INITIALIZED"

    # Capabilities on AppServer must still reflect first initialize
    caps = loop.app_server.get_client_capabilities(cid)
    assert caps is not None
    assert caps.experimental_api is True, (
        f"experimentalApi should still be True from first initialize, got {caps.experimental_api}"
    )
    assert "process/outputDelta" in caps.opt_out_notification_methods


@pytest.mark.asyncio
async def test_drain_loop_does_not_re_migrate_event_sink():
    """Real _drain_loop: event sink not re-migrated on repeated initialize."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=None,
    )
    await loop._init_app_server()
    try:
        # Register a desktop sink (what _setup_event_sink normally does)
        desktop_hits: list[int] = [0]

        async def _desktop_sink(envelope):
            desktop_hits[0] += 1

        loop.app_server.set_event_sink("desktop", _desktop_sink)

        await _run_drain_serial(loop, [
            {
                "id": "req-1",
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "test"},
                    "clientId": "sink-test-client",
                },
            },
            # Second initialize (rejected)
            {
                "id": "req-2",
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "test"},
                    "clientId": "sink-test-client",
                },
            },
        ], capturer)
    finally:
        await loop._shutdown()

    first = capturer.messages[0]
    assert "result" in first
    cid = first["result"]["clientId"]

    second = capturer.messages[1]
    assert second.get("code") == "ALREADY_INITIALIZED"

    # Event sink should be registered under the client_id (from first initialize)
    # and still present (not cleaned up)
    assert cid in loop.app_server._event_sinks
    assert "desktop" in loop.app_server._event_sinks


# ══════════════════════════════════════════════════════════════════════════════
# Phase 45 Hardening: experimentalApi must be actual bool
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_experimental_api_string_false_rejected():
    """String "false" is not bool → INVALID_PARAMS, not silently truthy."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "capabilities": {"experimentalApi": "false"},
    })

    assert "error" in resp, f"String 'false' should be rejected, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_experimental_api_string_true_rejected():
    """String "true" is not bool → INVALID_PARAMS."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "capabilities": {"experimentalApi": "true"},
    })

    assert "error" in resp, f"String 'true' should be rejected, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_experimental_api_int_rejected():
    """Integer 1 is not bool → INVALID_PARAMS."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "capabilities": {"experimentalApi": 1},
    })

    assert "error" in resp, f"Integer 1 should be rejected, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_experimental_api_int_zero_rejected():
    """Integer 0 is not bool → INVALID_PARAMS."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "capabilities": {"experimentalApi": 0},
    })

    assert "error" in resp, f"Integer 0 should be rejected, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_experimental_api_null_rejected():
    """null is not bool → INVALID_PARAMS."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "capabilities": {"experimentalApi": None},
    })

    assert "error" in resp, f"null should be rejected, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_experimental_api_true_accepted():
    """bool True is accepted."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "capabilities": {"experimentalApi": True},
    })

    assert "result" in resp, f"bool True should be accepted, got: {resp}"


@pytest.mark.asyncio
async def test_experimental_api_false_accepted():
    """bool False is accepted."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "capabilities": {"experimentalApi": False},
    })

    assert "result" in resp, f"bool False should be accepted, got: {resp}"


@pytest.mark.asyncio
async def test_experimental_api_absent_defaults_false():
    """When experimentalApi is absent, capabilities default to False."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "capabilities": {},
    })

    assert "result" in resp
    cid = resp["result"]["clientId"]
    caps = server.get_client_capabilities(cid)
    assert caps is not None
    assert caps.experimental_api is False


# ══════════════════════════════════════════════════════════════════════════════
# Phase 45 Hardening: explicit clientId validation
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_explicit_client_id_rejects_forward_slash():
    """Explicit clientId with '/' returns INVALID_PARAMS."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "clientId": "evil/../../../etc",
    })

    assert "error" in resp, f"Path char should be rejected, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_explicit_client_id_rejects_backslash():
    """Explicit clientId with backslash returns INVALID_PARAMS."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "clientId": "evil\\..\\..\\windows",
    })

    assert "error" in resp, f"Backslash should be rejected, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_explicit_client_id_rejects_dot_dot():
    """Explicit clientId with '..' returns INVALID_PARAMS."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "clientId": "client-..-etc",
    })

    assert "error" in resp, f"'..' should be rejected, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_explicit_client_id_rejects_control_chars():
    """Explicit clientId with control characters returns INVALID_PARAMS."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    for bad in ["\x00", "\x01", "\x1F", "\x7F", "hello\x00world", "\n", "\t"]:
        resp = await _dispatch(server, registry, "initialize", {
            "clientInfo": {"name": "test"},
            "clientId": bad,
        })
        assert "error" in resp, f"Control char {repr(bad)} should be rejected, got: {resp}"
        assert resp.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_explicit_client_id_rejects_too_long():
    """Explicit clientId > 128 chars returns INVALID_PARAMS."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    resp = await _dispatch(server, registry, "initialize", {
        "clientInfo": {"name": "test"},
        "clientId": "x" * 129,
    })

    assert "error" in resp, f"Too-long clientId should be rejected, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_explicit_client_id_rejects_blank():
    """Explicit clientId empty or whitespace-only returns INVALID_PARAMS."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    for bad in ["", "   ", "\t  \n"]:
        resp = await _dispatch(server, registry, "initialize", {
            "clientInfo": {"name": "test"},
            "clientId": bad,
        })
        assert "error" in resp, f"Blank clientId {repr(bad)} should be rejected, got: {resp}"
        assert resp.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_explicit_client_id_accepts_valid():
    """Valid explicit clientId is accepted."""
    server, registry = _make_server_with_caps()

    from miqi.runtime.initialize_protocol import register_initialize_handler
    register_initialize_handler(server)

    for good in ["my-client", "client_123", "valid.client-id", "a" * 128]:
        resp = await _dispatch(server, registry, "initialize", {
            "clientInfo": {"name": "test"},
            "clientId": good,
        })
        assert "result" in resp, f"Valid clientId {repr(good)} should be accepted, got: {resp}"
        assert resp["result"]["clientId"] == good.strip()
