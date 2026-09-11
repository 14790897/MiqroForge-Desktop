"""Collaborative plan boundary for the desktop runtime.

The base TurnRunner owns the large execution loop. This small adapter changes
only the plan interaction: when the user asks to adjust a harness-generated
plan, preserve that feedback on the turn so ToolRuntime can stop the old tool
batch and feed the user instruction back to the model for a fresh plan.
"""

from __future__ import annotations

from typing import Any

from miqi.runtime.turn_runner import TurnRunner


class CollaborativeTurnRunner(TurnRunner):
    """TurnRunner variant with an editable, model-driven plan boundary."""

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
        choice = str(answers.get("choice_id", "")) if result.get("status") == "submitted" else ""
        if choice in {"modify", "adjust"}:
            adjustment = str(answers.get("choice_label") or "").strip()
            # The base loop still owns the next iteration. ToolRuntime sees this
            # marker while executing the current batch and blocks old mutations;
            # the blocked result carries the feedback into the model context.
            turn._plan_adjustment_pending = adjustment
            turn._plan_gate_blocked = True
            return "confirm"
        return choice
