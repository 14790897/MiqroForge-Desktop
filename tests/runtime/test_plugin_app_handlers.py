import json
from pathlib import Path

import pytest

from miqi.runtime.app_server import AppServer, ClientSessionRegistry
from miqi.runtime.plugin_app_handlers import register_plugin_app_handlers
from miqi.skills.plugin_manager import PluginManager


def write_plugin(root: Path, name: str) -> None:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({
        "name": name,
        "version": "1.0.0",
        "description": "Sample plugin",
        "author": "test",
        "mcp_servers": [{"name": "sample-mcp", "command": "echo"}],
        "skills": ["sample-skill"],
        "slash_commands": [],
    }), encoding="utf-8")
    skill_dir = plugin_dir / "skills" / "sample-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: sample-skill\n---\n# Sample\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_plugin_list_and_installed(tmp_path):
    user_dir = tmp_path / "plugins"
    write_plugin(user_dir, "sample")
    pm = PluginManager(user_dir, tmp_path / "system")
    await pm.discover()
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    registry.bridge_context["marketplaces_dir"] = tmp_path / "marketplaces"
    server = AppServer(registry)
    register_plugin_app_handlers(server)

    listed = await server.dispatch("1", "plugin/list", {}, "client-1", None)
    installed = await server.dispatch("2", "plugin/installed", {}, "client-1", None)
    assert listed["result"]["marketplaces"][0]["name"] == "local"
    assert listed["result"]["plugins"][0]["pluginId"] == "sample@local"
    assert installed["result"]["plugins"][0]["mention"] == "plugin://sample@local"


@pytest.mark.asyncio
async def test_plugin_read_and_skill_read(tmp_path):
    user_dir = tmp_path / "plugins"
    write_plugin(user_dir, "sample")
    pm = PluginManager(user_dir, tmp_path / "system")
    await pm.discover()
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    registry.bridge_context["marketplaces_dir"] = tmp_path / "marketplaces"
    server = AppServer(registry)
    register_plugin_app_handlers(server)

    detail = await server.dispatch(
        "1", "plugin/read",
        {"pluginName": "sample", "marketplaceName": "local"},
        "client-1", None,
    )
    skill = await server.dispatch(
        "2", "plugin/skill/read",
        {"pluginName": "sample", "marketplaceName": "local", "skillName": "sample-skill"},
        "client-1", None,
    )
    assert detail["result"]["plugin"]["pluginId"] == "sample@local"
    assert "# Sample" in skill["result"]["content"]


@pytest.mark.asyncio
async def test_marketplace_add_remove_upgrade_return_stable_shapes(tmp_path):
    pm = PluginManager(tmp_path / "plugins", tmp_path / "system")
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    registry.bridge_context["marketplaces_dir"] = tmp_path / "marketplaces"
    server = AppServer(registry)
    register_plugin_app_handlers(server)

    source_dir = tmp_path / "marketplace-source"
    source_dir.mkdir()
    added = await server.dispatch(
        "1", "marketplace/add",
        {"name": "local-debug", "source": str(source_dir)},
        "client-1", None,
    )
    upgraded = await server.dispatch("2", "marketplace/upgrade", {}, "client-1", None)
    removed = await server.dispatch(
        "3", "marketplace/remove", {"name": "local-debug"}, "client-1", None,
    )
    assert added["result"]["marketplace"]["name"] == "local-debug"
    assert "selectedMarketplaces" in upgraded["result"]
    assert removed["result"]["removed"] is True


@pytest.mark.asyncio
async def test_plugin_read_not_found_is_sanitized(tmp_path):
    pm = PluginManager(tmp_path / "plugins", tmp_path / "system")
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    registry.bridge_context["marketplaces_dir"] = tmp_path / "marketplaces"
    server = AppServer(registry)
    register_plugin_app_handlers(server)
    response = await server.dispatch(
        "1", "plugin/read",
        {"pluginName": "../secret", "marketplaceName": "local"},
        "client-1", None,
    )
    assert response["code"] == "NOT_FOUND"
    assert "../secret" not in response["error"]


# ---------------------------------------------------------------------------
# Phase 37 Hardening: marketplace validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marketplace_add_rejects_invalid_name(tmp_path):
    pm = PluginManager(tmp_path / "plugins", tmp_path / "system")
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    registry.bridge_context["marketplaces_dir"] = tmp_path / "marketplaces"
    server = AppServer(registry)
    register_plugin_app_handlers(server)

    for bad_name in ["../escape", "bad/name", "-starts-dash", ""]:
        response = await server.dispatch(
            "1", "marketplace/add",
            {"name": bad_name, "source": "owner/repo"},
            "client-1", None,
        )
        assert response.get("code") == "INVALID_PARAMS", f"'{bad_name}' should be rejected"
        assert "invalid" in response.get("error", "").lower() or "is required" in response.get("error", "")


@pytest.mark.asyncio
async def test_marketplace_add_rejects_unsupported_source(tmp_path):
    pm = PluginManager(tmp_path / "plugins", tmp_path / "system")
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    registry.bridge_context["marketplaces_dir"] = tmp_path / "marketplaces"
    server = AppServer(registry)
    register_plugin_app_handlers(server)

    bad_sources = [
        ("http://github.com/org/repo.git", "http"),
        ("https://evil.com/repo.git", "unsupported host"),
        ("https://user:pass@github.com/org/repo.git", "credentials"),
    ]
    for source, _label in bad_sources:
        response = await server.dispatch(
            "1", "marketplace/add",
            {"name": "test-mp", "source": source},
            "client-1", None,
        )
        assert response.get("code") == "INVALID_PARAMS", f"'{source}' should be rejected"


@pytest.mark.asyncio
async def test_marketplace_add_accepts_https_allowed_host(tmp_path):
    pm = PluginManager(tmp_path / "plugins", tmp_path / "system")
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    registry.bridge_context["marketplaces_dir"] = tmp_path / "marketplaces"
    server = AppServer(registry)
    register_plugin_app_handlers(server)

    for source in [
        "https://github.com/org/repo.git",
        "https://gitlab.com/user/project",
        "https://bitbucket.org/team/repo",
    ]:
        mp_name = f"test-{source.split('://')[1].split('/')[0][:8]}"
        response = await server.dispatch(
            "1", "marketplace/add",
            {"name": mp_name, "source": source},
            "client-1", None,
        )
        assert "result" in response, f"HTTPS source '{source}' should be accepted"
        assert response["result"]["marketplace"]["name"] == mp_name


@pytest.mark.asyncio
async def test_marketplace_add_accepts_existing_local_dir(tmp_path):
    pm = PluginManager(tmp_path / "plugins", tmp_path / "system")
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    registry.bridge_context["marketplaces_dir"] = tmp_path / "marketplaces"
    server = AppServer(registry)
    register_plugin_app_handlers(server)

    # Local dir must exist and be within cwd-relative scope for test.
    local_marketplace = tmp_path / "my-marketplace"
    local_marketplace.mkdir()
    response = await server.dispatch(
        "1", "marketplace/add",
        {"name": "test-local-mp", "source": str(local_marketplace.resolve())},
        "client-1", None,
    )
    assert "result" in response
    assert response["result"]["marketplace"]["name"] == "test-local-mp"


@pytest.mark.asyncio
async def test_marketplace_remove_cannot_remove_local(tmp_path):
    pm = PluginManager(tmp_path / "plugins", tmp_path / "system")
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    registry.bridge_context["marketplaces_dir"] = tmp_path / "marketplaces"
    server = AppServer(registry)
    register_plugin_app_handlers(server)

    response = await server.dispatch(
        "1", "marketplace/remove", {"name": "local"}, "client-1", None,
    )
    assert response.get("code") == "INVALID_PARAMS"
    assert "local" in response.get("error", "").lower()


@pytest.mark.asyncio
async def test_marketplace_remove_rejects_path_like_name(tmp_path):
    pm = PluginManager(tmp_path / "plugins", tmp_path / "system")
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    registry.bridge_context["marketplaces_dir"] = tmp_path / "marketplaces"
    server = AppServer(registry)
    register_plugin_app_handlers(server)

    for bad_name in ["../escape", "path/traversal"]:
        response = await server.dispatch(
            "1", "marketplace/remove", {"name": bad_name}, "client-1", None,
        )
        assert response.get("code") == "INVALID_PARAMS", f"'{bad_name}' should be rejected"


# ---------------------------------------------------------------------------
# Phase 37 Hardening: control-plane error-path hardening
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plugin_skill_read_missing_skill_name_rejected(tmp_path):
    """plugin/skill/read without skillName returns INVALID_PARAMS, not INTERNAL."""
    pm = PluginManager(tmp_path / "plugins", tmp_path / "system")
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    registry.bridge_context["marketplaces_dir"] = tmp_path / "marketplaces"
    server = AppServer(registry)
    register_plugin_app_handlers(server)

    response = await server.dispatch(
        "1", "plugin/skill/read",
        {"pluginName": "sample", "marketplaceName": "local"},
        "client-1", None,
    )
    assert response.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_plugin_skill_read_missing_plugin_name_rejected(tmp_path):
    """plugin/skill/read without pluginName returns INVALID_PARAMS, not INTERNAL."""
    pm = PluginManager(tmp_path / "plugins", tmp_path / "system")
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    registry.bridge_context["marketplaces_dir"] = tmp_path / "marketplaces"
    server = AppServer(registry)
    register_plugin_app_handlers(server)

    response = await server.dispatch(
        "1", "plugin/skill/read",
        {"skillName": "sample-skill", "marketplaceName": "local"},
        "client-1", None,
    )
    assert response.get("code") == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_plugin_uninstall_invalid_name_rejected(tmp_path):
    """plugin/uninstall with invalid pluginId (e.g. path traversal) returns
    INVALID_PARAMS, not INTERNAL."""
    pm = PluginManager(tmp_path / "plugins", tmp_path / "system")
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    server = AppServer(registry)
    register_plugin_app_handlers(server)

    response = await server.dispatch(
        "1", "plugin/uninstall",
        {"pluginId": "../bad"},
        "client-1", None,
    )
    assert response.get("code") == "INVALID_PARAMS"
    # The error message should be safe — not leaking the raw traversal string
    # as the primary signal.
    assert "INTERNAL" not in response.get("code", "")


@pytest.mark.asyncio
async def test_marketplace_upgrade_rejects_invalid_name(tmp_path):
    """marketplace/upgrade with an invalid marketplaceName returns
    INVALID_PARAMS, not success."""
    pm = PluginManager(tmp_path / "plugins", tmp_path / "system")
    registry = ClientSessionRegistry()
    registry.bridge_context["plugin_manager"] = pm
    registry.bridge_context["marketplaces_dir"] = tmp_path / "marketplaces"
    server = AppServer(registry)
    register_plugin_app_handlers(server)

    for bad_name in ["../bad", "path/traversal", "-starts-dash"]:
        response = await server.dispatch(
            "1", "marketplace/upgrade",
            {"marketplaceName": bad_name},
            "client-1", None,
        )
        assert response.get("code") == "INVALID_PARAMS", (
            f"marketplaceName '{bad_name}' should be rejected"
        )
