"""Tests for experience handlers — Phase 35.7."""

import pytest

from miqi.runtime.app_server import ClientSessionRegistry


@pytest.fixture(autouse=True)
def _close_experience_store_singleton():
    """Close the ExperienceStore singleton (TraceStore SQLite) after each test.

    The handler tests create an ExperienceStore via _get_experience_store()
    which opens a SQLite connection (TraceStore).  Without explicit cleanup
    the connection is garbage-collected and emits ResourceWarning.
    """
    yield
    import miqi.bridge.server as bridge_module

    store = getattr(bridge_module, "_experience_store", None)
    if store is not None and hasattr(store, "close"):
        store.close()
    bridge_module._experience_store = None


@pytest.mark.asyncio
async def test_experience_list_returns_entries():
    from miqi.runtime.experience_handlers import experience_list_handler
    registry = ClientSessionRegistry()
    result = await experience_list_handler("req-1", {}, "client-1", None, registry)
    assert "entries" in result["result"]


@pytest.mark.asyncio
async def test_experience_delete_requires_type_and_id():
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.experience_handlers import experience_delete_handler
    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="type and id are required"):
        await experience_delete_handler("req-1", {}, "client-1", None, registry)


@pytest.mark.asyncio
async def test_experience_search_returns_entries():
    from miqi.runtime.experience_handlers import experience_search_handler
    registry = ClientSessionRegistry()
    result = await experience_search_handler(
        "req-1", {"query": "test"}, "client-1", None, registry,
    )
    assert "entries" in result["result"]
