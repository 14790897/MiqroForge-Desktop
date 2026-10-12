"""阶段5:MiQi→SURE 事件桥接(sure_hooks 模块)与插件清单声明。

被测对象是插件目录中的 `sure_hooks.py`——插件加载器以 sys.path + 顶层
模块名导入它(plugin_manager._make_module_callback),所以这里也按文件
路径加载,不依赖包导入。

桥接的硬约束(方案定稿):事件是观测(fire-and-forget)——SURE 未装、
超时、返回错误时一律静默跳过,绝不阻塞或改变 MiQi 的工具执行结果。
"""

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "miqi" / "plugins" / "sure"


def _load_sure_hooks():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sure_hooks", PLUGIN_DIR / "sure_hooks.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _capture_bin(tmp_path: Path) -> tuple[str, Path]:
    """假 sure:把每次 stdin 事件追加到 captured.jsonl,返回 (binary, 文件)。"""
    captured = tmp_path / "captured.jsonl"
    body = (
        "import sys\n"
        "data = sys.stdin.read()\n"
        f"with open(r'{captured}', 'a', encoding='utf-8') as fh:\n"
        "    fh.write(data + chr(10))\n"
    )
    return _wrap_as_sure(tmp_path, body), captured


def _read_events(captured: Path) -> list[dict]:
    return [json.loads(line) for line in captured.read_text(encoding="utf-8").splitlines()]


def _tool_ctx(tool: str = "exec", session: str = "c:s1", workspace: str = "D:/work/proj"):
    return SimpleNamespace(tool_name=tool, session_id=session, workspace=workspace)


def _lifecycle_ctx(session: str = "c:s1", workspace: str = "D:/work/proj"):
    return SimpleNamespace(data={"session_id": session, "workspace": workspace})


@pytest.mark.asyncio
async def test_pre_tool_use_sends_tool_requested(tmp_path, monkeypatch):
    sur = _load_sure_hooks()
    binary, captured = _capture_bin(tmp_path)
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: binary)

    await sur.on_pre_tool_use(_tool_ctx())

    event = _read_events(captured)[0]
    assert event["event"] == "PreToolUse"
    assert event["source"] == "miqi"
    assert event["harness_session_id"] == "c:s1"
    assert event["tool"] == "exec"
    assert event["project_root"] == "D:/work/proj"
    assert event["timestamp_utc"]
    # 最小采集:工具参数不上报
    assert "args" not in event


@pytest.mark.asyncio
async def test_post_tool_use_carries_ok_result(tmp_path, monkeypatch):
    sur = _load_sure_hooks()
    binary, captured = _capture_bin(tmp_path)
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: binary)

    await sur.on_post_tool_use(_tool_ctx(tool="write_file"))

    event = _read_events(captured)[0]
    assert event["event"] == "PostToolUse"
    assert event["tool"] == "write_file"
    assert event["result"] == "ok"


@pytest.mark.asyncio
async def test_non_vocabulary_tools_are_not_sent(tmp_path, monkeypatch):
    """词表外工具(list_dir、记忆/搜索类等)不上报——SURE 侧不认这些名字。"""
    sur = _load_sure_hooks()
    binary, captured = _capture_bin(tmp_path)
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: binary)

    await sur.on_pre_tool_use(_tool_ctx(tool="list_dir"))
    await sur.on_post_tool_use(_tool_ctx(tool="memory"))

    assert not captured.exists()


@pytest.mark.asyncio
async def test_post_tool_use_skips_error_status(tmp_path, monkeypatch):
    """失败不误记成功:工具内部报错(TOOL_ERROR)时不得发 PostToolUse。

    orchestrator 在 `_execute_in_sandbox` 自标 TOOL_ERROR 后仍会调用 POST 钩子
    ——观测语义应为"有 requested 无 completed",而不是 completed。
    """
    from miqi.execution.orchestrator import OrchestrationResult

    sur = _load_sure_hooks()
    binary, captured = _capture_bin(tmp_path)
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: binary)

    error_ctx = _tool_ctx()
    error_ctx.status = OrchestrationResult.TOOL_ERROR
    await sur.on_post_tool_use(error_ctx)
    assert not captured.exists(), "报错的工具调用不得发 completed"

    ok_ctx = _tool_ctx()
    ok_ctx.status = OrchestrationResult.SUCCESS
    await sur.on_post_tool_use(ok_ctx)
    event = _read_events(captured)[0]
    assert event["event"] == "PostToolUse"
    assert event["result"] == "ok"


@pytest.mark.asyncio
async def test_session_lifecycle_maps(tmp_path, monkeypatch):
    sur = _load_sure_hooks()
    binary, captured = _capture_bin(tmp_path)
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: binary)

    await sur.on_session_start(_lifecycle_ctx())
    await sur.on_stop(_lifecycle_ctx())

    events = _read_events(captured)
    assert [e["event"] for e in events] == ["SessionStart", "Stop"]
    assert all(e["source"] == "miqi" for e in events)
    assert all(e["harness_session_id"] == "c:s1" for e in events)
    assert all(e["project_root"] == "D:/work/proj" for e in events)


@pytest.mark.asyncio
async def test_missing_binary_is_silent(tmp_path, monkeypatch):
    sur = _load_sure_hooks()
    _, captured = _capture_bin(tmp_path)
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: None)

    await sur.on_pre_tool_use(_tool_ctx())  # 不应抛出

    assert not captured.exists()


@pytest.mark.asyncio
async def test_failing_ingest_is_silent(tmp_path, monkeypatch):
    sur = _load_sure_hooks()
    failing = _wrap_as_sure(tmp_path, "import sys\nsys.exit(3)\n")
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: failing)

    await sur.on_pre_tool_use(_tool_ctx())  # 退出码非零不应抛出


@pytest.mark.asyncio
async def test_ingest_timeout_is_silent_and_bounded(tmp_path, monkeypatch):
    sur = _load_sure_hooks()
    slow = _wrap_as_sure(tmp_path, "import time\ntime.sleep(30)\n")
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: slow)
    monkeypatch.setattr(sur, "INGEST_TIMEOUT_SECONDS", 0.5)

    started = time.monotonic()
    await sur.on_pre_tool_use(_tool_ctx())  # 超时应被吞掉并尽快返回

    assert time.monotonic() - started < 10


@pytest.mark.asyncio
async def test_plugin_manifest_registers_the_four_hooks(tmp_path):
    """真实插件清单(plugin.json)把四个钩子注册到共享 HookRuntime 上。"""
    from miqi.execution.hook_runtime import HookPoint, HookRuntime
    from miqi.skills.plugin_manager import PluginManager

    user_dir = tmp_path / "user"
    user_dir.mkdir()
    runtime = HookRuntime()
    pm = PluginManager(
        user_plugins_dir=user_dir,
        system_plugins_dir=REPO_ROOT / "miqi" / "plugins",
        hook_runtime=runtime,
    )

    await pm.discover()

    for point in (
        HookPoint.SESSION_START,
        HookPoint.PRE_TOOL_USE,
        HookPoint.POST_TOOL_USE,
        HookPoint.STOP,
    ):
        regs = [r for r in runtime._hooks[point] if r.source == "sure"]
        assert len(regs) == 1, f"{point} 未由 sure 插件注册"
        assert regs[0].tool_pattern == "*"
