"""MCP handlers for AppServer dispatch.

Phase 35.4: Migrates mcp.list, mcp.upsert, and mcp.delete from bridge
legacy handlers to AppServer async handlers.

Phase 35 hardening: Uses get_bridge_state(registry) for DI instead of
importing miqi.bridge.server directly.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from miqi.runtime.app_server import AppServerError, get_bridge_state


async def mcp_list_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """List all configured MCP servers."""
    state = get_bridge_state(registry)
    config = state.load_config()
    servers = config.tools.mcp_servers or {}
    return {"result": {
        "servers": [
            {"name": name, **srv.model_dump()}
            for name, srv in servers.items()
        ],
    }}


async def mcp_upsert_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Create or update an MCP server entry by name."""
    from miqi.config.loader import save_config
    from miqi.config.schema import MCPServerConfig

    name = str(params.get("name", "")).strip()
    if not name:
        raise AppServerError("name is required", code="INVALID_PARAMS")

    # Build params dict without 'name' key for MCPServerConfig
    cfg_params = {k: v for k, v in params.items() if k != "name"}
    try:
        server_cfg = MCPServerConfig(**cfg_params)
    except Exception as exc:
        logger.warning("mcp.upsert: invalid config for name={}: {}", name, exc)
        raise AppServerError(
            "Invalid MCP server configuration — check required fields",
            code="INVALID_PARAMS",
        ) from exc

    state = get_bridge_state(registry)
    config = state.load_config()
    if config.tools.mcp_servers is None:
        config.tools.mcp_servers = {}
    config.tools.mcp_servers[name] = server_cfg
    save_config(config)
    state.config = config

    return {"result": {"ok": True}}


async def mcp_delete_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Remove an MCP server entry by name."""
    from miqi.config.loader import save_config

    name = str(params.get("name", "")).strip()

    state = get_bridge_state(registry)
    config = state.load_config()
    if config.tools.mcp_servers and name in config.tools.mcp_servers:
        del config.tools.mcp_servers[name]
        save_config(config)
        state.config = config

    return {"result": {"ok": True}}
