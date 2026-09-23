"""Tests for ToolRuntime (Phase 12.1)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.runtime.tool_runtime import ToolRuntime


class _FakeTurnContext:
    turn_id = "turn-1"
    thread_id = "thread-1"

    class _Meta:
        name = "code-agent"
    agent_metadata = _Meta()


class _FakeToolCall:
    def __init__(self, name="read_file", args=None, tc_id="tc-1"):
        self.name = name
        self.arguments = args or {"path": "/tmp/x"}
        self.id = tc_id


@pytest.fixture
def fake_orchestrator():
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock()
    async def _execute(ctx):
        ctx.result = "ok"
        ctx.duration_ms = 5
        return ctx
    orchestrator.execute.side_effect = _execute
    return orchestrator


@pytest.fixture
def fake_turn_context():
    return _FakeTurnContext()


@pytest.fixture
def fake_tool_call():
    return _FakeToolCall()


# ── Single execution ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_runtime_executes_single_call_through_orchestrator(
    fake_turn_context, fake_orchestrator, fake_tool_call,
):
    runtime = ToolRuntime(orchestrator=fake_orchestrator)

    result = await runtime.execute_one(fake_turn_context, fake_tool_call)

    assert result.tool_call_id == fake_tool_call.id
    fake_orchestrator.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_runtime_executes_parallel_calls_through_orchestrator(
    fake_turn_context, fake_orchestrator, fake_tool_call,
):
    runtime = ToolRuntime(orchestrator=fake_orchestrator)

    results = await runtime.execute_many(
        fake_turn_context, [fake_tool_call, fake_tool_call],
    )

    assert len(results) == 2
    assert fake_orchestrator.execute.await_count == 2


def test_tool_runtime_requires_orchestrator():
    """ToolRuntime raises RuntimeError when orchestrator is None."""
    with pytest.raises(RuntimeError, match="ToolRuntime requires a ToolOrchestrator"):
        ToolRuntime(orchestrator=None)


# ---------------------------------------------------------------------------
# Phase 13: permission_profile propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_runtime_propagates_permission_profile_to_ctx(fake_orchestrator):
    """ToolRuntime must pass turn.permission_profile into ToolExecutionContext."""
    from pathlib import Path

    from miqi.runtime.permission_profile import PermissionProfile
    from miqi.runtime.tool_runtime import ToolRuntime

    profile = PermissionProfile(
        workspace=Path("/tmp/test"),
        filesystem_mode="workspace-readonly",
        network="none",
        allow_exec=False,
        permanent_allowlist={"safe-cmd"},
    )

    class _TurnWithProfile:
        turn_id = "turn-pp"
        thread_id = "thread-pp"

        class _Meta:
            name = "code-agent"
        agent_metadata = _Meta()
        permission_profile = profile

    turn = _TurnWithProfile()

    # Track the ctx the orchestrator receives
    received_ctx = None

    async def _capture(ctx):
        nonlocal received_ctx
        received_ctx = ctx
        ctx.result = "ok"
        ctx.duration_ms = 3
        return ctx

    fake_orchestrator.execute.side_effect = _capture

    runtime = ToolRuntime(orchestrator=fake_orchestrator)
    ctx_result = await runtime.execute_one(turn, _FakeToolCall())

    assert received_ctx is not None, "orchestrator.execute was not called"
    assert received_ctx.permission_profile is profile, (
        "permission_profile must be the same object"
    )
    assert received_ctx.permission_profile.filesystem_mode == "workspace-readonly"
    assert "safe-cmd" in received_ctx.permission_profile.permanent_allowlist
    assert ctx_result is received_ctx


# ---------------------------------------------------------------------------
# Phase 21: tool cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_runtime_passes_cancel_event():
    """ToolRuntime must forward turn.cancel_event into ToolExecutionContext."""
    import asyncio as _asyncio

    from miqi.runtime.tool_runtime import ToolRuntime

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=lambda ctx: ctx)
    runtime = ToolRuntime(orchestrator=orchestrator)

    turn = MagicMock()
    turn.turn_id = "turn-1"
    turn.thread_id = "thread-1"
    turn.agent_metadata.name = "main"
    turn.cancel_event = _asyncio.Event()

    call = MagicMock()
    call.name = "exec"
    call.id = "tc-1"
    call.arguments = {"command": "sleep 10"}

    ctx = await runtime.execute_one(turn, call)

    assert ctx.cancel_event is turn.cancel_event, (
        "ToolExecutionContext must carry the turn's cancel_event"
    )
