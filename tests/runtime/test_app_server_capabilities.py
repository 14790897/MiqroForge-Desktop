"""Tests for AppServer client capabilities and notification opt-out (Phase 45)."""

import pytest

from miqi.runtime.app_server import AppServer, ClientSessionRegistry

# ── ClientCapabilities dataclass ────────────────────────────────────────────


def test_client_capabilities_defaults():
    """ClientCapabilities has sensible defaults."""
    from miqi.runtime.app_server import ClientCapabilities

    caps = ClientCapabilities()
    assert caps.experimental_api is False
    assert caps.opt_out_notification_methods == set()
    assert caps.client_info == {}


def test_client_capabilities_with_values():
    """ClientCapabilities can be constructed with explicit values."""
    from miqi.runtime.app_server import ClientCapabilities

    caps = ClientCapabilities(
        experimental_api=True,
        opt_out_notification_methods={"process/outputDelta"},
        client_info={"name": "test", "version": "1.0"},
    )
    assert caps.experimental_api is True
    assert caps.opt_out_notification_methods == {"process/outputDelta"}
    assert caps.client_info == {"name": "test", "version": "1.0"}


# ── set/get_client_capabilities ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_and_get_client_capabilities():
    """Capabilities can be stored and retrieved per client_id."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)

    caps = server.get_client_capabilities("client-1")
    assert caps is None

    from miqi.runtime.app_server import ClientCapabilities

    sc = ClientCapabilities(
        experimental_api=True,
        opt_out_notification_methods={"process/outputDelta"},
        client_info={"name": "miqi_desktop"},
    )
    server.set_client_capabilities("client-1", sc)

    got = server.get_client_capabilities("client-1")
    assert got is not None
    assert got.experimental_api is True
    assert got.opt_out_notification_methods == {"process/outputDelta"}
    assert got.client_info["name"] == "miqi_desktop"


@pytest.mark.asyncio
async def test_get_client_capabilities_unknown_client_returns_none():
    """Unknown client returns None."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)

    assert server.get_client_capabilities("no-such-client") is None


# ── is_experimental_enabled ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_experimental_enabled_false_by_default():
    """Client without capabilities is not experimental-enabled."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)

    assert server.is_experimental_enabled("client-1") is False


@pytest.mark.asyncio
async def test_is_experimental_enabled_true_after_set():
    """Client with experimental_api=True capability is enabled."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)

    from miqi.runtime.app_server import ClientCapabilities

    server.set_client_capabilities(
        "client-1",
        ClientCapabilities(experimental_api=True),
    )
    assert server.is_experimental_enabled("client-1") is True


# ── should_deliver_notification (opt-out) ───────────────────────────────────


@pytest.mark.asyncio
async def test_should_deliver_notification_true_by_default():
    """Client without capabilities receives all notifications."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)

    assert server.should_deliver_notification("client-1", "process/outputDelta") is True
    assert server.should_deliver_notification("client-1", "process/exited") is True


@pytest.mark.asyncio
async def test_should_deliver_notification_respects_exact_opt_out():
    """Opted-out notification is suppressed; others pass through."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)

    from miqi.runtime.app_server import ClientCapabilities

    server.set_client_capabilities(
        "client-1",
        ClientCapabilities(
            opt_out_notification_methods={"process/outputDelta"},
        ),
    )

    assert server.should_deliver_notification("client-1", "process/outputDelta") is False
    assert server.should_deliver_notification("client-1", "process/exited") is True
    assert server.should_deliver_notification("client-1", "other/event") is True


@pytest.mark.asyncio
async def test_should_deliver_notification_unknown_opt_out_name_ignored():
    """Unknown opt-out names are accepted and do not suppress anything."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)

    from miqi.runtime.app_server import ClientCapabilities

    server.set_client_capabilities(
        "client-1",
        ClientCapabilities(
            opt_out_notification_methods={"non/existent", "made/up"},
        ),
    )

    # Unknown names only suppress exact string matches
    assert server.should_deliver_notification("client-1", "non/existent") is False
    assert server.should_deliver_notification("client-1", "process/outputDelta") is True
    assert server.should_deliver_notification("client-1", "process/exited") is True


@pytest.mark.asyncio
async def test_should_deliver_notification_unknown_client_true():
    """Unknown client_id (no capabilities) gets all notifications."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)

    assert server.should_deliver_notification("no-such-client", "process/outputDelta") is True


# ── emit_client_event respects opt-out ──────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_client_event_skips_opted_out_notification():
    """emit_client_event does not deliver opted-out notification to sink."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)

    delivered: list[dict] = []

    async def _capture(envelope):
        delivered.append(envelope)

    server.set_event_sink("client-1", _capture)

    from miqi.runtime.app_server import ClientCapabilities

    server.set_client_capabilities(
        "client-1",
        ClientCapabilities(
            opt_out_notification_methods={"process/outputDelta"},
        ),
    )

    # Should be skipped
    await server.emit_client_event("client-1", "process/outputDelta", {"pid": 1})
    assert len(delivered) == 0

    # Should be delivered
    await server.emit_client_event("client-1", "process/exited", {"pid": 1, "code": 0})
    assert len(delivered) == 1
    assert delivered[0]["event"] == "process/exited"


@pytest.mark.asyncio
async def test_emit_client_event_no_capabilities_delivers_all():
    """Without capabilities, all events are delivered."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)

    delivered: list[dict] = []

    async def _capture(envelope):
        delivered.append(envelope)

    server.set_event_sink("client-1", _capture)

    await server.emit_client_event("client-1", "process/outputDelta", {"pid": 1})
    assert len(delivered) == 1
    assert delivered[0]["event"] == "process/outputDelta"


# ── emit_event (session-scoped) respects opt-out ────────────────────────────


@pytest.mark.asyncio
async def test_emit_event_respects_opt_out_per_client():
    """Session-scoped emit_event suppresses opted-out notifications per client."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)

    delivered_a: list[dict] = []
    delivered_b: list[dict] = []

    async def _sink_a(envelope):
        delivered_a.append(envelope)

    async def _sink_b(envelope):
        delivered_b.append(envelope)

    server.set_event_sink("client-A", _sink_a)
    server.set_event_sink("client-B", _sink_b)

    from miqi.runtime.app_server import ClientCapabilities

    # Client A opts out of process/outputDelta
    server.set_client_capabilities(
        "client-A",
        ClientCapabilities(
            opt_out_notification_methods={"process/outputDelta"},
        ),
    )

    # Set up subscriptions
    registry._session_clients["sess-1"] = {"client-A", "client-B"}
    server.subscribe("client-A", "sess-1")
    server.subscribe("client-B", "sess-1")

    await server.emit_event("sess-1", "process/outputDelta", {"pid": 1})

    # Client A should NOT receive it
    assert len(delivered_a) == 0

    # Client B SHOULD receive it
    assert len(delivered_b) == 1
    assert delivered_b[0]["event"] == "process/outputDelta"


# ── Opt-out does not affect responses/errors ─────────────────────────────────


@pytest.mark.asyncio
async def test_opt_out_does_not_affect_dispatch():
    """Capability opt-out does not affect dispatch responses — only notifications."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)

    from miqi.runtime.app_server import ClientCapabilities

    server.set_client_capabilities(
        "client-1",
        ClientCapabilities(
            opt_out_notification_methods={"chat.send"},
        ),
    )

    # Register a simple handler
    async def _echo(_req_id, params, cid, _sid, _reg):
        return {"result": {"echo": params.get("msg", "")}}

    server.register_method("test.echo", _echo)

    # Dispatch should still work normally — opt-out only affects notifications
    resp = await server.dispatch("req-1", "test.echo", {"msg": "hello"}, "client-1")
    assert "result" in resp
    assert resp["result"]["echo"] == "hello"
