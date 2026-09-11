"""Regression tests for structured tool success/failure classification.

Issue #104 / PR #108 review: ``success`` must not be inferred from whether the
tool result text starts with ``"Error"``. The orchestrator sets a structured
``ctx.status`` (:class:`OrchestrationResult`) on every exit path, and callers
classify success as ``ctx.status == OrchestrationResult.SUCCESS`` — no text
guessing. This catches failure paths whose result text does NOT start with
``"Error"``: ``权限被拒绝：`` (hook block + DENY), ``用户已拒绝：``
(approval rejected), and ``工具执行已取消`` (cancellation), which
the old ``not result.startswith("Error")`` check wrongly reported as success.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.execution.hook_runtime import (
    HookOutcome,
    HookPoint,
    HookRegistration,
    HookRuntime,
)
from miqi.execution.orchestrator import (
    OrchestrationResult,
    ToolExecutionContext,
    ToolOrchestrator,
)
from miqi.execution.permission_engine import (
    PermissionDecision,
    PermissionVerdict,
)


def _make_ctx(**kwargs) -> ToolExecutionContext:
    return ToolExecutionContext(
        tool_name=kwargs.get("tool_name", "write_file"),
        tool_call_id=kwargs.get("tool_call_id", "call_001"),
        arguments=kwargs.get("arguments", {"path": "/tmp/x", "content": "x"}),
        turn_id=kwargs.get("turn_id", "turn_001"),
        thread_id=kwargs.get("thread_id", "thread_abc"),
        agent_type=kwargs.get("agent_type", "main"),
    )


def _build_orch(permission_engine, sandbox_engine, hook_runtime, tool_registry):
    ev = MagicMock()
    ev.emit = AsyncMock()
    return ToolOrchestrator(
        permission_engine=permission_engine,
        sandbox_engine=sandbox_engine,
        hook_runtime=hook_runtime,
        tool_registry=tool_registry,
        event_emitter=ev,
    )


@pytest.mark.asyncio
async def test_pre_tool_use_block_sets_denied_by_policy():
    """PRE_TOOL_USE 'block': result is '权限被拒绝：...' which does NOT
    start with 'Error' — the old startswith('Error') check reported success."""
    hr = HookRuntime()

    async def veto(ctx):
        return HookOutcome.block("policy violation")

    hr.register(HookRegistration(HookPoint.PRE_TOOL_USE, "*", veto, priority=10))

    pe = MagicMock()
    pe.check = AsyncMock()
    se = MagicMock()
    se.select = AsyncMock()
    tr = MagicMock()
    orch = _build_orch(pe, se, hr, tr)

    result_ctx = await orch.execute(_make_ctx())

    assert result_ctx.result.startswith("权限被拒绝：")  # noqa: RUF001
    assert result_ctx.status is OrchestrationResult.DENIED_BY_POLICY
    assert result_ctx.status != OrchestrationResult.SUCCESS


@pytest.mark.asyncio
async def test_permission_deny_sets_denied_by_policy():
    pe = MagicMock()
    pe.check = AsyncMock(return_value=PermissionDecision(
        verdict=PermissionVerdict.DENY, reason="not on allowlist"
    ))
    se = MagicMock()
    se.select = AsyncMock()
    tr = MagicMock()
    orch = _build_orch(pe, se, HookRuntime(), tr)

    result_ctx = await orch.execute(_make_ctx())

    assert result_ctx.result.startswith("权限被拒绝：")  # noqa: RUF001
    assert result_ctx.status is OrchestrationResult.DENIED_BY_POLICY
    assert result_ctx.status != OrchestrationResult.SUCCESS


@pytest.mark.asyncio
async def test_user_denied_approval_sets_denied_by_user():
    """'User denied: ...' does not start with 'Error' — old check reported
    success. Structured status classifies it correctly as failure."""
    hr = HookRuntime()

    async def _request_approval(ctx, decision):
        return PermissionDecision(
            verdict=PermissionVerdict.DENY, reason="user said no"
        )

    pe = MagicMock()
    pe.check = AsyncMock(return_value=PermissionDecision(
        verdict=PermissionVerdict.APPROVAL_REQUIRED, reason="needs approval"
    ))
    se = MagicMock()
    se.select = AsyncMock()
    tr = MagicMock()
    orch = _build_orch(pe, se, hr, tr)
    orch._request_approval = _request_approval

    result_ctx = await orch.execute(_make_ctx())

    assert result_ctx.result.startswith("用户已拒绝：")  # noqa: RUF001
    assert result_ctx.status is OrchestrationResult.DENIED_BY_USER
    assert result_ctx.status != OrchestrationResult.SUCCESS


@pytest.mark.asyncio
async def test_cancellation_sets_cancelled():
    """'Tool execution cancelled' does not start with 'Error' — old check
    reported success."""
    hr = HookRuntime()
    pe = MagicMock()
    pe.check = AsyncMock(return_value=PermissionDecision(
        verdict=PermissionVerdict.ALLOW
    ))
    se = MagicMock()

    async def _select(ctx, attempt):
        raise asyncio.CancelledError()

    se.select = AsyncMock(side_effect=_select)
    tr = MagicMock()
    orch = _build_orch(pe, se, hr, tr)

    result_ctx = await orch.execute(_make_ctx())

    assert result_ctx.result == "工具执行已取消"
    assert result_ctx.status is OrchestrationResult.CANCELLED
    assert result_ctx.status != OrchestrationResult.SUCCESS


@pytest.mark.asyncio
async def test_sandbox_max_retries_sets_sandbox_failed():
    hr = HookRuntime()
    pe = MagicMock()
    pe.check = AsyncMock(return_value=PermissionDecision(
        verdict=PermissionVerdict.ALLOW
    ))
    se = MagicMock()

    async def _select(ctx, attempt):
        from miqi.execution.sandbox_policy import SandboxDeniedError
        raise SandboxDeniedError("no sandbox available")

    se.select = AsyncMock(side_effect=_select)
    tr = MagicMock()
    orch = _build_orch(pe, se, hr, tr)

    result_ctx = await orch.execute(_make_ctx())

    assert result_ctx.result.startswith("错误：沙箱重试次数已耗尽")  # noqa: RUF001
    assert result_ctx.status is OrchestrationResult.SANDBOX_FAILED
    assert result_ctx.status != OrchestrationResult.SUCCESS


@pytest.mark.asyncio
async def test_tool_execution_exception_sets_tool_error():
    hr = HookRuntime()
    pe = MagicMock()
    pe.check = AsyncMock(return_value=PermissionDecision(
        verdict=PermissionVerdict.ALLOW
    ))
    se = MagicMock()

    async def _select(ctx, attempt):
        sel = MagicMock()
        sel.sandbox_type = MagicMock()
        return sel

    se.select = AsyncMock(side_effect=_select)
    tr = MagicMock()
    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(side_effect=ValueError("disk full"))
    tool_mock.validate_params.return_value = []
    tr.get.return_value = tool_mock
    orch = _build_orch(pe, se, hr, tr)

    result_ctx = await orch.execute(_make_ctx())

    assert result_ctx.result.startswith("工具执行失败")
    assert result_ctx.status is OrchestrationResult.TOOL_ERROR
    assert result_ctx.status != OrchestrationResult.SUCCESS


@pytest.mark.asyncio
async def test_successful_execution_sets_success():
    hr = HookRuntime()
    pe = MagicMock()
    pe.check = AsyncMock(return_value=PermissionDecision(
        verdict=PermissionVerdict.ALLOW
    ))
    se = MagicMock()

    async def _select(ctx, attempt):
        sel = MagicMock()
        sel.sandbox_type = MagicMock()
        return sel

    se.select = AsyncMock(side_effect=_select)
    tr = MagicMock()
    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="wrote /tmp/x")
    tool_mock.validate_params.return_value = []
    tr.get.return_value = tool_mock
    orch = _build_orch(pe, se, hr, tr)

    result_ctx = await orch.execute(_make_ctx())

    assert result_ctx.result == "wrote /tmp/x"
    assert result_ctx.status is OrchestrationResult.SUCCESS
