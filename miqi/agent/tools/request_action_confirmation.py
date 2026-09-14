"""request_action_confirmation — final confirmation for high-impact actions.

This tool is deliberately separate from the task-plan collaboration step.
It is used immediately before upload, payment, destructive delete, or other
external side effects.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from miqi.agent.tools.base import Tool

DEFAULT_TIMEOUT_SECONDS = 120

REQUEST_ACTION_CONFIRM_INSTRUCTION = (
    "执行危险动作前（向外部平台上传、支付/产生费用、破坏性删除、外发数据），"
    "必须调用 request_action_confirmation 工具弹确认卡并等待用户确认。"
)


class RequestActionConfirmationTool(Tool):
    """Tool for the model to request final confirmation for a dangerous action."""

    def __init__(
        self,
        resolver: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ):
        self._resolver = resolver

    @property
    def name(self) -> str:
        return "request_action_confirmation"

    @property
    def description(self) -> str:
        return (
            "危险动作执行前的最后确认：向外部平台上传、支付、破坏性删除、外发数据。"
            "展示动作目标/文件/指纹，等待用户确认。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["upload", "payment", "delete", "external"],
                    "description": "动作类型",
                },
                "target": {"type": "string", "description": "目标（如 Qraft / 外部平台 / 文件系统）"},
                "file_name": {"type": "string", "description": "（upload/delete 时）文件名"},
                "size_bytes": {"type": "integer", "description": "（upload 时）文件大小字节"},
                "sha256": {"type": "string", "description": "（upload 时）文件指纹"},
                "description": {"type": "string", "description": "动作描述（用户可理解）"},
                "timeout_seconds": {
                    "type": "integer",
                    "default": DEFAULT_TIMEOUT_SECONDS,
                    "minimum": 5,
                    "maximum": 600,
                },
            },
            "required": ["action", "target"],
        }

    @staticmethod
    def _timeout(args: dict[str, Any]) -> int:
        try:
            raw = int(args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            raw = DEFAULT_TIMEOUT_SECONDS
        return max(5, min(600, raw))

    def normalize_args(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": str(args.get("action", "external")),
            "target": str(args.get("target", "")),
            "file_name": str(args.get("file_name") or ""),
            "size_bytes": args.get("size_bytes"),
            "sha256": str(args.get("sha256") or ""),
            "description": str(args.get("description") or ""),
            "timeout_seconds": self._timeout(args),
        }

    @staticmethod
    def build_result(gate_result: dict[str, Any]) -> str:
        status = gate_result.get("status", "cancelled")
        answers = gate_result.get("answers") or {}
        choice_id = answers.get("choice_id") or "cancel"
        if status == "submitted" and choice_id == "confirm":
            return json.dumps({
                "status": "confirmed",
                "action_confirmed": True,
                "choice_id": "confirm",
                "remembered": gate_result.get("remembered", False),
            }, ensure_ascii=False)
        return json.dumps({
            "status": "cancelled",
            "action_confirmed": False,
            "choice_id": choice_id,
            "reason": "用户未确认危险动作",
        }, ensure_ascii=False)

    async def execute(self, **kwargs: Any) -> str:
        if self._resolver is not None:
            try:
                payload = self.normalize_args(kwargs)
                gate_result = await self._resolver(payload)
                return self.build_result(gate_result)
            except Exception as exc:  # noqa: BLE001
                return f"错误：request_action_confirmation 执行失败：{exc}"
        return "错误：request_action_confirmation 需要桌面端用户输入通道，当前环境未接线。"
