"""#1267 插件 MCP 接线:命令解析与服务器合并的纯函数测试。"""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miqi.config.schema import MCPServerConfig
from miqi.runtime.plugin_mcp import merge_plugin_mcp_servers, resolve_plugin_command


# ── resolve_plugin_command ──────────────────────────────────────────────


def test_resolve_empty_returns_none():
    assert resolve_plugin_command("") is None


def test_resolve_absolute_existing_file(tmp_path):
    exe = tmp_path / "sure.exe"
    exe.write_text("", encoding="utf-8")
    assert resolve_plugin_command(str(exe)) == str(exe)


def test_resolve_absolute_missing_file_returns_none(tmp_path):
    assert resolve_plugin_command(str(tmp_path / "missing.exe")) is None


def test_resolve_uses_path_which(monkeypatch):
    monkeypatch.setattr(
        "miqi.runtime.plugin_mcp.shutil.which",
        lambda name: "/usr/local/bin/sure" if name == "sure" else None,
    )
    assert resolve_plugin_command("sure") == "/usr/local/bin/sure"


def test_resolve_windows_per_user_convention(monkeypatch, tmp_path):
    """Windows 每用户安装约定:%LOCALAPPDATA%\\<NAME>\\bin\\<name>.exe
    (SURE 的 per-user 安装即此形态,与 SURE 自身启动器兜底一致)。"""
    import miqi.runtime.plugin_mcp as pm

    monkeypatch.setattr(pm.sys, "platform", "win32")
    monkeypatch.setattr(pm.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    # 结构不存在 → 未命中
    assert resolve_plugin_command("sure") is None
    # 结构存在 → 命中(目录名大写约定)
    target = tmp_path / "SURE" / "bin" / "sure.exe"
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")
    assert resolve_plugin_command("sure") == str(target)


def test_resolve_windows_convention_skipped_on_posix(monkeypatch, tmp_path):
    import miqi.runtime.plugin_mcp as pm

    monkeypatch.setattr(pm.sys, "platform", "linux")
    monkeypatch.setattr(pm.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    target = tmp_path / "SURE" / "bin" / "sure.exe"
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")
    assert resolve_plugin_command("sure") is None


def test_resolve_relative_against_plugin_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("miqi.runtime.plugin_mcp.shutil.which", lambda name: None)
    script = tmp_path / "bin" / "launch"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    assert resolve_plugin_command("bin/launch", plugin_dir=str(tmp_path)) == str(script)


# ── merge_plugin_mcp_servers ────────────────────────────────────────────


def _cfg(**kw) -> MCPServerConfig:
    return MCPServerConfig(**kw)


def test_merge_config_wins_on_name_collision(monkeypatch):
    monkeypatch.setattr(
        "miqi.runtime.plugin_mcp.resolve_plugin_command", lambda cmd, plugin_dir="": "/p/sure"
    )
    config = {"sure": _cfg(command="/explicit/sure.exe")}
    merged = merge_plugin_mcp_servers(config, [{"name": "sure", "command": "sure"}])
    assert merged["sure"].command == "/explicit/sure.exe"  # 显式配置优先,不合并


def test_merge_adds_plugin_server_and_resolves_command(monkeypatch):
    monkeypatch.setattr(
        "miqi.runtime.plugin_mcp.resolve_plugin_command", lambda cmd, plugin_dir="": "/found/sure.exe"
    )
    merged = merge_plugin_mcp_servers({}, [{
        "name": "sure",
        "type": "stdio",
        "command": "sure",
        "args": ["mcp", "serve"],
        "tool_timeout": 600,
        "progress_interval_seconds": 10,
        "description": "SURE 项目核查",
        # 插件层会注入的附加键(extra="ignore" 容忍;cwd 是合法字段)
        "cwd": "/plugin/dir",
    }])
    srv = merged["sure"]
    assert srv.command == "/found/sure.exe"
    assert srv.args == ["mcp", "serve"]
    assert srv.tool_timeout == 600
    assert srv.progress_interval_seconds == 10
    assert srv.cwd == "/plugin/dir"


def test_merge_skips_unresolvable_command(monkeypatch):
    monkeypatch.setattr(
        "miqi.runtime.plugin_mcp.resolve_plugin_command", lambda cmd, plugin_dir="": None
    )
    merged = merge_plugin_mcp_servers({}, [{"name": "sure", "command": "sure"}])
    assert merged == {}  # 解析不了 → 跳过(fail-visible,其余服务器不受影响)


def test_merge_skips_invalid_entries_and_keeps_others(monkeypatch):
    monkeypatch.setattr(
        "miqi.runtime.plugin_mcp.resolve_plugin_command", lambda cmd, plugin_dir="": "/ok/sure"
    )
    merged = merge_plugin_mcp_servers({}, [
        "not-a-dict",                     # 非 dict → 跳过
        {"no_name": True},                # 无 name → 跳过
        {"name": "bad", "tool_timeout": "not-an-int"},  # 非法配置 → 跳过
        {"name": "sure", "command": "sure"},
    ])
    assert set(merged.keys()) == {"sure"}


def test_merge_url_server_needs_no_command_resolution(monkeypatch):
    called = []

    def _boom(cmd, plugin_dir=""):
        called.append(cmd)
        return None

    monkeypatch.setattr("miqi.runtime.plugin_mcp.resolve_plugin_command", _boom)
    merged = merge_plugin_mcp_servers({}, [{"name": "remote", "url": "http://x/sse", "type": "sse"}])
    assert "remote" in merged and merged["remote"].url == "http://x/sse"
    assert called == []  # 无 command 的条目不做解析


# ── 随包内置插件:miqi/plugins/sure 可被真实 PluginManager 装载 ──────────


def test_shipped_sure_plugin_package_loads(tmp_path):
    """仓库内置的 SURE 插件包经 PluginManager 装载:清单合法、快捷命令可调用、
    MCP 声明可被合并路径消化(命令解析在装配机是否命中取决于本机安装,不在此断言)。"""
    import asyncio

    from miqi.skills.plugin_manager import PluginManager

    repo_root = Path(__file__).resolve().parents[2]
    system_plugins = repo_root / "miqi" / "plugins"
    assert (system_plugins / "sure" / "plugin.json").is_file(), "内置插件包应存在"

    pm = PluginManager(
        user_plugins_dir=tmp_path / "user-plugins",
        system_plugins_dir=system_plugins,
    )
    asyncio.run(pm.discover())

    plugins = {p.manifest.name: p for p in pm.list_plugins()}
    assert "sure" in plugins
    loaded = plugins["sure"]
    assert loaded.status == "active"

    # MCP 声明可被合并函数消化(此处不依赖本机解析是否命中)
    server_entries = pm.get_mcp_servers()
    entry = next(e for e in server_entries if e.get("name") == "sure")
    assert entry["args"] == ["mcp", "serve"]
    assert entry["tool_timeout"] == 600
    assert "cwd" in entry  # 插件层注入插件根目录(供相对 command/启动 cwd)

    # 快捷命令:commands/*.md 被发现且可注入(status active + 非空 body)
    cmd = pm.get_slash_command("sure-check")
    assert cmd is not None
    assert cmd["status"] == "active"
    assert cmd["body"]
    assert "绝对路径" in cmd["body"]
