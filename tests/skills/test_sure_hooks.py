"""阶段5:MiQi→SURE 事件桥接(sure_hooks 模块)与插件清单声明。

被测对象是插件目录中的 `sure_hooks.py`——插件加载器以 sys.path + 顶层
模块名导入它(plugin_manager._make_module_callback),所以这里也按文件
路径加载,不依赖包导入。

桥接的硬约束(方案定稿):事件是观测(fire-and-forget)——SURE 未装、
超时、返回错误时一律静默跳过,绝不阻塞或改变 MiQi 的工具执行结果。
"""

import asyncio
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
    # 与真实导入一致:先登记 sys.modules 再执行——@dataclass 依赖
    # 定义模块能在 sys.modules 中解析(生产侧 import_module 天然如此)。
    sys.modules["sure_hooks"] = module
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
    """假 sure:把每次 stdin 事件追加到 captured.jsonl,返回 (binary, 文件)。

    stdin 走**字节透传**(与真实 sure.exe 的字节流语义一致):文本模式
    sys.stdin.read() 会按 Windows ANSI 代码页解码 UTF-8 载荷,中文路径
    变 lone surrogate,写出时 UnicodeEncodeError(桥接 E2E 实测踩中)。
    """
    captured = tmp_path / "captured.jsonl"
    body = (
        "import sys\n"
        "data = sys.stdin.buffer.read()\n"
        f"with open(r'{captured}', 'ab') as fh:\n"
        "    fh.write(data + bytes([10]))\n"
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
    await sur.flush()  # 投递在后台进行:等排空后再断言

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
    await sur.flush()

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
    await sur.flush()
    event = _read_events(captured)[0]
    assert event["event"] == "PostToolUse"
    assert event["result"] == "ok"


@pytest.mark.asyncio
async def test_session_lifecycle_maps(tmp_path, monkeypatch):
    sur = _load_sure_hooks()
    binary, captured = _capture_bin(tmp_path)
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: binary)

    await sur.on_session_start(_lifecycle_ctx())
    await sur.on_stop(_lifecycle_ctx())  # on_stop 自带排空(等队列收尾再返回)
    await sur.flush()

    events = _read_events(captured)
    assert [e["event"] for e in events] == ["SessionStart", "Stop"]
    assert all(e["source"] == "miqi" for e in events)
    assert all(e["harness_session_id"] == "c:s1" for e in events)
    assert all(e["project_root"] == "D:/work/proj" for e in events)


@pytest.mark.asyncio
async def test_tool_callbacks_return_without_waiting_for_sure(tmp_path, monkeypatch):
    """P2(#1278 评审第 2 轮):观测不阻塞执行——回调投递即返回,
    SURE 子进程的等待/超时发生在后台工作里,与工具执行解耦。"""
    sur = _load_sure_hooks()
    hang_bin = _wrap_as_sure(tmp_path, "import time\ntime.sleep(30)\n")
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: hang_bin)
    monkeypatch.setattr(sur, "INGEST_TIMEOUT_SECONDS", 2.0)

    started = time.monotonic()
    await sur.on_pre_tool_use(_tool_ctx())
    await sur.on_post_tool_use(_tool_ctx())
    elapsed = time.monotonic() - started

    # 同步等待时这里会是 ~2s/次;解耦后应当毫秒级返回
    assert elapsed < 0.35, f"工具回调不得等待 SURE(实测 {elapsed:.2f}s)"
    stats = sur.bridge_stats()
    assert stats["attempted"] == 2
    assert stats["enqueued"] == 2
    assert stats["dropped"] == 0

    # 收尾:等后台把两次 ingest(各 ~2s 超时)排空,避免悬挂任务
    await sur.flush(10.0)

    # 卡死的 SURE 从未确认写入:计 failed 而非 ingested(评审第 3 轮口径)
    stats = sur.bridge_stats()
    assert stats["ingested"] == 0
    assert stats["failed"] == 2


@pytest.mark.asyncio
async def test_events_ingest_in_order(tmp_path, monkeypatch):
    """有界队列保序:同一工具的前/后事件按投递顺序落盘。"""
    sur = _load_sure_hooks()
    binary, captured = _capture_bin(tmp_path)
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: binary)

    await sur.on_pre_tool_use(_tool_ctx())
    await sur.on_post_tool_use(_tool_ctx())
    await sur.flush()

    assert [e["event"] for e in _read_events(captured)] == ["PreToolUse", "PostToolUse"]


@pytest.mark.asyncio
async def test_stats_distinguish_written_from_attempted(tmp_path, monkeypatch):
    """评审第 3 轮 P3:统计区分「投递 / 入队 / 写入成功 / 未写入」。

    只有子进程退出码 0 才计 ingested;SURE 未装/超时/出错/非零退出计
    failed;队满计 dropped——此前 ingested 把静默跳过也计数,排障时会
    误读为「SURE 已收到」。
    """
    sur = _load_sure_hooks()
    binary, captured = _capture_bin(tmp_path)
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: binary)

    await sur.on_pre_tool_use(_tool_ctx())
    await sur.flush()
    written = sur.bridge_stats()
    assert (
        written["attempted"], written["enqueued"], written["ingested"], written["failed"]
    ) == (1, 1, 1, 0)

    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: None)
    await sur.on_pre_tool_use(_tool_ctx())
    await sur.flush()
    skipped = sur.bridge_stats()
    assert (
        skipped["attempted"], skipped["enqueued"], skipped["ingested"], skipped["failed"]
    ) == (2, 2, 1, 1)
    assert len(_read_events(captured)) == 1, "捕获文件只应有成功写入的那条"


@pytest.mark.asyncio
async def test_full_queue_drops_quietly(tmp_path, monkeypatch):
    """队列满 → 静默丢弃并计数(观测绝不反过来阻塞工具),不抛异常。"""
    sur = _load_sure_hooks()
    hang_bin = _wrap_as_sure(tmp_path, "import time\ntime.sleep(30)\n")
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: hang_bin)
    monkeypatch.setattr(sur, "INGEST_TIMEOUT_SECONDS", 30.0)
    monkeypatch.setattr(sur, "MAX_QUEUE", 1)

    started = time.monotonic()
    for _ in range(4):
        await sur.on_pre_tool_use(_tool_ctx())
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, "满队列也必须立即返回"
    stats = sur.bridge_stats()
    # 4 次投递都发生在排空任务首次调度之前(回调不让出事件循环),单线程
    # 调度下确定性:首条入队,其余 3 条队满丢弃(评审第 3 轮口径:
    # attempted 记全部投递,enqueued 只记真正入队的)
    assert stats["attempted"] == 4
    assert stats["enqueued"] == 1
    assert stats["dropped"] == 3, stats

    # 收尾:等排空任务真正起来(卡进 30s ingest)再取消,并等取消清理完成(有界)
    await asyncio.sleep(0.1)
    st = sur._state()
    assert st.worker_ref is not None and st.worker_ref() is not None
    st.worker_ref().cancel()
    await sur.flush(5.0)


@pytest.mark.asyncio
async def test_missing_binary_is_silent(tmp_path, monkeypatch):
    sur = _load_sure_hooks()
    _, captured = _capture_bin(tmp_path)
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: None)

    await sur.on_pre_tool_use(_tool_ctx())  # 不应抛出
    await sur.flush()  # 契约:入队后收尾排空(不留半途 spawn 给 teardown)

    assert not captured.exists()
    stats = sur.bridge_stats()
    assert stats["attempted"] == 1 and stats["enqueued"] == 1
    assert stats["ingested"] == 0, "SURE 未装不算写入成功(评审第 3 轮口径)"
    assert stats["failed"] == 1


@pytest.mark.asyncio
async def test_failing_ingest_is_silent(tmp_path, monkeypatch):
    sur = _load_sure_hooks()
    failing = _wrap_as_sure(tmp_path, "import sys\nsys.exit(3)\n")
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: failing)

    await sur.on_pre_tool_use(_tool_ctx())  # 退出码非零不应抛出
    await sur.flush()  # 契约:入队后收尾排空(不留半途 spawn 给 teardown)

    stats = sur.bridge_stats()
    assert stats["ingested"] == 0, "非零退出码不算写入成功(评审第 3 轮口径)"
    assert stats["failed"] == 1


@pytest.mark.asyncio
async def test_ingest_timeout_is_silent_and_bounded(tmp_path, monkeypatch):
    sur = _load_sure_hooks()
    slow = _wrap_as_sure(tmp_path, "import time\ntime.sleep(30)\n")
    monkeypatch.setattr(sur, "resolve_sure_bin", lambda: slow)
    monkeypatch.setattr(sur, "INGEST_TIMEOUT_SECONDS", 0.5)

    started = time.monotonic()
    await sur.on_pre_tool_use(_tool_ctx())  # 投递即返回,不等 ingest
    assert time.monotonic() - started < 1.0

    await sur.flush(5.0)  # 后台按 0.5s 超时静默收掉,排空有界

    stats = sur.bridge_stats()
    assert stats["ingested"] == 0, "超时不算写入成功(评审第 3 轮口径)"
    assert stats["failed"] == 1


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

