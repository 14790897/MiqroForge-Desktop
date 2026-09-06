"""Slurm MCP 计费桥测试（issue #927，RUNNING 触发版）。

覆盖：服务器名匹配 / RUNNING 检测（JSON 与文本）/ 扣费事件发射
（submit 与 check_job_status）/ 非 RUNNING 不触发 / 非 slurm 服务器
不受影响 / 无 Desktop 通道静默跳过 / 注入参数不传给 MCP 服务端 /
作业 ID 提取。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from miqi.agent.billing_resolver import (
    billing_charge_emitter_for,
    is_slurm_server,
    set_billing_charge_emitter,
)
from miqi.agent.tools.mcp import MCPToolWrapper, _extract_job_id, _extract_job_state


@pytest.fixture(autouse=True)
def _clean_emitters():
    """每个测试后清理会话发射器，避免跨测试串扰。"""
    yield
    for key in list(billing_charge_emitter_for.__globals__["_emitters"].keys()):
        set_billing_charge_emitter(key, None)
    billing_charge_emitter_for.__globals__["_seen_job_ids"].clear()


class _FakeMCPResult:
    def __init__(self, text: str):
        from mcp import types

        self.content = [types.TextContent(type="text", text=text)]


class _FakeSession:
    """记录调用参数的假 MCP session。"""

    def __init__(self, result_text: str = "ok"):
        self._result = result_text
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any], **extra):
        self.calls.append((name, dict(arguments)))
        return _FakeMCPResult(self._result)


def _make_wrapper(server_name: str, session: _FakeSession, tool_name: str = "submit_slurm_job"):
    return MCPToolWrapper(
        session,
        server_name,
        SimpleNamespace(name=tool_name, description="submit a job", inputSchema=None),
    )


RUNNING_JSON = '{"job_id": "187654", "state": "RUNNING", "name": "lammps"}'
PENDING_JSON = '{"job_id": "187654", "state": "PENDING", "name": "lammps"}'


class TestServerMatching:
    def test_slurm_names_match(self):
        assert is_slurm_server("slurm")
        assert is_slurm_server("my-slurm-cluster")
        assert is_slurm_server("SLURM-PROD")

    def test_other_servers_do_not_match(self):
        assert not is_slurm_server("filesystem")
        assert not is_slurm_server("")


class TestJobStateExtraction:
    def test_json_state(self):
        assert _extract_job_state(RUNNING_JSON) == "RUNNING"

    def test_text_state(self):
        assert _extract_job_state("state: PENDING (reason: waiting)") == "PENDING"

    def test_no_state(self):
        assert _extract_job_state("no state here") is None

    def test_job_id(self):
        assert _extract_job_id(RUNNING_JSON) == "187654"
        assert _extract_job_id("Submitted batch job 9999") == "9999"


class TestMCPHttpUrlValidation:
    def test_loopback_http_allowed(self):
        from miqi.agent.tools.mcp import _validate_mcp_http_url

        assert _validate_mcp_http_url("http://127.0.0.1:9000/mcp") is None
        assert _validate_mcp_http_url("http://localhost:9000/mcp") is None
        assert _validate_mcp_http_url("http://[::1]:9000/mcp") is None

    def test_https_any_host_allowed(self):
        from miqi.agent.tools.mcp import _validate_mcp_http_url

        assert _validate_mcp_http_url("https://mcp.example.com/mcp") is None

    def test_non_loopback_http_rejected(self):
        from miqi.agent.tools.mcp import _validate_mcp_http_url

        assert _validate_mcp_http_url("http://mcp.example.com/mcp") is not None
        assert _validate_mcp_http_url("http://192.168.1.10:8080/mcp") is not None

    def test_non_loopback_http_allowed_with_explicit_opt_in(self):
        from miqi.agent.tools.mcp import _validate_mcp_http_url

        # insecure_http: true = 用户显式确认接受明文风险（平台托管
        # 网关暂无 https 的过渡方案）
        assert _validate_mcp_http_url("http://mcp.example.com/mcp", allow_insecure=True) is None
        # 回环 http 无需 opt-in 本就放行
        assert _validate_mcp_http_url("http://127.0.0.1:9000/mcp", allow_insecure=False) is None

    def test_no_scheme_not_blocked(self):
        from miqi.agent.tools.mcp import _validate_mcp_http_url

        # 无 scheme 的 URL 由 httpx 连接阶段自然失败，不在此拦截
        assert _validate_mcp_http_url("") is None
        assert _validate_mcp_http_url("localhost:9000/mcp") is None


class TestMCPWrapperBilling:
    async def test_submit_returning_running_emits_charge_event(self):
        session = _FakeSession(result_text=RUNNING_JSON)
        wrapper = _make_wrapper("slurm", session)
        emitted: list[dict] = []

        async def _emit(payload):
            emitted.append(payload)
            return True  # 模拟桥接：返回送达数（truthy = 已送达）

        set_billing_charge_emitter("desktop:s1", _emit)

        output = await wrapper.execute(
            _session_key="desktop:s1",
            _turn_id="turn-1",
            _tool_call_id="call-1",
            script="run.sh",
        )
        assert output == RUNNING_JSON
        # 注入参数不传给 MCP 服务端
        assert session.calls == [("submit_slurm_job", {"script": "run.sh"})]
        assert len(emitted) == 1
        event = emitted[0]
        assert event["state"] == "RUNNING"
        assert event["job_id"] == "187654"
        assert event["server_name"] == "slurm"
        assert event["tool_name"] == "submit_slurm_job"
        assert event["session_key"] == "desktop:s1"
        assert event["turn_id"] == "turn-1"
        assert event["charge_id"]

    async def test_check_job_status_running_emits_charge_event(self):
        session = _FakeSession(result_text=RUNNING_JSON)
        wrapper = _make_wrapper("slurm", session, tool_name="check_job_status")
        emitted: list[dict] = []

        async def _emit(payload):
            emitted.append(payload)
            return True  # 模拟桥接：返回送达数（truthy = 已送达）

        set_billing_charge_emitter("desktop:s1", _emit)
        await wrapper.execute(
            _session_key="desktop:s1", _turn_id="t", _tool_call_id="c", job_id="187654"
        )
        assert len(emitted) == 1
        assert emitted[0]["tool_name"] == "check_job_status"

    async def test_pending_does_not_emit(self):
        session = _FakeSession(result_text=PENDING_JSON)
        wrapper = _make_wrapper("slurm", session)
        emitted: list[dict] = []

        async def _emit(payload):
            emitted.append(payload)
            return True  # 模拟桥接：返回送达数（truthy = 已送达）

        set_billing_charge_emitter("desktop:s1", _emit)
        await wrapper.execute(_session_key="desktop:s1")
        assert emitted == []

    async def test_non_slurm_server_skips_billing(self):
        session = _FakeSession(result_text=RUNNING_JSON)
        wrapper = _make_wrapper("filesystem", session)
        set_billing_charge_emitter("desktop:s1", lambda p: pytest.fail("不应发起计费"))
        output = await wrapper.execute(_session_key="desktop:s1")
        assert output == RUNNING_JSON
        assert session.calls == [("submit_slurm_job", {})]

    async def test_no_desktop_channel_skips_silently(self):
        # 无 emitter（headless）→ 作业照常运行，计费事件静默跳过
        session = _FakeSession(result_text=RUNNING_JSON)
        wrapper = _make_wrapper("slurm", session)
        output = await wrapper.execute(_session_key="desktop:no-channel")
        assert output == RUNNING_JSON

    def test_args_summary_redacts_nested_and_embedded_secrets(self):
        from miqi.agent.tools.mcp import _summarize_args

        # 嵌套 dict 的敏感键 + 脚本文本内的凭据赋值都要脱敏
        summary = _summarize_args({
            'script': '#!/bin/bash\\nexport API_TOKEN=secret123\\nsrun hostname',
            'env': {'SLURM_PASSWORD': 'pw', 'nested': {'api_key': 'k'}},
        })
        assert 'secret123' not in summary
        assert '[REDACTED]' in summary
        assert 'pw' not in summary

        # 引号包裹的凭据值同样脱敏（CWE-201：export API_TOKEN="secret"）
        quoted = _summarize_args({
            'script': '#!/bin/bash\nexport API_TOKEN="secret123"\nexport PASS="pw1"\nexport PRIVATE_KEY="k2"',
        })
        assert 'secret123' not in quoted
        assert 'pw1' not in quoted
        assert 'k2' not in quoted
        assert '[REDACTED]' in quoted

        # 普通值不受影响
        assert _summarize_args({'partition': 'amd_256q'}) == '{"partition": "amd_256q"}'

    async def test_repeated_running_polls_emit_only_once(self):
        """轮询反复观察 RUNNING：同一会话同一作业只发一次计费事件。"""
        session = _FakeSession(result_text=RUNNING_JSON)
        wrapper = _make_wrapper("slurm", session, tool_name="check_job_status")
        emitted: list[dict] = []
        set_billing_charge_emitter("desktop:s1", lambda p: emitted.append(p) or True)

        for _ in range(3):
            await wrapper.execute(
                _session_key="desktop:s1", _turn_id="t", _tool_call_id="c", job_id="187654"
            )
        assert len(emitted) == 1
        assert emitted[0]["job_id"] == "187654"

        # 另一个作业仍会发事件
        session2 = _FakeSession(result_text='{"job_id": "999", "state": "RUNNING"}')
        wrapper2 = _make_wrapper("slurm", session2, tool_name="check_job_status")
        await wrapper2.execute(
            _session_key="desktop:s1", _turn_id="t", _tool_call_id="c", job_id="999"
        )
        assert len(emitted) == 2

    async def test_same_job_id_two_servers_both_reported(self):
        """不同 MCP 服务器的相同 job_id 互不遮蔽（CodeRabbit #936）。

        去重键含 server_name——slurm-b 的作业 187654 不能因为
        slurm-a 已报告过同 ID 作业而被丢弃。
        """
        s1 = _FakeSession(result_text=RUNNING_JSON)
        w1 = _make_wrapper("slurm-a", s1, tool_name="check_job_status")
        s2 = _FakeSession(result_text=RUNNING_JSON)
        w2 = _make_wrapper("slurm-b", s2, tool_name="check_job_status")
        emitted: list[dict] = []
        set_billing_charge_emitter("desktop:s1", lambda p: emitted.append(p) or True)

        await w1.execute(_session_key="desktop:s1", job_id="187654")
        await w2.execute(_session_key="desktop:s1", job_id="187654")
        assert len(emitted) == 2
        assert {e["server_name"] for e in emitted} == {"slurm-a", "slurm-b"}

    async def test_failed_emit_retries_on_next_poll(self):
        """发射失败不标记：下一次 RUNNING 轮询会重试发送（不丢计费）。"""
        session = _FakeSession(result_text=RUNNING_JSON)
        wrapper = _make_wrapper("slurm", session, tool_name="check_job_status")
        emitted: list[dict] = []
        calls = {"n": 0}

        def _flaky(p):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transport down")
            emitted.append(p)
            return True

        set_billing_charge_emitter("desktop:s1", _flaky)
        await wrapper.execute(_session_key="desktop:s1", job_id="187654")
        assert emitted == []
        # 第二次轮询重试成功 → 已送达后标记
        await wrapper.execute(_session_key="desktop:s1", job_id="187654")
        assert len(emitted) == 1
        # 已标记 → 后续轮询不再发送
        await wrapper.execute(_session_key="desktop:s1", job_id="187654")
        assert len(emitted) == 1
        assert calls["n"] == 2

    async def test_zero_delivery_not_marked(self):
        """发射器返回 0 送达（无订阅客户端）：不标记，下轮重试。"""
        session = _FakeSession(result_text=RUNNING_JSON)
        wrapper = _make_wrapper("slurm", session, tool_name="check_job_status")
        calls = {"n": 0}

        def _no_subs(p):
            calls["n"] += 1
            return 0  # 桥接层 emit_event 无订阅时返回 0

        set_billing_charge_emitter("desktop:s1", _no_subs)
        await wrapper.execute(_session_key="desktop:s1", job_id="187654")
        await wrapper.execute(_session_key="desktop:s1", job_id="187654")
        assert calls["n"] == 2  # 每次都重试，不标记

    async def test_running_without_job_id_skips_charge_event(self):
        """响应与请求参数都拿不到作业 ID：不发计费事件。

        空 job_id 无法去重，轮询每次 RUNNING 都会再扣一次——宁可
        漏计（fail-closed）也不重复扣费。
        """
        session = _FakeSession(result_text='{"state": "RUNNING"}')
        wrapper = _make_wrapper("slurm", session, tool_name="check_job_status")
        emitted: list[dict] = []
        set_billing_charge_emitter("desktop:s1", lambda p: emitted.append(p) or True)

        for _ in range(3):
            await wrapper.execute(_session_key="desktop:s1")
        assert emitted == []

    async def test_mcp_failure_does_not_emit(self):
        class _FailingSession:
            async def call_tool(self, name, arguments, **extra):
                raise RuntimeError("mcp down")

        wrapper = _make_wrapper("slurm", _FailingSession())
        emitted: list[dict] = []
        set_billing_charge_emitter("desktop:s1", lambda p: emitted.append(p) or True)
        with pytest.raises(RuntimeError):
            await wrapper.execute(_session_key="desktop:s1")
        assert emitted == []
