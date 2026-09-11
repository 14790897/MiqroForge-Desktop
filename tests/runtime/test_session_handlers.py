"""Tests for session handlers — Phase 28.4.

Validates that session listing, get, delete, archive, and metadata
operations properly manage RuntimeSession lifecycle through AppServer.
"""

import pytest

# ── sessions.list ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_list_merges_active_and_disk(fake_config, fake_provider, tmp_path):
    """sessions.list returns both active (AppServer) and inactive (disk) sessions."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_list_handler

    registry = ClientSessionRegistry()

    try:
        # Create an active session
        await registry.create_session(
            client_id="client-1",
            session_key="active-session",
            config=fake_config,
            provider=fake_provider,
            workspace=tmp_path,
        )

        result = await sessions_list_handler("req-1", {}, "client-1", None, registry)
        sessions = result["result"]["sessions"]
        assert isinstance(sessions, list)
        # Should have at least the active session
        active_keys = [s["key"] for s in sessions if s.get("status") == "running"]
        assert "active-session" in active_keys
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_sessions_list_scoped_to_client(fake_config, fake_provider, tmp_path):
    """sessions.list does not leak sessions from other clients."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_list_handler

    registry = ClientSessionRegistry()

    try:
        # Client A creates a session
        await registry.create_session(
            client_id="client-A",
            session_key="private",
            config=fake_config,
            provider=fake_provider,
            workspace=tmp_path,
        )

        # Client B's list should not see client-A's session
        result = await sessions_list_handler("req-1", {}, "client-B", None, registry)
        sessions = result["result"]["sessions"]
        # None of client-B's sessions should have key "private" with status "running"
        private_running = [s for s in sessions if s["key"] == "private" and s.get("status") == "running"]
        assert len(private_running) == 0
    finally:
        await registry.stop_all()


# ── sessions.get ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_get_active_session(fake_config, fake_provider, tmp_path):
    """sessions.get returns active session with runtime status."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_handler

    registry = ClientSessionRegistry()

    try:
        await registry.create_session(
            client_id="client-1",
            session_key="my-session",
            config=fake_config,
            provider=fake_provider,
            workspace=tmp_path,
        )

        result = await sessions_get_handler(
            "req-1", {"session_key": "my-session"}, "client-1", None, registry,
        )
        assert result["result"]["key"] == "my-session"
        assert result["result"]["status"] == "running"
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_sessions_get_missing_session_key(fake_config, fake_provider, tmp_path):
    """sessions.get rejects missing session_key."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_handler

    registry = ClientSessionRegistry()

    with pytest.raises(AppServerError) as exc_info:
        await sessions_get_handler("req-1", {}, "client-1", None, registry)
    assert exc_info.value.code == "INVALID_PARAMS"


# ── sessions.delete ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_delete_stops_runtime_and_cleans_registry(fake_config, fake_provider, tmp_path):
    """sessions.delete stops RuntimeSession and removes from AppServer registry."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_delete_handler

    registry = ClientSessionRegistry()

    session = await registry.create_session(
        client_id="client-1",
        session_key="to-delete",
        config=fake_config,
        provider=fake_provider,
        workspace=tmp_path,
    )
    sid = session.session_id

    # Verify session exists
    assert await registry.get_session("client-1", sid) is not None

    # Delete
    result = await sessions_delete_handler(
        "req-1", {"session_key": "to-delete"}, "client-1", None, registry,
    )
    assert result["result"]["deleted"] is True

    # Verify session is removed from registry
    assert await registry.get_session("client-1", sid) is None


@pytest.mark.asyncio
async def test_sessions_delete_missing_session_key(fake_config, fake_provider, tmp_path):
    """sessions.delete rejects missing session_key."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_delete_handler

    registry = ClientSessionRegistry()

    with pytest.raises(AppServerError) as exc_info:
        await sessions_delete_handler("req-1", {}, "client-1", None, registry)
    assert exc_info.value.code == "INVALID_PARAMS"


# ── sessions.archive ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_archive_stops_runtime(fake_config, fake_provider, tmp_path):
    """sessions.archive stops RuntimeSession and marks archived."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_archive_handler

    registry = ClientSessionRegistry()

    session = await registry.create_session(
        client_id="client-1",
        session_key="to-archive",
        config=fake_config,
        provider=fake_provider,
        workspace=tmp_path,
    )
    sid = session.session_id

    # Archive
    result = await sessions_archive_handler(
        "req-1", {"session_key": "to-archive"}, "client-1", None, registry,
    )
    assert result["result"]["archived"] is True

    # Verify session is removed from registry
    assert await registry.get_session("client-1", sid) is None


@pytest.mark.asyncio
async def test_sessions_archive_missing_session_key(fake_config, fake_provider, tmp_path):
    """sessions.archive rejects missing session_key."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_archive_handler

    registry = ClientSessionRegistry()

    with pytest.raises(AppServerError) as exc_info:
        await sessions_archive_handler("req-1", {}, "client-1", None, registry)
    assert exc_info.value.code == "INVALID_PARAMS"


# ── sessions.unarchive ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_unarchive_requires_session_key(fake_config, fake_provider, tmp_path):
    """sessions.unarchive rejects missing session_key."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_unarchive_handler

    registry = ClientSessionRegistry()

    with pytest.raises(AppServerError) as exc_info:
        await sessions_unarchive_handler("req-1", {}, "client-1", None, registry)
    assert exc_info.value.code == "INVALID_PARAMS"
