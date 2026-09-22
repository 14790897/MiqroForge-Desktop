"""Tool runtime — the sole adapter for single and parallel tool execution.

All tool calls (single and concurrent batches) go through this adapter,
which creates ToolExecutionContext and routes through ToolOrchestrator.
Historical: No tool context construction is scattered across the legacy
AgentLoop or other layers.
"""

from __future__ import annotations

import asyncio
from typing import Any

from miqi.execution.orchestrator import ToolExecutionContext


class ToolRuntime:
    """Unified tool execution adapter wrapping ToolOrchestrator."""

    def __init__(self, *, orchestrator: Any):
        if orchestrator is None:
            raise RuntimeError("ToolRuntime requires a ToolOrchestrator")
        self._orchestrator = orchestrator

    async def execute_one(self, turn: Any, tool_call: Any) -> ToolExecutionContext:
        """Execute a single tool call through the orchestrator.

        Propagates turn-level permission_profile into the tool execution
        context so the orchestrator can apply per-turn policy overrides.
        """
        ctx = ToolExecutionContext(
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            arguments=tool_call.arguments,
            turn_id=turn.turn_id,
            thread_id=turn.thread_id,
            agent_type=turn.agent_metadata.name,
            # Phase 31.4: propagate client/session for approval scoping
            client_id=getattr(turn, "client_id", ""),
            session_id=getattr(turn, "session_id", ""),
            # Execution policy flags
            bypass_approval=getattr(turn, "bypass_approval", False),
            force_approval=getattr(turn, "force_approval", False),
            # #821: user-mentioned output dirs auto-sensed by the turn runner
            user_mentioned_roots=[
                str(r) for r in getattr(turn, "user_mentioned_roots", []) or []
            ],
        )
        # Phase 13: pass per-turn permission profile to orchestrator
        permission_profile = getattr(turn, "permission_profile", None)
        if permission_profile is not None:
            ctx.permission_profile = permission_profile
        # Phase 21: pass cancellation event into tool execution context
        cancel_event = getattr(turn, "cancel_event", None)
        if cancel_event is not None:
            ctx.cancel_event = cancel_event
        return await self._orchestrator.execute(ctx)

    async def execute_many(
        self, turn: Any, tool_calls: list[Any],
    ) -> list[ToolExecutionContext]:
        """Execute multiple tool calls concurrently through the orchestrator."""
        return await asyncio.gather(
            *[self.execute_one(turn, call) for call in tool_calls],
        )
