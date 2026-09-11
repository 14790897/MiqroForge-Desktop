"""Integration tests: full execution policy pipeline + approval relationship.

Simulates: chat.send → UserMessage → TurnContext → turn_runner flags →
tool_runtime → ToolExecutionContext → permission_engine → verdict.
"""
import asyncio

from miqi.execution.orchestrator import ToolExecutionContext
from miqi.execution.permission_engine import PermissionEngine, PermissionVerdict


class TestFullPipelineBypass:
    """Verify bypass mode end-to-end: chat.send → approval bypass."""

    def test_bypass_skips_all_approval_checks(self):
        """Bypass mode → send exec 'rm -rf /' → permission engine returns ALLOW."""
        engine = PermissionEngine()
        ctx = ToolExecutionContext(
            tool_name="exec",
            tool_call_id="call-1",
            arguments={"command": "rm -rf /important"},
            turn_id="t1",
            thread_id="th1",
            agent_type="main",
            bypass_approval=True,  # ← set by turn_runner for bypass mode
        )
        decision = asyncio.run(engine.check(ctx))
        assert decision.verdict == PermissionVerdict.ALLOW, \
            f"Bypass mode should ALLOW dangerous commands, got {decision.verdict}: {decision.reason}"

    def test_bypass_allows_file_write(self):
        """Bypass mode → write_file should be allowed."""
        engine = PermissionEngine()
        ctx = ToolExecutionContext(
            tool_name="write_file",
            tool_call_id="call-2",
            arguments={"path": "/etc/hosts"},
            turn_id="t1",
            thread_id="th1",
            agent_type="main",
            bypass_approval=True,
        )
        decision = asyncio.run(engine.check(ctx))
        assert decision.verdict == PermissionVerdict.ALLOW

    def test_bypass_allows_network(self):
        """Bypass mode → network tools allowed."""
        engine = PermissionEngine()
        ctx = ToolExecutionContext(
            tool_name="web_fetch",
            tool_call_id="call-3",
            arguments={"url": "https://example.com"},
            turn_id="t1",
            thread_id="th1",
            agent_type="main",
            bypass_approval=True,
        )
        decision = asyncio.run(engine.check(ctx))
        assert decision.verdict == PermissionVerdict.ALLOW

    def test_bypass_does_not_override_deny_list(self):
        """Critical: deny patterns ALWAYS win, even during bypass."""
        engine = PermissionEngine(deny_patterns={"rm -rf /"})
        ctx = ToolExecutionContext(
            tool_name="exec",
            tool_call_id="call-4",
            arguments={"command": "rm -rf /"},
            turn_id="t1",
            thread_id="th1",
            agent_type="main",
            bypass_approval=True,
        )
        decision = asyncio.run(engine.check(ctx))
        assert decision.verdict == PermissionVerdict.DENY, \
            "Security: deny list must ALWAYS win over bypass mode"


class TestFullPipelineManual:
    """Verify manual mode: every action requires approval."""

    def test_manual_forces_approval_on_safe_tool(self):
        """Manual mode → even read_file requires approval."""
        engine = PermissionEngine()
        ctx = ToolExecutionContext(
            tool_name="read_file",
            tool_call_id="call-1",
            arguments={"path": "test.py"},
            turn_id="t1",
            thread_id="th1",
            agent_type="main",
            force_approval=True,  # ← set by turn_runner for manual mode
        )
        decision = asyncio.run(engine.check(ctx))
        assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED, \
            f"Manual mode should require approval for ALL tools, got {decision.verdict}"

    def test_manual_forces_approval_on_write(self):
        """Manual mode → write_file requires approval."""
        engine = PermissionEngine()
        ctx = ToolExecutionContext(
            tool_name="write_file",
            tool_call_id="call-2",
            arguments={"path": "/tmp/test.txt"},
            turn_id="t1",
            thread_id="th1",
            agent_type="main",
            force_approval=True,
        )
        decision = asyncio.run(engine.check(ctx))
        assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED

    def test_manual_forces_approval_on_exec(self):
        """Manual mode → exec always requires approval even for safe commands."""
        engine = PermissionEngine()
        ctx = ToolExecutionContext(
            tool_name="exec",
            tool_call_id="call-3",
            arguments={"command": "ls"},
            turn_id="t1",
            thread_id="th1",
            agent_type="main",
            force_approval=True,
        )
        decision = asyncio.run(engine.check(ctx))
        assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


class TestFullPipelineEdit:
    """Verify edit mode: normal permission logic (no flags)."""

    def test_edit_allows_read(self):
        """Edit → read_file auto-allowed."""
        engine = PermissionEngine()
        ctx = ToolExecutionContext(
            tool_name="read_file",
            tool_call_id="call-1",
            arguments={"path": "test.py"},
            turn_id="t1",
            thread_id="th1",
            agent_type="main",
            # no flags ← default edit behavior
        )
        decision = asyncio.run(engine.check(ctx))
        assert decision.verdict == PermissionVerdict.ALLOW

    def test_edit_requires_approval_for_exec(self):
        """Edit → dangerous exec still requires approval."""
        engine = PermissionEngine()
        ctx = ToolExecutionContext(
            tool_name="exec",
            tool_call_id="call-2",
            arguments={"command": "rm -rf /tmp/build"},
            turn_id="t1",
            thread_id="th1",
            agent_type="main",
        )
        decision = asyncio.run(engine.check(ctx))
        assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED

    def test_edit_requires_approval_for_file_write(self):
        """Edit → write_file requires approval (normal behavior)."""
        engine = PermissionEngine()
        ctx = ToolExecutionContext(
            tool_name="write_file",
            tool_call_id="call-3",
            arguments={"path": "/etc/test"},
            turn_id="t1",
            thread_id="th1",
            agent_type="main",
        )
        decision = asyncio.run(engine.check(ctx))
        assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


class TestApprovalModeCoexistence:
    """Verify that execution policy and approval switches coexist correctly."""

    def test_bypass_ignores_approval_switches(self):
        """Bypass mode → approval switches don't matter → ALLOW."""
        engine = PermissionEngine()
        # Even with all the approval bypass config present, bypass flag wins
        ctx = ToolExecutionContext(
            tool_name="exec",
            tool_call_id="call-1",
            arguments={"command": "rm -rf /tmp"},
            turn_id="t1",
            thread_id="th1",
            agent_type="main",
            bypass_approval=True,
        )
        decision = asyncio.run(engine.check(ctx))
        assert decision.verdict == PermissionVerdict.ALLOW

    def test_manual_overrides_approval_switches(self):
        """Manual mode → even with bypass config, force_approval wins."""
        engine = PermissionEngine()
        ctx = ToolExecutionContext(
            tool_name="read_file",
            tool_call_id="call-1",
            arguments={"path": "test.py"},
            turn_id="t1",
            thread_id="th1",
            agent_type="main",
            force_approval=True,
        )
        decision = asyncio.run(engine.check(ctx))
        assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED

    def test_bypass_beats_force(self):
        """If both flags somehow set → bypass wins (checked first in engine)."""
        engine = PermissionEngine()
        ctx = ToolExecutionContext(
            tool_name="exec",
            tool_call_id="call-1",
            arguments={"command": "rm -rf /tmp"},
            turn_id="t1",
            thread_id="th1",
            agent_type="main",
            bypass_approval=True,
            force_approval=True,
        )
        decision = asyncio.run(engine.check(ctx))
        assert decision.verdict == PermissionVerdict.ALLOW, \
            "Bypass must win over force — it's checked first"
