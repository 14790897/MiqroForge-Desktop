"""Tests for plugin handlers — Phase 35.3.

Validates plugins.list, plugins.install, plugins.uninstall,
and plugins.toggle migrated from bridge legacy to AppServer.
"""

import pytest

from miqi.runtime.app_server import ClientSessionRegistry

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_fake_plugin_manager(with_plugins=True):
    """Create a minimal fake PluginManager for testing handlers."""
    from unittest.mock import MagicMock

    pm = MagicMock()

    if with_plugins:
        # Fake a loaded plugin
        manifest = MagicMock()
        manifest.name = "test-plugin"
        manifest.version = "1.0.0"
        manifest.description = "A test plugin"
        manifest.author = "test-author"
        manifest.mcp_servers = []
        manifest.skills = []
        manifest.slash_commands = []

        plugin = MagicMock()
        plugin.manifest = manifest
        plugin.scope = "user"
        plugin.status = "active"
        plugin.error = None
        pm.list_plugins.return_value = [plugin]

    return pm


# ── plugins.list ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plugins_list_returns_empty_when_no_manager():
    """plugins.list returns empty plugins when PluginManager not initialized."""
    from miqi.runtime.plugin_handlers import plugins_list_handler

    registry = ClientSessionRegistry()
    result = await plugins_list_handler("req-1", {}, "client-1", None, registry)
    assert result["result"]["plugins"] == []


# ── plugins.install ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plugins_install_requires_url():
    """plugins.install should reject missing URL."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.plugin_handlers import plugins_install_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="Plugin manager not initialized"):
        await plugins_install_handler(
            "req-1", {"name": "test", "url": "https://github.com/a/b"},
            "client-1", None, registry,
        )


@pytest.mark.asyncio
async def test_plugins_install_no_url(registry_with_state):
    """plugins.install should reject request without url."""
    pm = _make_fake_plugin_manager(with_plugins=True)

    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.plugin_handlers import plugins_install_handler

    registry, mock_state = registry_with_state
    # Inject plugin manager via bridge_context (Phase 35 hardening DI)
    registry.bridge_context["plugin_manager"] = pm

    with pytest.raises(AppServerError, match="url is required"):
        await plugins_install_handler(
            "req-1", {"name": "test"}, "client-1", None, registry,
        )


# ── plugins.uninstall ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plugins_uninstall_no_manager():
    """plugins.uninstall should raise when PluginManager not initialized."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.plugin_handlers import plugins_uninstall_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="Plugin manager not initialized"):
        await plugins_uninstall_handler(
            "req-1", {"name": "test"}, "client-1", None, registry,
        )


# ── plugins.toggle ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plugins_toggle_no_manager():
    """plugins.toggle should raise when PluginManager not initialized."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.plugin_handlers import plugins_toggle_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="Plugin manager not initialized"):
        await plugins_toggle_handler(
            "req-1", {"name": "test", "enabled": True},
            "client-1", None, registry,
        )
