from __future__ import annotations

import pytest

from miqi.runtime.app_server import AppServer, AppServerError, ClientSessionRegistry
from miqi.runtime.thread_app_handlers import register_codex_thread_handlers


@pytest.mark.asyncio
async def test_thread_resume_missing_thread_id_returns_invalid():
    server = AppServer(ClientSessionRegistry())
    register_codex_thread_handlers(server)
    server.registry.bridge_context = {}

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner("req", "thread/resume", {}, "client", None)

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()


@pytest.mark.asyncio
async def test_thread_read_non_string_thread_id_returns_invalid():
    server = AppServer(ClientSessionRegistry())
    register_codex_thread_handlers(server)
    server.registry.bridge_context = {}

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner("req", "thread/read", {"threadId": 123}, "client", None)

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()


@pytest.mark.asyncio
async def test_thread_turns_list_string_limit_returns_invalid():
    server = AppServer(ClientSessionRegistry())
    register_codex_thread_handlers(server)
    server.registry.bridge_context = {}

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner(
            "req", "thread/turns/list", {"threadId": "t1", "limit": "50"}, "client", None,
        )

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()


@pytest.mark.asyncio
async def test_thread_import_bad_document_returns_invalid():
    server = AppServer(ClientSessionRegistry())
    register_codex_thread_handlers(server)
    server.registry.bridge_context = {}

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner("req", "thread/import", {"document": "bad"}, "client", None)

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()


@pytest.mark.asyncio
async def test_thread_rollback_zero_drop_returns_invalid():
    server = AppServer(ClientSessionRegistry())
    register_codex_thread_handlers(server)
    server.registry.bridge_context = {}

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner(
            "req", "thread/rollback",
            {"threadId": "t1", "dropLastTurns": 0}, "client", None,
        )

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()


@pytest.mark.asyncio
async def test_thread_turns_items_list_returns_unsupported():
    """Valid params pass validation; handler still returns UNSUPPORTED_METHOD."""
    server = AppServer(ClientSessionRegistry())
    register_codex_thread_handlers(server)

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner(
            "req", "thread/turns/items/list",
            {"threadId": "t1"}, "client", None,
        )

    assert exc.value.code == "UNSUPPORTED_METHOD"
    await server.stop()
