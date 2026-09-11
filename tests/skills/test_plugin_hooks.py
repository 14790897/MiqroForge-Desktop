"""Tests for plugin-declared lifecycle hooks (Phase 51.4).

Covers:
- command and module hook registration on plugin load/discover
- source tagging with the plugin manifest name
- unregister_source / uninstall / toggle semantics
"""

import json
import sys
from pathlib import Path

import pytest

from miqi.execution.hook_runtime import (
    HookPoint,
    HookRuntime,
)
from miqi.skills.plugin_manager import PluginManager


def _write_manifest(plugin_dir: Path, manifest: dict) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


class FakeToolContext:
    def __init__(self, tool_name: str):
        self.tool_name = tool_name


@pytest.mark.asyncio
async def test_command_hook_registers_and_blocks(tmp_path):
    """A command-type hook runs through a shell and maps non-zero exits to block."""
    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    user_dir.mkdir()
    system_dir.mkdir()

    hook_runtime = HookRuntime()
    pm = PluginManager(
        user_plugins_dir=user_dir,
        system_plugins_dir=system_dir,
        hook_runtime=hook_runtime,
    )

    ok_target = f'{sys.executable} -c "import sys; sys.exit(0)"'
    block_target = (
        f'{sys.executable} -c '
        '"import sys; sys.stderr.write(\'blocked by command\'); sys.exit(1)"'
    )

    _write_manifest(
        user_dir / "cmd-hook-plugin",
        {
            "name": "cmd-hook-plugin",
            "version": "1.0.0",
            "description": "command hook test",
            "hooks": [
                {
                    "point": "pre_tool_use",
                    "match": "exec",
                    "type": "command",
                    "target": ok_target,
                    "priority": 5,
                },
                {
                    "point": "pre-tool-use",
                    "match": "bash",
                    "type": "command",
                    "target": block_target,
                    "priority": 6,
                },
            ],
        },
    )

    await pm.discover()

    regs = [
        r
        for r in hook_runtime._hooks[HookPoint.PRE_TOOL_USE]
        if r.source == "cmd-hook-plugin"
    ]
    assert len(regs) == 2
    assert {r.tool_pattern for r in regs} == {"exec", "bash"}
    assert {r.priority for r in regs} == {5, 6}

    continue_outcome = await hook_runtime.run_with_outcome(
        HookPoint.PRE_TOOL_USE, FakeToolContext("exec")
    )
    assert continue_outcome.action == "continue"

    block_outcome = await hook_runtime.run_with_outcome(
        HookPoint.PRE_TOOL_USE, FakeToolContext("bash")
    )
    assert block_outcome.action == "block"
    assert "blocked by command" in block_outcome.reason


@pytest.mark.asyncio
async def test_module_hook_registers_and_runs(tmp_path):
    """A module-type hook imports pkg.mod:func from the plugin directory."""
    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    user_dir.mkdir()
    system_dir.mkdir()

    hook_runtime = HookRuntime()
    pm = PluginManager(
        user_plugins_dir=user_dir,
        system_plugins_dir=system_dir,
        hook_runtime=hook_runtime,
    )

    plugin_dir = user_dir / "module-hook-plugin"
    _write_manifest(
        plugin_dir,
        {
            "name": "module-hook-plugin",
            "version": "1.0.0",
            "description": "module hook test",
            "hooks": [
                {
                    "point": "pre_tool_use",
                    "match": "exec",
                    "type": "module",
                    "target": "plugin_hooks:plugin_hook",
                    "priority": 1,
                },
            ],
        },
    )
    (plugin_dir / "plugin_hooks.py").write_text(
        'from miqi.execution.hook_runtime import HookOutcome\n\n'
        'async def plugin_hook(ctx):\n'
        '    return HookOutcome.block(f"module blocked {ctx.tool_name}")\n',
        encoding="utf-8",
    )

    await pm.discover()

    regs = [
        r
        for r in hook_runtime._hooks[HookPoint.PRE_TOOL_USE]
        if r.source == "module-hook-plugin"
    ]
    assert len(regs) == 1
    assert regs[0].tool_pattern == "exec"
    assert regs[0].priority == 1

    outcome = await hook_runtime.run_with_outcome(
        HookPoint.PRE_TOOL_USE, FakeToolContext("exec")
    )
    assert outcome.action == "block"
    assert outcome.reason == "module blocked exec"


@pytest.mark.asyncio
async def test_uninstall_unregisters_plugin_hooks(tmp_path):
    """Uninstalling a plugin removes its hook registrations."""
    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    user_dir.mkdir()
    system_dir.mkdir()

    hook_runtime = HookRuntime()
    pm = PluginManager(
        user_plugins_dir=user_dir,
        system_plugins_dir=system_dir,
        hook_runtime=hook_runtime,
    )

    block_target = (
        f'{sys.executable} -c '
        '"import sys; sys.stderr.write(\'gone\'); sys.exit(1)"'
    )
    plugin_dir = user_dir / "uninstall-me"
    _write_manifest(
        plugin_dir,
        {
            "name": "uninstall-me",
            "version": "1.0.0",
            "description": "",
            "hooks": [
                {
                    "point": "pre_tool_use",
                    "match": "*",
                    "type": "command",
                    "target": block_target,
                },
            ],
        },
    )

    await pm.discover()
    assert pm.get_plugin("uninstall-me") is not None
    assert any(
        r.source == "uninstall-me"
        for r in hook_runtime._hooks[HookPoint.PRE_TOOL_USE]
    )

    assert pm.uninstall_plugin("uninstall-me") is True
    assert pm.get_plugin("uninstall-me") is None
    assert not any(
        r.source == "uninstall-me"
        for r in hook_runtime._hooks[HookPoint.PRE_TOOL_USE]
    )
    assert not plugin_dir.exists()


def test_toggle_disables_and_re_enables_hooks(tmp_path):
    """Toggling a plugin disabled removes its hooks; enabling re-registers them."""
    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    user_dir.mkdir()
    system_dir.mkdir()

    hook_runtime = HookRuntime()
    pm = PluginManager(
        user_plugins_dir=user_dir,
        system_plugins_dir=system_dir,
        hook_runtime=hook_runtime,
    )

    plugin_dir = user_dir / "toggle-me"
    _write_manifest(
        plugin_dir,
        {
            "name": "toggle-me",
            "version": "1.0.0",
            "description": "",
            "hooks": [
                {
                    "point": "pre_tool_use",
                    "match": "*",
                    "type": "module",
                    "target": "toggle_hooks:toggle_hook",
                },
            ],
        },
    )
    (plugin_dir / "toggle_hooks.py").write_text(
        'from miqi.execution.hook_runtime import HookOutcome\n\n'
        'async def toggle_hook(ctx):\n'
        '    return HookOutcome.block("toggled off")\n',
        encoding="utf-8",
    )

    # Load synchronously and register hooks.
    plugin = pm.load_plugin_from_dir(plugin_dir, "user")
    assert plugin.status == "active"
    assert any(
        r.source == "toggle-me"
        for r in hook_runtime._hooks[HookPoint.PRE_TOOL_USE]
    )

    # Disable removes hooks.
    pm.toggle_plugin("toggle-me", enabled=False)
    assert plugin.status == "disabled"
    assert not any(
        r.source == "toggle-me"
        for r in hook_runtime._hooks[HookPoint.PRE_TOOL_USE]
    )

    # Enable re-registers hooks.
    pm.toggle_plugin("toggle-me", enabled=True)
    assert plugin.status == "active"
    assert any(
        r.source == "toggle-me"
        for r in hook_runtime._hooks[HookPoint.PRE_TOOL_USE]
    )
