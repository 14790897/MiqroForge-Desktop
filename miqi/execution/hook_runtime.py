"""Hook runtime — pre/post tool-use hooks.

Hooks are Python callables registered against tool patterns.
They run synchronously in the tool execution context.
"""

from __future__ import annotations

import asyncio
import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from loguru import logger


class HookPoint(str, Enum):
    """Hook points matching the Codex runtime lifecycle.

    Pre-/post-tool hooks run in the tool execution context.
    Session/turn/subagent hooks allow plugins to observe and
    react to runtime state transitions.
    """

    # Tool-level hooks (existing)
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"

    # Permission / stop decision points
    PERMISSION_REQUEST = "permission_request"
    STOP = "stop"

    # Session lifecycle
    SESSION_START = "session_start"
    SESSION_END = "session_end"

    # Turn lifecycle
    PROMPT_SUBMIT = "prompt_submit"
    TURN_START = "turn_start"
    TURN_END = "turn_end"

    # Tool execution (aliases for pre/post)
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"

    # Compaction lifecycle
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"

    # Sub-agent lifecycle
    SUBAGENT_START = "subagent_start"
    SUBAGENT_END = "subagent_end"


@dataclass
class HookOutcome:
    """Decision returned by a hook callback.

    - ``continue``: proceed normally.
    - ``block``: veto the current operation; ``reason`` is returned to the caller.
    - ``modify``: apply ``patch`` to the operation and continue scanning for blocks.
    """

    action: str  # continue | block | modify
    reason: str = ""
    patch: dict | None = None

    @classmethod
    def continue_(cls) -> "HookOutcome":
        return cls(action="continue")

    @classmethod
    def block(cls, reason: str) -> "HookOutcome":
        return cls(action="block", reason=reason)

    @classmethod
    def modify(cls, patch: dict) -> "HookOutcome":
        return cls(action="modify", patch=patch)


HookCallback = Callable[[Any], Awaitable[HookOutcome | None]]


@dataclass
class LifecycleHookContext:
    """Lightweight context for lifecycle hooks that are not tied to a tool call."""

    hook_point: HookPoint
    tool_name: str = "*"
    data: dict = field(default_factory=dict)


@dataclass
class HookRegistration:
    hook_point: HookPoint
    tool_pattern: str  # fnmatch pattern, e.g. "exec", "write_*", "*"
    callback: HookCallback
    priority: int = 100  # lower = runs first
    source: str = ""  # source identifier for later bulk unregister


class HookRuntime:
    """Manages and executes hooks."""

    def __init__(self, hook_timeout: float = 30.0):
        self._hooks: dict[HookPoint, list[HookRegistration]] = {
            p: [] for p in HookPoint
        }
        self.hook_timeout = hook_timeout

    def register(self, reg: HookRegistration) -> None:
        self._hooks[reg.hook_point].append(reg)
        self._hooks[reg.hook_point].sort(key=lambda h: h.priority)
        logger.debug(
            "Registered hook: {} → {} (priority={})",
            reg.hook_point.value, reg.tool_pattern, reg.priority,
        )

    def unregister_source(self, source: str) -> None:
        """Drop every registration whose source matches ``source``."""
        dropped = 0
        for point, regs in self._hooks.items():
            kept = [r for r in regs if r.source != source]
            dropped += len(regs) - len(kept)
            self._hooks[point] = kept
        logger.debug("Unregistered {} hooks from {}", dropped, source)

    async def run_with_outcome(self, point: HookPoint, ctx: Any) -> HookOutcome:
        """Run matching hooks and return a decision outcome.

        Iterates registrations in priority order. A ``block`` outcome short-circuits
        and is returned immediately. A ``modify`` outcome is remembered and the scan
        continues in case a later hook blocks; if no block occurs, the last modify
        outcome is returned. Callbacks may return ``None`` (treated as ``continue``).
        Exceptions in a callback are logged and do not stop the scan.
        """
        last_modify: HookOutcome | None = None
        for reg in self._hooks.get(point, []):
            if not fnmatch.fnmatch(ctx.tool_name, reg.tool_pattern):
                continue
            try:
                outcome = await asyncio.wait_for(
                    reg.callback(ctx),
                    timeout=self.hook_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "Hook {} timed out after {}s for {}",
                    reg.tool_pattern, self.hook_timeout, ctx.tool_name
                )
                continue
            except Exception:
                logger.exception(
                    "Hook {} failed for {}", reg.tool_pattern, ctx.tool_name
                )
                continue

            if outcome is None:
                continue

            if outcome.action == "block":
                return outcome
            if outcome.action == "modify":
                last_modify = outcome

        return last_modify if last_modify is not None else HookOutcome.continue_()

    async def run(self, point: HookPoint, ctx: Any) -> None:
        """Run all hooks registered for this point that match the tool.

        This is the legacy fire-and-forget entrypoint; outcomes are ignored.
        """
        await self.run_with_outcome(point, ctx)
