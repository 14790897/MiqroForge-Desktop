"""Tests for diagnostic handlers — Phase 35.8."""

import pytest

from miqi.runtime.app_server import ClientSessionRegistry


@pytest.mark.asyncio
async def test_python_check_returns_status():
    """python.check should return ok, python_version, and issues list."""
    from miqi.runtime.diagnostic_handlers import python_check_handler

    registry = ClientSessionRegistry()
    result = await python_check_handler("req-1", {}, "client-1", None, registry)
    data = result["result"]
    assert "ok" in data
    assert isinstance(data["ok"], bool)
    assert "python_version" in data
    assert "issues" in data
    assert isinstance(data["issues"], list)
    assert "config_exists" in data
