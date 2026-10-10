"""阶段 3:SureTaskRuntime——spawn/心跳/报告/取消/失败(真实子进程,走 mock CLI)。

mock CLI 见 scripts/mock_sure_cli.py;包装成平台可执行的"sure"后经
bin_provider 注入。测试用全量 env(而非生产白名单),以便 MOCK_SURE_* 旋钮
经环境变量控制 mock 行为。
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_CLI = REPO_ROOT / "scripts" / "mock_sure_cli.py"


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


@pytest.fixture
def fake_sure_bin(tmp_path) -> str:
    mock_body = (
        "import runpy, sys\n"
        f"sys.argv = ['mock_sure_cli.py'] + sys.argv[1:]\n"
        f"runpy.run_path(r'{MOCK_CLI}', run_name='__main__')\n"
    )
    return _wrap_as_sure(tmp_path, mock_body)


def _runtime(binary: str | None, **overrides):
    from miqi.runtime.sure_task_runtime import SureTaskRuntime

    kwargs = dict(
        bin_provider=lambda: binary,
        env_builder=lambda: {**os.environ},  # 测试专用:全量 env 以驱动 mock 旋钮
        progress_interval=0.15,
    )
    kwargs.update(overrides)
    return SureTaskRuntime(**kwargs)


async def _wait_for(events: list, kinds: tuple[str, ...], timeout_s: float = 15.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if any(kind in kinds for kind, _ in events):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"未等到事件 {kinds};已收到: {[k for k, _ in events]}")


def _pipe_transports_closed(proc) -> bool:
    """stdout/stderr 两条管道传输是否都已关闭。

    子进程传输可能先自闭合,而管道传输单独悬置(悬置读流在事件循环关闭后
    被 GC → Windows proactor __del__ 告警);防线必须查管道层。
    """
    pipes = [p.pipe for p in proc._transport._pipes.values() if p is not None]
    return bool(pipes) and all(p.is_closing() for p in pipes)


@pytest.mark.asyncio
async def test_check_flow_emits_progress_then_report(fake_sure_bin, tmp_path):
    events: list[tuple[str, dict]] = []

    async def on_event(kind: str, data: dict) -> None:
        events.append((kind, data))

    rt = _runtime(fake_sure_bin)
    started = await rt.start(client_id="c1", project=str(tmp_path), on_event=on_event)
    assert started["taskId"].startswith("sure-")
    assert started["project"] == str(tmp_path)
    # 启动即有一条进度(0ms),随后心跳
    await _wait_for(events, ("sure_check_report",))
    kinds = [k for k, _ in events]
    assert kinds[0] == "sure_check_progress"
    assert kinds.count("sure_check_progress") >= 1

    report = next(d for k, d in events if k == "sure_check_report")
    envelope = report["envelope"]
    assert envelope["outcome"] == "not_green"
    assert envelope["details"]["report"]["totals"]["open_findings"] == 5
    assert report["taskId"] == started["taskId"]
    # 完成后状态清空
    assert rt.status(client_id="c1") is None


@pytest.mark.asyncio
async def test_cancel_kills_process_tree_and_emits_cancelled(
    fake_sure_bin, tmp_path, monkeypatch
):
    monkeypatch.setenv("MOCK_SURE_DELAY_MS", "30000")  # mock 睡 30s,给我们取消窗口
    events: list[tuple[str, dict]] = []

    async def on_event(kind: str, data: dict) -> None:
        events.append((kind, data))

    rt = _runtime(fake_sure_bin)
    await rt.start(client_id="c1", project=str(tmp_path), on_event=on_event)
    await _wait_for(events, ("sure_check_progress",))
    assert rt.status(client_id="c1") is not None

    assert await rt.cancel(client_id="c1") is True
    await _wait_for(events, ("sure_check_cancelled",), timeout_s=20.0)

    kinds = [k for k, _ in events]
    assert "sure_check_cancelled" in kinds
    assert "sure_check_report" not in kinds, "取消不得产出报告(与 CLI 语义一致)"
    assert rt.status(client_id="c1") is None
    # 重复取消 → False(任务已了)
    assert await rt.cancel(client_id="c1") is False


@pytest.mark.asyncio
async def test_busy_rejects_second_start(fake_sure_bin, tmp_path, monkeypatch):
    from miqi.runtime.sure_task_runtime import SureBusyError

    monkeypatch.setenv("MOCK_SURE_DELAY_MS", "5000")

    async def on_event(kind: str, data: dict) -> None:
        pass

    rt = _runtime(fake_sure_bin)
    await rt.start(client_id="c1", project=str(tmp_path), on_event=on_event)
    try:
        with pytest.raises(SureBusyError):
            await rt.start(client_id="c1", project=str(tmp_path), on_event=on_event)
        # 另一个 client 不受影响(每客户端一个任务)
        other = await rt.start(client_id="c2", project=str(tmp_path), on_event=on_event)
        assert other["taskId"]
    finally:
        await rt.cancel(client_id="c1")
        await rt.cancel(client_id="c2")


@pytest.mark.asyncio
async def test_unavailable_binary_raises(tmp_path):
    from miqi.runtime.sure_task_runtime import SureUnavailableError

    rt = _runtime(None)

    async def on_event(kind: str, data: dict) -> None:
        pass

    with pytest.raises(SureUnavailableError):
        await rt.start(client_id="c1", project=str(tmp_path), on_event=on_event)


@pytest.mark.asyncio
async def test_invalid_report_output_emits_failed(tmp_path):
    bad_bin = _wrap_as_sure(tmp_path, "print('this is not json')")
    events: list[tuple[str, dict]] = []

    async def on_event(kind: str, data: dict) -> None:
        events.append((kind, data))

    rt = _runtime(bad_bin)
    await rt.start(client_id="c1", project=str(tmp_path), on_event=on_event)
    await _wait_for(events, ("sure_check_failed",))
    failure = next(d for k, d in events if k == "sure_check_failed")
    assert failure["code"] == "SURE_REPORT_INVALID"
    assert failure["message"]


@pytest.mark.asyncio
async def test_oversized_stdout_is_bounded_and_process_killed(tmp_path, monkeypatch):
    """stdout 超过 8MiB 上限:读取阶段即截断并终止进程(评审:读取要有界)。

    旧实现 communicate() 整段读回后才查 len——失控输出会先把内存吃满;
    新实现分块累计,超限立即返回溢出标记并树杀写入方(不等它自然结束)。
    """
    import miqi.runtime.sure_task_runtime as rt_mod

    big_bin = _wrap_as_sure(
        tmp_path,
        "import sys\nsys.stdout.write('x' * (9 * 1024 * 1024))\n",
    )
    calls = []
    real_kill = rt_mod._kill_process_tree

    async def spy(proc, **kwargs):
        calls.append(proc)
        await real_kill(proc, **kwargs)

    monkeypatch.setattr(rt_mod, "_kill_process_tree", spy)

    events: list[tuple[str, dict]] = []

    async def on_event(kind: str, data: dict) -> None:
        events.append((kind, data))

    rt = _runtime(big_bin)
    await rt.start(client_id="c1", project=str(tmp_path), on_event=on_event)
    await _wait_for(events, ("sure_check_failed",))
    failure = next(d for k, d in events if k == "sure_check_failed")
    assert failure["code"] == "SURE_OUTPUT_TOO_LARGE"
    assert failure["message"]
    assert len(calls) == 1, "超限必须终止写入方进程树(不能等它自然结束)"
    assert calls[0].returncode is not None, "超限后子进程必须已被回收"
    assert _pipe_transports_closed(calls[0]), (
        "超限中止后 stdout/stderr 管道传输必须已收尾——读到一半停下的管道"
        "不会自行走到 EOF,会拖到循环关闭后才被 GC(Win: unclosed transport)"
    )


@pytest.mark.asyncio
async def test_stop_all_kills_running_tasks(fake_sure_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_SURE_DELAY_MS", "30000")

    async def on_event(kind: str, data: dict) -> None:
        pass

    rt = _runtime(fake_sure_bin)
    await rt.start(client_id="c1", project=str(tmp_path), on_event=on_event)
    await asyncio.sleep(0.3)
    await rt.stop_all()
    assert rt.status(client_id="c1") is None


def test_env_whitelist_only_allows_named_vars():
    from miqi.runtime.sure_task_runtime import build_sure_env

    base = {
        "SystemRoot": r"C:\Windows",
        "PATH": r"C:\bin",
        "TEMP": r"C:\tmp",
        "LOCALAPPDATA": r"C:\lad",
        "APPDATA": r"C:\ad",
        "USERPROFILE": r"C:\u",
        "SECRET_TOKEN": "leak-me-not",
        "MIQI_HOME": "leak-me-not",
        "OPENAI_API_KEY": "leak-me-not",
    }
    env = build_sure_env(base)
    assert env == {
        "SystemRoot": r"C:\Windows",
        "PATH": r"C:\bin",
        "TEMP": r"C:\tmp",
        "LOCALAPPDATA": r"C:\lad",
        "APPDATA": r"C:\ad",
        "USERPROFILE": r"C:\u",
    }
    # SURE_BIN 在白名单内(子进程兜底解析需要)
    assert build_sure_env({**base, "SURE_BIN": r"C:\sure.exe"})["SURE_BIN"] == r"C:\sure.exe"


def test_mock_cli_emits_strict_utf8_json(tmp_path):
    """CI(windows-latest)回归:mock 必须像真 sure.exe 一样输出**严格 UTF-8**。

    生产白名单会剥掉 PYTHONUTF8;若 mock 走 locale 编码,中文在 cp1252 下
    直接 UnicodeEncodeError(CI windows 实测),在 GBK 下字节碎裂。锁定
    sys.stdout.reconfigure(utf-8)。
    """
    import json
    import subprocess

    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    proc = subprocess.run(
        [sys.executable, str(MOCK_CLI), "check", str(tmp_path), "--format", "json"],
        capture_output=True,
        env=env,
    )
    text = proc.stdout.decode("utf-8")  # 严格解码:任一非法字节即失败
    data = json.loads(text)
    assert data["details"]["report"]["totals"]["open_findings"] == 5
    # 中文内容无损往返(fixture 的 settings_file 里含用户名)
    assert "董加钧" in text


@pytest.mark.asyncio
async def test_cancel_refused_after_process_exit(fake_sure_bin, tmp_path):
    """#1273 评审:进程已退出、报告在途的窗口内 cancel 必须拒绝,保住成品报告。"""
    from miqi.runtime.sure_task_runtime import SureTask

    rt = _runtime(fake_sure_bin)

    class _StubProc:
        returncode = 1  # 已退出(not_green 是正常结果)
        pid = 424242

    task = SureTask(
        task_id="sure-exited",
        client_id="c1",
        project=str(tmp_path),
        started_monotonic=0.0,
        started_at_ms=0,
    )
    task.proc = _StubProc()  # type: ignore[assignment]
    rt._tasks[task.task_id] = task

    assert await rt.cancel(client_id="c1") is False
    assert task.cancelled is False


# ── 健康检查:超时清理与退出码判定(外部评审第 2 轮)────────────────────


@pytest.mark.asyncio
async def test_health_timeout_kills_child_process(tmp_path, monkeypatch):
    """sure --version 卡死超时后,子进程必须被显式终止并回收(不留孤儿)。"""
    import miqi.runtime.sure_task_runtime as rt_mod

    slow_bin = _wrap_as_sure(tmp_path, "import time\ntime.sleep(300)\n")
    calls = []
    real_kill = rt_mod._kill_process_tree

    async def spy(proc, **kwargs):
        calls.append(proc)
        await real_kill(proc, **kwargs)

    monkeypatch.setattr(rt_mod, "_kill_process_tree", spy)

    res = await rt_mod.probe_sure_health(
        bin_provider=lambda: slow_bin,
        env_builder=lambda: {**os.environ},
        timeout=0.5,
    )
    assert res["installed"] is False
    assert len(calls) == 1, "超时必须触发显式进程清理"
    assert calls[0].returncode is not None, "清理后子进程必须已被回收"
    assert _pipe_transports_closed(calls[0]), (
        "超时后 stdout/stderr 管道传输必须已关闭——悬置的读流不会自行收尾,"
        "会拖到事件循环关闭后才被 GC(Windows proactor: unclosed transport)"
    )


@pytest.mark.asyncio
async def test_health_nonzero_exit_is_not_installed(tmp_path):
    """--version 退出码非零 → installed=False(不能把"能启动"当"能工作")。"""
    import miqi.runtime.sure_task_runtime as rt_mod

    bad_bin = _wrap_as_sure(tmp_path, "import sys\nprint('sure 0.1.2')\nsys.exit(3)\n")
    res = await rt_mod.probe_sure_health(
        bin_provider=lambda: bad_bin,
        env_builder=lambda: {**os.environ},
        timeout=5.0,
    )
    assert res["installed"] is False
    assert res["error"]


@pytest.mark.asyncio
async def test_health_cancellation_kills_child_process(tmp_path, monkeypatch):
    """协程被外部取消也必须终止并回收子进程,且取消语义原样上抛(外部评审 P2)。

    CancelledError 继承 BaseException,不落入 except Exception——取消若落在
    communicate 等待期,原实现会整体跳过清理分支,卡死的 sure --version 变孤儿。
    """
    import miqi.runtime.sure_task_runtime as rt_mod

    marker = tmp_path / "health_started.marker"
    slow_bin = _wrap_as_sure(
        tmp_path,
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('up', encoding='utf-8')\n"
        "import time\ntime.sleep(15)\n",
    )
    calls = []
    real_kill = rt_mod._kill_process_tree

    async def spy(proc, **kwargs):
        calls.append(proc)
        await real_kill(proc, **kwargs)

    monkeypatch.setattr(rt_mod, "_kill_process_tree", spy)

    task = asyncio.create_task(
        rt_mod.probe_sure_health(
            bin_provider=lambda: slow_bin,
            env_builder=lambda: {**os.environ},
            timeout=60.0,  # 远大于取消点:必须走"取消"路径而非"超时"路径
        )
    )
    # 等子进程真正起来(标记文件)再取消,保证取消点落在 communicate 等待期内
    deadline = asyncio.get_event_loop().time() + 15.0
    while not marker.exists():
        assert asyncio.get_event_loop().time() < deadline, "被测子进程未启动"
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 1, "取消必须触发显式进程清理"
    assert calls[0].returncode is not None, "清理后子进程必须已被回收"
    assert _pipe_transports_closed(calls[0]), (
        "取消后 stdout/stderr 管道传输必须已关闭——悬置的读流不会自行收尾,"
        "会拖到事件循环关闭后才被 GC(Windows proactor: unclosed transport)"
    )
