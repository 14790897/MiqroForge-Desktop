"""Integration tests for the execution pipeline (orchestrator + permission engine)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock


def _make_orchestrator(permission_engine=None, deny_patterns=None, permanent_allowlist=None):
    """Create a minimal ToolOrchestrator for testing."""
    from miqi.execution.hook_runtime import HookRuntime
    from miqi.execution.orchestrator import ToolOrchestrator
    from miqi.execution.permission_engine import PermissionEngine
    from miqi.execution.sandbox_policy import SandboxPolicyEngine

    pe = permission_engine or PermissionEngine()
    if deny_patterns:
        pe.deny_patterns = deny_patterns
    if permanent_allowlist:
        pe.permanent_allowlist = permanent_allowlist

    orchestrator = ToolOrchestrator(
        permission_engine=pe,
        sandbox_engine=SandboxPolicyEngine(),
        hook_runtime=HookRuntime(),
        tool_registry=None,
        event_emitter=MagicMock(),
    )
    orchestrator.events.emit = AsyncMock()
    return orchestrator


def test_orchestrator_denies_shell_metacharacters():
    """Orchestrator should reject shell metacharacter commands by default."""
    from miqi.execution.orchestrator import ToolExecutionContext

    orchestrator = _make_orchestrator()

    ctx = ToolExecutionContext(
        tool_name="exec",
        tool_call_id="test-1",
        arguments={"command": "ls; rm -rf /"},
        turn_id="t1",
        thread_id="th1",
        agent_type="main",
    )

    result = asyncio.run(orchestrator.execute(ctx))
    assert result.result is not None
    output = (result.result or "").lower()
    assert any(word in output for word in ("denied", "blocked", "rejected", "权限被拒绝", "拒绝"))


def test_orchestrator_allows_whitelisted_command():
    """Orchestrator should allow permanently whitelisted commands."""
    from miqi.execution.orchestrator import ToolExecutionContext

    orchestrator = _make_orchestrator(
        permanent_allowlist={"echo hello"},
    )

    ctx = ToolExecutionContext(
        tool_name="exec",
        tool_call_id="test-2",
        arguments={"command": "echo hello"},
        turn_id="t1",
        thread_id="th1",
        agent_type="main",
    )

    result = asyncio.run(orchestrator.execute(ctx))
    assert result.result is not None
    output = (result.result or "").lower()
    assert "denied" not in output


def test_orchestrator_deny_patterns():
    """Orchestrator should reject commands matching deny patterns."""
    from miqi.execution.orchestrator import ToolExecutionContext

    orchestrator = _make_orchestrator(
        deny_patterns={"rm -rf"},
    )

    ctx = ToolExecutionContext(
        tool_name="exec",
        tool_call_id="test-3",
        arguments={"command": "rm -rf /tmp/*"},
        turn_id="t1",
        thread_id="th1",
        agent_type="main",
    )

    result = asyncio.run(orchestrator.execute(ctx))
    assert result.result is not None
    output = (result.result or "").lower()
    assert any(word in output for word in ("denied", "blocked", "rejected", "权限被拒绝", "拒绝"))
