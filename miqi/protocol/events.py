"""Typed event protocol for MiQi runtime.

All events are immutable dataclasses serializable to JSON.
The protocol uses a Submission-Queue / Event-Queue pattern:
  - Frontend pushes Submissions (user messages, approvals, config changes)
  - Runtime emits Events (streaming text, tool progress, state changes)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ── Event Severity ──────────────────────────────────────────

class EventSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


# ── Agent Status ────────────────────────────────────────────

class AgentStatus(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    ERROR = "error"
    ABORTED = "aborted"


# ── Core Events ─────────────────────────────────────────────


@dataclass
class TurnStartedEvent:
    """A new agent turn has started."""
    type: str = field(default="turn_started", init=False)
    turn_id: str
    agent_name: str
    thread_id: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class TurnCompleteEvent:
    """The agent turn has completed."""
    type: str = field(default="turn_complete", init=False)
    turn_id: str
    thread_id: str
    outcome: str  # "success" | "partial" | "error" | "aborted"
    tools_used: list[str] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class TurnAbortedEvent:
    """The agent turn was aborted by user or system."""
    type: str = field(default="turn_aborted", init=False)
    turn_id: str
    thread_id: str
    reason: str
    timestamp: float = field(default_factory=time.time)


# ── Streaming Content Events ────────────────────────────────


@dataclass
class AgentMessageDeltaEvent:
    """Incremental text delta from the agent (streaming)."""
    type: str = field(default="agent_message_delta", init=False)
    turn_id: str
    delta: str
    index: int = 0  # position in the response stream


@dataclass
class AgentReasoningEvent:
    """Reasoning/thinking content from the model."""
    type: str = field(default="agent_reasoning", init=False)
    turn_id: str
    content: str
    summary: str | None = None


@dataclass
class AgentMessageEvent:
    """Complete agent message (non-streaming or final)."""
    type: str = field(default="agent_message", init=False)
    turn_id: str
    content: str
    finish_reason: str = "stop"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str | None = None
    reasoning_elapsed_s: float | None = None  # #834: server-side thinking proxy


# ── Tool Call Events ────────────────────────────────────────


@dataclass
class ToolCallBeginEvent:
    """A tool call has started."""
    type: str = field(default="tool_call_begin", init=False)
    turn_id: str
    tool_call_id: str
    tool_name: str
    tool_display: str  # Human-readable label
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallEndEvent:
    """A tool call has completed."""
    type: str = field(default="tool_call_end", init=False)
    turn_id: str
    tool_call_id: str
    tool_name: str
    success: bool
    output_preview: str  # First 200 chars of output
    output_size: int  # Total output size in chars
    duration_ms: int


@dataclass
class ToolCallOutputDeltaEvent:
    """Incremental output from a long-running tool (non-exec)."""
    type: str = field(default="tool_call_output_delta", init=False)
    turn_id: str
    tool_call_id: str
    delta: str


@dataclass
class ExecCommandBeginEvent:
    """A shell command is about to execute."""
    type: str = field(default="exec_command_begin", init=False)
    turn_id: str
    tool_call_id: str
    command: str
    cwd: str
    sandbox_type: str  # "bwrap" | "landlock" | "restricted" | "none"
    source: str = "shell"  # "shell" | "userShell"


@dataclass
class ExecCommandOutputDeltaEvent:
    """Live output from a running shell command."""
    type: str = field(default="exec_command_output_delta", init=False)
    turn_id: str
    tool_call_id: str
    stream: str  # "stdout" | "stderr"
    delta: str


@dataclass
class ExecCommandEndEvent:
    """A shell command has finished."""
    type: str = field(default="exec_command_end", init=False)
    turn_id: str
    tool_call_id: str
    exit_code: int
    duration_ms: int
    output_size: int


# ── Approval Events ─────────────────────────────────────────


@dataclass
class ApprovalRequestedEvent:
    """The runtime needs user approval for an action."""
    type: str = field(default="approval_requested", init=False)
    approval_id: str
    turn_id: str
    category: str  # "exec" | "file_write" | "network" | "patch_apply"
    description: str
    details: dict[str, Any] = field(default_factory=dict)
    allow_permanent: bool = False
    thread_id: str = ""  # Phase 31.4: scoped to thread for abort reconciliation
    timestamp: float = field(default_factory=time.time)


@dataclass
class ApprovalResolvedEvent:
    """An approval was resolved (by user, timeout, or policy)."""
    type: str = field(default="approval_resolved", init=False)
    approval_id: str
    decision: str  # "allow" | "deny" | "allow_permanent" | "timeout" | "abort"
    turn_id: str = ""  # Phase 31.4: turn that generated the approval
    timestamp: float = field(default_factory=time.time)


# ── Multi-Agent Events ──────────────────────────────────────


@dataclass
class SubAgentSpawnedEvent:
    """A sub-agent was spawned."""
    type: str = field(default="sub_agent_spawned", init=False)
    parent_turn_id: str
    sub_agent_id: str
    sub_thread_id: str
    agent_type: str
    task_label: str


@dataclass
class SubAgentCompletedEvent:
    """A sub-agent completed its task."""
    type: str = field(default="sub_agent_completed", init=False)
    sub_agent_id: str
    sub_thread_id: str
    outcome: str
    summary: str


# ── Plan Events ─────────────────────────────────────────────


@dataclass
class PlanUpdateEvent:
    """The agent updated its plan."""
    type: str = field(default="plan_update", init=False)
    turn_id: str
    plan: dict[str, Any]


# ── System Events ───────────────────────────────────────────


@dataclass
class ErrorEvent:
    """A non-fatal error occurred."""
    type: str = field(default="error", init=False)
    turn_id: str
    severity: EventSeverity = EventSeverity.ERROR
    message: str = ""
    recoverable: bool = True
    error_kind: str | None = None  # Plan 57: provider error category (additive)


@dataclass
class WarningEvent:
    """A warning that the user should see."""
    type: str = field(default="warning", init=False)
    turn_id: str
    message: str
    source: str = "runtime"  # "runtime" | "guardian" | "sandbox"


@dataclass
class ToolErrorEvent:
    """A tool execution failed with an exception (recoverable)."""
    type: str = field(default="tool_error", init=False)
    turn_id: str
    tool_name: str
    tool_call_id: str
    message: str
    recoverable: bool = True


@dataclass
class ContextCompactedEvent:
    """Conversation history was compacted."""
    type: str = field(default="context_compacted", init=False)
    turn_id: str
    thread_id: str
    messages_before: int
    messages_after: int
    tokens_saved: int


@dataclass
class SessionConfiguredEvent:
    """Session configuration was applied."""
    type: str = field(default="session_configured", init=False)
    thread_id: str
    model: str
    permission_profile: str
    sandbox_enforcement: str


# ── Phase 18: Thread Lifecycle Events ──────────────────────


@dataclass
class ThreadCreatedEvent:
    """A new thread was created."""
    type: str = field(default="thread_created", init=False)
    thread_id: str
    title: str
    parent_thread_id: str | None = None


@dataclass
class ThreadUpdatedEvent:
    """A thread's title or status was changed."""
    type: str = field(default="thread_updated", init=False)
    thread_id: str
    title: str | None = None
    status: str | None = None


@dataclass
class ThreadDeletedEvent:
    """A thread was deleted."""
    type: str = field(default="thread_deleted", init=False)
    thread_id: str


# ── Phase 18: Config & Command Events ──────────────────────


@dataclass
class ConfigUpdatedEvent:
    """Session configuration was updated at runtime."""
    type: str = field(default="config_updated", init=False)
    path: str
    value: Any


@dataclass
class CommandRejectedEvent:
    """A command was rejected because it is unsupported or invalid."""
    type: str = field(default="command_rejected", init=False)
    command_type: str
    reason: str
    recoverable: bool = True


# ── Union type for dispatch ─────────────────────────────────

EventMsg = (
    TurnStartedEvent | TurnCompleteEvent | TurnAbortedEvent |
    AgentMessageDeltaEvent | AgentMessageEvent | AgentReasoningEvent |
    ToolCallBeginEvent | ToolCallEndEvent | ToolCallOutputDeltaEvent |
    ExecCommandBeginEvent | ExecCommandOutputDeltaEvent | ExecCommandEndEvent |
    ApprovalRequestedEvent | ApprovalResolvedEvent |
    SubAgentSpawnedEvent | SubAgentCompletedEvent |
    PlanUpdateEvent |
    ErrorEvent | WarningEvent |
    ContextCompactedEvent | SessionConfiguredEvent |
    ThreadCreatedEvent | ThreadUpdatedEvent | ThreadDeletedEvent |
    ConfigUpdatedEvent | CommandRejectedEvent
)
