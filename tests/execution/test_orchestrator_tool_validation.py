"""Phase 63: ToolOrchestrator parameter validation regression tests.

Validates that the orchestrator calls tool.validate_params() after pre-tool
hooks and before permission checks / sandbox selection.  Invalid tool calls
must return a structured error message, must NOT invoke tool.execute(), and
must NOT trigger approval.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.execution.hook_runtime import (
    HookOutcome,
    HookPoint,
    HookRegistration,
    HookRuntime,
)
from miqi.execution.orchestrator import (
    ToolExecutionContext,
    ToolOrchestrator,
)
from miqi.execution.permission_engine import (
    PermissionDecision,
    PermissionVerdict,
)
from miqi.protocol.permissions import (
    FileSystemSandboxPolicy,
    NetworkSandboxPolicy,
)


def make_ctx(**kwargs):
    return ToolExecutionContext(
        tool_name=kwargs.get("tool_name", "my_tool"),
        tool_call_id=kwargs.get("tool_call_id", "call_001"),
        arguments=kwargs.get("arguments", {}),
        turn_id=kwargs.get("turn_id", "turn_001"),
        thread_id=kwargs.get("thread_id", "thread_abc"),
        agent_type=kwargs.get("agent_type", "main"),
    )


def make_orch(tool):
    """Build a minimal ToolOrchestrator wired with a single tool."""
    from miqi.execution.sandbox_policy import SandboxPolicyEngine, SandboxSelection, SandboxType

    pe = MagicMock()
    pe.check = AsyncMock(return_value=PermissionDecision(
        verdict=PermissionVerdict.ALLOW, reason="",
    ))

    se = MagicMock(spec=SandboxPolicyEngine)
    se.select = AsyncMock(return_value=SandboxSelection(
        sandbox_type=SandboxType.NONE,
        filesystem_policy=FileSystemSandboxPolicy(),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        env_passthrough=[],
        reason="no sandbox",
    ))

    hr = HookRuntime()
    ev = MagicMock()
    ev.emit = AsyncMock()

    tr = MagicMock()
    tr.get.return_value = tool

    return ToolOrchestrator(
        permission_engine=pe,
        sandbox_engine=se,
        hook_runtime=hr,
        tool_registry=tr,
        event_emitter=ev,
    ), pe, hr


# ── web_search missing query ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_search_missing_query_returns_validation_error():
    """web_search with empty arguments → Invalid parameters, not TypeError."""
    from miqi.agent.tools.web import WebSearchTool
    tool = WebSearchTool(api_key=None, max_results=3)
    orch, permission_engine, _hook_runtime = make_orch(tool)

    # Make sure execute is never called — validation should short-circuit
    tool.execute = AsyncMock(return_value="should-not-run")

    ctx = make_ctx(tool_name="web_search", arguments={})
    result_ctx = await orch.execute(ctx)

    # Result text
    assert "错误：工具" in result_ctx.result  # noqa: RUF001
    assert "missing required query" in result_ctx.result
    assert "[Analyze the error above" in result_ctx.result
    # NOT a Python TypeError traceback
    assert "TypeError" not in result_ctx.result

    # Tool.execute() must NOT have been called
    tool.execute.assert_not_called()

    # Permission engine must NOT have been consulted for approval
    permission_engine.check.assert_not_called()

    # Must set a DENY decision (well-behaved for tool-level rejections)
    assert result_ctx.permission_decision is not None
    assert result_ctx.permission_decision.verdict == PermissionVerdict.DENY


@pytest.mark.asyncio
async def test_web_search_empty_query_returns_validation_error():
    """web_search with empty query should stop before tool execution."""
    from miqi.agent.tools.web import WebSearchTool
    tool = WebSearchTool(api_key=None, max_results=3)
    orch, permission_engine, _hook_runtime = make_orch(tool)

    tool.execute = AsyncMock(return_value="should-not-run")

    ctx = make_ctx(tool_name="web_search", arguments={"query": ""})
    result_ctx = await orch.execute(ctx)

    assert "错误：工具" in result_ctx.result  # noqa: RUF001
    assert "query must be at least 1 chars" in result_ctx.result
    tool.execute.assert_not_called()
    permission_engine.check.assert_not_called()


@pytest.mark.asyncio
async def test_web_search_with_valid_query_proceeds_normally():
    """web_search with query → validation passes, tool executes normally."""
    from miqi.agent.tools.web import WebSearchTool
    tool = WebSearchTool(api_key=None, max_results=3)
    tool.execute = AsyncMock(return_value="Found 3 results")

    orch, permission_engine, _hook_runtime = make_orch(tool)

    ctx = make_ctx(tool_name="web_search", arguments={"query": "python"})
    result_ctx = await orch.execute(ctx)

    # Validation should pass; tool.execute should have been called
    tool.execute.assert_called_once()
    assert "Found 3 results" in result_ctx.result
    # No validation error
    assert "Invalid parameters" not in result_ctx.result


# ── web_fetch missing url ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_fetch_missing_url_returns_validation_error():
    """web_fetch with empty arguments → Invalid parameters."""
    from miqi.agent.tools.web import WebFetchTool
    tool = WebFetchTool(max_chars=1000)
    tool.execute = AsyncMock(return_value="should-not-run")

    orch, permission_engine, _hook_runtime = make_orch(tool)

    ctx = make_ctx(tool_name="web_fetch", arguments={})
    result_ctx = await orch.execute(ctx)

    assert "错误：工具" in result_ctx.result  # noqa: RUF001
    assert "missing required url" in result_ctx.result
    tool.execute.assert_not_called()
    permission_engine.check.assert_not_called()


@pytest.mark.asyncio
async def test_web_fetch_empty_url_returns_validation_error():
    """web_fetch with empty url should stop before tool execution."""
    from miqi.agent.tools.web import WebFetchTool
    tool = WebFetchTool(max_chars=1000)
    tool.execute = AsyncMock(return_value="should-not-run")

    orch, permission_engine, _hook_runtime = make_orch(tool)

    ctx = make_ctx(tool_name="web_fetch", arguments={"url": ""})
    result_ctx = await orch.execute(ctx)

    assert "错误：工具" in result_ctx.result  # noqa: RUF001
    assert "url must be at least 1 chars" in result_ctx.result
    tool.execute.assert_not_called()
    permission_engine.check.assert_not_called()


@pytest.mark.asyncio
async def test_web_fetch_with_valid_url_proceeds_normally():
    """web_fetch with url → validation passes."""
    from miqi.agent.tools.web import WebFetchTool
    tool = WebFetchTool(max_chars=1000)
    tool.execute = AsyncMock(return_value="# Page Content")

    orch, _pe, _hr = make_orch(tool)

    ctx = make_ctx(tool_name="web_fetch", arguments={"url": "https://example.com"})
    result_ctx = await orch.execute(ctx)

    tool.execute.assert_called_once()
    assert "# Page Content" in result_ctx.result
    assert "Invalid parameters" not in result_ctx.result


# ── exec missing command ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exec_missing_command_returns_validation_error():
    """exec with empty arguments → Invalid parameters."""
    from miqi.agent.tools.shell import ExecTool
    tool = ExecTool(timeout=10, working_dir="/tmp")
    tool.execute = AsyncMock(return_value="should-not-run")

    orch, permission_engine, _hook_runtime = make_orch(tool)

    ctx = make_ctx(tool_name="exec", arguments={})
    result_ctx = await orch.execute(ctx)

    assert "错误：工具" in result_ctx.result  # noqa: RUF001
    assert "missing required command" in result_ctx.result
    tool.execute.assert_not_called()
    permission_engine.check.assert_not_called()


@pytest.mark.asyncio
async def test_exec_with_valid_command_proceeds_normally():
    """exec with command → validation passes."""
    from miqi.agent.tools.shell import ExecTool
    tool = ExecTool(timeout=10, working_dir="/tmp")
    tool.execute = AsyncMock(return_value="output: ok")

    orch, _pe, _hr = make_orch(tool)

    ctx = make_ctx(tool_name="exec", arguments={"command": "echo hello"})
    result_ctx = await orch.execute(ctx)

    tool.execute.assert_called_once()
    assert "output: ok" in result_ctx.result
    assert "Invalid parameters" not in result_ctx.result


# ── unknown tool ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_tool_returns_error_before_validation():
    """A tool not in the registry → 'Unknown tool' error immediately."""
    tr = MagicMock()
    tr.get.return_value = None  # simulate unknown tool

    from miqi.execution.sandbox_policy import SandboxPolicyEngine, SandboxSelection, SandboxType
    pe = MagicMock()
    pe.check = AsyncMock()
    se = MagicMock(spec=SandboxPolicyEngine)
    se.select = AsyncMock(return_value=SandboxSelection(
        sandbox_type=SandboxType.NONE,
        filesystem_policy=FileSystemSandboxPolicy(),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        env_passthrough=[],
        reason="",
    ))
    hr = HookRuntime()
    ev = MagicMock()
    ev.emit = AsyncMock()

    orch = ToolOrchestrator(
        permission_engine=pe,
        sandbox_engine=se,
        hook_runtime=hr,
        tool_registry=tr,
        event_emitter=ev,
    )

    ctx = make_ctx(tool_name="nonexistent_tool", arguments={"x": 1})
    result_ctx = await orch.execute(ctx)

    assert "错误：未知工具" in result_ctx.result  # noqa: RUF001
    assert "nonexistent_tool" in result_ctx.result
    pe.check.assert_not_called()


# ── hooks still patch arguments before validation ─────────────────────

@pytest.mark.asyncio
async def test_pre_tool_hook_patches_args_before_validation():
    """A hook that adds 'query' → validation passes even with empty args."""
    from miqi.agent.tools.web import WebSearchTool
    tool = WebSearchTool(api_key=None, max_results=3)
    tool.execute = AsyncMock(return_value="patched result")

    orch, _pe, hr = make_orch(tool)

    async def patch_query(ctx):
        ctx.arguments["query"] = "injected query"
        return HookOutcome.continue_()

    hr.register(HookRegistration(
        HookPoint.PRE_TOOL_USE, "*", patch_query, priority=10,
    ))

    ctx = make_ctx(tool_name="web_search", arguments={})
    result_ctx = await orch.execute(ctx)

    # Hook injected the query → validation should pass → tool executes
    tool.execute.assert_called_once()
    assert "patched result" in result_ctx.result
    assert "Invalid parameters" not in result_ctx.result


# ── type validation ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exec_command_not_a_string_returns_validation_error():
    """exec with command=123 → 'should be string' validation error."""
    from miqi.agent.tools.shell import ExecTool
    tool = ExecTool(timeout=10, working_dir="/tmp")
    tool.execute = AsyncMock(return_value="should-not-run")

    orch, permission_engine, _hook_runtime = make_orch(tool)

    ctx = make_ctx(tool_name="exec", arguments={"command": 123})
    result_ctx = await orch.execute(ctx)

    assert "错误：工具" in result_ctx.result  # noqa: RUF001
    assert "should be string" in result_ctx.result
    tool.execute.assert_not_called()
    permission_engine.check.assert_not_called()
