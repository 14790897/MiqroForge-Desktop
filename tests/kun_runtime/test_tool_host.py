"""Phase 6 tests — ToolHost adapter (MiQiToolHost + FakeToolHost)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from miqi.agent.tools.base import Tool
from miqi.agent.tools.registry import ToolRegistry
from miqi.config.schema import ApprovalBypassConfig
from miqi.kun_runtime.approval_gate import ApprovalGate
from miqi.kun_runtime.tool_host import (
    _MAX_PARALLEL_TOOL_CALLS,
    FakeToolHost,
    MiQiToolHost,
    ToolCallLike,
    ToolHostContext,
    _classify_tool_kind,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures — real MiQi tools for integration tests
# ═══════════════════════════════════════════════════════════════════════════════


class _DummyTool(Tool):
    """A simple tool that returns its arguments."""
    name = "dummy"
    description = "A dummy tool for testing"
    parameters = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
    }

    async def execute(self, message: str = "") -> str:
        return f"echo: {message}"


class _ReadTool(Tool):
    name = "read_file"
    description = "Read a file"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, path: str = "", **kwargs) -> str:
        return f"content of {path}"


class _WriteTool(Tool):
    name = "write_file"
    description = "Write a file"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}

    async def execute(self, path: str = "", content: str = "", **kwargs) -> str:
        return f"wrote {path}"


class _ErrorTool(Tool):
    name = "failing_tool"
    description = "Always fails"
    parameters = {"type": "object", "properties": {}}

    async def execute(self) -> str:
        raise RuntimeError("simulated failure")


class _CountingWriteTool(Tool):
    name = "write_file"
    description = "Write a file and count calls"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, path: str = "", content: str = "", **kwargs) -> str:
        self.calls += 1
        return f"wrote {path}"


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_DummyTool())
    reg.register(_ReadTool())
    reg.register(_WriteTool())
    reg.register(_ErrorTool())
    return reg


@pytest.fixture
def host(registry: ToolRegistry) -> MiQiToolHost:
    return MiQiToolHost(registry)


_pytest_builtins: set = set()


@pytest.fixture(autouse=True)
def _capture_pytest_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    # No-op; just ensures pytest fixtures don't interfere
    pass


@pytest.fixture
def context() -> ToolHostContext:
    return ToolHostContext(
        thread_id="th1",
        turn_id="t1",
        workspace=str(Path("/tmp/test_ws")),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ToolKind classification tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolKindClassification:
    def test_read_is_tool_call(self) -> None:
        assert _classify_tool_kind("read") == "tool_call"

    def test_bash_is_command_execution(self) -> None:
        assert _classify_tool_kind("bash") == "command_execution"
        assert _classify_tool_kind("exec") == "command_execution"

    def test_write_is_file_change(self) -> None:
        assert _classify_tool_kind("write") == "file_change"
        assert _classify_tool_kind("edit") == "file_change"
        assert _classify_tool_kind("write_file") == "file_change"


# ═══════════════════════════════════════════════════════════════════════════════
# MiQiToolHost — list_tools
# ═══════════════════════════════════════════════════════════════════════════════


class TestMiQiToolHostListTools:
    @pytest.mark.asyncio
    async def test_lists_all_tools(self, host: MiQiToolHost, context: ToolHostContext) -> None:
        tools = await host.list_tools(context)
        names = [t["name"] for t in tools]
        assert "dummy" in names
        assert "read_file" in names
        assert "write_file" in names

    @pytest.mark.asyncio
    async def test_tool_spec_format(self, host: MiQiToolHost, context: ToolHostContext) -> None:
        tools = await host.list_tools(context)
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert "toolKind" in tool
            assert "providerId" in tool
            assert "providerKind" in tool

    @pytest.mark.asyncio
    async def test_filters_by_allowed_names(self, host: MiQiToolHost) -> None:
        ctx = ToolHostContext(
            thread_id="th1", turn_id="t1", workspace="/tmp",
            allowed_tool_names=["dummy"],
        )
        tools = await host.list_tools(ctx)
        names = [t["name"] for t in tools]
        assert names == ["dummy"]


# ═══════════════════════════════════════════════════════════════════════════════
# MiQiToolHost — execute
# ═══════════════════════════════════════════════════════════════════════════════


class TestMiQiToolHostExecute:
    @pytest.mark.asyncio
    async def test_execute_normal(self, host: MiQiToolHost, context: ToolHostContext) -> None:
        call = ToolCallLike(call_id="call_1", tool_name="dummy", arguments={"message": "hello"})
        result = await host.execute(call, context)
        assert result.item["kind"] == "tool_result"
        assert result.item["toolName"] == "dummy"
        assert result.item["isError"] is False
        assert "echo: hello" in str(result.item["output"])

    @pytest.mark.asyncio
    async def test_execute_read_file(self, host: MiQiToolHost, context: ToolHostContext) -> None:
        call = ToolCallLike(call_id="call_2", tool_name="read_file", arguments={"path": "/tmp/test.txt"})
        result = await host.execute(call, context)
        assert result.item["isError"] is False
        assert "content of /tmp/test.txt" in str(result.item["output"])

    @pytest.mark.asyncio
    async def test_execute_error_handling(self, host: MiQiToolHost, context: ToolHostContext) -> None:
        call = ToolCallLike(call_id="call_3", tool_name="failing_tool", arguments={})
        result = await host.execute(call, context)
        assert result.item["isError"] is True
        assert result.item["status"] == "failed"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self, host: MiQiToolHost, context: ToolHostContext) -> None:
        call = ToolCallLike(call_id="call_4", tool_name="nonexistent", arguments={})
        result = await host.execute(call, context)
        assert result.item["isError"] is True
        assert "not found" in str(result.item["output"])

    @pytest.mark.asyncio
    async def test_execute_result_shape(self, host: MiQiToolHost, context: ToolHostContext) -> None:
        call = ToolCallLike(call_id="call_5", tool_name="dummy", arguments={"message": "test"})
        result = await host.execute(call, context)
        item = result.item
        assert item["id"] == f"item_{context.turn_id}_call_5"
        assert item["turnId"] == context.turn_id
        assert item["threadId"] == context.thread_id
        assert item["role"] == "tool"
        assert item["callId"] == "call_5"

    @pytest.mark.asyncio
    async def test_approval_deny_blocks_tool_execution(self) -> None:
        tool = _CountingWriteTool()
        reg = ToolRegistry()
        reg.register(tool)
        host = MiQiToolHost(reg)

        async def deny(_payload: dict[str, object]) -> str:
            return "deny"

        ctx = ToolHostContext(
            thread_id="th1",
            turn_id="t1",
            workspace="/tmp",
            await_approval=deny,
        )
        call = ToolCallLike(call_id="call_6", tool_name="write_file", arguments={"path": "a.txt"})
        result = await host.execute(call, ctx)

        assert result.item["isError"] is True
        assert result.item["status"] == "failed"
        assert "denied" in str(result.item["output"])
        assert tool.calls == 0

    @pytest.mark.asyncio
    async def test_approval_allow_executes_tool(self) -> None:
        tool = _CountingWriteTool()
        reg = ToolRegistry()
        reg.register(tool)
        host = MiQiToolHost(reg)

        async def allow(_payload: dict[str, object]) -> str:
            return "allow"

        ctx = ToolHostContext(
            thread_id="th1",
            turn_id="t1",
            workspace="/tmp",
            await_approval=allow,
        )
        call = ToolCallLike(call_id="call_7", tool_name="write_file", arguments={"path": "a.txt"})
        result = await host.execute(call, ctx)

        assert result.item["isError"] is False
        assert "wrote a.txt" in str(result.item["output"])
        assert tool.calls == 1

    @pytest.mark.asyncio
    async def test_bypass_all_gate_executes_without_pending(self) -> None:
        tool = _CountingWriteTool()
        reg = ToolRegistry()
        reg.register(tool)
        host = MiQiToolHost(reg)
        gate = ApprovalGate(ApprovalBypassConfig(bypass_all=True))

        async def approve(payload: dict[str, object]) -> str:
            return await gate.request(
                str(payload["threadId"]),
                str(payload["turnId"]),
                str(payload["toolName"]),
                str(payload["summary"]),
                payload,
            )

        ctx = ToolHostContext(
            thread_id="th1",
            turn_id="t1",
            workspace="/tmp",
            await_approval=approve,
        )
        call = ToolCallLike(call_id="call_8", tool_name="write_file", arguments={"path": "a.txt"})
        result = await host.execute(call, ctx)

        assert result.item["isError"] is False
        assert gate.pending_count == 0
        assert tool.calls == 1


# ═══════════════════════════════════════════════════════════════════════════════
# MiQiToolHost — concurrency
# ═══════════════════════════════════════════════════════════════════════════════


class TestMiQiToolHostConcurrency:
    def test_parallel_safe_tools_can_parallelize(self, host: MiQiToolHost) -> None:
        calls = [
            ToolCallLike(call_id="c1", tool_name="read_file", arguments={"path": "a.txt"}),
            ToolCallLike(call_id="c2", tool_name="read_file", arguments={"path": "b.txt"}),
        ]
        assert host.should_parallelize(calls) is True

    def test_single_call_no_parallelize(self, host: MiQiToolHost) -> None:
        calls = [ToolCallLike(call_id="c1", tool_name="read_file", arguments={"path": "a.txt"})]
        assert host.should_parallelize(calls) is False

    def test_write_tool_same_path_no_parallelize(self, host: MiQiToolHost) -> None:
        # write_file to the SAME path must serialize (path overlap)
        calls = [
            ToolCallLike(call_id="c1", tool_name="write_file", arguments={"path": "a.txt"}),
            ToolCallLike(call_id="c2", tool_name="write_file", arguments={"path": "a.txt"}),
        ]
        assert host.should_parallelize(calls) is False

    def test_write_tool_different_paths_can_parallelize(self, host: MiQiToolHost) -> None:
        # write_file to DIFFERENT paths is safe to parallelize (MiQi path-scoping)
        calls = [
            ToolCallLike(call_id="c1", tool_name="write_file", arguments={"path": "a.txt"}),
            ToolCallLike(call_id="c2", tool_name="write_file", arguments={"path": "b.txt"}),
        ]
        assert host.should_parallelize(calls) is True

    def test_mixed_tools_uses_registry_logic(self, host: MiQiToolHost) -> None:
        calls = [
            ToolCallLike(call_id="c1", tool_name="read_file", arguments={"path": "a.txt"}),
            ToolCallLike(call_id="c2", tool_name="dummy", arguments={"message": "hi"}),
        ]
        # Delegates to ToolRegistry.should_parallelize()
        result = host.should_parallelize(calls)
        assert isinstance(result, bool)

    def test_untrusted_policy_no_parallel(self, host: MiQiToolHost) -> None:
        calls = [
            ToolCallLike(call_id="c1", tool_name="read_file", arguments={"path": "a.txt"}),
            ToolCallLike(call_id="c2", tool_name="read_file", arguments={"path": "b.txt"}),
        ]
        assert host.should_parallelize(calls, approval_policy="untrusted") is False

    def test_max_parallel(self, host: MiQiToolHost) -> None:
        assert host.max_parallel() == _MAX_PARALLEL_TOOL_CALLS


# ═══════════════════════════════════════════════════════════════════════════════
# FakeToolHost tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFakeToolHost:
    @pytest.mark.asyncio
    async def test_lists_configured_tools(self) -> None:
        host = FakeToolHost(tools=[
            {"name": "read", "description": "Read file", "inputSchema": {}, "toolKind": "tool_call"},
            {"name": "bash", "description": "Run command", "inputSchema": {}, "toolKind": "command_execution"},
        ])
        tools = await host.list_tools()
        assert len(tools) == 2
        assert tools[0]["name"] == "read"

    @pytest.mark.asyncio
    async def test_execute_returns_configured_result(self) -> None:
        host = FakeToolHost(
            tools=[{"name": "read", "description": "Read", "inputSchema": {}}],
            results={"read": "file content here"},
        )
        ctx = ToolHostContext(thread_id="th1", turn_id="t1", workspace="/tmp")
        call = ToolCallLike(call_id="c1", tool_name="read", arguments={"path": "x.txt"})
        result = await host.execute(call, ctx)
        assert result.item["output"] == "file content here"
        assert result.item["isError"] is False

    @pytest.mark.asyncio
    async def test_execute_error_tool(self) -> None:
        host = FakeToolHost(
            tools=[{"name": "bad_tool", "description": "Fails", "inputSchema": {}}],
            error_tools={"bad_tool"},
            results={"bad_tool": "something broke"},
        )
        ctx = ToolHostContext(thread_id="th1", turn_id="t1", workspace="/tmp")
        call = ToolCallLike(call_id="c1", tool_name="bad_tool", arguments={})
        result = await host.execute(call, ctx)
        assert result.item["isError"] is True

    @pytest.mark.asyncio
    async def test_records_calls(self) -> None:
        host = FakeToolHost(
            tools=[{"name": "read", "description": "Read", "inputSchema": {}}],
            results={"read": "ok"},
        )
        ctx = ToolHostContext(thread_id="th1", turn_id="t1", workspace="/tmp")
        call = ToolCallLike(call_id="c1", tool_name="read", arguments={"path": "a.txt"})
        await host.execute(call, ctx)
        assert len(host.calls) == 1
        assert host.calls[0][0].tool_name == "read"

    def test_parallel_classification(self) -> None:
        host = FakeToolHost()
        read_call = ToolCallLike(call_id="c1", tool_name="read", arguments={})
        bash_call = ToolCallLike(call_id="c2", tool_name="bash", arguments={"command": "rm -rf /"})
        assert host.is_parallel_safe(read_call) is True
        assert host.is_parallel_safe(bash_call) is False

    def test_should_parallelize(self) -> None:
        host = FakeToolHost()
        calls = [
            ToolCallLike(call_id="c1", tool_name="read", arguments={}),
            ToolCallLike(call_id="c2", tool_name="read", arguments={}),
        ]
        assert host.should_parallelize(calls) is True


# ═══════════════════════════════════════════════════════════════════════════════
# Issue #821 — user-mentioned roots injection
# ═══════════════════════════════════════════════════════════════════════════════


class _RecordingWriteTool(Tool):
    """Captures the kwargs injected by MiQiToolHost.execute."""

    name = "write_file"
    description = "Write a file"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}}

    def __init__(self) -> None:
        self.last_kwargs: dict[str, object] | None = None

    async def execute(self, path: str = "", content: str = "", **kwargs: Any) -> str:
        self.last_kwargs = dict(kwargs)
        return f"wrote {path}"


class TestUserRootsInjection:
    @pytest.mark.asyncio
    async def test_user_roots_injected_for_file_tools(self) -> None:
        tool = _RecordingWriteTool()
        reg = ToolRegistry()
        reg.register(tool)
        host = MiQiToolHost(reg)

        ctx = ToolHostContext(
            thread_id="th1",
            turn_id="t1",
            workspace="/tmp",
            user_mentioned_roots=["C:/Users/x/Desktop/test_result"],
        )
        call = ToolCallLike(
            call_id="c1", tool_name="write_file",
            arguments={"path": "C:/Users/x/Desktop/test_result/report.md"},
        )
        result = await host.execute(call, ctx)
        assert result.item["isError"] is False
        assert tool.last_kwargs is not None
        assert tool.last_kwargs["_user_roots"] == ["C:/Users/x/Desktop/test_result"]
        # Session key is injected alongside (KUN file-tool contract).
        assert "_session_key" in tool.last_kwargs

    @pytest.mark.asyncio
    async def test_no_user_roots_when_context_empty(self) -> None:
        tool = _RecordingWriteTool()
        reg = ToolRegistry()
        reg.register(tool)
        host = MiQiToolHost(reg)

        ctx = ToolHostContext(thread_id="th1", turn_id="t1", workspace="/tmp")
        call = ToolCallLike(call_id="c1", tool_name="write_file", arguments={"path": "a.txt"})
        await host.execute(call, ctx)
        assert tool.last_kwargs is not None
        assert "_user_roots" not in tool.last_kwargs

    def test_graph_render_receives_session_key(self) -> None:
        # Parity with the legacy orchestrator (CodeRabbit #761): graph_render
        # writes artifacts and reads source JSON, so it must be in the
        # session-key injection set on the KUN tool host.
        from miqi.kun_runtime.tool_host import _SESSION_KEY_TOOLS

        assert "graph_render" in _SESSION_KEY_TOOLS
