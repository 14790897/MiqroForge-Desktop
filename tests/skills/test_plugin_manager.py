"""Tests for miqi.skills.plugin_manager — plugin lifecycle (Phase 10).

Tests: discovery, toggle, invalid names, traversal rejection.
No network dependencies — uses local temp directories.
"""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest


def _make_plugin_dir(parent: Path, name: str, manifest: dict) -> Path:
    """Create a minimal plugin directory with plugin.json."""
    plugin_dir = parent / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = plugin_dir / "plugin.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return plugin_dir


# ---------------------------------------------------------------------------
# Test 1: PluginManager discovers a local plugin with plugin.json
# ---------------------------------------------------------------------------

def test_plugin_manager_discovers_local_plugin():
    """PluginManager discovers plugins from configured directories."""
    from miqi.skills.plugin_manager import PluginManager

    with tempfile.TemporaryDirectory() as tmp:
        user_dir = Path(tmp) / "user"
        user_dir.mkdir()
        system_dir = Path(tmp) / "system"
        system_dir.mkdir()

        _make_plugin_dir(
            user_dir, "my-plugin",
            {
                "name": "my-plugin",
                "version": "1.0.0",
                "description": "A test plugin",
                "author": "tester",
                "mcp_servers": [],
                "skills": [],
                "slash_commands": [],
                "dependencies": [],
            },
        )

        pm = PluginManager(
            user_plugins_dir=user_dir,
            system_plugins_dir=system_dir,
        )

        discovered = asyncio.run(pm.discover())
        assert len(discovered) == 1
        assert discovered[0].manifest.name == "my-plugin"
        assert discovered[0].status == "active"
        assert discovered[0].scope == "user"


# ---------------------------------------------------------------------------
# Test 2: Toggle changes plugin status active/disabled
# ---------------------------------------------------------------------------

def test_plugin_toggle_changes_status():
    """Toggling a plugin via toggle_plugin changes its status."""
    from miqi.skills.plugin_manager import PluginManager

    with tempfile.TemporaryDirectory() as tmp:
        user_dir = Path(tmp) / "user"
        user_dir.mkdir()
        system_dir = Path(tmp) / "system"
        system_dir.mkdir()

        _make_plugin_dir(
            user_dir, "toggle-test",
            {
                "name": "toggle-test",
                "version": "1.0.0",
                "description": "Toggle test plugin",
                "mcp_servers": [],
                "skills": [],
                "slash_commands": [],
                "dependencies": [],
            },
        )

        pm = PluginManager(
            user_plugins_dir=user_dir,
            system_plugins_dir=system_dir,
        )
        asyncio.run(pm.discover())

        assert pm._plugins["toggle-test"].status == "active"

        pm.toggle_plugin("toggle-test", enabled=False)
        assert pm._plugins["toggle-test"].status == "disabled"

        pm.toggle_plugin("toggle-test", enabled=True)
        assert pm._plugins["toggle-test"].status == "active"


# ---------------------------------------------------------------------------
# Test 3: Invalid plugin names are rejected (real validate_plugin_name)
# ---------------------------------------------------------------------------

def test_invalid_plugin_names_rejected():
    """Invalid names raise ValueError from the production validator."""
    from miqi.skills.plugin_manager import validate_plugin_name

    valid_names = ["my-plugin", "hello_world", "test.tool", "a", "MyPlugin"]
    invalid_names = [
        "../escape",       # traversal
        "plugin/escape",   # path separator
        "-start-dash",     # starts with dash
        ".dot-start",      # starts with dot
        "",                # empty
        "a" * 65,          # too long
        "rm -rf",          # spaces
    ]

    for name in valid_names:
        validate_plugin_name(name)  # must not raise

    for name in invalid_names:
        with pytest.raises(ValueError, match="Invalid plugin manifest name"):
            validate_plugin_name(name)


# ---------------------------------------------------------------------------
# Test 4: Uninstall refuses traversal paths and removes real plugin dirs
# ---------------------------------------------------------------------------

def test_uninstall_plugin_removes_installed_plugin():
    """uninstall_plugin removes the plugin directory and unregisters it."""

    from miqi.skills.plugin_manager import PluginManager

    with tempfile.TemporaryDirectory() as tmp:
        user_dir = Path(tmp) / "user"
        user_dir.mkdir()
        system_dir = Path(tmp) / "system"
        system_dir.mkdir()

        _make_plugin_dir(
            user_dir, "remove-me",
            {
                "name": "remove-me",
                "version": "1.0.0",
                "description": "Plugin to remove",
                "mcp_servers": [],
                "skills": [],
                "slash_commands": [],
                "dependencies": [],
            },
        )

        pm = PluginManager(
            user_plugins_dir=user_dir,
            system_plugins_dir=system_dir,
        )
        asyncio.run(pm.discover())
        assert pm.get_plugin("remove-me") is not None

        assert pm.uninstall_plugin("remove-me") is True
        assert not (user_dir / "remove-me").exists(), "Plugin dir must be removed"
        assert pm.get_plugin("remove-me") is None


def test_uninstall_plugin_unknown_returns_false():
    """Uninstalling an unknown plugin returns False."""
    from miqi.skills.plugin_manager import PluginManager

    with tempfile.TemporaryDirectory() as tmp:
        user_dir = Path(tmp) / "user"
        system_dir = Path(tmp) / "system"
        pm = PluginManager(
            user_plugins_dir=user_dir,
            system_plugins_dir=system_dir,
        )
        assert pm.uninstall_plugin("not-installed") is False


def test_uninstall_plugin_rejects_traversal_name(tmp_path, monkeypatch):
    """Traversal names are rejected by the real validator before any IO."""
    import shutil

    from miqi.skills.plugin_manager import PluginManager

    # tmp_path (not TemporaryDirectory): its cleanup also goes through
    # shutil.rmtree and must run AFTER monkeypatch restores the attribute.
    pm = PluginManager(
        user_plugins_dir=tmp_path / "user",
        system_plugins_dir=tmp_path / "system",
    )

    # uninstall_plugin imports shutil inside the function — patching the
    # module attribute catches any destructive filesystem access.  If a
    # regression lets a traversal name past validation, this raises.
    def _boom(*args, **kwargs):
        raise AssertionError("uninstall_plugin must validate before filesystem access")

    monkeypatch.setattr(shutil, "rmtree", _boom)

    for bad_name in ["../escape", "..", "a/../b"]:
        with pytest.raises(ValueError, match="Invalid plugin manifest name"):
            pm.uninstall_plugin(bad_name)


# ---------------------------------------------------------------------------
# MCP server collection from active plugins
# ---------------------------------------------------------------------------

def test_get_mcp_servers_only_returns_active():
    """MCP servers from disabled plugins are excluded."""
    from miqi.skills.plugin_manager import PluginManager

    with tempfile.TemporaryDirectory() as tmp:
        user_dir = Path(tmp) / "user"
        user_dir.mkdir()
        system_dir = Path(tmp) / "system"
        system_dir.mkdir()

        _make_plugin_dir(
            user_dir, "server-plugin",
            {
                "name": "server-plugin",
                "version": "1.0.0",
                "description": "Plugin with servers",
                "mcp_servers": [
                    {"name": "test-server", "command": "echo", "args": ["hello"]},
                ],
                "skills": [],
                "slash_commands": [],
                "dependencies": [],
            },
        )

        pm = PluginManager(
            user_plugins_dir=user_dir,
            system_plugins_dir=system_dir,
        )
        asyncio.run(pm.discover())

        # Active plugin exposes servers
        servers = pm.get_mcp_servers()
        assert len(servers) == 1
        assert servers[0]["name"] == "test-server"

        # Disable it — servers should disappear
        pm._plugins["server-plugin"].status = "disabled"
        servers = pm.get_mcp_servers()
        assert len(servers) == 0


# ---------------------------------------------------------------------------
# Test: await discover() in async context — no RuntimeWarning
# ---------------------------------------------------------------------------

def test_await_discover_in_async_context_no_warning():
    """Bridge pattern: asyncio.run() wraps an async fn that awaits discover().
    Must NOT produce 'coroutine was never awaited' RuntimeWarning.
    """
    import asyncio
    import warnings
    from pathlib import Path

    from miqi.skills.plugin_manager import PluginManager

    with tempfile.TemporaryDirectory() as tmp:
        user_dir = Path(tmp) / "user"
        user_dir.mkdir()
        system_dir = Path(tmp) / "system"
        system_dir.mkdir()

        async def _bridge_init():
            pm = PluginManager(
                user_plugins_dir=user_dir,
                system_plugins_dir=system_dir,
            )
            return await pm.discover()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = asyncio.run(_bridge_init())

        runtime_warnings = [
            x for x in w
            if "never awaited" in str(x.message)
        ]
        assert len(runtime_warnings) == 0, (
            f"RuntimeWarning: {[str(x.message) for x in runtime_warnings]}"
        )
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Test: install_plugin is deterministic — no background ensure_future
# ---------------------------------------------------------------------------

def test_install_plugin_does_not_schedule_background_discover(tmp_path, monkeypatch):
    import subprocess

    from miqi.skills.plugin_manager import PluginManager

    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "plugin.json").write_text(
        '{"name":"sample","version":"1.0.0","description":"Sample"}',
        encoding="utf-8",
    )
    pm = PluginManager(user_dir, system_dir)

    def fake_run(cmd, check, capture_output, text, timeout):
        target = Path(cmd[-1])
        target.mkdir(parents=True)
        (target / "plugin.json").write_text(
            '{"name":"sample","version":"1.0.0","description":"Sample"}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    plugin = pm.install_plugin("sample", "https://github.com/org/sample.git")
    assert plugin.manifest.name == "sample"
    assert pm.get_plugin("sample") is plugin


# ---------------------------------------------------------------------------
# Phase 37 Hardening: manifest name validation and install cleanup
# ---------------------------------------------------------------------------


def test_install_plugin_rejects_manifest_name_mismatch_and_cleans_directory(tmp_path, monkeypatch):
    """Install must reject a plugin.json whose name differs from the requested name,
    and the cloned directory must be removed."""
    import subprocess

    import pytest

    from miqi.skills.plugin_manager import PluginManager

    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    user_dir.mkdir()
    system_dir.mkdir()

    pm = PluginManager(user_dir, system_dir)

    def fake_clone(cmd, check, capture_output, text, timeout):
        target = Path(cmd[-1])
        target.mkdir(parents=True)
        (target / "plugin.json").write_text(
            '{"name":"evil-name","version":"1.0.0","description":"mismatch"}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_clone)

    with pytest.raises(ValueError, match="does not match requested name"):
        pm.install_plugin("requested-name", "https://github.com/org/sample.git")

    # The conflicting directory must be cleaned up.
    target_dir = user_dir / "requested-name"
    assert not target_dir.exists(), (
        f"target_dir {target_dir} should have been removed after manifest mismatch"
    )
    # Nothing registered under either name.
    assert pm.get_plugin("requested-name") is None
    assert pm.get_plugin("evil-name") is None


def test_install_plugin_rejects_invalid_manifest_name_and_cleans_directory(tmp_path, monkeypatch):
    """Install must reject a plugin.json with a name that fails validation,
    and the cloned directory must be removed."""
    import subprocess

    import pytest

    from miqi.skills.plugin_manager import PluginManager

    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    user_dir.mkdir()
    system_dir.mkdir()

    pm = PluginManager(user_dir, system_dir)

    def fake_clone(cmd, check, capture_output, text, timeout):
        target = Path(cmd[-1])
        target.mkdir(parents=True)
        (target / "plugin.json").write_text(
            '{"name":"../escape","version":"1.0.0","description":"bad"}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_clone)

    with pytest.raises(ValueError, match="Invalid plugin manifest name"):
        pm.install_plugin("ok-name", "https://github.com/org/sample.git")

    target_dir = user_dir / "ok-name"
    assert not target_dir.exists(), "target_dir should have been removed after manifest validation failure"
    assert pm.get_plugin("ok-name") is None


def test_load_plugin_from_dir_rejects_invalid_manifest_name(tmp_path):
    """load_plugin_from_dir must reject manifest names that fail validation."""
    import pytest

    from miqi.skills.plugin_manager import PluginManager

    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    user_dir.mkdir()
    system_dir.mkdir()

    pm = PluginManager(user_dir, system_dir)

    for bad_name in ["../escape", "bad/name", "", "a" * 65, "-starts-dash"]:
        plugin_dir = user_dir / f"test-bad-{bad_name[:10].replace('/', '_') or 'empty'}"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            '{{"name":"{}","version":"1.0.0","description":""}}'.format(bad_name),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Invalid plugin manifest name"):
            pm.load_plugin_from_dir(plugin_dir, "user")
        # Should not be registered under bad name
        assert pm.get_plugin(bad_name) is None


def test_install_plugin_registers_under_requested_name_only(tmp_path, monkeypatch):
    """When expected_name matches manifest name, the plugin is registered
    under the manifest name and is accessible via get_plugin."""
    import subprocess

    from miqi.skills.plugin_manager import PluginManager

    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    user_dir.mkdir()
    system_dir.mkdir()

    pm = PluginManager(user_dir, system_dir)

    def fake_clone(cmd, check, capture_output, text, timeout):
        target = Path(cmd[-1])
        target.mkdir(parents=True)
        (target / "plugin.json").write_text(
            '{"name":"my-plugin","version":"2.0.0","description":"ok"}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_clone)

    plugin = pm.install_plugin("my-plugin", "https://github.com/org/repo.git")
    assert plugin.manifest.name == "my-plugin"
    assert pm.get_plugin("my-plugin") is plugin
    # Registration key equals manifest name.
    assert "my-plugin" in pm._plugins


# ---------------------------------------------------------------------------
# Issue #88: plugin name must not end with a separator (- _ .)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [
    "my-plugin-",      # trailing dash
    "my.plugin.",      # trailing dot
    "my_plugin_",      # trailing underscore
    "a---",            # all separators after first char
    "ab-",             # length 3, trailing dash
])
def test_validate_plugin_name_rejects_trailing_separator(name):
    """A plugin name ending in a separator is invalid (filesystem/URL safety)."""
    from miqi.skills.plugin_manager import validate_plugin_name

    with pytest.raises(ValueError):
        validate_plugin_name(name)


@pytest.mark.parametrize("name", [
    "my-plugin",       # normal
    "hello_world",     # underscore in middle
    "test.tool",       # dot in middle
    "a",               # single char (length 1, no trailing sep)
    "ab",              # length 2, both alphanumeric
    "MyPlugin",
    "x" * 64,          # max length, alphanumeric
])
def test_validate_plugin_name_accepts_valid_names(name):
    """Valid names — including single char and alnum-only max-length — pass."""
    from miqi.skills.plugin_manager import validate_plugin_name

    # Should not raise.
    validate_plugin_name(name)
