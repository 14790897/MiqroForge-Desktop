"""Tests for session-scoped approval allowlist (Phase 31.6).

Validates that:
1. "session" approval suppresses repeated approval in the same session
2. "session" approval does not apply to another session
3. "always" approval still persists permanently
"""

import asyncio
from unittest.mock import MagicMock

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_meta(tool_name="exec", command="rm -rf /test", details=None):
    """Create minimal approval metadata for testing."""
    if details is None:
        details = {"command": command}
    return {
        "tool_name": tool_name,
        "command": command,
        "description": f"Run: {command}",
        "details": details,
    }


# ── PermissionEngine session_allowlist tests ──────────────────────────────


@pytest.mark.asyncio
async def test_session_allowlist_auto_approves_exec():
    """Session allowlist auto-approves a previously session-approved exec command."""
    from miqi.execution.permission_engine import PermissionEngine, PermissionVerdict

    engine = PermissionEngine(
        session_allowlist={"exec:echo hello"},
    )

    ctx = MagicMock()
    ctx.tool_name = "exec"
    ctx.arguments = {"command": "echo hello"}
    ctx.permission_profile = None
    ctx.bypass_approval = False
    ctx.force_approval = False

    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.ALLOW


@pytest.mark.asyncio
async def test_session_allowlist_auto_approves_file_write():
    """Session allowlist auto-approves a file write."""
    from miqi.execution.permission_engine import PermissionEngine, PermissionVerdict

    engine = PermissionEngine(
        session_allowlist={"write_file:/tmp/test.txt"},
    )

    ctx = MagicMock()
    ctx.tool_name = "write_file"
    ctx.arguments = {"path": "/tmp/test.txt"}
    ctx.permission_profile = None
    ctx.bypass_approval = False
    ctx.force_approval = False

    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.ALLOW


@pytest.mark.asyncio
async def test_session_allowlist_does_not_match_different_command():
    """Session allowlist for one command does not auto-approve another."""
    from miqi.execution.permission_engine import PermissionEngine, PermissionVerdict

    engine = PermissionEngine(
        session_allowlist={"exec:echo hello"},
    )

    ctx = MagicMock()
    ctx.tool_name = "exec"
    ctx.arguments = {"command": "rm -rf /"}
    ctx.permission_profile = None
    ctx.bypass_approval = False
    ctx.force_approval = False

    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_session_allowlist_does_not_match_different_path():
    """Session allowlist for one file path does not match another."""
    from miqi.execution.permission_engine import PermissionEngine, PermissionVerdict

    engine = PermissionEngine(
        session_allowlist={"write_file:/tmp/a.txt"},
    )

    ctx = MagicMock()
    ctx.tool_name = "write_file"
    ctx.arguments = {"path": "/tmp/b.txt"}
    ctx.permission_profile = None
    ctx.bypass_approval = False
    ctx.force_approval = False

    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


# ── ToolOrchestrator session approval recording tests ────────────────────


@pytest.mark.asyncio
async def test_resolve_approval_session_records_pattern():
    """resolve_approval with 'session' adds pattern to session allowlist."""
    from miqi.execution.orchestrator import ToolOrchestrator
    from miqi.execution.permission_engine import PermissionEngine

    permission_engine = PermissionEngine()
    orchestrator = ToolOrchestrator(
        permission_engine=permission_engine,
        sandbox_engine=MagicMock(),
        hook_runtime=MagicMock(),
        tool_registry=MagicMock(),
        event_emitter=MagicMock(),
        session_id="test-session",
    )

    # Create a pending approval
    approval_id = "turn-1:tool-1"
    future = asyncio.get_event_loop().create_future()
    orchestrator._pending_approvals[approval_id] = future
    orchestrator._approval_meta[approval_id] = _make_meta(
        tool_name="exec", command="echo hello",
    )

    result = orchestrator.resolve_approval(approval_id, "session")
    assert result.resolved is True
    assert result.normalized_decision == "session"
    assert "exec:echo hello" in permission_engine.session_allowlist


@pytest.mark.asyncio
async def test_resolve_approval_session_does_not_affect_permanent():
    """resolve_approval with 'session' does NOT add to permanent allowlist."""
    from miqi.execution.orchestrator import ToolOrchestrator
    from miqi.execution.permission_engine import PermissionEngine

    permission_engine = PermissionEngine()
    orchestrator = ToolOrchestrator(
        permission_engine=permission_engine,
        sandbox_engine=MagicMock(),
        hook_runtime=MagicMock(),
        tool_registry=MagicMock(),
        event_emitter=MagicMock(),
        session_id="test-session",
    )

    approval_id = "turn-1:tool-2"
    future = asyncio.get_event_loop().create_future()
    orchestrator._pending_approvals[approval_id] = future
    orchestrator._approval_meta[approval_id] = _make_meta(
        tool_name="exec", command="echo session",
    )

    result = orchestrator.resolve_approval(approval_id, "session")
    assert result.resolved is True
    assert "exec:echo session" in permission_engine.session_allowlist
    assert "exec:echo session" not in permission_engine.permanent_allowlist


@pytest.mark.asyncio
async def test_resolve_approval_always_adds_to_permanent():
    """resolve_approval with 'always' adds to permanent allowlist (existing behavior)."""
    from miqi.execution.orchestrator import ToolOrchestrator
    from miqi.execution.permission_engine import PermissionEngine

    permission_engine = PermissionEngine()
    orchestrator = ToolOrchestrator(
        permission_engine=permission_engine,
        sandbox_engine=MagicMock(),
        hook_runtime=MagicMock(),
        tool_registry=MagicMock(),
        event_emitter=MagicMock(),
        session_id="test-session",
    )

    approval_id = "turn-1:tool-3"
    future = asyncio.get_event_loop().create_future()
    orchestrator._pending_approvals[approval_id] = future
    orchestrator._approval_meta[approval_id] = _make_meta(
        tool_name="exec", command="echo always",
    )

    result = orchestrator.resolve_approval(approval_id, "always")
    assert result.resolved is True
    assert "exec:echo always" in permission_engine.permanent_allowlist


@pytest.mark.asyncio
async def test_apply_patch_always_approval_matches_future_permission_check():
    """always-approved apply_patch calls must use the same key as PermissionEngine."""
    from miqi.execution.orchestrator import ToolOrchestrator
    from miqi.execution.permission_engine import PermissionEngine, PermissionVerdict

    permission_engine = PermissionEngine()
    orchestrator = ToolOrchestrator(
        permission_engine=permission_engine,
        sandbox_engine=MagicMock(),
        hook_runtime=MagicMock(),
        tool_registry=MagicMock(),
        event_emitter=MagicMock(),
        session_id="test-session",
    )

    approval_id = "turn-1:tool-apply-patch"
    future = asyncio.get_event_loop().create_future()
    orchestrator._pending_approvals[approval_id] = future
    orchestrator._approval_meta[approval_id] = _make_meta(
        tool_name="apply_patch",
        command="",
        details={"path": "/tmp/test.txt", "operation": "apply_patch"},
    )

    result = orchestrator.resolve_approval(approval_id, "always")
    assert result.resolved is True
    assert "apply_patch:/tmp/test.txt" in permission_engine.permanent_allowlist

    ctx = MagicMock()
    ctx.tool_name = "apply_patch"
    ctx.arguments = {"path": "/tmp/test.txt"}
    ctx.permission_profile = None
    ctx.bypass_approval = False
    ctx.force_approval = False

    decision = await permission_engine.check(ctx)
    assert decision.verdict == PermissionVerdict.ALLOW


@pytest.mark.asyncio
async def test_resolve_approval_once_does_not_add_to_allowlist():
    """resolve_approval with 'once' does NOT add to any allowlist."""
    from miqi.execution.orchestrator import ToolOrchestrator
    from miqi.execution.permission_engine import PermissionEngine

    permission_engine = PermissionEngine()
    orchestrator = ToolOrchestrator(
        permission_engine=permission_engine,
        sandbox_engine=MagicMock(),
        hook_runtime=MagicMock(),
        tool_registry=MagicMock(),
        event_emitter=MagicMock(),
        session_id="test-session",
    )

    approval_id = "turn-1:tool-4"
    future = asyncio.get_event_loop().create_future()
    orchestrator._pending_approvals[approval_id] = future
    orchestrator._approval_meta[approval_id] = _make_meta(
        tool_name="exec", command="echo once",
    )

    result = orchestrator.resolve_approval(approval_id, "once")
    assert result.resolved is True
    assert "exec:echo once" not in permission_engine.session_allowlist
    assert "exec:echo once" not in permission_engine.permanent_allowlist


@pytest.mark.asyncio
async def test_session_allowlists_are_independent():
    """Two orchestrators have independent session allowlists (no cross-session leak)."""
    from miqi.execution.orchestrator import ToolOrchestrator
    from miqi.execution.permission_engine import PermissionEngine

    # Session A
    engine_a = PermissionEngine()
    orch_a = ToolOrchestrator(
        permission_engine=engine_a,
        sandbox_engine=MagicMock(),
        hook_runtime=MagicMock(),
        tool_registry=MagicMock(),
        event_emitter=MagicMock(),
        session_id="session-a",
    )

    approval_id_a = "turn-a:tool-1"
    future_a = asyncio.get_event_loop().create_future()
    orch_a._pending_approvals[approval_id_a] = future_a
    orch_a._approval_meta[approval_id_a] = _make_meta(
        tool_name="exec", command="echo shared",
    )
    result_a = orch_a.resolve_approval(approval_id_a, "session")
    assert result_a.resolved is True

    # Session B
    engine_b = PermissionEngine()
    ToolOrchestrator(
        permission_engine=engine_b,
        sandbox_engine=MagicMock(),
        hook_runtime=MagicMock(),
        tool_registry=MagicMock(),
        event_emitter=MagicMock(),
        session_id="session-b",
    )

    # Session A's allowlist should have the pattern
    assert "exec:echo shared" in engine_a.session_allowlist
    # Session B's allowlist should NOT have the pattern (independent)
    assert "exec:echo shared" not in engine_b.session_allowlist


@pytest.mark.asyncio
async def test_session_approval_after_permanent_approval():
    """Session-scoped approval adds to session allowlist even if permanent exists."""
    from miqi.execution.orchestrator import ToolOrchestrator
    from miqi.execution.permission_engine import PermissionEngine

    permission_engine = PermissionEngine()
    # Pre-populate permanent allowlist for a different command
    permission_engine.permanent_allowlist.add("exec:echo permanent")

    orchestrator = ToolOrchestrator(
        permission_engine=permission_engine,
        sandbox_engine=MagicMock(),
        hook_runtime=MagicMock(),
        tool_registry=MagicMock(),
        event_emitter=MagicMock(),
        session_id="test-session",
    )

    approval_id = "turn-1:tool-5"
    future = asyncio.get_event_loop().create_future()
    orchestrator._pending_approvals[approval_id] = future
    orchestrator._approval_meta[approval_id] = _make_meta(
        tool_name="exec", command="echo new",
    )

    result = orchestrator.resolve_approval(approval_id, "session")
    assert result.resolved is True
    assert "exec:echo new" in permission_engine.session_allowlist
    assert "exec:echo permanent" in permission_engine.permanent_allowlist
    # Permanent allowlist should NOT have "echo new" (only session-scoped)
    assert "exec:echo new" not in permission_engine.permanent_allowlist


def test_sanitize_details_truncates_deeply_nested_dicts():
    """Deep approval metadata should be truncated instead of overflowing stack."""
    from miqi.execution.orchestrator import ToolOrchestrator

    details = current = {}
    for _ in range(1200):
        child = {}
        current["child"] = child
        current = child

    sanitized = ToolOrchestrator._sanitize_details(details)

    current = sanitized
    for _ in range(20):
        if current.get("child") == "<max_depth_exceeded>":
            break
        current = current["child"]
    else:
        pytest.fail("expected deeply nested metadata to be truncated")


def test_sanitize_details_handles_self_referential_dicts():
    """Cyclic approval metadata should not recurse forever."""
    from miqi.execution.orchestrator import ToolOrchestrator

    details = {"name": "cyclic"}
    details["self"] = details

    sanitized = ToolOrchestrator._sanitize_details(details)

    assert sanitized["name"] == "cyclic"
    assert sanitized["self"] == "<cycle>"
