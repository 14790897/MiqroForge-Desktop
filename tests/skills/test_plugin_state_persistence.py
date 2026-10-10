"""#1267 评审:插件启停/卸载的持久化状态(跨会话与跨启动生效)。

状态文件与 user 插件目录同级(plugins_state.json)。语义:
- toggle 停用 → 写入;启用 → 清除;
- 内置(system)插件「卸载」= 持久停用:磁盘副本不删除(onefile 每次启动
  重新解包;开发机上是仓库源码),绝不出现「报告成功、下次启动又回来」;
- user 插件卸载 = 真实删除目录 + 清除停用记录(同名重装不被误停用);
- 状态文件损坏/缺失一律容忍(视为空)。
"""

import asyncio
import json
from pathlib import Path


def _manifest(name: str) -> dict:
    return {
        "name": name,
        "version": "1.0.0",
        "description": f"{name} plugin",
        "mcp_servers": [],
        "skills": [],
        "slash_commands": [],
        "dependencies": [],
    }


def _make_plugin_dir(parent: Path, name: str) -> Path:
    plugin_dir = parent / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(_manifest(name)), encoding="utf-8"
    )
    return plugin_dir


def _manager(user_dir: Path, system_dir: Path):
    from miqi.skills.plugin_manager import PluginManager

    return PluginManager(user_plugins_dir=user_dir, system_plugins_dir=system_dir)


def _read_disabled(tmp_path: Path) -> list:
    state_file = tmp_path / "plugins_state.json"
    if not state_file.exists():
        return []
    return json.loads(state_file.read_text(encoding="utf-8")).get("disabled", [])


def test_toggle_disable_is_persistent_across_managers(tmp_path):
    """停用写入状态文件;新建 manager(模拟下个会话)发现时即应用为 disabled。"""
    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    user_dir.mkdir()
    system_dir.mkdir()
    _make_plugin_dir(user_dir, "sticky")

    pm1 = _manager(user_dir, system_dir)
    asyncio.run(pm1.discover())
    assert pm1.get_plugin("sticky").status == "active"

    pm1.toggle_plugin("sticky", enabled=False)
    assert "sticky" in _read_disabled(tmp_path), "停用状态应写入状态文件"

    pm2 = _manager(user_dir, system_dir)
    asyncio.run(pm2.discover())
    assert pm2.get_plugin("sticky").status == "disabled"

    # 重新启用 → 状态清除,再发现即恢复 active
    pm2.toggle_plugin("sticky", enabled=True)
    assert "sticky" not in _read_disabled(tmp_path)

    pm3 = _manager(user_dir, system_dir)
    asyncio.run(pm3.discover())
    assert pm3.get_plugin("sticky").status == "active"


def test_uninstall_system_plugin_records_exclusion_without_deleting(tmp_path):
    """内置插件卸载:磁盘副本保留,判为持久停用;下个 manager 发现时不再
    active——消除「卸载报告成功、下次启动又回来」的假卸载。"""
    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    user_dir.mkdir()
    system_dir.mkdir()
    _make_plugin_dir(system_dir, "bundled")

    pm1 = _manager(user_dir, system_dir)
    asyncio.run(pm1.discover())
    assert pm1.get_plugin("bundled").status == "active"

    assert pm1.uninstall_plugin("bundled") is True
    assert (system_dir / "bundled").exists(), "内置插件目录绝不允许被删除"
    assert pm1.get_plugin("bundled").status == "disabled"
    assert "bundled" in _read_disabled(tmp_path)

    pm2 = _manager(user_dir, system_dir)
    asyncio.run(pm2.discover())
    assert pm2.get_plugin("bundled").status == "disabled", "卸载须跨会话生效"


def test_uninstall_user_plugin_removes_dir_and_clears_state(tmp_path):
    """user 插件卸载:目录真实删除,停用记录清除(同名重装不被误停用)。"""
    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    user_dir.mkdir()
    system_dir.mkdir()
    _make_plugin_dir(user_dir, "removable")

    pm = _manager(user_dir, system_dir)
    asyncio.run(pm.discover())
    pm.toggle_plugin("removable", enabled=False)
    assert "removable" in _read_disabled(tmp_path)

    assert pm.uninstall_plugin("removable") is True
    assert not (user_dir / "removable").exists(), "user 插件目录应被删除"
    assert "removable" not in _read_disabled(tmp_path)


def test_corrupt_state_file_is_tolerated(tmp_path):
    """状态文件损坏不得影响发现(视为空)。"""
    user_dir = tmp_path / "user"
    system_dir = tmp_path / "system"
    user_dir.mkdir()
    system_dir.mkdir()
    _make_plugin_dir(user_dir, "sticky")
    (tmp_path / "plugins_state.json").write_text("{ not json", encoding="utf-8")

    pm = _manager(user_dir, system_dir)
    asyncio.run(pm.discover())
    assert pm.get_plugin("sticky").status == "active"
