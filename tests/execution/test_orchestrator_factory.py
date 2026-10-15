"""Tests for miqi.execution.factory — create_default_orchestrator (Phase 48)."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_create_default_orchestrator_noop_emitter_when_none():
    """When event_emitter is None, a NoopEmitter is used instead of crashing."""
    from miqi.execution.factory import create_default_orchestrator

    orchestrator = create_default_orchestrator(
        tool_registry=None,
        event_emitter=None,
    )

    assert orchestrator is not None
    assert orchestrator.events is not None
    # NoopEmitter should not raise on emit
    import asyncio
    asyncio.run(orchestrator.events.emit({"type": "test"}))


def test_create_default_orchestrator_with_event_emitter():
    """When event_emitter is provided, it is used directly."""
    from miqi.execution.factory import create_default_orchestrator

    emitter = MagicMock()
    emitter.emit = AsyncMock()
    orchestrator = create_default_orchestrator(
        tool_registry=None,
        event_emitter=emitter,
    )

    assert orchestrator.events is emitter


def test_create_default_orchestrator_permanent_allowlist():
    """Permanent allowlist is passed through to PermissionEngine."""
    from miqi.execution.factory import create_default_orchestrator

    orchestrator = create_default_orchestrator(
        tool_registry=None,
        permanent_allowlist={"echo hello"},
    )

    assert "echo hello" in orchestrator.permissions.permanent_allowlist


def test_create_default_orchestrator_uses_injected_hook_runtime():
    """共享 HookRuntime 注入后,orchestrator 必须就用它,而不是自建私有实例。

    阶段5 前置修复回归:此前 factory 无条件 `HookRuntime()`,而 PluginManager
    把插件钩子注册在 services 的共享实例上——orchestrator 消费的却是另一个
    实例,插件注册的 PRE_TOOL_USE/POST_TOOL_USE/PERMISSION_REQUEST 钩子在
    生产路径永远不会触发(注册成功、单测通过,装配上石沉大海)。
    """
    from miqi.execution.factory import create_default_orchestrator
    from miqi.execution.hook_runtime import HookRuntime

    shared = HookRuntime()
    orchestrator = create_default_orchestrator(
        tool_registry=None,
        hook_runtime=shared,
    )

    assert orchestrator.hooks is shared
    # 未注入时保持历史行为:factory 自建一个(旧调用方不受影响)
    default_orchestrator = create_default_orchestrator(tool_registry=None)
    assert default_orchestrator.hooks is not shared


@pytest.mark.asyncio
async def test_shared_hook_runtime_pre_tool_use_hook_actually_fires():
    """行为级回归:注册进共享实例的 PRE_TOOL_USE 钩子,经工厂装配后真的执行。

    这是"石沉大海"bug 的用户可感知面:钩子注册在共享 HookRuntime 上,
    orchestrator.execute 必须真的跑它(阻断语义在此一并验证)。
    """
    from miqi.execution.factory import create_default_orchestrator
    from miqi.execution.hook_runtime import (
        HookOutcome,
        HookPoint,
        HookRegistration,
        HookRuntime,
    )
    from miqi.execution.orchestrator import OrchestrationResult, ToolExecutionContext

    shared = HookRuntime()
    fired: list[str] = []

    async def blocker(ctx):
        fired.append(ctx.tool_name)
        return HookOutcome.block("blocked by shared plugin hook")

    shared.register(HookRegistration(HookPoint.PRE_TOOL_USE, "exec", blocker))

    orchestrator = create_default_orchestrator(
        tool_registry=None,
        hook_runtime=shared,
    )
    ctx = ToolExecutionContext(
        tool_name="exec",
        tool_call_id="call-1",
        arguments={},
        turn_id="turn-1",
        thread_id="thread-1",
        agent_type="primary",
    )

    result = await orchestrator.execute(ctx)

    assert fired == ["exec"], "共享实例上注册的钩子必须被 orchestrator 执行"
    assert result.status == OrchestrationResult.DENIED_BY_POLICY
