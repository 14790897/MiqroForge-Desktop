"""todo_write — Agent 进度状态协议工具（#646-v2 v3.3 最终拍板）。

Grok todo_write 吸收 + v3.3 约束：
- merge 增量（只发变化：{id, status} / 新增 auxiliary）
- plan item 只允许 status transition（禁删/改 content）→ error-as-output
- transition validator（状态机）
- revision 递增
- summary_for_prompt（结构化短摘要——不塞全列表）
- is_read_only: true（Task metadata 变更——不触发审批）

定位：PlanSnapshot（不可变）→ TodoState（可变）→ Timeline（projection）。
模型维护"语义进度"；harness 用 ToolEvent 写 observed 条目兜底。
"""

from __future__ import annotations

from typing import Any

from miqi.agent.tools.base import Tool
from miqi.runtime.task_objects import TodoState


class TodoWriteTool(Tool):
    """todo_write — 维护任务执行进度（Agent 与 Timeline 之间的状态协议）。"""

    name = "todo_write"
    description = (
        "创建并维护结构化任务步骤列表（用户实时看到——这是你展示进度的主要方式）。\n"
        "用于 3+ 步骤的复杂任务。简单单步任务跳过。\n"
        "规则：\n"
        "1. 增量更新：只发送变化项——翻转状态只需 {id, status}（如 "
        '{"id": "step-1", "status": "in_progress"}）\n'
        "2. 新增辅助步骤：{id, content, kind: \"auxiliary\", status}（完成核心步骤所需的补充动作）\n"
        "3. 不能修改已批准计划（plan 步骤）的内容——如需要修改，先调用 "
        "ask_user_plan_confirm 重新确认\n"
        "4. 状态转换：queued→in_progress→completed；in_progress⇄blocked；任何→cancelled\n"
        "5. 执行开始前先把对应步骤标记为 in_progress，完成后再标记 completed\n"
        "6. 等待用户输入/权限/外部资源时标记 blocked 并附 blocked_reason\n"
        "每次调用后返回当前进度摘要。"
    )

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "要写入的 todo 项（merge 模式只发变化项）",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "稳定 ID（计划步骤的 ID 或新辅助步骤 ID）"},
                        "status": {
                            "type": "string",
                            "enum": ["queued", "in_progress", "blocked", "completed", "cancelled"],
                        },
                        "content": {"type": "string", "description": "内容（仅新增 auxiliary 步骤时提供）"},
                        "kind": {"type": "string", "enum": ["auxiliary", "observed"]},
                        "blocked_reason": {
                            "type": "string",
                            "enum": ["waiting_user", "waiting_permission", "waiting_external", "execution_failed", "unknown"],
                        },
                    },
                    "required": ["id"],
                },
            },
            "merge": {
                "type": "boolean",
                "description": "默认 true：按 id 合并（只更新提供的字段）；false：整体替换（危险——勿用于已确认计划）",
                "default": True,
            },
        },
        "required": ["todos"],
    }

    def __init__(self, todo_state: TodoState | None = None):
        # todo_state 可为 None：注册表静态 schema 用；运行时由 turn_runner
        # 用 turn._run_ctx.todo_state 实例化（v3.3：进度协议挂执行上下文）
        self._todo = todo_state

    async def execute(self, **kwargs: Any) -> str:
        import json

        if self._todo is None:
            return json.dumps({
                "status": "error",
                "reason": "NO_RUN_CONTEXT",
                "suggestion": "任务计划尚未确认——先完成计划确认（ask_user_plan_confirm）",
            }, ensure_ascii=False)

        patches = kwargs.get("todos") or []
        merge = bool(kwargs.get("merge", True))

        if not isinstance(patches, list):
            return json.dumps({"status": "error", "reason": "todos must be a list"}, ensure_ascii=False)

        if not merge:
            # 整体替换：仅允许"清空 auxiliary + 保留 plan"之外的场景——保守起见拒绝
            return json.dumps({
                "status": "rejected",
                "reason": "FULL_REPLACE_NOT_ALLOWED",
                "suggestion": "use merge=true for incremental updates",
            }, ensure_ascii=False)

        rejected = self._todo.merge(patches)
        result: dict[str, Any] = {"status": "ok", "summary": self._todo.summary(), "revision": self._todo.revision}
        if rejected:
            result["status"] = "partial"
            result["rejected"] = rejected
        return json.dumps(result, ensure_ascii=False)
