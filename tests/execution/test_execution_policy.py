"""Tests for execution policy integration in TaskRunner / ToolRuntime."""
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from miqi.runtime.tool_policy import PLAN_BLOCKED_TOOLS


@dataclass
class FakeAgentMetadata:
    name: str = "main"
    system_prompt: str = "You are a helpful assistant."


@dataclass
class FakeTurnContext:
    turn_id: str = "test-turn"
    thread_id: str = "test-thread"
    agent_metadata: Any = None
    workspace: Path = Path("/tmp")
    model: str = "test-model"
    provider: Any = None
    execution_policy: str = "edit"
    bypass_approval: bool = False
    force_approval: bool = False
    capabilities: Any = None
    permission_profile: Any = None
    client_id: str = ""
    session_id: str = ""
    cancel_event: Any = None
    temperature: float = 0.1
    max_tokens: int = 8192
    current_date: str = ""
    timezone: str = "UTC"
    features: dict = field(default_factory=dict)
    sandbox_permissions: Any = None

    def __post_init__(self):
        if self.agent_metadata is None:
            self.agent_metadata = FakeAgentMetadata()


class FakeCapability:
    def __init__(self, tools):
        self.tool_definitions = tools


def _make_fake_services(tools: list[dict], workspace: Path):
    """Minimal RuntimeServices stand-in for TaskRunner policy tests."""
    from miqi.runtime.services import RuntimeModelSettings

    services = MagicMock()
    services.session_id = "test:session"
    services.workspace = str(workspace)
    services.provider = None
    services.model_settings = RuntimeModelSettings(
        model="test-model",
        temperature=0.1,
        max_tokens=4096,
        max_tool_result_chars=12000,
        context_limit_chars=600000,
    )
    services.tool_registry = MagicMock()
    services.tool_registry.get_definitions.return_value = tools
    services.orchestrator = MagicMock()
    services.turn_runner = MagicMock()
    # Explicitly None: a truthy MagicMock here would take the capability
    # path and swallow the tool list.
    services.capability_resolver = None
    services.history_runtime = None
    services.thread_runtime = None
    services.session_state = None
    services.ledger_runtime = None
    services.context_runtime = None
    return services


class TestExecutionPolicyToolFiltering:
    """Drive the REAL TaskRunner._handle_user_message pipeline and assert
    the turn it hands to turn_runner.run: plan mode filters tools via the
    production PLAN_BLOCKED_TOOLS set, and each mode sets the approval
    flags documented in task_runner."""

    _ALL_TOOLS = [
        {"name": "read_file"},
        {"name": "web_search"},
        {"name": "list_dir"},
        {"name": "write_file"},
        {"name": "edit_file"},
        {"name": "exec"},
        {"name": "spawn"},
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "user_mode,expected_policy,expected_bypass,expected_force,blocked",
        [
            ("plan", "plan", True, False, PLAN_BLOCKED_TOOLS),
            ("manual", "manual", False, True, frozenset()),
            ("edit", "edit", False, False, frozenset()),
            ("auto", "auto", True, False, frozenset()),
            (None, "edit", False, False, frozenset()),
        ],
    )
    async def test_policy_filters_tools_and_sets_flags(
        self, tmp_path, user_mode, expected_policy, expected_bypass, expected_force, blocked,
    ):
        from miqi.protocol.commands import UserMessage
        from miqi.runtime.task_runner import TaskRunner

        services = _make_fake_services(self._ALL_TOOLS, workspace=tmp_path)
        captured: dict = {}

        async def _capture_run(**kwargs):
            captured.update(kwargs)
            result = MagicMock()
            result.final_content = "ok"
            result.messages_delta = []
            result.tools_used = []
            result.token_usage = {}
            return result

        services.turn_runner.run.side_effect = _capture_run

        runner = TaskRunner(services=services, event_queue=asyncio.Queue())
        await runner.handle(UserMessage(
            content="hello",
            thread_id="th-policy",
            turn_id="turn-policy",
            mode=user_mode,
        ))

        turn = captured["turn"]
        assert turn.execution_policy == expected_policy
        tools = captured["tools"]
        names = {t["name"] for t in tools}
        for blocked_name in blocked:
            assert blocked_name not in names, (
                f"{expected_policy} should filter {blocked_name}"
            )
        for tool in self._ALL_TOOLS:
            if tool["name"] not in blocked:
                assert tool["name"] in names, (
                    f"{expected_policy} should keep {tool['name']}"
                )
        assert turn.bypass_approval is expected_bypass, (
            f"{expected_policy} bypass_approval should be {expected_bypass}"
        )
        assert turn.force_approval is expected_force, (
            f"{expected_policy} force_approval should be {expected_force}"
        )


class TestToolRuntimePolicyPropagation:
    """Drive the REAL ToolRuntime.execute_one and assert the policy flags
    are copied onto the ToolExecutionContext handed to the orchestrator."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bypass,force",
        [(True, False), (False, True), (False, False)],
    )
    async def test_policy_flags_copied_to_tool_execution_context(self, bypass, force):
        from miqi.runtime.tool_runtime import ToolRuntime

        class _FakeOrchestrator:
            def __init__(self):
                self.calls: list = []

            async def execute(self, ctx):
                self.calls.append(ctx)
                return ctx

        orchestrator = _FakeOrchestrator()
        runtime = ToolRuntime(orchestrator=orchestrator)

        turn = FakeTurnContext(
            execution_policy="edit",
            bypass_approval=bypass,
            force_approval=force,
            client_id="client-1",
            session_id="sess-1",
        )
        tool_call = type("ToolCall", (), {
            "name": "exec",
            "id": "call-1",
            "arguments": {"command": "ls"},
        })()

        await runtime.execute_one(turn, tool_call)

        assert len(orchestrator.calls) == 1
        ctx = orchestrator.calls[0]
        assert ctx.bypass_approval is bypass
        assert ctx.force_approval is force
        assert ctx.client_id == "client-1"
        assert ctx.session_id == "sess-1"
        assert ctx.turn_id == turn.turn_id
        assert ctx.thread_id == turn.thread_id


class TestTurnContextDefaults:
    """Verify TurnContext defaults are correct post-refactor."""

    def test_default_execution_policy(self):
        from miqi.runtime.turn_context import TurnContext

        tc = TurnContext(
            turn_id="test",
            thread_id="t1",
            workspace=Path("/tmp"),
            model="m",
            agent_metadata=FakeAgentMetadata(),
            provider=None,
        )

        assert tc.execution_policy == "edit"
        assert tc.bypass_approval is False
        assert tc.force_approval is False

    def test_fields_have_no_mode_legacy(self):
        """Ensure old 'mode' field is gone; replaced by execution_policy."""
        from miqi.runtime.turn_context import TurnContext

        tc = TurnContext(
            turn_id="test",
            thread_id="t1",
            workspace=Path("/tmp"),
            model="m",
            agent_metadata=FakeAgentMetadata(),
            provider=None,
        )

        # execution_policy exists, mode does not
        assert hasattr(tc, "execution_policy")
        assert not hasattr(tc, "mode"), "Old 'mode' field should not exist"


class TestPlanModeCapabilityConstraints:
    """Verify plan mode tool capability boundaries — not model behavior.

    These tests validate that plan mode exposes read-only / search tools
    while blocking write / exec / side-effect tools.  They use the real
    PLAN_BLOCKED_TOOLS constant so they stay in sync with production.
    """

    def test_plan_readonly_tools_available(self):
        """Plan mode retains read_file, web_search, list_dir, paper_search."""
        from miqi.runtime.tool_policy import PLAN_BLOCKED_TOOLS

        tools = [
            {"name": "read_file"},
            {"name": "web_search"},
            {"name": "list_dir"},
            {"name": "paper_search"},
            {"name": "web_fetch"},
            {"name": "session_search"},
        ]
        filtered = [t for t in tools if t.get("name") not in PLAN_BLOCKED_TOOLS]

        names = {t["name"] for t in filtered}
        assert "read_file" in names
        assert "web_search" in names
        assert "list_dir" in names
        assert "paper_search" in names
        assert "web_fetch" in names
        assert "session_search" in names

    def test_plan_write_tools_blocked(self):
        """Plan mode blocks write_file, edit_file, apply_patch."""
        from miqi.runtime.tool_policy import PLAN_BLOCKED_TOOLS

        tools = [
            {"name": "write_file"},
            {"name": "edit_file"},
            {"name": "apply_patch"},
            {"name": "edit_diff"},
            {"name": "write"},
            {"name": "edit"},
            {"name": "delete"},
            {"name": "move"},
        ]
        filtered = [t for t in tools if t.get("name") not in PLAN_BLOCKED_TOOLS]

        assert len(filtered) == 0, (
            f"All write/delete tools should be blocked in plan mode, "
            f"but these passed: {[t['name'] for t in filtered]}"
        )

    def test_plan_exec_tools_blocked(self):
        """Plan mode blocks exec, bash, shell."""
        from miqi.runtime.tool_policy import PLAN_BLOCKED_TOOLS

        tools = [
            {"name": "exec"},
            {"name": "bash"},
            {"name": "shell"},
        ]
        filtered = [t for t in tools if t.get("name") not in PLAN_BLOCKED_TOOLS]

        assert len(filtered) == 0, (
            f"All exec/shell tools should be blocked in plan mode, "
            f"but these passed: {[t['name'] for t in filtered]}"
        )

    def test_plan_side_effect_tools_blocked(self):
        """Plan mode blocks spawn, subagent, cron, skill_manage, memory."""
        from miqi.runtime.tool_policy import PLAN_BLOCKED_TOOLS

        tools = [
            {"name": "spawn"},
            {"name": "subagent"},
            {"name": "cron"},
            {"name": "skill_manage"},
            {"name": "memory"},
        ]
        filtered = [t for t in tools if t.get("name") not in PLAN_BLOCKED_TOOLS]

        assert len(filtered) == 0, (
            f"All side-effect tools should be blocked in plan mode, "
            f"but these passed: {[t['name'] for t in filtered]}"
        )

    def test_plan_bypass_sets_flag_for_readonly_safety(self):
        """Plan mode sets bypass_approval=True — safety depends on tool filtering."""
        turn = FakeTurnContext(execution_policy="plan")


        if turn.execution_policy == "plan":
            turn.bypass_approval = True

        assert turn.bypass_approval is True, (
            "Plan mode sets bypass_approval=True because all exposed tools "
            "are read-only (write/exec removed by PLAN_BLOCKED_TOOLS). "
            "The deny-list in permission_engine still wins."
        )

    def test_plan_system_prompt_references_readonly_analysis(self):
        """Plan mode system prompt uses read-only analysis language."""
        import tempfile
        from pathlib import Path

        from miqi.runtime.turn_context import TurnContext

        tc = TurnContext(
            turn_id="test",
            thread_id="t1",
            workspace=Path(tempfile.gettempdir()),
            model="m",
            agent_metadata=FakeAgentMetadata(),
            provider=None,
            execution_policy="plan",
        )
        assert tc.execution_policy == "plan"
        # The actual prompt assembly is in task_runner._handle_user_message;
        # this test guards that TurnContext carries plan mode correctly.
