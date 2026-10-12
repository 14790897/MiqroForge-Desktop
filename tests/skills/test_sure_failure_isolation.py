"""阶段 6·安全验证:SURE / 插件失效时,MiQi 核心流程不受影响。

验收句(方案 §阶段 6):「插件/SURE 失效时,MiQi 核心聊天与执行流程不受影响」。
本文件的口径是工具执行链:桥接挂着一个失效的 SURE(缺失 / 卡死)或一个坏
插件(清单损坏 / 钩子模块导入失败)时,orchestrator 的工具调用照常完成,
只有观测事件被静默跳过——绝不阻塞、绝不改变结果。
"""

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SURE_PLUGIN_DIR = REPO_ROOT / "miqi" / "plugins" / "sure"


def _wrap_as_sure(tmp_path: Path, body: str) -> str:
    """把一段 python 代码包装成平台可执行的 'sure' 二进制。"""
    script = tmp_path / "sure_impl.py"
    script.write_text(body, encoding="utf-8")
    if os.name == "nt":
        wrapper = tmp_path / "sure.cmd"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
        )
    else:
        wrapper = tmp_path / "sure"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
        )
        wrapper.chmod(0o755)
    return str(wrapper)


def _load_plugin_module(monkeypatch):
    """按插件加载器的方式导入 sure_hooks(顶层模块名 + sys.path),返回模块。

    生产路径(plugin_manager._make_module_callback)以 sys.modules 缓存该模块——
    这里预导入拿到的就是**同一个对象**,保证 monkeypatch 作用于真实回调。
    """
    p = str(SURE_PLUGIN_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)
    import importlib

    return importlib.import_module("sure_hooks")


async def _wire(tmp_path: Path):
    """真实装配:真实插件目录 → 共享 HookRuntime → factory orchestrator。"""
    from miqi.agent.tools.filesystem import ReadFileTool
    from miqi.agent.tools.registry import ToolRegistry
    from miqi.execution.factory import create_default_orchestrator
    from miqi.execution.hook_runtime import HookRuntime
    from miqi.runtime.tool_runtime import ToolRuntime
    from miqi.skills.plugin_manager import PluginManager

    project = tmp_path / "proj"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("contents-x", encoding="utf-8")
    user_dir = tmp_path / "user"
    user_dir.mkdir(exist_ok=True)

    runtime = HookRuntime()
    pm = PluginManager(
        user_plugins_dir=user_dir,
        system_plugins_dir=REPO_ROOT / "miqi" / "plugins",
        hook_runtime=runtime,
    )
    await pm.discover()

    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace=project))
    orchestrator = create_default_orchestrator(
        tool_registry=registry,
        hook_runtime=runtime,
        permanent_allowlist={f"read_file:{target}"},
    )
    turn = SimpleNamespace(
        turn_id="t-iso",
        thread_id="th-iso",
        agent_metadata=SimpleNamespace(name="main"),
        session_id="c:isolation",
        workspace=project,
    )
    call = SimpleNamespace(name="read_file", id="call-iso", arguments={"path": str(target)})
    return ToolRuntime(orchestrator=orchestrator), turn, call


@pytest.mark.asyncio
async def test_tool_execution_survives_hanging_sure(tmp_path, monkeypatch):
    """SURE 卡死(60s 不返回)→ 桥接按超时静默跳过,工具照常完成且有界。"""
    hang_bin = _wrap_as_sure(tmp_path, "import time\ntime.sleep(60)\n")
    monkeypatch.setenv("SURE_BIN", hang_bin)
    sure_hooks = _load_plugin_module(monkeypatch)
    monkeypatch.setattr(sure_hooks, "INGEST_TIMEOUT_SECONDS", 0.2)

    tool_runtime, turn, call = await _wire(tmp_path)

    started = time.monotonic()
    result = await tool_runtime.execute_one(turn, call)
    elapsed = time.monotonic() - started

    assert result.status == "success", result.result
    assert result.result and "contents-x" in result.result
    assert elapsed < 5.0, f"卡死的 SURE 不得拖垮工具执行(实测 {elapsed:.1f}s)"

    # 契约:入队后收尾排空(不留半途 spawn 给 teardown)
    await sure_hooks.flush(3.0)


@pytest.mark.asyncio
async def test_tool_execution_survives_missing_sure(tmp_path, monkeypatch):
    """SURE 缺失(解析不到二进制)→ 桥接静默跳过,工具照常完成。"""
    sure_hooks = _load_plugin_module(monkeypatch)
    monkeypatch.setattr(sure_hooks, "resolve_sure_bin", lambda: None)

    tool_runtime, turn, call = await _wire(tmp_path)

    result = await tool_runtime.execute_one(turn, call)

    assert result.status == "success", result.result
    assert result.result and "contents-x" in result.result

    await sure_hooks.flush(2.0)  # 契约:入队后收尾排空


@pytest.mark.asyncio
async def test_broken_plugin_manifest_does_not_block_discovery(tmp_path):
    """单个插件清单损坏 → 被标记错误,不影响其余插件(含 sure)注册钩子。"""
    from miqi.execution.hook_runtime import HookPoint, HookRuntime
    from miqi.skills.plugin_manager import PluginManager

    user_dir = tmp_path / "user"
    broken = user_dir / "broken-plugin"
    broken.mkdir(parents=True)
    (broken / "plugin.json").write_text("{ not json", encoding="utf-8")

    runtime = HookRuntime()
    pm = PluginManager(
        user_plugins_dir=user_dir,
        system_plugins_dir=REPO_ROOT / "miqi" / "plugins",
        hook_runtime=runtime,
    )

    await pm.discover()  # 不得抛出

    regs = [r for r in runtime._hooks[HookPoint.PRE_TOOL_USE] if r.source == "sure"]
    assert len(regs) == 1, "坏插件不得影响正常插件的钩子注册"


@pytest.mark.asyncio
async def test_broken_hook_module_does_not_block_tool_execution(tmp_path, monkeypatch):
    """插件声明的钩子模块导入失败(ImportError)→ HookRuntime fail-open,工具照常完成。"""
    import json

    user_dir = tmp_path / "user"
    bad = user_dir / "bad-hook-plugin"
    bad.mkdir(parents=True)
    (bad / "plugin.json").write_text(
        json.dumps(
            {
                "name": "bad-hook-plugin",
                "version": "1.0.0",
                "description": "module hook whose module does not import",
                "hooks": [
                    {
                        "point": "pre_tool_use",
                        "match": "*",
                        "type": "module",
                        "target": "nope_hooks:on_pre_tool_use",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    # 保持用例与机器环境无关:桥接一律走"SURE 缺失"静默路径
    sure_hooks = _load_plugin_module(monkeypatch)
    monkeypatch.setattr(sure_hooks, "resolve_sure_bin", lambda: None)

    # _wire 的装配会一并装载 tmp_path/user 下的坏钩子插件与 system 目录的 sure 插件
    tool_runtime, turn, call = await _wire(tmp_path)

    result = await tool_runtime.execute_one(turn, call)

    assert result.status == "success", result.result

    await sure_hooks.flush(2.0)  # 契约:入队后收尾排空
