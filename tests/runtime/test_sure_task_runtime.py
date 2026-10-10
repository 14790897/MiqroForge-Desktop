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
