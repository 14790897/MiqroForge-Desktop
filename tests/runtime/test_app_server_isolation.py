"""Tests for client/session isolation and TTL eviction (Phase 26.3)."""

import pytest

# ── Session-level isolation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_cannot_access_another_clients_session(fake_config, fake_provider, tmp_path):
    """Client A creates a session; Client B cannot access it."""
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()
    session = await registry.create_session(
        client_id="client-A",
        session_key="private",
        config=fake_config,
        provider=fake_provider,
        workspace=tmp_path,
    )
    # Client B tries to access — must return None
    got = await registry.get_session("client-B", session.session_id)
    assert got is None

    # Cleanup
    await registry.stop_all()


@pytest.mark.asyncio
async def test_multiple_clients_can_share_session_when_authorized(fake_config, fake_provider, tmp_path):
    """Client A authorizes Client B; both can access the same session."""
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()
    session = await registry.create_session(
        client_id="client-A",
        session_key="shared",
        config=fake_config,
        provider=fake_provider,
        workspace=tmp_path,
    )
    registry.authorize_client("client-A", session.session_id, "client-B")

    # Both can access
    assert await registry.get_session("client-A", session.session_id) is session
    assert await registry.get_session("client-B", session.session_id) is session

    # Both see the session in list
    assert session.session_id in registry.list_sessions("client-A")
    assert session.session_id in registry.list_sessions("client-B")

    await registry.stop_all()


@pytest.mark.asyncio
async def test_one_client_abort_targets_own_session(fake_config, fake_provider, tmp_path):
    """Client A's abort on their session doesn't affect Client B's session."""
    from miqi.protocol.commands import AbortTurn
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()
    session_a = await registry.create_session(
        client_id="client-A", session_key="session-a",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    session_b = await registry.create_session(
        client_id="client-B", session_key="session-b",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )

    # Client A aborts their own session — Client B's session is unaffected
    await session_a.submit(AbortTurn(thread_id="default"))

    # Client B's session must still be accessible
    assert await registry.get_session("client-B", session_b.session_id) is session_b

    await registry.stop_all()


@pytest.mark.asyncio
async def test_list_sessions_only_returns_authorized(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()
    s1 = await registry.create_session(
        client_id="client-A", session_key="s1",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    s2 = await registry.create_session(
        client_id="client-B", session_key="s2",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    # A can see s1, not s2
    assert registry.list_sessions("client-A") == [s1.session_id]
    # B can see s2, not s1
    assert registry.list_sessions("client-B") == [s2.session_id]

    await registry.stop_all()


# ── TTL eviction ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registry_evicts_idle_sessions(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry(idle_timeout_seconds=0)  # immediate eviction
    session = await registry.create_session(
        client_id="client-1", session_key="ephemeral",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )

    # Advance last_activity to be "idle"
    registry._last_activity[session.session_id] = 0  # epoch

    evicted = await registry.evict_idle_sessions()
    assert session.session_id in evicted

    # Session is gone
    assert await registry.get_session("client-1", session.session_id) is None
    assert registry.list_sessions("client-1") == []


@pytest.mark.asyncio
async def test_ttl_task_runs_in_app_server(fake_config, fake_provider, tmp_path):
    """AppServer runs a background TTL eviction task."""
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry(idle_timeout_seconds=3600)
    server = AppServer(registry)
    await server.start()
    try:
        # TTL task should be running
        assert server._ttl_task is not None
        assert not server._ttl_task.done()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_active_session_not_evicted(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry(idle_timeout_seconds=3600)
    session = await registry.create_session(
        client_id="client-1", session_key="active",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    # Session just created — last_activity is recent
    evicted = await registry.evict_idle_sessions()
    assert session.session_id not in evicted

    # Session still accessible
    assert await registry.get_session("client-1", session.session_id) is session

    await registry.stop_all()


# ── AppServer start/stop lifecycle ────────────────────────────────────────


@pytest.mark.asyncio
async def test_app_server_start_and_stop():
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    server = AppServer(registry)

    await server.start()
    assert server._running

    await server.stop()
    assert not server._running


@pytest.mark.asyncio
async def test_app_server_stop_stops_all_sessions(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    server = AppServer(registry)
    await server.start()

    # Create a session through the registry
    await registry.create_session(
        client_id="c1", session_key="s1",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )

    await server.stop()

    # Stop should close all sessions
    assert registry.session_count == 0
    assert registry.list_sessions("c1") == []
