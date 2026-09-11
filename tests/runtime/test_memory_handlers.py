"""Tests for memory handlers — Phase 35.7."""

import pytest

from miqi.runtime.app_server import ClientSessionRegistry


@pytest.mark.asyncio
async def test_memory_list_returns_files():
    from miqi.runtime.memory_handlers import memory_list_handler
    registry = ClientSessionRegistry()
    result = await memory_list_handler("req-1", {}, "client-1", None, registry)
    assert "files" in result["result"]


@pytest.mark.asyncio
async def test_memory_get_requires_path():
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.memory_handlers import memory_get_handler
    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="path is required"):
        await memory_get_handler("req-1", {}, "client-1", None, registry)


@pytest.mark.asyncio
async def test_memory_update_requires_path():
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.memory_handlers import memory_update_handler
    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="path is required"):
        await memory_update_handler("req-1", {}, "client-1", None, registry)


@pytest.mark.asyncio
async def test_memory_delete_requires_path():
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.memory_handlers import memory_delete_handler
    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="path is required"):
        await memory_delete_handler("req-1", {}, "client-1", None, registry)


@pytest.mark.asyncio
async def test_memory_lessons_returns_lessons():
    from miqi.runtime.memory_handlers import memory_lessons_handler
    registry = ClientSessionRegistry()
    result = await memory_lessons_handler("req-1", {}, "client-1", None, registry)
    assert "lessons" in result["result"]


@pytest.mark.asyncio
async def test_memory_lesson_unlearn_requires_id():
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.memory_handlers import memory_lesson_unlearn_handler
    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="lesson_id is required"):
        await memory_lesson_unlearn_handler("req-1", {}, "client-1", None, registry)
