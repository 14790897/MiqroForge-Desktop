"""ask_user_plan_confirm — 任务计划确认工具（#646-v2）。

本工具表达的是 Agent 与用户协作，而不是逐工具权限审批：
- ask_user_plan_confirm → 多步骤任务的工作计划/协作节点
- ask_user_confirm_card / request_action_confirmation → 真正危险动作的最后确认

当用户选择“调整方案”时，修改意见必须原样回传给模型；模型据此重新规划，
再次调用本工具，直到用户确认或取消。
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from miqi.agent.tools.base import Tool

DEFAULT_TIMEOUT_SECONDS = 180

ASK_PLAN_CONFIRM_INSTRUCTION = (
    "开始明显的多步骤任务前，可以先调用 ask_user_plan_confirm 展示工作计划，让用户参与规划；"
    "单个简单工具调用不要为了审批而弹计划卡。\n"
    "规则：\n"
    "1. 计划用于表达 Agent 准备怎么完成目标，不等同于逐工具权限审批；\n"
    "2. 上传/支付/删除/对外发送等危险动作执行前，系统单独处理最终安全确认，计划里不要重复确认；\n"
    "3. 用户确认后开始执行；用户取消后停止；\n"
    "4. 用户选择 choice_id=modify 时，必须读取 choice_label 中的修改意见，"
    "   按这些意见重新规划，并再次调用本工具展示新计划；不要假装修改成功后直接执行旧方案。"
)


class AskUserPlanConfirmTool(Tool):
    """Present a task plan, allow the user to adjust it, and await a decision."""

    def __init__(
        self,
        resolver: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ):
        self._resolver = resolver

    @property
    def name(self) -> str:
        return "ask_user_plan_confirm"

    @property
    def description(self) -> str:
        return (
            "在多步骤任务的关键规划节点展示工作计划。用户可以按当前方案执行、调整方案后重新规划，或取消。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "任务标题（用户可理解的描述，非工具名）",
                },
                "goal": {
                    "type": "string",
                    "description": "目标描述：本次任务要完成什么",
                },
                "steps": {
                    "type": "array",
                    "description": "执行计划步骤（建议 3-8 步）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "步骤名（用户可理解）"},
                            "tools": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "该步骤预计使用的工具（可选，仅作透明度信息）",
                            },
                        },
                        "required": ["name"],
                    },
                },
                "permissions": {
                    "type": "array",
                    "description": "计划可能涉及的边界：network_read / workspace_write / external_upload / exec 等",
                    "items": {"type": "string"},
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "等待用户输入秒数，默认 180",
                    "minimum": 5,
                    "maximum": 600,
                    "default": DEFAULT_TIMEOUT_SECONDS,
                },
            },
            "required": ["title", "goal", "steps"],
        }

    def normalize_args(self, args: dict[str, Any]) -> dict[str, Any]:
        steps = []
        for i, step in enumerate(args.get("steps") or []):
            if not isinstance(step, dict):
                continue
            steps.append(
                {
                    "name": str(step.get("name", f"步骤 {i + 1}")),
                    "tools": [str(t) for t in (step.get("tools") or []) if isinstance(t, str)],
                }
            )
        permissions = [
            str(permission)
            for permission in (args.get("permissions") or [])
            if isinstance(permission, str)
        ]
        try:
            timeout_raw = int(args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            timeout_raw = DEFAULT_TIMEOUT_SECONDS
        return {
            "title": str(args.get("title", "任务计划")),
            "goal": str(args.get("goal", "")),
            "steps": steps,
            "permissions": permissions,
            "timeout_seconds": max(5, min(600, timeout_raw)),
        }

    @staticmethod
    def build_result(gate_result: dict[str, Any]) -> str:
        status = gate_result.get("status", "cancelled")
        answers = gate_result.get("answers") or {}
        choice_id = str(answers.get("choice_id") or "cancel")
        choice_label = str(answers.get("choice_label") or "")

        if status == "submitted" and choice_id == "confirm":
            return json.dumps(
                {
                    "status": "confirmed",
                    "plan_confirmed": True,
                    "choice_id": "confirm",
                    "remembered": gate_result.get("remembered", False),
                },
                ensure_ascii=False,
            )

        if status == "submitted" and choice_id in {"modify", "adjust"}:
            return json.dumps(
                {
                    "status": "modify_requested",
                    "plan_confirmed": False,
                    "choice_id": "modify",
                    "adjustment": choice_label,
                    "reason": "用户要求调整计划。请结合 adjustment 重新规划后再次调用 ask_user_plan_confirm。",
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "status": "cancelled",
                "plan_confirmed": False,
                "choice_id": choice_id,
                "reason": "用户未确认任务计划",
            },
            ensure_ascii=False,
        )

    async def execute(self, **kwargs: Any) -> str:
        if self._resolver is not None:
            try:
                payload = self.normalize_args(kwargs)
                gate_result = await self._resolver(payload)
                return self.build_result(gate_result)
            except Exception as exc:  # noqa: BLE001
                return f"Error: ask_user_plan_confirm failed: {exc}"
        return (
            "Error: ask_user_plan_confirm 需要桌面端用户输入通道，当前环境未接线。"
            "请先向用户展示计划并等待聊天内确认。"
        )
