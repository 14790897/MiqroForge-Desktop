"""Tests for replay/debug APIs through AppServer (Phase 26.5)."""

import pytest

from miqi.protocol.commands import UserMessage
from miqi.protocol.events import TurnCompleteEvent

# ── Replay turns ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_turns_through_app_server(fake_config, fake_provider, tmp_path):
    """replay.turns returns turn_ids from ledger for an authorized client."""
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    # Create a real RuntimeSession for the registry
    registry = ClientSessionRegistry()
    session = await registry.create_session(
        client_id="client-1", session_key="replay-test",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    # Run a turn to populate the ledger
    await session.submit(UserMessage(content="hello", thread_id="thread-replay"))
    while True:
        event = await session.next_event(timeout=2)
        if isinstance(event, TurnCompleteEvent):
            break

    server = AppServer(registry)
    await server.start()
    try:
        # Register replay handler
        _register_replay_handlers(server)

        response = await server.dispatch(
            request_id="req-1",
            method="replay.turns",
            params={"thread_id": "thread-replay"},
            client_id="client-1",
            session_id=session.session_id,
        )
        assert "result" in response, f"Expected result, got {response}"
        turns = response["result"]["turns"]
        assert len(turns) == 1
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_replay_timeline_through_app_server(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    session = await registry.create_session(
        client_id="client-1", session_key="timeline-test",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    await session.submit(UserMessage(content="hello", thread_id="thread-tl"))
    turn_id = None
    while True:
        event = await session.next_event(timeout=2)
        if isinstance(event, TurnCompleteEvent):
            turn_id = event.turn_id
            break

    server = AppServer(registry)
    await server.start()
    try:
        _register_replay_handlers(server)

        response = await server.dispatch(
            request_id="req-2",
            method="replay.timeline",
            params={"thread_id": "thread-tl", "turn_id": turn_id},
            client_id="client-1",
            session_id=session.session_id,
        )
        assert "result" in response
        timeline = response["result"]["timeline"]
        assert timeline["turn_id"] == turn_id
        assert timeline["status"] == "completed"
        assert timeline["user_input"] == "hello"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_replay_messages_through_app_server(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    session = await registry.create_session(
        client_id="client-1", session_key="msgs-test",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    await session.submit(UserMessage(content="hello", thread_id="thread-msgs"))
    while True:
        event = await session.next_event(timeout=2)
        if isinstance(event, TurnCompleteEvent):
            break

    server = AppServer(registry)
    await server.start()
    try:
        _register_replay_handlers(server)

        response = await server.dispatch(
            request_id="req-3",
            method="replay.messages",
            params={"thread_id": "thread-msgs"},
            client_id="client-1",
            session_id=session.session_id,
        )
        assert "result" in response
        messages = response["result"]["messages"]
        roles = [m["role"] for m in messages]
        assert "user" in roles
        assert "assistant" in roles
    finally:
        await server.stop()


# ── Authorization ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unauthorized_client_cannot_access_replay(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    session = await registry.create_session(
        client_id="client-A", session_key="private-replay",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )

    server = AppServer(registry)
    await server.start()
    try:
        _register_replay_handlers(server)

        # Client B tries to access replay — must get UNAUTHORIZED
        response = await server.dispatch(
            request_id="req-unauth",
            method="replay.turns",
            params={"thread_id": "thread-any"},
            client_id="client-B",  # NOT authorized
            session_id=session.session_id,
        )
        assert "error" in response
        assert response["code"] == "UNAUTHORIZED"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_replay_session_scoped_data(fake_config, fake_provider, tmp_path):
    """Replay API for session A does not return data from session B."""
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    # Session A with data
    s_a = await registry.create_session(
        client_id="client-A", session_key="sa",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    await s_a.submit(UserMessage(content="data from A", thread_id="thread-a"))
    while True:
        evt = await s_a.next_event(timeout=2)
        if isinstance(evt, TurnCompleteEvent):
            break

    # Session B with data
    s_b = await registry.create_session(
        client_id="client-B", session_key="sb",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    await s_b.submit(UserMessage(content="data from B", thread_id="thread-b"))
    while True:
        evt = await s_b.next_event(timeout=2)
        if isinstance(evt, TurnCompleteEvent):
            break

    server = AppServer(registry)
    await server.start()
    try:
        _register_replay_handlers(server)

        # Client A replaying session A: should see A's data only
        r_a = await server.dispatch(
            request_id="ra", method="replay.messages",
            params={"thread_id": "thread-a"},
            client_id="client-A", session_id=s_a.session_id,
        )
        assert "result" in r_a
        # Client A cannot access session B's data (different session)
        r_a_cross = await server.dispatch(
            request_id="ra2", method="replay.messages",
            params={"thread_id": "thread-a"},
            client_id="client-A", session_id=s_b.session_id,
        )
        assert "error" in r_a_cross
    finally:
        await server.stop()


# ── Handler registration helper ──────────────────────────────────────────


def _register_replay_handlers(server):
    """Register replay handlers on AppServer (mirrors bridge registration)."""
    from dataclasses import asdict

    async def _replay_turns(request_id, params, client_id, session_id, registry):
        session = await registry.get_session(client_id, session_id)
        if session is None:
            from miqi.runtime.app_server import AppServerError
            raise AppServerError("Not authorized", code="UNAUTHORIZED")
        thread_id = params["thread_id"]
        turns = await session.list_turns(thread_id)
        return {"result": {"turns": turns}}

    async def _replay_timeline(request_id, params, client_id, session_id, registry):
        session = await registry.get_session(client_id, session_id)
        if session is None:
            from miqi.runtime.app_server import AppServerError
            raise AppServerError("Not authorized", code="UNAUTHORIZED")
        timeline = await session.get_turn_replay(
            params["thread_id"], params["turn_id"],
        )
        if timeline is None:
            return {"result": {"timeline": None}}
        return {"result": {"timeline": asdict(timeline)}}

    async def _replay_messages(request_id, params, client_id, session_id, registry):
        session = await registry.get_session(client_id, session_id)
        if session is None:
            from miqi.runtime.app_server import AppServerError
            raise AppServerError("Not authorized", code="UNAUTHORIZED")
        msgs = await session.get_provider_messages(params["thread_id"])
        return {"result": {"messages": msgs}}

    server.register_method("replay.turns", _replay_turns)
    server.register_method("replay.timeline", _replay_timeline)
    server.register_method("replay.messages", _replay_messages)
