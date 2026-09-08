"""MCP 连接传输选择测试（stdio / streamable HTTP / SSE，2026-09-05 平台托管网关接入）。

#975（2026-09-08）追加：MCPToolWrapper 下载分类 / Artifact Boundary 接线 /
billing-materialization 解耦回归。
"""

import base64
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from miqi.agent.tools.mcp import MCPToolWrapper, _transport_for
from miqi.agent.tools.mcp_download_sink import (
    DOWNLOAD_TOOL_GUIDANCE,
    is_download_tool,
)


def _cfg(**kw):
    defaults = dict(type="", command="", url="", headers={})
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_explicit_sse_type_wins_over_command():
    assert _transport_for(_cfg(type="sse", url="http://h:9000/sse", command="npx")) == "sse"


def test_sse_without_url_falls_through():
    # type=sse 但没有 url：按字段推断（command 优先）
    assert _transport_for(_cfg(type="sse", command="npx")) == "stdio"


def test_command_implies_stdio():
    assert _transport_for(_cfg(command="npx", args=["-y", "x"])) == "stdio"


def test_url_implies_http_by_default():
    assert _transport_for(_cfg(url="http://127.0.0.1:9000/mcp")) == "http"


def test_explicit_http_type():
    assert _transport_for(_cfg(type="http", url="http://h/mcp")) == "http"


def test_empty_config_returns_empty():
    assert _transport_for(_cfg()) == ""


class TestGatewayTokenInjection:
    def test_reads_key_from_token_file(self, tmp_path):
        from miqi.agent.tools.mcp import _gateway_key_from_token_file

        f = tmp_path / "token.json"
        f.write_text('{"accessToken": "a", "mcpGatewayKey": "k-123"}', encoding="utf-8")
        assert _gateway_key_from_token_file(f) == "k-123"

    def test_missing_file_or_field_returns_none(self, tmp_path):
        from miqi.agent.tools.mcp import _gateway_key_from_token_file

        assert _gateway_key_from_token_file(tmp_path / "nope.json") is None
        f = tmp_path / "token.json"
        f.write_text('{"accessToken": "a"}', encoding="utf-8")
        assert _gateway_key_from_token_file(f) is None
        f.write_text("not json", encoding="utf-8")
        assert _gateway_key_from_token_file(f) is None

    def test_default_gateway_name_matches_schema(self):
        from miqi.agent.tools.mcp import _DEFAULT_GATEWAY_NAME
        from miqi.config.schema import DEFAULT_MCP_SERVERS

        assert _DEFAULT_GATEWAY_NAME in DEFAULT_MCP_SERVERS


class TestInjectionGuards:
    def test_https_detection(self):
        from miqi.agent.tools.mcp import _is_https_url

        assert _is_https_url("https://mcp.example.com/sse") is True
        assert _is_https_url("http://127.0.0.1:9000/sse") is False
        assert _is_https_url("") is False

    def test_trusted_gateway_url_match(self):
        from miqi.agent.tools.mcp import _url_matches_trusted_gateway
        from miqi.config.schema import DEFAULT_MCP_SERVERS

        builtin = DEFAULT_MCP_SERVERS["miqroforge-slurm"]["url"]
        assert _url_matches_trusted_gateway(builtin) is True
        assert _url_matches_trusted_gateway("http://evil.example.com/sse") is False
        assert _url_matches_trusted_gateway("") is False


# ── #975 Artifact Boundary：wrapper 接线（C2）───────────────────────────────

class _FakeSession:
    """call_tool 假会话：可预置响应或抛错。"""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    async def call_tool(self, name, arguments=None, progress_callback=None):
        self.calls.append((name, arguments))
        if self._error is not None:
            raise self._error
        return self._response


def _mcp_text_result(payload_json: str):
    from mcp import types

    return SimpleNamespace(
        isError=False,
        structuredContent=None,
        content=[types.TextContent(type="text", text=payload_json)],
    )


def _wrapper(session, tool_name: str, *, server: str = "miqroforge", base_workspace=None):
    tool_def = SimpleNamespace(
        name=tool_name,
        description=f"原始描述 {tool_name}",
        inputSchema={"type": "object", "properties": {}},
    )
    return MCPToolWrapper(
        session, server, tool_def,
        tool_timeout=5, progress_interval=0, base_workspace=base_workspace,
    )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _artifact_json(data: bytes, name: str = "cube.cube") -> str:
    return json.dumps(
        {
            "name": name,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_base64": _b64(data),
        }
    )


class TestDownloadClassificationWiring:
    def test_construction_classifies_download_tool(self):
        w = _wrapper(_FakeSession(), "download_file")
        assert w.is_download_tool is True

    def test_non_download_tool_not_classified(self):
        w = _wrapper(_FakeSession(), "check_job_status")
        assert w.is_download_tool is False

    def test_classification_matches_sink_helper(self):
        assert MCPToolWrapper is not None
        for server, tool in (("miqroforge", "download_file"), ("x", "download_bulk")):
            assert is_download_tool(server, tool)

    def test_download_tool_description_appends_guidance(self):
        w = _wrapper(_FakeSession(), "download_file")
        assert "原始描述 download_file" in w.description
        assert DOWNLOAD_TOOL_GUIDANCE in w.description
        # 普通工具 description 原样
        w2 = _wrapper(_FakeSession(), "check_job_status")
        assert DOWNLOAD_TOOL_GUIDANCE not in w2.description


class TestDownloadExecute:
    async def test_download_tool_returns_summary_only(self, tmp_path):
        data = os.urandom(1024 * 1024)
        session = _FakeSession(response=_mcp_text_result(_artifact_json(data)))
        w = _wrapper(session, "download_file", base_workspace=tmp_path)
        result = await w.execute(name="cube.cube", _session_key="cli:direct")
        summary = json.loads(result)
        assert summary["type"] == "download_artifact"
        assert summary["name"] == "cube.cube"
        assert summary["size_bytes"] == len(data)
        assert summary["sha256"] == hashlib.sha256(data).hexdigest()
        # base64 不进 ctx.result（wrapper 输出）
        assert _b64(data) not in result
        # 落盘文件内容正确（custom workspace → <ws>/.miqi/downloads）
        target = tmp_path / ".miqi" / "downloads" / "cube.cube"
        assert target.read_bytes() == data

    async def test_non_download_tool_passthrough_unchanged(self, tmp_path):
        text = "普通工具输出，原样返回"
        session = _FakeSession(response=_mcp_text_result(json.dumps({"ok": True, "note": text})))
        w = _wrapper(session, "check_job_status", base_workspace=tmp_path)
        result = await w.execute(job_id="42")
        assert json.loads(result)["note"] == text
        # 不进 sink、不建 downloads
        assert not (tmp_path / ".miqi").exists()

    async def test_timeout_semantics_unchanged(self, tmp_path):
        import asyncio

        session = _FakeSession(error=asyncio.TimeoutError())
        w = _wrapper(session, "download_file", base_workspace=tmp_path)
        result = await w.execute(name="cube.cube")
        assert "timed out" in result  # 与现有 timeout 文案一致，不触发落盘
        assert not (tmp_path / ".miqi").exists()

    async def test_sink_failure_digested_to_error_json(self, tmp_path):
        session = _FakeSession(response=_mcp_text_result('{"success": true, "name": "a.cube"}'))
        w = _wrapper(session, "download_file", base_workspace=tmp_path)
        result = await w.execute(name="a.cube")
        err = json.loads(result)
        assert err["type"] == "download_error"
        assert err["code"] == "DOWNLOAD_PROTOCOL_ERROR"
        assert err["retryable"] is False


class TestDownloadBillingNoRegression:
    """billing 与 materialization 解耦：#927 语义在下载分支不丢失、不早退。"""

    @pytest.fixture
    def billing_state(self, monkeypatch):
        import miqi.agent.billing_resolver as br

        emissions = []
        reported = set()

        def _emitter_for(session_key):
            def _emit(payload):
                emissions.append(payload)
                return True

            return _emit

        monkeypatch.setattr(br, "is_slurm_server", lambda name: True)
        monkeypatch.setattr(br, "billing_charge_emitter_for", _emitter_for)
        monkeypatch.setattr(br, "job_reported", lambda sk, sn, jid: (sk, sn, jid) in reported)
        monkeypatch.setattr(br, "mark_job_reported", lambda sk, sn, jid: reported.add((sk, sn, jid)))
        return emissions, reported

    async def test_slurm_billing_still_fires_on_running(self, tmp_path, billing_state):
        emissions, _ = billing_state
        output = json.dumps({"job_id": 900001, "state": "RUNNING", "note": "ok"})
        session = _FakeSession(response=_mcp_text_result(output))
        w = _wrapper(session, "check_job_status", base_workspace=tmp_path)
        await w.execute(_session_key="cli:direct", job_id="900001")
        await w.execute(_session_key="cli:direct", job_id="900001")
        # 同一 session+server+job 只发一次（dedupe 不回归）
        assert len(emissions) == 1
        assert emissions[0]["job_id"] == "900001"
        assert emissions[0]["state"] == "RUNNING"

    async def test_download_branch_does_not_bypass_billing(self, tmp_path, billing_state):
        emissions, _ = billing_state
        data = b"billing-and-download"
        # 响应里带 state=RUNNING（如文件名/元数据含该文本）→ billing 必须照常触发
        payload = json.loads(_artifact_json(data))
        payload["state"] = "RUNNING"
        session = _FakeSession(response=_mcp_text_result(json.dumps(payload)))
        w = _wrapper(session, "download_file", base_workspace=tmp_path)
        result = await w.execute(
            name="cube.cube", job_id="900002", _session_key="cli:direct"
        )
        summary = json.loads(result)
        # 计费已触发（未因下载分支早退被旁路）……
        assert len(emissions) == 1
        assert emissions[0]["job_id"] == "900002"
        # ……且下载照常交付摘要
        assert summary["type"] == "download_artifact"
        assert (tmp_path / ".miqi" / "downloads" / "cube.cube").read_bytes() == data
        # 摘要只含 5 字段（billing 视图与 materialize 完全解耦，互不污染）
        assert set(summary) == {"type", "name", "path", "size_bytes", "sha256"}
