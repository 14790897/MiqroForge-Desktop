"""Tool runtime — the sole adapter for single and parallel tool execution.

All tool calls (single and concurrent batches) go through this adapter,
which creates ToolExecutionContext and routes through ToolOrchestrator.
Historical: No tool context construction is scattered across the legacy
AgentLoop or other layers.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from miqi.execution.orchestrator import OrchestrationResult, ToolExecutionContext


# Interactive confirmation tools are transaction boundaries. A model may emit
# a confirmation request and sibling mutations in the same provider response;
# those siblings must never start before the user has explicitly approved the
# request. This keeps the safety boundary in the runtime instead of relying on
# prompt discipline or tool-call ordering from a provider.
_INTERACTIVE_CONFIRM_TOOLS = frozenset({
    "ask_user_confirm_card",
    "ask_user_plan_confirm",
    "request_action_confirmation",
})


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

    @staticmethod
    def _confirmation_approved(ctx: ToolExecutionContext) -> bool:
        """Return True only for an explicit confirmation result.

        Confirmation tools return structured JSON in their tool result. Never
        infer approval from tool completion alone: cancellation, timeout, or
        malformed output must keep sibling calls blocked.
        """
        if not isinstance(ctx.result, str):
            return False
        try:
            payload = json.loads(ctx.result)
        except (TypeError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        return (
            payload.get("status") == "confirmed"
            and payload.get("choice_id") == "confirm"
        )

    @staticmethod
    def _blocked_context(turn: Any, tool_call: Any) -> ToolExecutionContext:
        """Build a non-executed result for a sibling blocked by confirmation."""
        return ToolExecutionContext(
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            arguments=tool_call.arguments,
            turn_id=turn.turn_id,
            thread_id=turn.thread_id,
            agent_type=turn.agent_metadata.name,
            result="未执行：前置确认未获用户明确批准。",
            status=OrchestrationResult.DENIED_BY_USER,
            duration_ms=0,
            bypass_approval=getattr(turn, "bypass_approval", False),
            force_approval=getattr(turn, "force_approval", False),
        )

    async def execute_many(
        self, turn: Any, tool_calls: list[Any],
    ) -> list[ToolExecutionContext]:
        """Execute a batch while serializing interactive confirmation boundaries.

        Normal tool calls still run concurrently. If the provider returns one
        or more interactive confirmation calls together with siblings, all
        confirmations are completed first; only an explicit ``confirmed``
        result releases the sibling batch. On cancel/timeout/error, siblings
        are returned as non-executed user-denied contexts so the model gets a
        deterministic result and the mutation never starts.
        """
        if not tool_calls:
            return []

        confirmation_calls = [
            call for call in tool_calls if call.name in _INTERACTIVE_CONFIRM_TOOLS
        ]
        sibling_calls = [
            call for call in tool_calls if call.name not in _INTERACTIVE_CONFIRM_TOOLS
        ]

        if not confirmation_calls:
            return await asyncio.gather(
                *[self.execute_one(turn, call) for call in tool_calls],
            )

        # Confirmations must be executed in provider order. In particular, a
        # plan card is allowed to block the turn while no mutation sibling has
        # started. A later confirmation in the same batch is only reached if
        # the earlier one was explicitly approved.
        confirmation_contexts: list[ToolExecutionContext] = []
        all_confirmed = True
        for call in confirmation_calls:
            ctx = await self.execute_one(turn, call)
            confirmation_contexts.append(ctx)
            if not self._confirmation_approved(ctx):
                all_confirmed = False
                break

        sibling_contexts: list[ToolExecutionContext]
        if all_confirmed:
            sibling_contexts = await asyncio.gather(
                *[self.execute_one(turn, call) for call in sibling_calls],
            )
        else:
            sibling_contexts = [self._blocked_context(turn, call) for call in sibling_calls]

        by_id = {
            ctx.tool_call_id: ctx
            for ctx in [*confirmation_contexts, *sibling_contexts]
        }
        return [by_id[call.id] for call in tool_calls if call.id in by_id]
