"""Tests for event subscription and fanout (Phase 26.4)."""

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────


class _EventCollector:
    """Simulates a transport adapter that collects events for a client."""
    def __init__(self):
        self.events: list[dict] = []

    async def sink(self, event: dict) -> None:
        self.events.append(event)


# ── Subscribe / unsubscribe ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_subscribes_and_receives_events():
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    # Register client-1 as authorized for session-1
    registry._session_clients["session-1"] = {"client-1"}
    registry._client_sessions.setdefault("client-1", set()).add("session-1")

    server = AppServer(registry)
    await server.start()
    try:
        collector = _EventCollector()
        server.set_event_sink("client-1", collector.sink)

        server.subscribe("client-1", "session-1")
        await server.emit_event("session-1", "test_event", {"msg": "hello"})

        assert len(collector.events) == 1
        assert collector.events[0]["event"] == "test_event"
        assert collector.events[0]["data"] == {"msg": "hello"}
        assert collector.events[0]["request_id"] is None  # push event
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_client_does_not_receive_after_unsubscribe():
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    registry._session_clients["session-1"] = {"client-1"}
    registry._client_sessions.setdefault("client-1", set()).add("session-1")

    server = AppServer(registry)
    await server.start()
    try:
        collector = _EventCollector()
        server.set_event_sink("client-1", collector.sink)
        server.subscribe("client-1", "session-1")

        await server.emit_event("session-1", "event_1", {})
        server.unsubscribe("client-1", "session-1")
        await server.emit_event("session-1", "event_2", {})

        assert len(collector.events) == 1
        assert collector.events[0]["event"] == "event_1"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_multiple_clients_receive_same_session_events():
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    registry._session_clients["shared-session"] = {"client-A", "client-B"}
    registry._client_sessions.setdefault("client-A", set()).add("shared-session")
    registry._client_sessions.setdefault("client-B", set()).add("shared-session")

    server = AppServer(registry)
    await server.start()
    try:
        c1 = _EventCollector()
        c2 = _EventCollector()
        server.set_event_sink("client-A", c1.sink)
        server.set_event_sink("client-B", c2.sink)
        server.subscribe("client-A", "shared-session")
        server.subscribe("client-B", "shared-session")

        await server.emit_event("shared-session", "broadcast", {"x": 1})

        assert len(c1.events) == 1
        assert len(c2.events) == 1
        assert c1.events[0] == c2.events[0]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_unsubscribed_client_not_notified_of_other_session():
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    registry._session_clients["session-A"] = {"client-A"}
    registry._client_sessions.setdefault("client-A", set()).add("session-A")

    server = AppServer(registry)
    await server.start()
    try:
        c_a = _EventCollector()
        c_b = _EventCollector()
        server.set_event_sink("client-A", c_a.sink)
        server.set_event_sink("client-B", c_b.sink)
        server.subscribe("client-A", "session-A")

        # Event on session-B — client-A should NOT receive it
        await server.emit_event("session-B", "secret", {})

        assert len(c_a.events) == 0
        assert len(c_b.events) == 0  # B never subscribed to anything
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_event_with_request_id_correlates_to_request():
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    registry._session_clients["session-1"] = {"client-1"}
    registry._client_sessions.setdefault("client-1", set()).add("session-1")
    server = AppServer(registry)
    await server.start()
    try:
        collector = _EventCollector()
        server.set_event_sink("client-1", collector.sink)
        server.subscribe("client-1", "session-1")

        await server.emit_event(
            "session-1", "progress", {"delta": "hello"},
            request_id="req-123",
        )

        assert len(collector.events) == 1
        assert collector.events[0]["request_id"] == "req-123"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_emit_event_no_subscribers_is_noop():
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    server = AppServer(registry)
    await server.start()
    try:
        # Should not raise or fail
        await server.emit_event("nonexistent-session", "event", {})
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_client_without_sink_ignored():
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    registry._session_clients["session-1"] = {"no-sink-client"}
    registry._client_sessions.setdefault("no-sink-client", set()).add("session-1")
    server = AppServer(registry)
    server.subscribe("no-sink-client", "session-1")
    # No sink set — event should be dropped silently, not crash
    await server.emit_event("session-1", "event", {})


@pytest.mark.asyncio
async def test_event_preserves_order_for_single_client():
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    registry._session_clients["session-1"] = {"client-1"}
    registry._client_sessions.setdefault("client-1", set()).add("session-1")
    server = AppServer(registry)
    await server.start()
    try:
        collector = _EventCollector()
        server.set_event_sink("client-1", collector.sink)
        server.subscribe("client-1", "session-1")

        for i in range(5):
            await server.emit_event("session-1", "seq", {"i": i})

        assert len(collector.events) == 5
        assert [e["data"]["i"] for e in collector.events] == [0, 1, 2, 3, 4]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_unauthorized_client_cannot_subscribe():
    """Security: unauthorized clients are silently refused subscription."""
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    registry._session_clients["session-1"] = {"authorized-client"}
    # unauthorized-client is NOT in session-1's client set

    server = AppServer(registry)
    collector = _EventCollector()
    server.set_event_sink("unauthorized-client", collector.sink)
    server.subscribe("unauthorized-client", "session-1")

    # Emit event — unauthorized client should NOT receive it
    await server.emit_event("session-1", "test", {"msg": "secret"})
    assert len(collector.events) == 0
