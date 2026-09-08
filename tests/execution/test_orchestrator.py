"""ToolOrchestrator hook outcome integration tests (Task 51.2).

Verifies that PRE_TOOL_USE and PERMISSION_REQUEST hook outcomes can
block, modify, or short-circuit the tool execution pipeline.
"""

import json
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


def make_ctx(**kwargs):
    return ToolExecutionContext(
        tool_name=kwargs.get("tool_name", "my_tool"),
        tool_call_id=kwargs.get("tool_call_id", "call_001"),
        arguments=kwargs.get("arguments", {"value": "original"}),
        turn_id=kwargs.get("turn_id", "turn_001"),
        thread_id=kwargs.get("thread_id", "thread_abc"),
        agent_type=kwargs.get("agent_type", "main"),
    )


@pytest.fixture
def mock_orch_components():
    """Mocked orchestrator dependencies for hook-outcome tests."""
    pe = MagicMock()
    pe.check = AsyncMock()
    se = MagicMock()
    se.select = AsyncMock()
    # Use a real HookRuntime so we can register actual hook callbacks.
    hr = HookRuntime()
    tr = MagicMock()
    ev = MagicMock()
    ev.emit = AsyncMock()
    return {
        "permission_engine": pe,
        "sandbox_engine": se,
        "hook_runtime": hr,
        "tool_registry": tr,
        "event_emitter": ev,
    }


@pytest.fixture
def orch(mock_orch_components):
    return ToolOrchestrator(
        permission_engine=mock_orch_components["permission_engine"],
        sandbox_engine=mock_orch_components["sandbox_engine"],
        hook_runtime=mock_orch_components["hook_runtime"],
        tool_registry=mock_orch_components["tool_registry"],
        event_emitter=mock_orch_components["event_emitter"],
    )


@pytest.mark.asyncio
async def test_pre_tool_use_block_skips_execution(orch, mock_orch_components):
    """A PRE_TOOL_USE 'block' outcome must stop the pipeline before execution."""
    hr = mock_orch_components["hook_runtime"]

    async def veto(ctx):
        return HookOutcome.block("hook policy violation")

    hr.register(HookRegistration(
        HookPoint.PRE_TOOL_USE, "*", veto, priority=10
    ))

    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="should-not-run")
    mock_orch_components["tool_registry"].get.return_value = tool_mock

    ctx = make_ctx()
    result_ctx = await orch.execute(ctx)

    assert "权限被拒绝" in result_ctx.result
    assert "hook policy violation" in result_ctx.result
    assert result_ctx.permission_decision is not None
    assert result_ctx.permission_decision.verdict == PermissionVerdict.DENY
    assert "hook policy violation" in result_ctx.permission_decision.reason
    mock_orch_components["permission_engine"].check.assert_not_called()
    tool_mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_pre_tool_use_modify_patches_arguments(orch, mock_orch_components):
    """A PRE_TOOL_USE 'modify' outcome must update the tool arguments."""
    hr = mock_orch_components["hook_runtime"]

    async def rewrite(ctx):
        return HookOutcome.modify({"arguments": {"value": "patched"}})

    hr.register(HookRegistration(
        HookPoint.PRE_TOOL_USE, "*", rewrite, priority=10
    ))

    mock_orch_components["permission_engine"].check.return_value = PermissionDecision(
        verdict=PermissionVerdict.ALLOW,
    )
    mock_orch_components["sandbox_engine"].select.return_value = MagicMock(
        sandbox_type="none",
        filesystem_policy=MagicMock(),
        network_policy="allow_all",
    )

    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="ran with patched")
    mock_orch_components["tool_registry"].get.return_value = tool_mock

    ctx = make_ctx(arguments={"value": "original"})
    result_ctx = await orch.execute(ctx)

    assert result_ctx.result == "ran with patched"
    tool_mock.execute.assert_called_once()
    call_kwargs = tool_mock.execute.call_args.kwargs
    assert call_kwargs["value"] == "patched"
    assert ctx.arguments["value"] == "patched"


@pytest.mark.asyncio
async def test_permission_request_block_short_circuits_approval(orch, mock_orch_components):
    """A PERMISSION_REQUEST 'block' must deny before emitting ApprovalRequested."""
    hr = mock_orch_components["hook_runtime"]

    async def auto_deny(ctx):
        return HookOutcome.block("auto-denied by hook")

    hr.register(HookRegistration(
        HookPoint.PERMISSION_REQUEST, "*", auto_deny, priority=10
    ))

    mock_orch_components["permission_engine"].check.return_value = PermissionDecision(
        verdict=PermissionVerdict.APPROVAL_REQUIRED,
        category="file_write",
        description="write_file: /tmp/x.txt",
        allow_permanent=True,
    )

    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="should-not-run")
    mock_orch_components["tool_registry"].get.return_value = tool_mock

    ctx = make_ctx(tool_name="write_file", arguments={"path": "/tmp/x.txt"})
    result_ctx = await orch.execute(ctx)

    assert "权限被拒绝" in result_ctx.result
    assert "auto-denied by hook" in result_ctx.result
    mock_orch_components["event_emitter"].emit.assert_not_called()
    tool_mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_graph_render_receives_session_key_injection(orch, mock_orch_components):
    """graph_render 属文件变更工具：orchestrator 必须注入 _session_key/_sandbox。

    回归（CodeRabbit #761）：graph_render 不在注入集合时 _sess_key 恒为
    None，资产栏追踪（_persist_tracked_file）在生产环境永不生效——
    测试直接调用 execute 传入 _session_key 无法暴露该缺口。
    """
    from miqi.execution.permission_engine import PermissionDecision, PermissionVerdict
    from miqi.execution.sandbox_policy import SandboxSelection, SandboxType

    mock_orch_components["permission_engine"].check.return_value = PermissionDecision(
        verdict=PermissionVerdict.ALLOW,
        category="file_write",
    )
    mock_orch_components["sandbox_engine"].select = AsyncMock(
        return_value=SandboxSelection(
            sandbox_type=SandboxType.NONE,
            filesystem_policy=MagicMock(),
            network_policy=MagicMock(),
        )
    )

    captured: dict = {}

    class _FakeTool:
        def validate_params(self, params):
            return []

        async def execute(self, **kwargs):
            captured.update(kwargs)
            return json.dumps({"ok": True})

    mock_orch_components["tool_registry"].get.return_value = _FakeTool()

    ctx = make_ctx(
        tool_name="graph_render",
        arguments={"path": "graph-demo/bvse-mof-run/output", "format": "svg"},
    )
    ctx.session_id = "miqi-desktop:desktop:1787046883657"
    result_ctx = await orch.execute(ctx)

    assert result_ctx.status.value in ("success", "SUCCESS")
    assert captured.get("_session_key") == "miqi-desktop:desktop:1787046883657"
    assert "_sandbox" in captured
    assert captured.get("path") == "graph-demo/bvse-mof-run/output"
