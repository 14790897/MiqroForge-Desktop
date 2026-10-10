"""插件声明的 MCP 服务器 → 会话连接集(#1267)。

背景:PluginManager 一直能解析 ``plugin.json`` 的 ``mcp_servers`` 列表,
但运行时会话此前只连 ``config.tools.mcp_servers``——插件声明的服务器
永远到不了工具表。本模块提供两个**纯函数**,由 ``RuntimeSession`` 在
连接前调用:

- ``resolve_plugin_command``:把插件声明的 ``command`` 解析为可执行路径
  (绝对路径 → PATH → Windows 每用户安装约定);解析失败返回 ``None``,
  调用方跳过该服务器并记日志(fail-visible:宁可工具缺席,不给一个连不
  上的服务器)。
- ``merge_plugin_mcp_servers``:合并配置与插件服务器,``config`` 显式
  同名为准;非法条目逐条跳过,不影响其余服务器。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from loguru import logger

from miqi.config.schema import MCPServerConfig

#: Windows 每用户安装约定的目录名归一大写(%LOCALAPPDATA%\SURE\bin\sure.exe 形)。
_WINDOWS_PER_USER_DESC = r"%LOCALAPPDATA%\<NAME>\bin\<name>.exe"


def resolve_plugin_command(command: str, *, plugin_dir: str = "") -> str | None:
    """解析插件声明的 stdio ``command`` 为可执行路径。

    顺序:
    1. 绝对路径——存在原样返回,不存在视为未命中;
    2. ``PATH``(``shutil.which``,处理 PATHEXT);
    3. Windows 每用户安装约定 ``%LOCALAPPDATA%\\<NAME>\\bin\\<name>.exe``
       (SURE 的 per-user 安装即此形态,与 SURE 自身启动器的兜底一致);
    4. 含路径分隔符的相对路径——按插件根目录(``plugin_dir``)解析。

    全部未命中返回 ``None``(调用方跳过该服务器并记日志,不静默注入)。
    """
    if not command:
        return None
    if os.path.isabs(command):
        return command if os.path.isfile(command) else None
    found = shutil.which(command)
    if found:
        return found
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA", "").strip()
        if root:
            candidate = Path(root) / command.upper() / "bin" / f"{command}.exe"
            if candidate.is_file():
                return str(candidate)
    if plugin_dir and ("/" in command or "\\" in command):
        candidate = Path(plugin_dir) / command
        if candidate.is_file():
            return str(candidate)
    return None


def merge_plugin_mcp_servers(
    config_servers: dict[str, MCPServerConfig],
    plugin_servers: list[dict],
) -> dict[str, MCPServerConfig]:
    """把插件声明的服务器并入会话连接集。

    - ``config_servers``(用户显式配置)同名为准,插件条目不覆盖;
    - 插件条目转换为 ``MCPServerConfig``(附加键如 ``name`` 由 extra="ignore"
      容忍;``cwd`` 为合法字段,会随 stdio 启动生效);
    - 带 ``command`` 的条目先做路径解析(见 ``resolve_plugin_command``),
      解析失败或条目非法 → 跳过并记日志,其余服务器不受影响。
    """
    merged = dict(config_servers)
    for entry in plugin_servers or []:
        if not isinstance(entry, dict):
            logger.warning("插件 MCP 条目不是对象,已跳过: {!r}", entry)
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            logger.warning("插件 MCP 条目缺少 name,已跳过")
            continue
        if name in merged:
            logger.debug("插件 MCP 服务器 '{}' 与显式配置同名,以显式配置为准", name)
            continue
        try:
            cfg = MCPServerConfig(**entry)
        except Exception as exc:  # noqa: BLE001 — 单条非法不拖垮其余
            logger.warning("插件 MCP 服务器 '{}' 配置非法,已跳过: {}", name, exc)
            continue
        if cfg.command:
            resolved = resolve_plugin_command(cfg.command, plugin_dir=cfg.cwd)
            if resolved is None:
                logger.warning(
                    "插件 MCP 服务器 '{}':命令 '{}' 无法解析(不在 PATH,也无 Windows "
                    "每用户安装 {}),已跳过——SURE 未安装或请改用显式配置写绝对路径",
                    name,
                    cfg.command,
                    _WINDOWS_PER_USER_DESC,
                )
                continue
            cfg = cfg.model_copy(update={"command": resolved})
        merged[name] = cfg
    return merged
