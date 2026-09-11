"""Phase 29 ownership audit tests — verify invariants after migration.

Validates:
- AppServer session handlers pass client_id to SessionManager
- Cross-client access is rejected by all handlers
- Explicit claim works for unowned sessions
- AppServer path cannot auto-claim unowned legacy sessions
- session_handlers always have client_id in their signatures
- AgentLoop/process_direct remain zero in production paths
"""

from pathlib import Path

import pytest

from miqi.runtime.app_server import AppServerError
from miqi.session.manager import SessionManager

# ── Helpers ──────────────────────────────────────────────────────────────────


def _handler_sm(*, legacy_sessions_dir: Path | None = None):
    """Get a SessionManager using the same path the AppServer handlers use."""
    import miqi.bridge.server as bridge_module
    state = getattr(bridge_module, "_state", None)
    if state is None:
        pytest.skip("Bridge state not available")
    config = state.load_config()
    return SessionManager(config.workspace_path, legacy_sessions_dir=legacy_sessions_dir)


def _cleanup_session(sm, key):
    """Clean up a test session from disk."""
    try:
        sm.delete(key)
    except Exception:
        pass


# ── Handler ownership scoping ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_list_handler_passes_client_id(fake_config, fake_provider, tmp_path):
    """sessions.list handler passes client_id — only shows current client's sessions."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_list_handler

    registry = ClientSessionRegistry()
    workspace = tmp_path

    try:
        # Create sessions for two clients
        await registry.create_session(
            client_id="client-A", session_key="a-session",
            config=fake_config, provider=fake_provider, workspace=workspace,
        )
        await registry.create_session(
            client_id="client-B", session_key="b-session",
            config=fake_config, provider=fake_provider, workspace=workspace,
        )

        # Client A lists — should only see "a-session"
        result = await sessions_list_handler("req-1", {}, "client-A", None, registry)
        sessions = result["result"]["sessions"]
        keys = [s["key"] for s in sessions]
        assert "a-session" in keys
        assert "b-session" not in keys, "client-A should not see client-B's session"
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_sessions_get_handler_rejects_cross_client(fake_config, fake_provider, tmp_path):
    """sessions.get handler rejects cross-client access."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_handler

    registry = ClientSessionRegistry()
    sm = _handler_sm()
    key = "_p29_test_get_cross"

    # Create a disk session owned by client-A
    try:
        s = sm.get_or_create(key, client_id="client-A")
        s.add_message("user", "secret")
        sm.save(s)
        sm.invalidate(key)

        # Client B tries to get it — should fail
        with pytest.raises(AppServerError) as exc_info:
            await sessions_get_handler(
                "req-1", {"session_key": key},
                "client-B", None, registry,
            )
        assert exc_info.value.code in ("UNAUTHORIZED", "REQUIRES_CLAIM")
    finally:
        _cleanup_session(sm, key)


@pytest.mark.asyncio
async def test_sessions_delete_handler_rejects_cross_client(fake_config, fake_provider, tmp_path):
    """sessions.delete handler rejects cross-client deletion."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_delete_handler

    registry = ClientSessionRegistry()
    sm = _handler_sm()
    key = "_p29_test_del_cross"

    try:
        s = sm.get_or_create(key, client_id="client-A")
        s.add_message("user", "data")
        sm.save(s)
        sm.invalidate(key)

        with pytest.raises(AppServerError) as exc_info:
            await sessions_delete_handler(
                "req-1", {"session_key": key},
                "client-B", None, registry,
            )
        assert exc_info.value.code in ("UNAUTHORIZED", "REQUIRES_CLAIM")
    finally:
        _cleanup_session(sm, key)


@pytest.mark.asyncio
async def test_sessions_archive_handler_rejects_cross_client(fake_config, fake_provider, tmp_path):
    """sessions.archive handler rejects cross-client archive."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_archive_handler

    registry = ClientSessionRegistry()
    sm = _handler_sm()
    key = "_p29_test_archive_cross"

    try:
        s = sm.get_or_create(key, client_id="client-A")
        sm.save(s)
        sm.invalidate(key)

        with pytest.raises(AppServerError) as exc_info:
            await sessions_archive_handler(
                "req-1", {"session_key": key},
                "client-B", None, registry,
            )
        assert exc_info.value.code in ("UNAUTHORIZED", "REQUIRES_CLAIM")
    finally:
        _cleanup_session(sm, key)


@pytest.mark.asyncio
async def test_sessions_get_tracked_files_rejects_cross_client(fake_config, fake_provider, tmp_path):
    """sessions.get_tracked_files handler rejects cross-client read."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_tracked_files_handler

    registry = ClientSessionRegistry()
    sm = _handler_sm()
    key = "_p29_test_tf_cross"

    try:
        s = sm.get_or_create(key, client_id="client-A")
        sm.save(s)
        sm.invalidate(key)

        with pytest.raises(AppServerError) as exc_info:
            await sessions_get_tracked_files_handler(
                "req-1", {"session_key": key},
                "client-B", None, registry,
            )
        assert exc_info.value.code in ("UNAUTHORIZED", "REQUIRES_CLAIM")
    finally:
        _cleanup_session(sm, key)


@pytest.mark.asyncio
async def test_sessions_clear_tracked_files_rejects_cross_client(fake_config, fake_provider, tmp_path):
    """sessions.clear_tracked_files handler rejects cross-client clear."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_clear_tracked_files_handler

    registry = ClientSessionRegistry()
    sm = _handler_sm()
    key = "_p29_test_ctf_cross"

    try:
        s = sm.get_or_create(key, client_id="client-A")
        sm.save(s)
        sm.invalidate(key)

        with pytest.raises(AppServerError) as exc_info:
            await sessions_clear_tracked_files_handler(
                "req-1", {"session_key": key},
                "client-B", None, registry,
            )
        assert exc_info.value.code in ("UNAUTHORIZED", "REQUIRES_CLAIM")
    finally:
        _cleanup_session(sm, key)


# ── Explicit claim handler ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_claim_legacy_succeeds(fake_config, fake_provider, tmp_path):
    """sessions.claim_legacy handler can claim an unowned legacy session."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_claim_legacy_handler

    registry = ClientSessionRegistry()
    sm = _handler_sm()
    key = "_p29_test_claim_ok"

    try:
        # Create unowned legacy session on disk
        s = sm.get_or_create(key)  # no client_id
        s.add_message("user", "legacy data")
        sm.save(s)
        sm.invalidate(key)

        result = await sessions_claim_legacy_handler(
            "req-1", {"session_key": key},
            "client-A", None, registry,
        )
        assert result["result"]["claimed"] is True
        assert sm.get_owner(key) == "client-A"
    finally:
        _cleanup_session(sm, key)


@pytest.mark.asyncio
async def test_sessions_claim_legacy_rejects_foreign_owned(fake_config, fake_provider, tmp_path):
    """sessions.claim_legacy handler rejects claiming a foreign-owned session."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_claim_legacy_handler

    registry = ClientSessionRegistry()
    sm = _handler_sm()
    key = "_p29_test_claim_foreign"

    try:
        s = sm.get_or_create(key, client_id="client-A")
        sm.save(s)
        sm.invalidate(key)

        with pytest.raises(AppServerError) as exc_info:
            await sessions_claim_legacy_handler(
                "req-1", {"session_key": key},
                "client-B", None, registry,
            )
        assert exc_info.value.code == "UNAUTHORIZED"
    finally:
        _cleanup_session(sm, key)


# ── No auto-claim in AppServer path ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_app_server_path_cannot_auto_claim_legacy(fake_config, fake_provider, tmp_path):
    """AppServer session handlers read unowned sessions without auto-claiming."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_handler

    registry = ClientSessionRegistry()
    sm = _handler_sm()
    key = "_p29_test_no_auto_claim"

    try:
        s = sm.get_or_create(key)  # no client_id
        s.add_message("user", "old data")
        sm.save(s)
        sm.invalidate(key)

        # Handler should return messages even for unowned sessions
        result = await sessions_get_handler(
            "req-1", {"session_key": key},
            "client-A", None, registry,
        )
        assert result["result"]["key"] == key
        assert len(result["result"]["messages"]) == 1
        assert result["result"]["messages"][0]["content"] == "old data"
        assert result["result"]["ownership"] == "unowned"

        # Verify it's still unowned (wasn't auto-claimed)
        assert sm.get_owner(key) is None
    finally:
        _cleanup_session(sm, key)


# ── session_handlers always pass client_id ──────────────────────────────────


@pytest.mark.asyncio
async def test_session_handlers_always_pass_client_id(fake_config, fake_provider, tmp_path):
    """Handlers pass client_id: unowned sessions get REQUIRES_CLAIM, not bypassed."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_delete_handler

    registry = ClientSessionRegistry()
    sm = _handler_sm()
    key = "_p29_test_always_client_id"

    try:
        s = sm.get_or_create(key)  # no client_id
        sm.save(s)
        sm.invalidate(key)

        # If the handler did NOT pass client_id, delete would succeed (bypass).
        # REQUIRES_CLAIM proves client_id was passed (ownership check triggered).
        with pytest.raises(AppServerError) as exc_info:
            await sessions_delete_handler(
                "req-1", {"session_key": key},
                "client-A", None, registry,
            )
        assert exc_info.value.code == "REQUIRES_CLAIM"
    finally:
        _cleanup_session(sm, key)


# ── AppServer handler signatures have client_id ─────────────────────────────


def test_appserver_handlers_signature_always_has_client_id():
    """All AppServer session handler functions accept client_id parameter."""
    import inspect

    from miqi.runtime.session_handlers import (
        sessions_archive_handler,
        sessions_claim_legacy_handler,
        sessions_clear_tracked_files_handler,
        sessions_delete_handler,
        sessions_get_handler,
        sessions_get_tracked_files_handler,
        sessions_list_archived_handler,
        sessions_list_handler,
        sessions_unarchive_handler,
    )

    handlers = [
        sessions_list_handler,
        sessions_get_handler,
        sessions_delete_handler,
        sessions_archive_handler,
        sessions_unarchive_handler,
        sessions_list_archived_handler,
        sessions_get_tracked_files_handler,
        sessions_clear_tracked_files_handler,
        sessions_claim_legacy_handler,
    ]

    for handler in handlers:
        sig = inspect.signature(handler)
        params = list(sig.parameters.keys())
        assert "client_id" in params, (
            f"{handler.__name__} is missing client_id parameter"
        )


# ── AgentLoop/process_direct audit ─────────────────────────────────────────


def test_no_agentloop_in_production_paths_phase29():
    """AgentLoop constructor and process_direct should not appear in
    runtime, bridge, cli, tui, channels, or cron modules.
    """
    miqi_dir = Path(__file__).parent.parent.parent / "miqi"

    dirs_to_check = ["runtime", "bridge", "cli", "tui", "channels", "cron"]
    for dirname in dirs_to_check:
        d = miqi_dir / dirname
        if not d.exists():
            continue
        for py_file in d.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            if "AgentLoop(" in content:
                pytest.fail(
                    f"AgentLoop( found in {py_file.relative_to(miqi_dir.parent)}"
                )
            if "process_direct(" in content:
                pytest.fail(
                    f"process_direct( found in {py_file.relative_to(miqi_dir.parent)}"
                )
