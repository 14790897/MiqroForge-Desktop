"""Tests for bridge transport adapter over AppServer (Phase 26.2)."""

import json

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────


class _CaptureStdout:
    """Capture _send() calls during a test."""
    def __init__(self):
        self.messages: list[dict] = []

    def send(self, data: dict) -> None:
        self.messages.append(data)


def _make_app_server():
    """Create an AppServer with a few registered test methods."""
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    server = AppServer(registry)

    async def echo_handler(request_id, params, client_id, session_id, registry_):
        return {"result": {"echo": params.get("msg", "")}}

    async def status_handler(request_id, params, client_id, session_id, registry_):
        return {"result": {"status": "ok"}}

    async def error_handler(request_id, params, client_id, session_id, registry_):
        from miqi.runtime.app_server import AppServerError
        raise AppServerError("test error", code="TEST_ERROR", recoverable=True)

    server.register_method("test.echo", echo_handler)
    server.register_method("test.status", status_handler)
    server.register_method("test.error", error_handler)

    return server


# ── AppServer dispatch via transport layer ───────────────────────────────


@pytest.mark.asyncio
async def test_transport_dispatches_request_to_app_server():
    """Simulate a full dispatch cycle: request JSON → AppServer → response JSON."""
    server = _make_app_server()
    capturer = _CaptureStdout()

    request = json.dumps({
        "id": "req-001",
        "method": "test.echo",
        "params": {"msg": "hello"},
    })

    # Parse request (simulating stdin read)
    req = json.loads(request)
    req_id = req["id"]
    method = req["method"]
    params = req.get("params", {})

    # Resolve client_id (Phase 27.5: required)
    client_id = params.get("client_id") or "desktop-test"

    # Dispatch through AppServer
    response = await server.dispatch(
        request_id=req_id,
        method=method,
        params=params,
        client_id=client_id,
    )

    # Write response (simulating stdout write)
    capturer.send(response)

    assert len(capturer.messages) == 1
    assert capturer.messages[0]["request_id"] == "req-001"
    assert capturer.messages[0]["result"] == {"echo": "hello"}


@pytest.mark.asyncio
async def test_transport_unknown_method_returns_error():
    server = _make_app_server()
    capturer = _CaptureStdout()

    response = await server.dispatch(
        request_id="req-002",
        method="nonexistent.method",
        params={},
        client_id="desktop-test",
    )
    capturer.send(response)

    assert "error" in capturer.messages[0]
    assert capturer.messages[0]["code"] == "UNKNOWN_METHOD"


@pytest.mark.asyncio
async def test_transport_handler_error_is_caught():
    server = _make_app_server()
    capturer = _CaptureStdout()

    response = await server.dispatch(
        request_id="req-003",
        method="test.error",
        params={},
        client_id="desktop-test",
    )
    capturer.send(response)

    assert "error" in capturer.messages[0]
    assert capturer.messages[0]["code"] == "TEST_ERROR"
    assert capturer.messages[0]["recoverable"] is True


@pytest.mark.asyncio
async def test_transport_sends_valid_json_line():
    """The output must be valid JSON as one line (bridge protocol)."""
    server = _make_app_server()

    response = await server.dispatch(
        request_id="req-004",
        method="test.status",
        params={},
        client_id="desktop-test",
    )

    # Serialize as the bridge would
    line = json.dumps(response, ensure_ascii=False)
    assert "\n" not in line  # single line

    # Must be parseable by a JSON consumer
    parsed = json.loads(line)
    assert parsed["request_id"] == "req-004"
    assert parsed["result"]["status"] == "ok"


# ── client_id shim integration ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_transport_missing_client_id_raises_error():
    """Phase 27.5: missing client_id raises AppServerError in production."""
    server = _make_app_server()

    from miqi.runtime.app_server import AppServerError

    with pytest.raises(AppServerError, match="client_id is required"):
        server.registry.resolve_client_id(None)


@pytest.mark.asyncio
async def test_transport_explicit_client_id_no_warning():
    server = _make_app_server()

    client_id = server.registry.resolve_client_id("my-client")
    assert client_id == "my-client"

    response = await server.dispatch(
        request_id="req-006",
        method="test.status",
        params={},
        client_id=client_id,
    )
    assert "result" in response


# ── Bridge main() integration ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bridge_app_server_created_in_main(monkeypatch, tmp_path):
    """Verify that the bridge creates an AppServer during initialization.

    Phase 27.2: AppServer is created by BridgeRuntimeLoop._init_app_server(),
    not by _ensure_app_server() (which is now a simple getter).
    """
    import miqi.bridge.server as bridge_module
    from miqi.bridge.loop import BridgeRuntimeLoop
    from miqi.runtime.app_server import AppServer

    # Create a BridgeRuntimeLoop and init its AppServer
    capturer = _CaptureStdout()
    loop = BridgeRuntimeLoop(
        send_func=capturer.send,
        dispatch_legacy_func=bridge_module._dispatch,
    )
    await loop._init_app_server()

    app_server = loop.app_server
    assert isinstance(app_server, AppServer)
    assert app_server.registry is not None
    assert "status" in app_server._methods
    assert "replay.turns" in app_server._methods
    assert "chat.abort" in app_server._methods

    await loop._shutdown()


# ── Existing dispatch compatibility ──────────────────────────────────────


def test_bridge_existing_dispatch_still_works(monkeypatch):
    """The existing _METHODS dispatch table must still work after
    AppServer is added — backward compatibility."""
    from miqi.bridge.server import _METHODS

    # _METHODS should contain the remaining legacy handlers
    assert "status" in _METHODS
    # chat.send and chat.abort are now AppServer methods (Phase 27.3)
    # approvals.* are now AppServer methods (Phase 28.2)
    # config.get/config.update are now AppServer methods (Phase 28.3)
    # sessions.* are now AppServer methods (Phase 28.4)
    assert "approvals.list" not in _METHODS  # migrated to AppServer
    assert "config.get" not in _METHODS  # migrated to AppServer
    assert "sessions.list" not in _METHODS  # migrated to AppServer
    # files.* migrated to AppServer (Phase 30)
    assert "files.tree" not in _METHODS  # migrated to AppServer
    # providers.*, channels.*, permissions.* migrated to AppServer (Phase 35.2)
    assert "providers.list" not in _METHODS  # migrated to AppServer
    # cron.* migrated to AppServer (Phase 35.6)
    assert "cron.list" not in _METHODS  # migrated to AppServer
    # memory.* migrated to AppServer (Phase 35.7)
    assert "memory.list" not in _METHODS  # migrated to AppServer
    # python.check migrated to AppServer (Phase 35.8)
    assert "python.check" not in _METHODS  # migrated to AppServer
    # These legacy handlers should still be present
    assert "status" in _METHODS
    assert "plan.get" in _METHODS
