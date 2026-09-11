"""Tests for agent handlers — Phase 28.5.

Validates that agent.list and agent.get use the same AgentControl
data source as agent.spawn/kill (RuntimeSession.services.agent_control),
not the dead _state._agent_control pointer.
"""

import pytest

# ── agent.list ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_list_empty_when_no_agents(fake_config, fake_provider, tmp_path):
    """agent.list returns empty list when no agents have been spawned."""
    from miqi.runtime.agent_handlers import agent_list_handler
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()
    try:
        session = await registry.create_session(
            client_id="client-1",
            session_key="test-session",
            config=fake_config,
            provider=fake_provider,
            workspace=tmp_path,
        )

        result = await agent_list_handler(
            "req-1", {}, "client-1", session.session_id, registry,
        )
        assert result["result"]["agents"] == []
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_agent_list_requires_session_id(fake_config, fake_provider, tmp_path):
    """agent.list requires session_id."""
    from miqi.runtime.agent_handlers import agent_list_handler
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry

    registry = ClientSessionRegistry()

    with pytest.raises(AppServerError) as exc_info:
        await agent_list_handler("req-1", {}, "client-1", None, registry)
    assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_agent_list_requires_authorized_session(fake_config, fake_provider, tmp_path):
    """agent.list returns UNAUTHORIZED for wrong client."""
    from miqi.runtime.agent_handlers import agent_list_handler
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry

    registry = ClientSessionRegistry()
    try:
        session = await registry.create_session(
            client_id="client-A",
            session_key="private-session",
            config=fake_config,
            provider=fake_provider,
            workspace=tmp_path,
        )

        # Client B tries to access client A's session
        with pytest.raises(AppServerError) as exc_info:
            await agent_list_handler(
                "req-1", {}, "client-B", session.session_id, registry,
            )
        assert exc_info.value.code == "UNAUTHORIZED"
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_agent_list_missing_session_returns_empty(fake_config, fake_provider, tmp_path):
    """agent.list returns empty list when the session_id does not exist yet
    (desktop pre-session call) for any client."""
    from miqi.runtime.agent_handlers import agent_list_handler
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()

    result = await agent_list_handler(
        "req-1", {}, "any-client", "nonexistent:session", registry,
    )
    assert result["result"]["agents"] == []


# ── agent.get ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_get_requires_session_id(fake_config, fake_provider, tmp_path):
    """agent.get requires session_id."""
    from miqi.runtime.agent_handlers import agent_get_handler
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry

    registry = ClientSessionRegistry()

    with pytest.raises(AppServerError) as exc_info:
        await agent_get_handler(
            "req-1", {"agent_id": "test-1"}, "client-1", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_agent_get_requires_agent_id(fake_config, fake_provider, tmp_path):
    """agent.get requires agent_id."""
    from miqi.runtime.agent_handlers import agent_get_handler
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry

    registry = ClientSessionRegistry()
    try:
        session = await registry.create_session(
            client_id="client-1",
            session_key="test-session",
            config=fake_config,
            provider=fake_provider,
            workspace=tmp_path,
        )

        with pytest.raises(AppServerError) as exc_info:
            await agent_get_handler(
                "req-1", {}, "client-1", session.session_id, registry,
            )
        assert exc_info.value.code == "INVALID_PARAMS"
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_agent_get_unknown_agent(fake_config, fake_provider, tmp_path):
    """agent.get returns NOT_FOUND for unknown agent_id."""
    from miqi.runtime.agent_handlers import agent_get_handler
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry

    registry = ClientSessionRegistry()
    try:
        session = await registry.create_session(
            client_id="client-1",
            session_key="test-session",
            config=fake_config,
            provider=fake_provider,
            workspace=tmp_path,
        )

        with pytest.raises(AppServerError) as exc_info:
            await agent_get_handler(
                "req-1", {"agent_id": "nonexistent"}, "client-1", session.session_id, registry,
            )
        assert exc_info.value.code == "NOT_FOUND"
    finally:
        await registry.stop_all()
