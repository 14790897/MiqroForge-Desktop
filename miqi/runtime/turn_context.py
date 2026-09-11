"""Per-turn configuration and execution context.

Wraps all the context needed for a single agent turn:
model provider, skills, permissions, sandbox config, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from miqi.protocol.permissions import SandboxPermissions
from miqi.runtime.agent_registry import AgentMetadata


@dataclass
class TurnContext:
    """Context for a single agent turn."""

    turn_id: str
    agent_metadata: AgentMetadata
    thread_id: str
    workspace: Path
    # Provider
    model: str
    provider: Any  # LLMProvider
    # Phase 31.4: client/session identity for approval scoping
    client_id: str = ""
    session_id: str = ""
    # Execution policy: "plan" | "manual" | "edit" | "auto"
    execution_policy: str = "edit"
    # #680: reasoning mode ("fast" | "think") — generation budget + prompts
    # applied by the turn executor (desktop chain).
    reasoning_mode: str | None = None
    temperature: float = 0.1
    max_tokens: int = 8192
    # Permissions
    sandbox_permissions: SandboxPermissions = field(
        default_factory=SandboxPermissions
    )
    # Feature flags
    features: dict[str, bool] = field(default_factory=dict)
    # Current date/time for the system prompt
    current_date: str = ""
    timezone: str = "UTC"
    # Phase 13: resolved capabilities and permission profile
    capabilities: Any | None = None
    permission_profile: Any | None = None
    cancel_event: Any | None = None  # asyncio.Event for turn abort signalling
    # Execution policy flags for approval layer
    bypass_approval: bool = False    # skip all approval checks
    force_approval: bool = False     # require approval even if switch is off
    # #821: directories the user mentioned in this turn's messages
    # (auto-sensed by the turn runner; injected into file tools as
    # ``_user_roots`` via ToolRuntime/orchestrator).  Host Paths.
    user_mentioned_roots: list[Path] = field(default_factory=list)
