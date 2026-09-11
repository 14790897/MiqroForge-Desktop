"""Submission/Command types — messages from frontend → runtime."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserMessage:
    """A user chat message."""
    type: str = field(default="user_message", init=False)
    content: str
    thread_id: str | None = None  # None = use active thread
    mode: str | None = None  # "plan" | "manual" | "edit" | "auto"
    media: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    turn_id: str | None = None
    input_items: list[dict[str, Any]] = field(default_factory=list)
    client_user_message_id: str | None = None
    settings_overrides: dict[str, Any] = field(default_factory=dict)
    # #740: resume an interrupted turn — the new turn continues from the
    # snapshot's half-generated content instead of starting fresh.
    resume_turn_id: str | None = None
    # #680: reasoning mode (fast/think) from the frontend mode switch —
    # consumed by the turn executor to apply generation budget + prompts.
    reasoning_mode: str | None = None


@dataclass
class ApprovalResponse:
    """User response to an approval request."""
    type: str = field(default="approval_response", init=False)
    approval_id: str
    decision: str  # "allow" | "deny" | "allow_permanent"


@dataclass
class AbortTurn:
    """User requests to abort the current turn."""
    type: str = field(default="abort_turn", init=False)
    thread_id: str | None = None


@dataclass
class ConfigUpdate:
    """Configuration was changed through the UI."""
    type: str = field(default="config_update", init=False)
    path: str  # dot-separated config path, e.g. "permissions.filesystem"
    value: Any


@dataclass
class ThreadCommand:
    """Thread lifecycle commands."""
    type: str = field(default="thread_command", init=False)
    action: str  # "new" | "archive" | "delete" | "fork" | "rename"
    thread_id: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompactCommand:
    """Request context compaction for a thread."""
    type: str = field(default="compact", init=False)
    thread_id: str
    reason: str = "manual"


@dataclass
class UserInputAnswer:
    """Answer to a runtime input request (elicitation)."""
    type: str = field(default="user_input_answer", init=False)
    request_id: str
    answers: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunUserShellCommand:
    """Execute a shell command requested by the user, scoped to a thread."""
    type: str = field(default="run_user_shell_command", init=False)
    command: str
    thread_id: str
    turn_id: str
    cwd: str | None = None
    client_user_message_id: str | None = None
    standalone: bool = False


@dataclass
class SteerTurn:
    """Append user input to an active turn."""
    type: str = field(default="steer_turn", init=False)
    thread_id: str
    expected_turn_id: str
    content: str
    input_items: list[dict[str, Any]] = field(default_factory=list)
    client_user_message_id: str | None = None


# ── Union type ──────────────────────────────────────────────

Submission = (
    UserMessage | ApprovalResponse | AbortTurn | ConfigUpdate |
    ThreadCommand | CompactCommand | UserInputAnswer | RunUserShellCommand |
    SteerTurn
)
