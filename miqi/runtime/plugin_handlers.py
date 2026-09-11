"""Plugin handlers for AppServer dispatch.

Phase 35.3: Migrates plugins.list, plugins.install, plugins.uninstall,
and plugins.toggle from bridge legacy handlers to AppServer async handlers.
All lifecycle operations delegate to PluginManager.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from miqi.runtime.app_server import AppServerError, get_bridge_context, get_bridge_state


def _get_plugin_manager(registry: Any) -> Any:
    """Get the PluginManager from registry.bridge_context.

    Returns None if bridge state or plugin manager is not available.
    """
    return get_bridge_context(registry, "plugin_manager", None)


async def plugins_list_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """List installed plugins with status, MCP servers, skills, commands."""
    pm = _get_plugin_manager(registry)
    if pm is None:
        return {"result": {"plugins": []}}

    plugins = []
    for p in pm.list_plugins():
        plugins.append({
            "name": p.manifest.name,
            "version": p.manifest.version,
            "description": p.manifest.description,
            "author": p.manifest.author,
            "scope": p.scope,
            "status": p.status,
            "error": p.error,
            "mcp_servers": p.manifest.mcp_servers,
            "skills": p.manifest.skills,
            "slash_commands": p.manifest.slash_commands,
        })

    return {"result": {"plugins": plugins}}


async def plugins_install_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Install a plugin from a GitHub URL.

    Delegates to PluginManager.install_plugin() which validates name,
    clones the repo, and discovers the new plugin.
    """
    name = params.get("name", "")
    url = params.get("url", "")
    pm = _get_plugin_manager(registry)
    if pm is None:
        raise AppServerError(
            "Plugin manager not initialized", code="INTERNAL",
        )

    if not url:
        raise AppServerError(
            "url is required for plugin installation", code="INVALID_PARAMS",
        )

    try:
        pm.install_plugin(name, url)
        # Update MCP servers from newly installed plugin
        new_servers = pm.get_mcp_servers()
        if new_servers:
            state = get_bridge_state(registry)
            existing = getattr(state, "_mcp_servers", None)
            if existing is not None:
                existing.update({s.get("name", ""): s for s in new_servers})
        return {"result": {"ok": True, "name": name}}
    except ValueError as exc:
        logger.warning("plugins.install: name={} validation error: {}", name, exc)
        raise AppServerError(
            "Invalid plugin name or URL", code="INVALID_PARAMS",
        ) from exc
    except Exception as exc:
        logger.warning("plugins.install: name={} error: {}", name, exc)
        raise AppServerError(
            "Plugin installation failed", code="INTERNAL",
        ) from exc


async def plugins_uninstall_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Uninstall a plugin by name.

    Delegates to PluginManager.uninstall_plugin().
    """
    name = params.get("name", "")
    pm = _get_plugin_manager(registry)
    if pm is None:
        raise AppServerError(
            "Plugin manager not initialized", code="INTERNAL",
        )

    try:
        removed = pm.uninstall_plugin(name)
        if not removed:
            raise AppServerError(
                f"Plugin '{name}' not found", code="NOT_FOUND",
            )
        return {"result": {"ok": True, "name": name}}
    except ValueError as exc:
        logger.warning("plugins.uninstall: name={} error: {}", name, exc)
        raise AppServerError(
            "Invalid plugin name", code="INVALID_PARAMS",
        ) from exc


async def plugins_toggle_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Toggle a plugin enabled/disabled.

    Delegates to PluginManager.toggle_plugin().
    """
    name = params.get("name", "")
    enabled = params.get("enabled", False)
    pm = _get_plugin_manager(registry)
    if pm is None:
        raise AppServerError(
            "Plugin manager not initialized", code="INTERNAL",
        )

    try:
        plugin = pm.toggle_plugin(name, enabled)
        return {"result": {
            "ok": True,
            "name": name,
            "enabled": plugin.status == "active",
        }}
    except ValueError as exc:
        logger.warning("plugins.toggle: name={} error: {}", name, exc)
        raise AppServerError(
            "Plugin not found or invalid state", code="NOT_FOUND",
        ) from exc
