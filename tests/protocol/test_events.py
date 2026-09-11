"""Tests for miqi.protocol.events."""

import json

# ── Task 1.1: Enums ────────────────────────────────────────


def test_event_severity_values():
    from miqi.protocol.events import EventSeverity

    assert EventSeverity.INFO.value == "info"
    assert EventSeverity.WARNING.value == "warning"
    assert EventSeverity.ERROR.value == "error"


def test_event_severity_serialization():
    from miqi.protocol.events import EventSeverity

    assert json.dumps(EventSeverity.INFO) == '"info"'


def test_agent_status_values():
    from miqi.protocol.events import AgentStatus

    assert AgentStatus.IDLE.value == "idle"
    assert AgentStatus.THINKING.value == "thinking"
    assert AgentStatus.EXECUTING.value == "executing"
    assert AgentStatus.WAITING_APPROVAL.value == "waiting_approval"
    assert AgentStatus.COMPLETED.value == "completed"
    assert AgentStatus.ERROR.value == "error"
    assert AgentStatus.ABORTED.value == "aborted"


# ── Task 1.2: Turn lifecycle events ────────────────────────


def test_turn_started_event():
    from miqi.protocol.events import TurnStartedEvent

    event = TurnStartedEvent(
        turn_id="turn_001",
        agent_name="main",
        thread_id="thread_abc",
    )
    assert event.type == "turn_started"
    assert event.turn_id == "turn_001"
    assert event.agent_name == "main"
    assert isinstance(event.timestamp, float)

    from dataclasses import asdict
    data = asdict(event)
    assert data["type"] == "turn_started"
    assert data["turn_id"] == "turn_001"


def test_turn_complete_event():
    from miqi.protocol.events import TurnCompleteEvent

    event = TurnCompleteEvent(
        turn_id="turn_001",
        thread_id="thread_abc",
        outcome="success",
        tools_used=["read_file", "exec"],
        token_usage={"prompt": 500, "completion": 200},
    )
    assert event.outcome == "success"
    assert len(event.tools_used) == 2


def test_turn_aborted_event():
    from miqi.protocol.events import TurnAbortedEvent

    event = TurnAbortedEvent(
        turn_id="turn_001",
        thread_id="thread_abc",
        reason="user requested abort",
    )
    assert event.reason == "user requested abort"


# ── Task 1.3: Streaming content events ─────────────────────


def test_agent_message_delta_event():
    from miqi.protocol.events import AgentMessageDeltaEvent

    event = AgentMessageDeltaEvent(
        turn_id="turn_001",
        delta="Hello",
        index=0,
    )
    assert event.type == "agent_message_delta"
    assert event.delta == "Hello"
    assert event.index == 0


def test_agent_reasoning_event():
    from miqi.protocol.events import AgentReasoningEvent

    event = AgentReasoningEvent(
        turn_id="turn_001",
        content="Let me think about this...",
        summary="Thinking about the problem",
    )
    assert event.type == "agent_reasoning"
    assert event.summary == "Thinking about the problem"


def test_agent_message_event():
    from miqi.protocol.events import AgentMessageEvent

    tool_calls = [{
        "id": "call_001",
        "type": "function",
        "function": {"name": "web_search", "arguments": '{"query":"MiQi"}'},
    }]
    event = AgentMessageEvent(
        turn_id="turn_001",
        content="Here is the result.",
        finish_reason="stop",
        tool_calls=tool_calls,
    )
    assert event.type == "agent_message"
    assert event.finish_reason == "stop"
    assert event.tool_calls == tool_calls


# ── Task 1.4: Tool call events ─────────────────────────────


def test_tool_call_begin_event():
    from miqi.protocol.events import ToolCallBeginEvent

    event = ToolCallBeginEvent(
        turn_id="turn_001",
        tool_call_id="call_abc",
        tool_name="read_file",
        tool_display='read_file("config.json")',
        arguments={"path": "config.json"},
    )
    assert event.type == "tool_call_begin"
    assert event.tool_name == "read_file"


def test_tool_call_end_event():
    from miqi.protocol.events import ToolCallEndEvent

    event = ToolCallEndEvent(
        turn_id="turn_001",
        tool_call_id="call_abc",
        tool_name="read_file",
        success=True,
        output_preview='{"key": "value"}...',
        output_size=1024,
        duration_ms=150,
    )
    assert event.success is True
    assert event.duration_ms == 150


def test_exec_command_begin_event():
    from miqi.protocol.events import ExecCommandBeginEvent

    event = ExecCommandBeginEvent(
        turn_id="turn_001",
        tool_call_id="call_def",
        command="npm test",
        cwd="/home/user/project",
        sandbox_type="bwrap",
    )
    assert event.sandbox_type == "bwrap"


def test_exec_command_output_delta_event():
    from miqi.protocol.events import ExecCommandOutputDeltaEvent

    event = ExecCommandOutputDeltaEvent(
        turn_id="turn_001",
        tool_call_id="call_def",
        stream="stdout",
        delta="Running tests...\n",
    )
    assert event.stream == "stdout"


def test_exec_command_end_event():
    from miqi.protocol.events import ExecCommandEndEvent

    event = ExecCommandEndEvent(
        turn_id="turn_001",
        tool_call_id="call_def",
        exit_code=0,
        duration_ms=2300,
        output_size=4096,
    )
    assert event.exit_code == 0


# ── Task 1.5: Approval, multi-agent, plan, system events ───


def test_approval_requested_event():
    from miqi.protocol.events import ApprovalRequestedEvent

    event = ApprovalRequestedEvent(
        approval_id="appr_001",
        turn_id="turn_001",
        category="exec",
        description="Run command: rm -rf /tmp/test",
        details={"command": "rm -rf /tmp/test"},
        allow_permanent=True,
    )
    assert event.category == "exec"
    assert event.allow_permanent is True


def test_approval_resolved_event():
    from miqi.protocol.events import ApprovalResolvedEvent

    event = ApprovalResolvedEvent(
        approval_id="appr_001",
        decision="allow",
    )
    assert event.decision == "allow"


def test_sub_agent_spawned_event():
    from miqi.protocol.events import SubAgentSpawnedEvent

    event = SubAgentSpawnedEvent(
        parent_turn_id="turn_001",
        sub_agent_id="agent_abc123",
        sub_thread_id="thread_xyz",
        agent_type="code-agent",
        task_label="Fix lint errors",
    )
    assert event.agent_type == "code-agent"


def test_sub_agent_completed_event():
    from miqi.protocol.events import SubAgentCompletedEvent

    event = SubAgentCompletedEvent(
        sub_agent_id="agent_abc123",
        sub_thread_id="thread_xyz",
        outcome="success",
        summary="Fixed 5 lint errors",
    )
    assert event.outcome == "success"


def test_plan_update_event():
    from miqi.protocol.events import PlanUpdateEvent

    event = PlanUpdateEvent(
        turn_id="turn_001",
        plan={
            "steps": [
                {"id": "1", "description": "Analyze", "status": "completed"},
                {"id": "2", "description": "Implement", "status": "in_progress"},
            ]
        },
    )
    assert len(event.plan["steps"]) == 2


def test_error_event():
    from miqi.protocol.events import ErrorEvent, EventSeverity

    event = ErrorEvent(
        turn_id="turn_001",
        severity=EventSeverity.ERROR,
        message="API connection failed",
        recoverable=True,
    )
    assert event.severity == EventSeverity.ERROR
    assert event.recoverable is True


def test_warning_event():
    from miqi.protocol.events import WarningEvent

    event = WarningEvent(
        turn_id="turn_001",
        message="Disk space low",
        source="sandbox",
    )
    assert event.source == "sandbox"


def test_context_compacted_event():
    from miqi.protocol.events import ContextCompactedEvent

    event = ContextCompactedEvent(
        turn_id="turn_001",
        thread_id="thread_abc",
        messages_before=100,
        messages_after=50,
        tokens_saved=5000,
    )
    assert event.tokens_saved == 5000


def test_session_configured_event():
    from miqi.protocol.events import SessionConfiguredEvent

    event = SessionConfiguredEvent(
        thread_id="thread_abc",
        model="claude-sonnet-4-6",
        permission_profile="standard",
        sandbox_enforcement="strict",
    )
    assert event.model == "claude-sonnet-4-6"


def test_exec_command_begin_event_has_source_default():
    from miqi.protocol.events import ExecCommandBeginEvent

    event = ExecCommandBeginEvent(
        turn_id="turn-1",
        tool_call_id="exec-1",
        command="echo hi",
        cwd="/workspace",
        sandbox_type="restricted",
    )

    assert event.source == "shell"


def test_exec_command_begin_event_accepts_user_shell_source():
    from miqi.protocol.events import ExecCommandBeginEvent

    event = ExecCommandBeginEvent(
        turn_id="turn-1",
        tool_call_id="exec-1",
        command="git status --short",
        cwd="/workspace",
        sandbox_type="restricted",
        source="userShell",
    )

    assert event.source == "userShell"
