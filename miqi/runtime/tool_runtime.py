"""Tool runtime — the sole adapter for single and parallel tool execution."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from miqi.execution.orchestrator import OrchestrationResult, ToolExecutionContext

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
        if not isinstance(ctx.result, str):
            return False
        try:
            payload = json.loads(ctx.result)
        except (TypeError, ValueError):
            return False
        return (
            isinstance(payload, dict)
            and payload.get("status") == "confirmed"
            and payload.get("choice_id") == "confirm"
        )

    @staticmethod
    def _blocked_context(turn: Any, tool_call: Any, reason: str) -> ToolExecutionContext:
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
        try:
            from miqi.execution.task_policy import tool_risk
            return tool_risk(tool_name) >= 2
        except Exception:
            return True

    async def execute_many(self, turn: Any, tool_calls: list[Any]) -> list[ToolExecutionContext]:
        if not tool_calls:
            return []

        confirmation_calls = [c for c in tool_calls if c.name in _INTERACTIVE_CONFIRM_TOOLS]
        sibling_calls = [c for c in tool_calls if c.name not in _INTERACTIVE_CONFIRM_TOOLS]

        plan_gate_blocked = bool(getattr(turn, "_plan_gate_blocked", False))
        adjustment = str(getattr(turn, "_plan_adjustment_pending", "") or "").strip()

        # A harness-generated plan was adjusted before the model had a chance
        # to produce a new plan. The current model batch belongs to the old
        # plan, so reads may inspect state but mutations must not run. Returning
        # the feedback as a tool result makes the next model round aware of why.
        if adjustment and plan_gate_blocked and not confirmation_calls:
            reason = (
                "未执行：用户刚刚要求调整任务方案。"
                f"用户意见：{adjustment}\n"
                "请基于这条意见重新规划，并在执行任何修改前获得新的计划确认。"
            )
            contexts: list[ToolExecutionContext | None] = []
            for call in tool_calls:
                if self._is_mutating_tool(call.name):
                    contexts.append(self._blocked_context(turn, call, reason))
                else:
                    contexts.append(None)
            executable = [c for c, ctx in zip(tool_calls, contexts) if ctx is None]
            executable_contexts = await asyncio.gather(
                *(self.execute_one(turn, c) for c in executable)
            ) if executable else []
            by_id: dict[str, ToolExecutionContext] = {}
            for call, ctx in zip(tool_calls, contexts):
                if ctx is not None:
                    by_id[call.id] = ctx
            for call, ctx in zip(executable, executable_contexts):
                by_id[call.id] = ctx
            # The next provider round must be allowed to ask for a fresh plan.
            setattr(turn, "_plan_confirm_done", False)
            setattr(turn, "_plan_adjustment_pending", "")
            return [by_id[call.id] for call in tool_calls]

        # A rejected or modified plan closes the mutation gate for subsequent
        # rounds. Read-only inspection can continue so the model can revise it.
        if plan_gate_blocked and not confirmation_calls:
            contexts: list[ToolExecutionContext | None] = [
                self._blocked_context(
                    turn,
                    call,
                    "未执行：任务计划尚未重新获用户明确批准。请先重新提交调整后的计划。",
                ) if self._is_mutating_tool(call.name) else None
                for call in tool_calls
            ]
            executable = [c for c, ctx in zip(tool_calls, contexts) if ctx is None]
            executable_contexts = await asyncio.gather(
                *(self.execute_one(turn, c) for c in executable)
            ) if executable else []
            by_id: dict[str, ToolExecutionContext] = {}
            for call, ctx in zip(tool_calls, contexts):
                if ctx is not None:
                    by_id[call.id] = ctx
            for call, ctx in zip(executable, executable_contexts):
                by_id[call.id] = ctx
            return [by_id[call.id] for call in tool_calls]

        if not confirmation_calls:
            return await asyncio.gather(*(self.execute_one(turn, call) for call in tool_calls))

        confirmation_contexts: list[ToolExecutionContext] = []
        all_confirmed = True
        for call in confirmation_calls:
            # Interactive confirmations form an explicit FIFO queue. A user
            # decision for one card must not suppress later confirmation cards
            # from the same provider response; they are still shown one by one.
            ctx = await self.execute_one(turn, call)
            confirmation_contexts.append(ctx)
            approved = self._confirmation_approved(ctx)
            if call.name == "ask_user_plan_confirm":
                if approved:
                    setattr(turn, "_plan_gate_blocked", False)
                    setattr(turn, "_plan_adjustment_pending", "")
                else:
                    setattr(turn, "_plan_gate_blocked", True)
                    setattr(turn, "_plan_confirm_done", False)
            if not approved:
                all_confirmed = False

        if all_confirmed:
            sibling_contexts = await asyncio.gather(
                *(self.execute_one(turn, call) for call in sibling_calls)
            )
        else:
            sibling_contexts = [self._blocked_context(turn, call, "未执行：前置确认未获用户明确批准。") for call in sibling_calls]

        by_id = {ctx.tool_call_id: ctx for ctx in [*confirmation_contexts, *sibling_contexts]}
        return [by_id[call.id] for call in tool_calls if call.id in by_id]
