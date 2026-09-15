"""Collaborative plan boundary for the desktop runtime.

The base TurnRunner owns the execution loop. This adapter adds the editable
plan loop: when a user adjusts a harness-generated plan, the old plan is not
executed and the model gets a fresh planning round with the user's constraint.
"""

from __future__ import annotations

from typing import Any

from miqi.runtime.turn_runner import TurnRunner

_MAX_REPLANS_PER_TURN = 5


class CollaborativeTurnRunner(TurnRunner):
    """TurnRunner variant with an editable, model-driven plan boundary."""

    # 基类计划闸门据此判断"本 runner 支持计划重规划循环"——支持时闸门先请模型
    # 产出计划（而不是用工具标签代笔拼模板）。见 turn_runner.py 计划闸门段。
    supports_plan_replan = True

    async def run(self, *, turn: Any, user_content: str, **kwargs: Any) -> Any:
        """Run the turn, restarting planning when the user adjusts the plan."""
        base_content = user_content
        current_content = base_content
        last_result: Any = None

        for _ in range(_MAX_REPLANS_PER_TURN):
            # TurnContext deliberately has no user_content field. The plan
            # boundary uses this transient value only for the plan-card goal.
            setattr(turn, "user_content", current_content)
            result = await super().run(
                turn=turn,
                user_content=current_content,
                **kwargs,
            )
            last_result = result

            adjustment = str(
                getattr(turn, "_plan_adjustment_pending", "") or ""
            ).strip()
            # #646-v2（2026-09-15）：闸门请求模型给出计划（而不是 harness 代笔）。
            plan_request = str(
                getattr(turn, "_plan_request_pending", "") or ""
            ).strip()
            if not adjustment and not plan_request:
                return result

            if plan_request:
                # 追加一轮：把"请先给出计划"作为本轮约束注入，模型下一轮的
                # ask_user_plan_confirm 内容即卡片内容（对齐 Claude Code
                # ExitPlanMode 语义：计划由模型提交、审批由用户完成）。
                current_content = f"{base_content}\n\n{plan_request}"
                setattr(turn, "_plan_request_pending", "")
                setattr(turn, "_plan_gate_blocked", False)
                setattr(turn, "_plan_confirm_done", False)
                setattr(turn, "_plan_phases", [])
                setattr(turn, "_plan_seen_tools", [])
                setattr(turn, "_plan_calls", [])
                setattr(turn, "_plan_timeline_shown", False)
                setattr(turn, "_run_ctx", None)
                continue

            # Base TurnRunner returns before PlanSnapshot/TodoState creation
            # when the decision is "modify". Reset all per-plan state so the
            # next provider round cannot reuse the rejected plan.
            current_content = (
                f"{base_content}\n\n"
                "【用户调整后的任务约束】\n"
                f"{adjustment}\n"
                "请严格基于这条约束重新规划，不要执行之前被否决的方案。"
            )
            setattr(turn, "_plan_adjustment_pending", "")
            setattr(turn, "_plan_gate_blocked", False)
            setattr(turn, "_plan_confirm_done", False)
            setattr(turn, "_plan_phases", [])
            setattr(turn, "_plan_seen_tools", [])
            setattr(turn, "_plan_calls", [])
            setattr(turn, "_plan_timeline_shown", False)
            setattr(turn, "_run_ctx", None)

        # A bounded replan loop must never silently discard the final base
        # result. Returning the last result keeps the existing exhaustion
        # semantics intact if the model repeatedly asks for adjustments.
        return last_result

    async def _harness_plan_confirm(self, turn: Any, tool_names: list[str]) -> str:
        from miqi.agent.user_input_resolver import (
            make_resolver,
            session_for_thread,
            user_input_emitter_for,
        )
        from miqi.execution.task_policy import permissions_for_tools, plan_card_steps

        thread_id = str(getattr(turn, "thread_id", "") or "")
        session_key = session_for_thread(thread_id) or thread_id
        if user_input_emitter_for(session_key) is None:
            return "confirm"

        resolver = make_resolver()
        result = await resolver({
            "threadId": turn.thread_id,
            "turnId": turn.turn_id,
            "title": "AI 准备执行任务",
            "goal": str(getattr(turn, "user_content", "") or "")[:60] or "多步骤任务",
            "steps": plan_card_steps([(name, "") for name in tool_names]),
            "permissions": permissions_for_tools(tool_names),
            "timeout_seconds": 300,
        })
        answers = result.get("answers") or {}
        choice = (
            str(answers.get("choice_id", ""))
            if result.get("status") == "submitted"
            else ""
        )
        if choice in {"modify", "adjust"}:
            # choice_label is the actual free-text user instruction collected
            # by PlanCard, not the button caption. Carry it to Collaborative
            # TurnRunner.run so the next provider round sees the new constraint.
            adjustment = str(answers.get("choice_label") or "").strip()
            if not adjustment:
                return "modify"
            turn._plan_adjustment_pending = adjustment
            turn._plan_gate_blocked = True
            return "modify"
        return choice
