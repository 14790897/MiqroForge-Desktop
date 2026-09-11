"""Tests for MCP handlers — Phase 35.4 / hardening.

Validates mcp.list, mcp.upsert, mcp.delete migrated from bridge
legacy to AppServer async handlers.
"""

import tempfile

import pytest

from miqi.runtime.app_server import ClientSessionRegistry


def _make_config_with_workspace():
    from miqi.config.schema import Config
    config = Config()
    config.agents.defaults.workspace = tempfile.mkdtemp()
    return config


# ── mcp.list ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_list_returns_servers(registry_with_state):
    """mcp.list should return servers list from config."""
    from miqi.runtime.mcp_handlers import mcp_list_handler

    registry, mock_state = registry_with_state
    config = _make_config_with_workspace()
    mock_state.load_config.return_value = config

    result = await mcp_list_handler("req-1", {}, "client-1", None, registry)
    assert "servers" in result["result"]
    assert isinstance(result["result"]["servers"], list)


# ── mcp.upsert ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_upsert_requires_name():
    """mcp.upsert should reject empty name."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.mcp_handlers import mcp_upsert_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="name is required"):
        await mcp_upsert_handler(
            "req-1", {"command": "npx"}, "client-1", None, registry,
        )


# ── mcp.delete ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_delete_returns_ok(registry_with_state):
    """mcp.delete should return ok even for non-existent server (no-op)."""
    from miqi.runtime.mcp_handlers import mcp_delete_handler

    registry, mock_state = registry_with_state
    config = _make_config_with_workspace()
    mock_state.load_config.return_value = config

    result = await mcp_delete_handler(
        "req-1", {"name": "nonexistent"}, "client-1", None, registry,
    )
    assert result["result"]["ok"] is True
