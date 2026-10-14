"""阶段 3:sure/* AppServer handlers——参数校验 / 事件转发 / 健康检查。"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_CLI = REPO_ROOT / "scripts" / "mock_sure_cli.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "sure"


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

    # 2026-10-14:完成后的 status 快照要带回最近报告——面板切页/重开靠它恢复
    # 完成态(此前只带回"运行中",完成报告一离开面板就丢)
    status = await _dispatch(server, "sure.check.status", {})
    assert status["result"]["task"] is None
    last = status["result"]["lastReport"]
    assert last is not None
    assert last["command"] == "check"
    assert last["details"]["report"]["totals"]["open_findings"] == 5
    # 修复派发并发快照(与 spawn 闸同源):无会话/无代理时如实 0 + 默认上限 3
    assert status["result"]["runningAgents"] == 0
    assert status["result"]["agentCap"] == 3


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
    # 取消不产生完成报告:lastReport 保持 None
    assert status2["result"]["lastReport"] is None


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


def _bin_with_doctor(tmp_path: Path, doctor_mode: str) -> str:
    """假 sure:--version 正常;doctor 按 doctor_mode 输出(with-miqi/plain/fail)。"""
    script = tmp_path / f"sure_impl_{doctor_mode}.py"
    body = (
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if args and args[0] == '--version':\n"
        "    print('sure 0.1.2')\n"
        "elif args and args[0] == 'doctor':\n"
    )
    if doctor_mode == "with-miqi":
        body += "    print('  miqi                 hooks that record what an agent did')\n"
    elif doctor_mode == "plain":
        body += "    print('  claude-code          hooks that record what an agent did')\n"
    else:  # fail
        body += "    sys.exit(3)\n"
    script.write_text(body, encoding="utf-8")
    if os.name == "nt":
        wrapper = tmp_path / f"sure_{doctor_mode}.cmd"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
        )
    else:
        wrapper = tmp_path / f"sure_{doctor_mode}"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
        )
        wrapper.chmod(0o755)
    return str(wrapper)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("doctor_mode", "expected"),
    [("with-miqi", True), ("plain", False), ("fail", None)],
)
async def test_health_reports_miqi_support(tmp_path, monkeypatch, doctor_mode, expected):
    """易用性批次:健康检查探测该构建是否含 MiQi 事件集成(doctor 清单行)。

    True=列出 miqi(fork 构建);False=未列出(官方版);None=探测失败(无法判断)。
    """
    import miqi.runtime.sure_task_runtime as sure_rt_mod

    fake = _bin_with_doctor(tmp_path, doctor_mode)
    monkeypatch.setattr(sure_rt_mod, "resolve_sure_bin", lambda: fake)
    server, _, _ = _make_server(None)
    resp = await _dispatch(server, "sure.health", {})
    assert resp["result"]["installed"] is True, resp
    assert resp["result"]["supports_miqi"] is expected, resp


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


# ── 阶段 4:「交给 AI 修复」(spawn code-agent 子代理)────────────────────


class _FakeControl:
    def __init__(self):
        self.calls: list[dict] = []

    async def spawn(self, agent_type, task, *, label=None, user_roots=None, **kwargs):
        from types import SimpleNamespace

        self.calls.append(
            {"agent_type": agent_type, "task": task, "label": label, "user_roots": user_roots}
        )
        # 与真实 LiveAgent 同形:thread_id 恒在(作业自己的线程,事件 drain
        # 的 set_thread_session 映射要用它)。
        return SimpleNamespace(agent_id="agent-1", thread_id="agent-1-thread")


def _make_fix_server(
    *,
    with_session: bool,
    cached_repair_project: str | None = None,
    auto_session: bool = False,
):
    from types import SimpleNamespace

    from miqi.runtime.app_server import AppServer, ClientSessionRegistry
    from miqi.runtime.sure_app_handlers import register_sure_handlers
    from miqi.runtime.sure_report import parse_sure_output
    from miqi.runtime.sure_task_runtime import SureTaskRuntime

    registry = ClientSessionRegistry()
    runtime = SureTaskRuntime(bin_provider=lambda: None, env_builder=lambda: {**os.environ})
    registry.bridge_context = {"sure_task_runtime": runtime}
    control = _FakeControl()
    if with_session:
        registry._sessions["c:s1"] = SimpleNamespace(
            services=SimpleNamespace(agent_control=control)
        )
        registry._client_sessions["c"] = {"c:s1"}  # list_sessions 读取处
        registry._session_clients["c:s1"] = {"c"}
        registry._last_activity["c:s1"] = 100.0
    if auto_session:
        # 2026-10-14:模拟 bridge 发布的 ensure_session——登记调用并真的把
        # 会话注册进 registry(修复派发应在"没有任何会话"时用它自动开一个)。
        ensured: list[tuple[str, str]] = []
        control.ensured = ensured  # 测试从这里读记录,不改返回元组

        async def _ensure(client_id: str, session_key: str) -> None:
            ensured.append((client_id, session_key))
            sid = f"{client_id}:{session_key}"
            registry._sessions[sid] = SimpleNamespace(
                services=SimpleNamespace(agent_control=control)
            )
            registry._client_sessions.setdefault(client_id, set()).add(sid)
            registry._session_clients[sid] = {client_id}
            registry._last_activity[sid] = 1.0

        registry.bridge_context["ensure_session"] = _ensure
    if cached_repair_project is not None:
        envelope = parse_sure_output(
            (FIXTURES / "report-repair-fake-payment.json").read_text(encoding="utf-8")
        )
        # 报告里的项目与请求项目一致才允许启动修复(评审第 2 轮 P2)
        envelope.details.project = cached_repair_project
        runtime._last_reports["c"] = envelope
    server = AppServer(registry)
    register_sure_handlers(server)
    return server, registry, runtime, control


def test_build_fix_task_carries_contract_and_hard_rules():
    from miqi.runtime.sure_app_handlers import build_fix_task
    from miqi.runtime.sure_report import parse_sure_output

    envelope = parse_sure_output(
        (FIXTURES / "report-repair-fake-payment.json").read_text(encoding="utf-8")
    )
    text = build_fix_task("D:/proj", list(envelope.details.repairs))
    assert "【契约 1/5】" in text
    assert "必须修复" in text
    assert "禁止走捷径" in text
    assert "chk_noopfakepayment" in text  # 复核项逐字保留
    assert "不算证据" in text  # 硬规则:完成语不是证据
    assert "D:/proj" in text


@pytest.mark.asyncio
async def test_fix_requires_cached_repair_contract(tmp_path):
    server, _, _, _ = _make_fix_server(with_session=True, cached_repair_project=None)
    resp = await _dispatch(
        server, "sure.fix.start", {"project": str(tmp_path)}, client_id="c"
    )
    assert resp["code"] == "SURE_NO_CONTRACT"


@pytest.mark.asyncio
async def test_fix_rejects_project_mismatch(tmp_path):
    """评审第 2 轮 P2:报告中的项目与请求项目不一致 → 拒绝启动修复子代理。"""
    server, _, _, control = _make_fix_server(
        with_session=True, cached_repair_project=str(tmp_path / "another-project")
    )
    resp = await _dispatch(
        server, "sure.fix.start", {"project": str(tmp_path)}, client_id="c"
    )
    assert resp["code"] == "SURE_PROJECT_MISMATCH"
    assert "重新生成修复契约" in resp["error"]
    assert not control.calls, "不一致时不得派发子代理"


@pytest.mark.asyncio
async def test_fix_accepts_project_spelling_differences(tmp_path):
    """同一路径的不同书写(尾部分隔符等)不算不一致。"""
    import os as _os

    spelled = str(tmp_path) + _os.sep
    server, _, _, control = _make_fix_server(
        with_session=True, cached_repair_project=str(tmp_path)
    )
    resp = await _dispatch(
        server, "sure.fix.start", {"project": spelled}, client_id="c"
    )
    assert "result" in resp, resp
    assert len(control.calls) == 1


@pytest.mark.asyncio
async def test_fix_requires_session(tmp_path):
    server, _, _, _ = _make_fix_server(
        with_session=False, cached_repair_project=str(tmp_path)
    )
    resp = await _dispatch(
        server, "sure.fix.start", {"project": str(tmp_path)}, client_id="c"
    )
    assert resp["code"] == "SURE_NO_SESSION"


@pytest.mark.asyncio
async def test_fix_without_session_auto_creates_one(tmp_path):
    """2026-10-14:没有会话时自动开一个,不再爆红要求用户先手动创建。

    借用 bridge 发布的 ensure_session;建好后照常派发,目标会话即为新建的
    那只(用户可在聊天列表里围观)。
    """
    server, registry, _, control = _make_fix_server(
        with_session=False,
        cached_repair_project=str(tmp_path),
        auto_session=True,
    )
    resp = await _dispatch(
        server, "sure.fix.start", {"project": str(tmp_path)}, client_id="c"
    )
    assert "result" in resp, resp
    assert len(control.ensured) == 1
    cid, skey = control.ensured[0]
    assert cid == "c"
    assert skey.startswith("desktop:")
    assert len(control.calls) == 1  # 照常派发子代理
    assert resp["result"]["sessionKey"] == f"c:{skey}"


@pytest.mark.asyncio
async def test_fix_spawns_code_agent_with_user_root(tmp_path):
    server, _, _, control = _make_fix_server(
        with_session=True, cached_repair_project=str(tmp_path)
    )
    resp = await _dispatch(
        server, "sure.fix.start", {"project": str(tmp_path)}, client_id="c"
    )
    assert "result" in resp, resp
    assert resp["result"]["agentId"] == "agent-1"
    assert resp["result"]["sessionKey"] == "c:s1"
    assert len(control.calls) == 1
    call = control.calls[0]
    assert call["agent_type"] == "code-agent"
    assert call["user_roots"] == [str(tmp_path)]  # 项目授权给子代理
    assert "不算证据" in call["task"]


@pytest.mark.asyncio
async def test_fix_registers_a_job_event_drain(tmp_path):
    """2026-10-14:派发成功后要补一条作业事件 drain——作业的流没人排空会冻住它。

    现场:作业回合的事件仍落进会话事件队列(4096 有界;唯一的消费者是
    chat.send 的 drain),而面板派发没有聊天请求兜着——队列填满后作业冻在
    第一个增量上(agent_jobs 失速 dump:作业任务停在 turn_runner 的 emit,
    链条末端是 asyncio 队列的 put)。桥通过 bridge_context 发布
    `drain_agent_job_events`;派发成功后必须调用它,参数指回本会话与这只作业。
    """
    server, registry, _, control = _make_fix_server(
        with_session=True, cached_repair_project=str(tmp_path)
    )
    drained: list[dict] = []

    async def _drain(**kwargs):
        drained.append(kwargs)

    registry.bridge_context["drain_agent_job_events"] = _drain
    resp = await _dispatch(
        server, "sure.fix.start", {"project": str(tmp_path)}, client_id="c"
    )
    assert "result" in resp, resp
    assert len(control.calls) == 1
    assert len(drained) == 1, "派发后没有注册作业事件 drain"
    assert drained[0]["client_id"] == "c"
    assert drained[0]["session_key"] == "c:s1"
    assert drained[0]["session_id"] == "c:s1"
    assert drained[0]["agent_id"] == "agent-1"
    assert drained[0]["thread_id"] == "agent-1-thread"
    # 必须是 **RuntimeSession 对象本身**（它才持有 next_event;drain 靠它消费
    # 会话事件队列）。此前传的是 `_get_runtime` 的 SureTaskRuntime——drain 任务
    # 第一次 next_event 就 AttributeError 而死,队列无人排空,子代理流一满
    # (4096)就冻在 emit 上;本机通过只是事件量没到上限的运气,CI 重试必挂
    # (2026-10-14 run 38525430992 的两个 900s stall,链条同旧形态)。
    assert drained[0]["runtime"] is registry._sessions["c:s1"], (
        "drain 拿到的不是会话对象(它必须持有 next_event):"
        f"{type(drained[0]['runtime']).__name__}"
    )


@pytest.mark.asyncio
async def test_fix_without_bridge_drain_still_dispatches(tmp_path):
    """桥未提供 drain_agent_job_events(旧桥/测试替身)时如实跳过,派发不受影响。"""
    server, _, _, control = _make_fix_server(
        with_session=True, cached_repair_project=str(tmp_path)
    )
    resp = await _dispatch(
        server, "sure.fix.start", {"project": str(tmp_path)}, client_id="c"
    )
    assert "result" in resp, resp
    assert len(control.calls) == 1
