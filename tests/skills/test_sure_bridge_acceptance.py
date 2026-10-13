"""阶段5·M3 端到端验收(mock SURE):真实插件 → 真实钩子运行时 → 真实
orchestrator →(mock)`sure hook ingest` 的全链路。

对应方案 §阶段5 验收:
1. 会话与项目归属正确——每条事件带 session_id 与 project_root;
2. 失败/拒绝事件没被误记成成功——被拒的调用只有 requested、无 completed;
3. 重启后历史可读——由真机腿(真实 sure.exe + `sure history`)覆盖,不在本文件;
4. 未授权内容不被采集——载荷不含工具参数(args/command)。

额外的观测语义:工具内部报错(TOOL_ERROR)同样不发 completed(见
test_sure_hooks.py 的单测)。
"""

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _fake_sure_capture(tmp_path: Path) -> tuple[str, Path]:
    """假 sure:每次 stdin 事件追加到 captured.jsonl,返回 (binary, 文件)。

    stdin 走**字节透传**(与真实 sure.exe 的字节流语义一致):文本模式
    sys.stdin.read() 会按 Windows ANSI 代码页解码 UTF-8 载荷(中文路径
    会变 lone surrogate 并在写出时崩溃,E2E 实测踩中)。
    """
    captured = tmp_path / "accept-captured.jsonl"
    script = tmp_path / "sure_impl.py"
    script.write_text(
        "import sys\n"
        "data = sys.stdin.buffer.read()\n"
        f"with open(r'{captured}', 'ab') as fh:\n"
        "    fh.write(data + bytes([10]))\n",
        encoding="utf-8",
    )
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
    return str(wrapper), captured


def _read_events(captured: Path) -> list[dict]:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if captured.exists():
            text = captured.read_text(encoding="utf-8").strip()
            if text:
                return [json.loads(line) for line in text.splitlines()]
        time.sleep(0.05)
    raise AssertionError("未捕获到任何事件")


@pytest.mark.asyncio
async def test_full_bridge_flow_records_attributed_events(tmp_path, monkeypatch):
    from miqi.agent.tools.filesystem import ReadFileTool
    from miqi.agent.tools.registry import ToolRegistry
    from miqi.execution.factory import create_default_orchestrator
    from miqi.execution.hook_runtime import (
        HookPoint,
        HookRuntime,
        LifecycleHookContext,
    )
    from miqi.runtime.tool_runtime import ToolRuntime
    from miqi.skills.plugin_manager import PluginManager

    project = tmp_path / "project with spaces"
    project.mkdir()
    target = project / "hello.txt"
    target.write_text("hi", encoding="utf-8")

    binary, captured = _fake_sure_capture(tmp_path)
    # 走真实解析链(SURE_BIN → …),不 monkeypatch 桥接内部
    monkeypatch.setenv("SURE_BIN", binary)

    # 真实装配:真实插件目录装载 → 共享 HookRuntime → factory(共享实例)
    user_dir = tmp_path / "user"
    user_dir.mkdir()
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
    tool_runtime = ToolRuntime(orchestrator=orchestrator)

    session_id = "c:accept"
    workspace = str(project)

    def lifecycle(point):
        return LifecycleHookContext(
            hook_point=point,
            data={"session_id": session_id, "workspace": workspace},
        )

    turn = SimpleNamespace(
        turn_id="turn-accept",
        thread_id="thread-accept",
        agent_metadata=SimpleNamespace(name="main"),
        session_id=session_id,
        workspace=project,
    )

    await runtime.run(HookPoint.SESSION_START, lifecycle(HookPoint.SESSION_START))

    allowed = SimpleNamespace(
        name="read_file", id="call-allowed", arguments={"path": str(target)}
    )
    result = await tool_runtime.execute_one(turn, allowed)
    assert result.status == "success", f"允许的调用应成功:{result.result!r}"

    denied = SimpleNamespace(
        name="exec",
        id="call-denied",
        arguments={"command": "git clone https://example.invalid/repo"},
    )
    denied_result = await tool_runtime.execute_one(turn, denied)
    assert denied_result.status != "success", "该调用应被拒绝"

    await runtime.run(HookPoint.STOP, lifecycle(HookPoint.STOP))

    events = _read_events(captured)
    names = [e["event"] for e in events]

    # 1) 归属:每条事件带正确 session 与项目根
    assert all(e["harness_session_id"] == session_id for e in events), names
    assert all(e["project_root"] == workspace for e in events), names
    assert all(e["source"] == "miqi" for e in events)

    # 2) 序列:被拒的 exec 只有 requested、没有 completed
    assert names == ["SessionStart", "PreToolUse", "PostToolUse", "PreToolUse", "Stop"]

    # 4) 未授权内容不被采集:任何载荷都不带工具参数
    for event in events:
        assert "args" not in event and "command" not in event, event

    done = events[2]
    assert done["tool"] == "read_file" and done["result"] == "ok"
    refused = events[3]
    assert refused["tool"] == "exec"
