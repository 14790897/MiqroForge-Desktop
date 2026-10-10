"""#1267 评审(P1 安全边界 / P2 并发):工作区插件默认不可信 + 状态写入串行化。

工作区(<workspace>/.forge/plugins)内容随被打开的项目而来,可能不可信:
发现的插件默认 disabled——不注册 hooks、不并入 MCP 连接、斜杠命令不注入
(task_runner 按 status 门控)。只有用户在插件页显式启用后才生效,授权按
「工作区绝对路径 + 插件名」持久化,不跨工作区泄漏。
user(~/.forge/plugins)与 system(随包内置)维持默认可用。

P2:状态文件的读-改-写在进程内互斥锁下完成,并发写不同插件不丢更新。
跨进程并发为文档化约束(单用户桌面场景,最后写者胜出)。
"""

import asyncio
import json
import threading
from pathlib import Path

_MANIFEST = {
    "version": "1.0.0",
    "description": "workspace plugin",
    "mcp_servers": [{"name": "ws-mcp", "command": "echo"}],
    "skills": [],
    "slash_commands": [],
}


def _make_plugin(parent: Path, name: str) -> Path:
    plugin_dir = parent / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = dict(_MANIFEST, name=name)
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    return plugin_dir


def _manager(tmp_path: Path, workspace: Path):
    from miqi.skills.plugin_manager import PluginManager

    return PluginManager(
        user_plugins_dir=tmp_path / "user",
        system_plugins_dir=tmp_path / "system",
        workspace=workspace,
    )


def test_workspace_plugin_defaults_to_disabled(tmp_path):
    """未授权的工作区插件:装载为 disabled,MCP 服务器不自动接入。"""
    ws = tmp_path / "ws"
    _make_plugin(ws / ".forge" / "plugins", "tool")
    pm = _manager(tmp_path, ws)
    asyncio.run(pm.discover())

    plugin = pm.get_plugin("tool")
    assert plugin is not None and plugin.status == "disabled"
    assert pm.get_mcp_servers() == [], "未授权的工作区插件不得自动接入 MCP"


def test_workspace_enable_persists_for_that_workspace(tmp_path):
    """显式启用后生效,且跨会话/重启持久(按工作区路径持久化)。"""
    ws = tmp_path / "ws"
    _make_plugin(ws / ".forge" / "plugins", "tool")
    pm = _manager(tmp_path, ws)
    asyncio.run(pm.discover())

    pm.toggle_plugin("tool", enabled=True)
    assert pm.get_plugin("tool").status == "active"
    assert any(s.get("name") == "ws-mcp" for s in pm.get_mcp_servers())

    pm2 = _manager(tmp_path, ws)
    asyncio.run(pm2.discover())
    assert pm2.get_plugin("tool").status == "active", "授权须跨会话/重启生效"


def test_workspace_trust_does_not_leak_to_other_workspace(tmp_path):
    """授权绑定工作区路径:另一工作区中的同名插件仍为未授权。"""
    ws_a = tmp_path / "ws-a"
    ws_b = tmp_path / "ws-b"
    _make_plugin(ws_a / ".forge" / "plugins", "tool")
    _make_plugin(ws_b / ".forge" / "plugins", "tool")

    pm_a = _manager(tmp_path, ws_a)
    asyncio.run(pm_a.discover())
    pm_a.toggle_plugin("tool", enabled=True)

    pm_b = _manager(tmp_path, ws_b)
    asyncio.run(pm_b.discover())
    assert pm_b.get_plugin("tool").status == "disabled", "同名插件的授权不得跨工作区"


def test_workspace_disable_revokes_trust(tmp_path):
    """停用即撤销授权,后续会话不再自动生效。"""
    ws = tmp_path / "ws"
    _make_plugin(ws / ".forge" / "plugins", "tool")
    pm = _manager(tmp_path, ws)
    asyncio.run(pm.discover())
    pm.toggle_plugin("tool", enabled=True)
    pm.toggle_plugin("tool", enabled=False)

    pm2 = _manager(tmp_path, ws)
    asyncio.run(pm2.discover())
    assert pm2.get_plugin("tool").status == "disabled"


def test_user_scope_plugin_still_defaults_active(tmp_path):
    """策略区分:用户级插件维持默认可用(不因工作区规则误伤)。"""
    ws = tmp_path / "ws"
    (ws / ".forge" / "plugins").mkdir(parents=True)
    _make_plugin(tmp_path / "user", "trusted-user-plugin")
    pm = _manager(tmp_path, ws)
    asyncio.run(pm.discover())
    assert pm.get_plugin("trusted-user-plugin").status == "active"


def test_concurrent_state_updates_do_not_lose_entries(tmp_path):
    """P2:多线程并发写不同插件的停用状态,所有写入必须保留(无丢更新)。"""
    from miqi.skills.plugin_manager import PluginManager

    pm = PluginManager(tmp_path / "user", tmp_path / "system")
    names = [f"plug-{i:02d}" for i in range(24)]
    threads_n = 8
    barrier = threading.Barrier(threads_n)

    def worker(subset):
        barrier.wait()
        for n in subset:
            pm._set_disabled(n, True)

    threads = [
        threading.Thread(target=worker, args=(names[i::threads_n],))
        for i in range(threads_n)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    state = json.loads((tmp_path / "plugins_state.json").read_text(encoding="utf-8"))
    assert set(state.get("disabled", [])) == set(names), "并发写入发生了丢更新"
