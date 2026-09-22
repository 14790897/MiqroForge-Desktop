"""ask_user_confirm_card tool (issue #646).

AI-initiated human-in-the-loop confirmation. The model calls this tool when
it needs the user to make a structured decision (e.g. before external network
requests, file writes, multi-step skill execution, cost-incurring actions, or
uploading a workflow definition). The desktop renders an inline confirm card,
blocks the turn, and returns the user's choice as a tool result.

Execution path:
    ToolHost.execute() intercepts ``ask_user_confirm_card`` and routes it
    through ``ToolHostContext.await_user_input`` (the KUN user-input gate),
    exactly like dangerous commands route through ``await_approval``.
    This Tool's own ``execute()`` is the fallback when no user-input channel
    is wired (CLI / headless) — it returns a structured error so the model
    never silently proceeds without a decision.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from miqi.agent.tools.base import Tool

DEFAULT_CHOICES: list[dict[str, str]] = [
    {"id": "confirm", "label": "确认执行"},
    {"id": "adjust", "label": "调整方案", "role": "adjust"},
    {"id": "cancel", "label": "取消", "role": "cancel"},
]
DEFAULT_TIMEOUT_SECONDS = 120

# System-prompt guidance injected into every KUN turn (issue #646, 功能描述④).
# Tells the model WHEN to call ask_user_confirm_card and how to interpret it.
ASK_USER_CONFIRM_INSTRUCTION = (
    "你可以调用 ask_user_confirm_card 工具，在关键步骤执行前主动弹出一张确认卡片，"
    "暂停当前任务等待用户做结构化选择。**必须**在以下场景主动调用（不要只在文本里询问）：\n"
    "1. 执行多步骤 Skill 方案前（先展示步骤列表让用户确认）；\n"
    "2. 任何外部网络请求、文件写入、可能产生费用的操作前；\n"
    "3. 向外部平台（如 MiQroForge）上传文件前；\n"
    "4. 需要用户补充关键参数、或在多个方案间做选择时。\n"
    "调用前先在正文解释为什么需要用户决定；调用后工具会返回用户的选择。"
    "返回 status 为 cancelled（用户取消或超时）时不要继续执行，"
    "choice_id 为 adjust 时应重新规划方案并再次调用本工具。"
)


class AskUserConfirmCardTool(Tool):
    """Tool for the model to request a structured user decision via an
    inline confirm card (blocking human-in-the-loop)."""

    def __init__(
        self,
        resolver: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ):
        """Optional async resolver: (payload) -> {"status": ..., "answers": ...}.

        The legacy desktop path injects ``user_input_resolver.make_resolver()``
        so execution blocks on the shared user-input gate; without a resolver
        the tool returns a structured error (never fabricates a decision).
        """
        self._resolver = resolver

    @property
    def name(self) -> str:
        return "ask_user_confirm_card"

    @property
    def description(self) -> str:
        return (
            "弹出一张确认卡片，等待用户在桌面端做结构化选择，并把选择结果返回给你。"
            "这是 AI 主动发起的人机握手：调用后当前 Turn 会暂停，直到用户点选或超时。"
            "**必须在你准备执行以下动作之前主动调用**：涉及外部网络请求、文件写入、"
            "多步骤 Skill 执行、可能产生费用的操作、或向外部平台上传文件（如 MiQroForge）。"
            "调用前先在正文里解释为什么需要用户决定。"
            "返回结果 status 为 confirmed / cancelled（超时或用户取消）；"
            "用户选择 adjust 时 status 为 cancelled 且 choice_id 为 adjust，"
            "此时应重新规划方案并再次调用本工具，不要继续执行。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "卡片标题，如「确认执行方案？」",
                },
                "message": {
                    "type": "string",
                    "description": "说明文字：为什么需要用户决定、确认后会发生什么",
                },
                "steps": {
                    "type": "array",
                    "description": "（可选）执行步骤列表，每项为 {id, title}。"
                    "后续执行阶段的状态卡会与这里的 step_id 一一对应",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "稳定步骤 ID（如 search_papers）"},
                            "title": {"type": "string", "description": "用户可见的步骤名"},
                        },
                        "required": ["id", "title"],
                    },
                },
                "choices": {
                    "type": "array",
                    "description": "（可选）结构化选项，每项为 {id, label}。"
                    "默认 [确认执行/调整方案/取消]；上传类场景建议 [确认上传/取消]",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                        },
                        "required": ["id", "label"],
                    },
                },
                "allow_remember_choice": {
                    "type": "boolean",
                    "description": "（可选）是否允许用户勾选「本次会话不再询问」。"
                    "勾选后本会话内同类确认将复用上次选择，不再弹卡；换会话失效",
                    "default": False,
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "（可选）等待秒数，默认 120；超时按 cancelled 处理，"
                    "绝不自动选择危险操作",
                    "minimum": 5,
                    "maximum": 600,
                    "default": DEFAULT_TIMEOUT_SECONDS,
                },
            },
            "required": ["title", "message"],
        }

    async def execute(self, **kwargs: Any) -> str:
        """Blocking path (desktop): wait for the user's structured choice.

        Falls back to a safe error when no user-input channel is wired —
        never fabricates a user decision.
        """
        if self._resolver is not None:
            try:
                payload = self.normalize_args(kwargs)
                gate_result = await self._resolver(payload)
                return self.build_result(gate_result)
            except Exception as exc:  # noqa: BLE001 - surface as tool error
                return f"Error: ask_user_confirm_card 执行失败：{exc}"
        return (
            "Error: ask_user_confirm_card 需要桌面端用户输入通道（KUN runtime），"
            "当前环境未接线。不要假设用户已同意；请说明需要确认的内容并请用户在聊天中回复。"
        )

    # ── helpers shared with the ToolHost interception ──────────────────

    @staticmethod
    def normalize_args(args: dict[str, Any]) -> dict[str, Any]:
        """Validate/normalize card payload, applying defaults."""
        steps = args.get("steps") or []
        choices = args.get("choices") or DEFAULT_CHOICES
        # guard: steps must be {id,title}; choices must be {id,label}.
        # Truthiness fallbacks (not dict.get defaults) — an empty string IS a
        # stored value and would render as a blank title/label (CodeRabbit #711).
        steps = [
            {
                "id": str(s.get("id") or f"step_{i}"),
                "title": str(s.get("title") or f"步骤 {i + 1}"),
            }
            for i, s in enumerate(steps)
            if isinstance(s, dict)
        ]
        choices = [
            {
                "id": str(c.get("id") or f"choice_{i}"),
                "label": str(c.get("label") or c.get("id") or f"选项 {i + 1}"),
                **({"role": str(c["role"])} if isinstance(c.get("role"), str) else {}),
            }
            for i, c in enumerate(choices)
            if isinstance(c, dict) and (c.get("id") or c.get("label"))
        ]
        if not choices:
            choices = DEFAULT_CHOICES
        # Clamp to the schema-declared bounds (5..600) so a model-supplied 0,
        # negative, or huge value can never mean "wait forever" (CodeRabbit).
        try:
            timeout_raw = int(args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            timeout_raw = DEFAULT_TIMEOUT_SECONDS
        timeout_seconds = max(5, min(600, timeout_raw))
        return {
            "title": str(args.get("title", "请确认")),
            "message": str(args.get("message", "")),
            "steps": steps,
            "choices": choices,
            "allow_remember_choice": bool(args.get("allow_remember_choice", False)),
            "timeout_seconds": timeout_seconds,
        }

    @staticmethod
    def build_result(gate_result: dict[str, Any], choice_id: str | None = None) -> str:
        """Convert the user-input-gate resolution into the tool result JSON.

        Args:
            gate_result: {"status": "submitted", "answers": {...}} or
                         {"status": "cancelled"} (timeout / user cancel /
                         turn stop).
            choice_id: Explicit choice (from resolution) if answers are empty.

        Status semantics (issue #646 功能描述③): only a confirm-type choice
        reports ``confirmed``. Cancel/adjust choices (by ``choice_role``, with
        the literal ids as fallback) report ``cancelled`` with the choice
        retained, so the model reliably aborts or re-plans.
        """
        answers = gate_result.get("answers") or {}
        cid = str(answers.get("choice_id") or choice_id or "")
        clabel = str(answers.get("choice_label") or "")
        role = str(answers.get("choice_role") or "")
        non_confirm = role in ("cancel", "adjust") or (
            not role and cid in ("cancel", "adjust")
        )
        if gate_result.get("status") == "submitted" and cid and not non_confirm:
            payload = {
                "request_id": gate_result.get("request_id", ""),
                "status": "confirmed",
                "choice_id": cid,
                "choice_label": clabel,
            }
            if gate_result.get("remembered"):
                payload["remembered"] = True
        else:
            reason = gate_result.get("reason", "cancelled")
            payload = {
                "request_id": gate_result.get("request_id", ""),
                "status": "cancelled",
                "choice_id": cid or "",
                "choice_label": clabel,
                "reason": reason,
            }
        return json.dumps(payload, ensure_ascii=False)
