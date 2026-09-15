"""Issue #1045: headless sessions must fail closed immediately.

Before the fix, a session with no approval responder still emitted
ApprovalRequestedEvent, blocked for ``approval_timeout_ms`` (60s), and then
handed the model "用户已拒绝：Approval timeout" — a denial nobody made. A turn
with a few tool calls stacked several of those.

The repo already had a "no channel → immediate deny" convention
(``user_input_resolver``, ``mcp``, ``shell`` deny_no_channel); only the
orchestrator approval path was missing it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.execution.hook_runtime import HookRuntime
from miqi.execution.orchestrator import (
    OrchestrationResult,
    ToolExecutionContext,
    ToolOrchestrator,
    _no_channel_message,
)
from miqi.execution.permission_engine import (
    PermissionDecision,
    PermissionVerdict,
)


def _make_ctx(tool_name: str = "web_search") -> ToolExecutionContext:
    return ToolExecutionContext(
        tool_name=tool_name,
        tool_call_id="call_001",
        arguments={"query": "x"},
        turn_id="turn_001",
        thread_id="thread_abc",
        agent_type="main",
    )


def _make_orch(
    *,
    has_responder: bool,
    category: str = "network",
    verdict: PermissionVerdict = PermissionVerdict.APPROVAL_REQUIRED,
) -> ToolOrchestrator:
    pe = MagicMock()
    pe.check = AsyncMock(
        return_value=PermissionDecision(
            verdict=verdict, category=category, reason="needs approval"
        )
    )
    se = MagicMock()
    se.select = AsyncMock()
    ev = MagicMock()
    ev.emit = AsyncMock()
    return ToolOrchestrator(
        permission_engine=pe,
        sandbox_engine=se,
        hook_runtime=HookRuntime(),
        tool_registry=MagicMock(),
        event_emitter=ev,
        has_approval_responder=has_responder,
    )


@pytest.mark.asyncio
async def test_no_responder_denies_immediately(monkeypatch):
    """No responder → fail closed without emitting or waiting for an answer."""
    orch = _make_orch(has_responder=False)
    called = {"request": False}

    async def _must_not_run(*args, **kwargs):
        called["request"] = True
        raise AssertionError("_request_approval must not run with no responder")

    monkeypatch.setattr(orch, "_request_approval", _must_not_run)

    # A 2s budget proves nothing waited on the 60s approval timeout.
    ctx = await asyncio.wait_for(orch.execute(_make_ctx()), timeout=2)

    assert called["request"] is False, "approval must not be requested"
    assert orch._pending_approvals == {}, "no future may be left registered"
    assert ctx.status is OrchestrationResult.DENIED_BY_POLICY
    assert ctx.permission_decision.verdict is PermissionVerdict.DENY
    assert ctx.permission_decision.reason == "deny_no_channel"


@pytest.mark.asyncio
async def test_no_responder_is_not_reported_as_user_denial(monkeypatch):
    """The model must not be told a user refused — nobody was asked."""
    orch = _make_orch(has_responder=False)
    ctx = await asyncio.wait_for(orch.execute(_make_ctx()), timeout=2)

    assert ctx.status is not OrchestrationResult.DENIED_BY_USER
    assert "用户已拒绝" not in (ctx.result or "")
    assert "deny_no_channel" not in (ctx.result or ""), (
        "raw decision token should not leak into the model-facing text"
    )


@pytest.mark.parametrize(
    ("category", "label"),
    [
        ("network", "网络审批"),
        ("file_write", "文件写入审批"),
        ("tool_confirmation", "工具确认"),
        ("exec", "命令审批"),
    ],
)
def test_no_channel_message_names_the_actual_category(category, label):
    """The short-circuit fires for every category, so the hint must match.

    Quoting only the network switch would misdirect someone whose exec /
    file_write / tool_confirmation call was the one denied.
    """
    msg = _no_channel_message(
        PermissionDecision(verdict=PermissionVerdict.DENY, category=category)
    )
    assert label in msg


def test_no_channel_message_falls_back_for_unknown_category():
    msg = _no_channel_message(
        PermissionDecision(verdict=PermissionVerdict.DENY, category="")
    )
    assert "该类别" in msg


@pytest.mark.asyncio
async def test_responder_present_keeps_the_normal_approval_path(monkeypatch):
    """The default must not change behaviour for desktop frontends."""
    orch = _make_orch(has_responder=True)
    assert orch.has_approval_responder is True

    seen = {"requested": False}

    async def _fake_request(ctx, decision):
        seen["requested"] = True
        return PermissionDecision(verdict=PermissionVerdict.ALLOW)

    monkeypatch.setattr(orch, "_request_approval", _fake_request)

    ctx = await asyncio.wait_for(orch.execute(_make_ctx()), timeout=2)

    assert seen["requested"] is True, "with a responder the ask path still runs"
    assert ctx.status is not OrchestrationResult.DENIED_BY_POLICY
