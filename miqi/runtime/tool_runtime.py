"""Tool runtime — the sole adapter for single and parallel tool execution.

All tool calls (single and concurrent batches) go through this adapter,
which creates ToolExecutionContext and routes through ToolOrchestrator.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from miqi.execution.orchestrator import OrchestrationResult, ToolExecutionContext


# Interactive confirmations are transaction boundaries. A provider response
# may contain a confirmation call and sibling mutations; no mutation may start
# before the confirmation has explicitly been approved.
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
        """Execute one tool call through the orchestrator."""
        ctx = ToolExecutionContext(
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            arguments=tool_call.arguments,
            turn_id=turn.turn_id,
            thread_id=turn.thread_id,
            agent_type=turn.agent_metadata.name,
            client_id=getattr(turn, "client_id", ""),
            session_id=getattr(turn, "session_id", ""),
            bypass_approval=getattr(turn, "bypass_approval", False),
            force_approval=getattr(turn, "force_approval", False),
        )
        permission_profile = getattr(turn, "permission_profile", None)
        if permission_profile is not None:
            ctx.permission_profile = permission_profile
        cancel_event = getattr(turn, "cancel_event", None)
        if cancel_event is not None:
            ctx.cancel_event = cancel_event
        return await self._orchestrator.execute(ctx)

    @staticmethod
    def _confirmation_approved(ctx: ToolExecutionContext) -> bool:
        """Only a structured, explicit confirm result releases siblings."""
        if not isinstance(ctx.result, str):
            return False
        try:
            payload = json.loads(ctx.result)
        except (TypeError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        return payload.get("status") == "confirmed" and payload.get("choice_id") == "confirm"

    @staticmethod
    def _blocked_context(turn: Any, tool_call: Any, reason: str = "未执行：前置确认未获用户明确批准。") -> ToolExecutionContext:
        """Build a non-executed result for a tool blocked by an interaction gate."""
        return ToolExecutionContext(
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            arguments=tool_call.arguments,
            turn_id=turn.turn_id,
            thread_id=turn.thread_id,
            agent_type=turn.agent_metadata.name,
            result=reason,
            status=OrchestrationResult.DENIED_BY_USER,
            duration_ms=0,
            bypass_approval=getattr(turn, "bypass_approval", False),
            force_approval=getattr(turn, "force_approval", False),
        )

    @staticmethod
    def _is_mutating_tool(tool_name: str) -> bool:
        """Treat unknown tools conservatively; reads may continue after a rejected plan."""
        try:
            from miqi.execution.task_policy import tool_risk
            return tool_risk(tool_name) >= 2
        except Exception:
            return True

    async def execute_many(self, turn: Any, tool_calls: list[Any]) -> list[ToolExecutionContext]:
        """Execute a batch while serializing confirmation boundaries."""
        if not tool_calls:
            return []

        confirmation_calls = [
            call for call in tool_calls if call.name in _INTERACTIVE_CONFIRM_TOOLS
        ]
        sibling_calls = [
            call for call in tool_calls if call.name not in _INTERACTIVE_CONFIRM_TOOLS
        ]

        # A rejected/modified plan closes the mutation gate for the rest of the
        # turn. Read-only inspection is still allowed so the agent can revise a
        # plan intelligently, but another write/exec cannot sneak through in a
        # later model round without a fresh explicit plan confirmation.
        plan_gate_blocked = bool(getattr(turn, "_plan_gate_blocked", False))
        if not confirmation_calls and plan_gate_blocked:
            contexts = [
                self._blocked_context(
                    turn,
                    call,
                    "未执行：任务计划尚未重新获用户明确批准。请先重新提交调整后的计划。"
                    if self._is_mutating_tool(call.name)
                    else "",
                ) if self._is_mutating_tool(call.name) else None
                for call in tool_calls
            ]
            executable = [call for call, ctx in zip(tool_calls, contexts) if ctx is None]
            executable_contexts = await asyncio.gather(
                *[self.execute_one(turn, call) for call in executable]
            ) if executable else []
            by_id = {}
            for call, ctx in zip(tool_calls, contexts):
                if ctx is not None:
                    by_id[call.id] = ctx
            for call, ctx in zip(executable, executable_contexts):
                by_id[call.id] = ctx
            return [by_id[call.id] for call in tool_calls]

        if not confirmation_calls:
            return await asyncio.gather(*[self.execute_one(turn, call) for call in tool_calls])

        confirmation_contexts: list[ToolExecutionContext] = []
        all_confirmed = True
        for call in confirmation_calls:
            ctx = await self.execute_one(turn, call)
            confirmation_contexts.append(ctx)
            approved = self._confirmation_approved(ctx)
            if call.name == "ask_user_plan_confirm":
                if approved:
                    setattr(turn, "_plan_gate_blocked", False)
                else:
                    setattr(turn, "_plan_gate_blocked", True)
            if not approved:
                all_confirmed = False
                break

        if all_confirmed:
            sibling_contexts = await asyncio.gather(
                *[self.execute_one(turn, call) for call in sibling_calls]
            )
        else:
            sibling_contexts = [
                self._blocked_context(turn, call)
                for call in sibling_calls
            ]

        by_id = {ctx.tool_call_id: ctx for ctx in [*confirmation_contexts, *sibling_contexts]}
        return [by_id[call.id] for call in tool_calls if call.id in by_id]
