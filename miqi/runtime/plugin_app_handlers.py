"""Codex-style plugin and marketplace AppServer handlers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import miqi.runtime.protocol_specs as protocol_specs
from miqi.runtime.app_server import AppServer, AppServerError, get_bridge_context
from miqi.runtime.plugin_catalog import PluginCatalogRuntime
from miqi.runtime.plugin_protocol import MarketplaceView
from miqi.runtime.plugin_skill_request_models import validate_plugin_skill_params

# ── marketplace validation helpers ────────────────────────────────────────

# Same safe-name pattern as plugins.
_MARKETPLACE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
# Hosts allowed for HTTPS marketplace sources.
_ALLOWED_SOURCE_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}
# GitHub shorthand: owner/repo with both parts matching safe slug rules.
_SHORTHAND_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")


def _validate_marketplace_name(name: str) -> str:
    """Validate a marketplace name. Returns the trimmed name on success,
    raises AppServerError on invalid input."""
    trimmed = name.strip()
    if not trimmed:
        raise AppServerError("Marketplace name is required", code="INVALID_PARAMS")
    if not _MARKETPLACE_NAME_RE.match(trimmed):
        raise AppServerError(
            f"Invalid marketplace name: {trimmed}", code="INVALID_PARAMS"
        )
    if ".." in trimmed:
        raise AppServerError(
            f"Invalid marketplace name: {trimmed}", code="INVALID_PARAMS"
        )
    return trimmed


def _validate_marketplace_source(source: str) -> None:
    """Raise AppServerError if source is not an allowed path, URL, or shorthand."""
    trimmed = source.strip()
    if not trimmed:
        raise AppServerError("Marketplace source is required", code="INVALID_PARAMS")

    # 1) Existing local directory path, resolved safely.
    path = Path(trimmed).resolve()
    if path.is_dir():
        return

    # 2) HTTPS URL with allowed host and no credentials.
    parsed = urlparse(trimmed)
    if parsed.scheme == "https":
        if parsed.hostname not in _ALLOWED_SOURCE_HOSTS:
            raise AppServerError(
                f"Unsupported marketplace host: {parsed.hostname}",
                code="INVALID_PARAMS",
            )
        if "@" in parsed.netloc:
            raise AppServerError(
                "Credentials in marketplace URL are not allowed",
                code="INVALID_PARAMS",
            )
        return

    # 3) GitHub shorthand owner/repo.
    if _SHORTHAND_RE.match(trimmed):
        # owner/repo is valid shorthand; currently no-op (Phase 37 only validates).
        return

    raise AppServerError(
        f"Unsupported marketplace source: {trimmed}", code="INVALID_PARAMS"
    )


def _catalog(registry: Any) -> PluginCatalogRuntime:
    pm = get_bridge_context(registry, "plugin_manager", None)
    if pm is None:
        raise AppServerError("Plugin manager not initialized", code="INTERNAL")
    from miqi.paths import get_miqi_home
    marketplaces_dir = Path(get_bridge_context(registry, "marketplaces_dir", get_miqi_home() / "marketplaces"))
    catalog = get_bridge_context(registry, "plugin_catalog", None)
    if catalog is None:
        catalog = PluginCatalogRuntime(plugin_manager=pm, marketplaces_dir=marketplaces_dir)
        registry.bridge_context["plugin_catalog"] = catalog
    return catalog


def register_plugin_app_handlers(server: AppServer) -> None:
    async def _plugin_list(request_id, params, client_id, session_id, registry):
        validate_plugin_skill_params("plugin/list", params)
        catalog = _catalog(registry)
        return {"result": {
            "marketplaces": [m.to_dict() for m in catalog.list_marketplaces()],
            "plugins": [p.to_dict() for p in catalog.list_plugins()],
            "featuredPluginIds": [],
            "marketplaceLoadErrors": [],
        }}

    async def _plugin_installed(request_id, params, client_id, session_id, registry):
        validate_plugin_skill_params("plugin/installed", params)
        catalog = _catalog(registry)
        return {"result": {
            "plugins": [p.to_dict() for p in catalog.list_installed()],
        }}

    async def _plugin_read(request_id, params, client_id, session_id, registry):
        typed = validate_plugin_skill_params("plugin/read", params)
        catalog = _catalog(registry)
        try:
            detail = catalog.read_plugin(
                plugin_name=typed.plugin_name,
                marketplace_name=typed.marketplace_name or "local",
            )
        except KeyError as exc:
            raise AppServerError("Plugin not found", code="NOT_FOUND") from exc
        return {"result": {"plugin": detail.to_dict()}}

    async def _plugin_skill_read(request_id, params, client_id, session_id, registry):
        typed = validate_plugin_skill_params("plugin/skill/read", params)
        catalog = _catalog(registry)
        try:
            content = catalog.read_plugin_skill(
                plugin_name=typed.plugin_name,
                marketplace_name=typed.marketplace_name or "local",
                skill_name=typed.skill_name,
            )
        except (KeyError, ValueError) as exc:
            raise AppServerError("Plugin skill not found", code="NOT_FOUND") from exc
        return {"result": {"content": content}}

    async def _plugin_install(request_id, params, client_id, session_id, registry):
        typed = validate_plugin_skill_params("plugin/install", params)
        pm = get_bridge_context(registry, "plugin_manager", None)
        if pm is None:
            raise AppServerError("Plugin manager not initialized", code="INTERNAL")
        name = typed.plugin_name
        source = typed.source
        try:
            plugin = pm.install_plugin(str(name).split("@")[0], str(source))
        except ValueError as exc:
            raise AppServerError("Plugin installation rejected", code="INVALID_PARAMS") from exc
        registry.bridge_context.pop("plugin_catalog", None)
        return {"result": {"plugin": {"pluginId": f"{plugin.manifest.name}@local", "name": plugin.manifest.name}}}

    async def _plugin_uninstall(request_id, params, client_id, session_id, registry):
        typed = validate_plugin_skill_params("plugin/uninstall", params)
        pm = get_bridge_context(registry, "plugin_manager", None)
        if pm is None:
            raise AppServerError("Plugin manager not initialized", code="INTERNAL")
        raw = typed.plugin_id
        name = str(raw).split("@")[0]
        try:
            removed = pm.uninstall_plugin(name)
        except ValueError as exc:
            raise AppServerError("Plugin uninstall rejected", code="INVALID_PARAMS") from exc
        registry.bridge_context.pop("plugin_catalog", None)
        return {"result": {"removed": bool(removed), "pluginId": raw}}

    async def _marketplace_add(request_id, params, client_id, session_id, registry):
        typed = validate_plugin_skill_params("marketplace/add", params)
        catalog = _catalog(registry)
        name = _validate_marketplace_name(typed.name)
        _validate_marketplace_source(typed.source)
        view = MarketplaceView(
            name=name,
            display_name=name.replace("-", " ").title(),
            source=typed.source,
            path=None,
            load_errors=[],
        )
        catalog._marketplaces[name] = view
        return {"result": {"marketplace": view.to_dict(), "alreadyPresent": False}}

    async def _marketplace_remove(request_id, params, client_id, session_id, registry):
        typed = validate_plugin_skill_params("marketplace/remove", params)
        catalog = _catalog(registry)
        name = _validate_marketplace_name(typed.name)
        if name == "local":
            raise AppServerError(
                "The 'local' marketplace cannot be removed", code="INVALID_PARAMS"
            )
        removed = name in catalog._marketplaces
        catalog._marketplaces.pop(name, None)
        return {"result": {"removed": removed, "marketplaceName": name}}

    async def _marketplace_upgrade(request_id, params, client_id, session_id, registry):
        typed = validate_plugin_skill_params("marketplace/upgrade", params)
        catalog = _catalog(registry)
        name = typed.marketplace_name
        if name is not None:
            name = _validate_marketplace_name(name)
        selected = [name] if name else [m.name for m in catalog.list_marketplaces()]
        return {"result": {"selectedMarketplaces": selected, "errors": []}}

    server.register_method("plugin/list", _plugin_list, spec=protocol_specs.PLUGIN_LIST)
    server.register_method("plugin/installed", _plugin_installed, spec=protocol_specs.PLUGIN_INSTALLED)
    server.register_method("plugin/read", _plugin_read, spec=protocol_specs.PLUGIN_READ)
    server.register_method("plugin/skill/read", _plugin_skill_read, spec=protocol_specs.PLUGIN_SKILL_READ)
    server.register_method("plugin/install", _plugin_install, spec=protocol_specs.PLUGIN_INSTALL)
    server.register_method("plugin/uninstall", _plugin_uninstall, spec=protocol_specs.PLUGIN_UNINSTALL)
    server.register_method("marketplace/add", _marketplace_add, spec=protocol_specs.MARKETPLACE_ADD)
    server.register_method("marketplace/remove", _marketplace_remove, spec=protocol_specs.MARKETPLACE_REMOVE)
    server.register_method("marketplace/upgrade", _marketplace_upgrade, spec=protocol_specs.MARKETPLACE_UPGRADE)
