"""阶段 3:sure/* AppServer handlers——参数校验 / 事件转发 / 健康检查。"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_CLI = REPO_ROOT / "scripts" / "mock_sure_cli.py"


def _fake_sure_bin(tmp_path: Path) -> str:
    script = tmp_path / "sure_impl.py"
    script.write_text(
        "import runpy, sys\n"
        f"sys.argv = ['mock_sure_cli.py'] + sys.argv[1:]\n"
        f"runpy.run_path(r'{MOCK_CLI}', run_name='__main__')\n",
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
    return str(wrapper)


def _make_server(binary: str | None):
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry
    from miqi.runtime.sure_app_handlers import register_sure_handlers
    from miqi.runtime.sure_task_runtime import SureTaskRuntime

    registry = ClientSessionRegistry()
    runtime = SureTaskRuntime(
        bin_provider=lambda: binary,
        env_builder=lambda: {**os.environ},
        progress_interval=0.15,
    )
    registry.bridge_context = {"sure_task_runtime": runtime}
    server = AppServer(registry)
    register_sure_handlers(server)
    return server, registry, runtime


async def _dispatch(server, method, params, client_id="test-client"):
    return await server.dispatch(
        request_id="req-1", method=method, params=params,
        client_id=client_id, session_id=None,
    )


@pytest.mark.asyncio
async def test_start_rejects_bad_project(tmp_path):
    server, _, _ = _make_server(None)
    resp = await _dispatch(server, "sure.check.start", {})
    assert resp["code"] == "INVALID_PARAMS"

    resp = await _dispatch(server, "sure.check.start", {"project": "relative/path"})
    assert resp["code"] == "INVALID_PARAMS"

    resp = await _dispatch(
        server, "sure.check.start", {"project": str(tmp_path / "no-such-dir")}
    )
    assert resp["code"] == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_start_without_binary_reports_unavailable(tmp_path):
    server, _, _ = _make_server(None)
    resp = await _dispatch(server, "sure.check.start", {"project": str(tmp_path)})
    assert resp["code"] == "SURE_UNAVAILABLE"
    assert "SURE" in resp["error"]


@pytest.mark.asyncio
async def test_start_streams_events_as_orphans(tmp_path):
    server, _, _ = _make_server(_fake_sure_bin(tmp_path))
    events: list[dict] = []

    async def sink(envelope):
        events.append(envelope)

    server.set_event_sink("test-client", sink)

    resp = await _dispatch(server, "sure.check.start", {"project": str(tmp_path)})
    assert "result" in resp
    assert resp["result"]["taskId"].startswith("sure-")

    deadline = asyncio.get_event_loop().time() + 15.0
    while asyncio.get_event_loop().time() < deadline:
        if any(e["event"] == "sure_check_report" for e in events):
            break
        await asyncio.sleep(0.05)

    names = [e["event"] for e in events]
    assert "sure_check_progress" in names
    assert "sure_check_report" in names
    report = next(e for e in events if e["event"] == "sure_check_report")
    # 孤儿事件(request_id=None)→ Electron bridge.ts 自动转发到渲染层
    assert report["request_id"] is None
    assert report["data"]["envelope"]["details"]["report"]["totals"]["open_findings"] == 5


@pytest.mark.asyncio
async def test_busy_and_cancel_and_status(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_SURE_DELAY_MS", "30000")
    server, _, _ = _make_server(_fake_sure_bin(tmp_path))
    events: list[dict] = []

    async def sink(envelope):
        events.append(envelope)

    server.set_event_sink("test-client", sink)

    resp = await _dispatch(server, "sure.check.start", {"project": str(tmp_path)})
    assert "result" in resp

    resp2 = await _dispatch(server, "sure.check.start", {"project": str(tmp_path)})
    assert resp2["code"] == "SURE_BUSY"

    status = await _dispatch(server, "sure.check.status", {})
    assert status["result"]["task"] is not None
    assert status["result"]["task"]["project"] == str(tmp_path)

    cancel = await _dispatch(server, "sure.check.cancel", {})
    assert cancel["result"]["ok"] is True

    deadline = asyncio.get_event_loop().time() + 20.0
    while asyncio.get_event_loop().time() < deadline:
        if any(e["event"] == "sure_check_cancelled" for e in events):
            break
        await asyncio.sleep(0.05)
    assert any(e["event"] == "sure_check_cancelled" for e in events)

    status2 = await _dispatch(server, "sure.check.status", {})
    assert status2["result"]["task"] is None


@pytest.mark.asyncio
async def test_health_reports_version_and_missing(tmp_path, monkeypatch):
    import miqi.runtime.sure_task_runtime as sure_rt_mod

    fake = _fake_sure_bin(tmp_path)
    monkeypatch.setattr(sure_rt_mod, "resolve_sure_bin", lambda: fake)
    server, _, _ = _make_server(None)
    resp = await _dispatch(server, "sure.health", {})
    assert resp["result"]["installed"] is True
    assert resp["result"]["version"] == "9.9.9-mock"

    monkeypatch.setattr(sure_rt_mod, "resolve_sure_bin", lambda: None)
    resp2 = await _dispatch(server, "sure.health", {})
    assert resp2["result"]["installed"] is False
    assert resp2["result"]["error"]


# ── 阶段 4:repair / recheck 命令 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_rejects_unknown_command(tmp_path):
    server, _, _ = _make_server(_fake_sure_bin(tmp_path))
    resp = await _dispatch(
        server, "sure.check.start", {"project": str(tmp_path), "command": "doctor"}
    )
    assert resp["code"] == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_repair_command_streams_contracts(tmp_path):
    server, _, _ = _make_server(_fake_sure_bin(tmp_path))
    events: list[dict] = []

    async def sink(envelope):
        events.append(envelope)

    server.set_event_sink("test-client", sink)

    resp = await _dispatch(
        server, "sure.check.start", {"project": str(tmp_path), "command": "repair"}
    )
    assert resp["result"]["command"] == "repair"

    deadline = asyncio.get_event_loop().time() + 15.0
    while asyncio.get_event_loop().time() < deadline:
        if any(e["event"] == "sure_check_report" for e in events):
            break
        await asyncio.sleep(0.05)
    report = next(e for e in events if e["event"] == "sure_check_report")
    assert report["data"]["command"] == "repair"
    assert len(report["data"]["envelope"]["details"]["repairs"]) == 5
