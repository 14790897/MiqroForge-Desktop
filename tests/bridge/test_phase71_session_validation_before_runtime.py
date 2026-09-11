from __future__ import annotations

import pytest

from miqi.runtime.app_server import AppServer, AppServerError, ClientSessionRegistry
from miqi.runtime.session_handlers import (
    sessions_archive_handler,
    sessions_claim_legacy_handler,
    sessions_delete_handler,
    sessions_get_handler,
    sessions_get_tracked_files_handler,
)


@pytest.mark.asyncio
async def test_sessions_get_empty_key_invalid_before_manager():
    server = AppServer(ClientSessionRegistry())
    server.register_method("sessions.get", sessions_get_handler)

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner("req", "sessions.get", {"sessionKey": ""}, "client", None)

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()


@pytest.mark.asyncio
async def test_sessions_delete_dotdot_key_invalid_before_sandbox():
    server = AppServer(ClientSessionRegistry())
    server.register_method("sessions.delete", sessions_delete_handler)

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner("req", "sessions.delete", {"sessionKey": "../bad"}, "client", None)

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()


@pytest.mark.asyncio
async def test_sessions_archive_slash_key_invalid():
    server = AppServer(ClientSessionRegistry())
    server.register_method("sessions.archive", sessions_archive_handler)

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner("req", "sessions.archive", {"sessionKey": "a/b"}, "client", None)

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()


@pytest.mark.asyncio
async def test_sessions_get_tracked_files_missing_key_invalid():
    server = AppServer(ClientSessionRegistry())
    server.register_method("sessions.get_tracked_files", sessions_get_tracked_files_handler)

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner("req", "sessions.get_tracked_files", {}, "client", None)

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()


@pytest.mark.asyncio
async def test_sessions_claim_legacy_non_string_key_invalid():
    server = AppServer(ClientSessionRegistry())
    server.register_method("sessions.claim_legacy", sessions_claim_legacy_handler)

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner("req", "sessions.claim_legacy", {"session_key": 123}, "client", None)

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()
