"""Plan step progress tracking (issue #1078).

计划卡（`ask_user_plan_confirm`）里每步都带 `tools` 列表，前端 PlanCard 读
`stepStatus` 渲染步骤勾选与「执行中 N/M」——但后端从未填充过这个字段，于是
图标永远是空心圆、进度永远 0。

本模块在执行期把工具事件映射回步骤：工具开始 → 该步 running，工具结束 →
done / failed。turn 执行侧每个工具事件后取一次快照推给前端。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

# 工具结束后稍等一下再标记完成：产物落盘/镜像回写是异步的，立刻改状态会让
# 前端先勾上、产物后到，看起来像「勾了但没有文件」。
_SETTLE_SECONDS = 0.4


@dataclass
class PlanStepState:
    step_id: str
    name: str
    tools: list[str] = field(default_factory=list)
    status: str = PENDING


class StepProgressTracker:
    """把工具执行事件归到计划步骤上，产出前端要的状态表。"""

    def __init__(self, steps: list[dict[str, Any]]):
        self._steps: list[PlanStepState] = []
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            name = str(step.get("title") or step.get("name") or f"步骤 {index}").strip()
            step_id = str(step.get("id") or f"step_{index}").strip() or f"step_{index}"
            tools = [str(t) for t in (step.get("tools") or []) if isinstance(t, str)]
            self._steps.append(PlanStepState(step_id=step_id, name=name, tools=tools))

    # ── 事件入口 ─────────────────────────────────────────────────────────

    def on_tool_begin(self, tool_name: str) -> dict[str, str] | None:
        """工具开始执行 → 所属步骤进入 running。"""
        step = self._match(tool_name)
        if step is None:
            return None
        if step.status in (PENDING, FAILED):
            step.status = RUNNING
        return self.step_status()

    def on_tool_end(self, tool_name: str, ok: bool) -> dict[str, str] | None:
        """工具结束 → 所属步骤 done / failed。"""
        step = self._match(tool_name)
        if step is None:
            return None
        time.sleep(_SETTLE_SECONDS)
        step.status = DONE if ok else FAILED
        return self.step_status()

    # ── 匹配 ─────────────────────────────────────────────────────────────

    def _match(self, tool_name: str) -> PlanStepState | None:
        """工具名 → 步骤。

        模型给的 `tools` 写法并不统一（有写 `read_file` 的，也有写「读取文件」
        的），所以这里按包含关系匹配提高命中率；匹配不到就当作不属于任何步骤。
        """
        try:
            for step in self._steps:
                for declared in step.tools:
                    if not declared:
                        continue
                    if declared in tool_name or tool_name in declared:
                        return step
        except Exception:
            pass
        return None

    # ── 快照 ─────────────────────────────────────────────────────────────

    def step_status(self) -> dict[str, str]:
        """计划卡用的状态表：键是步骤名（`PlanCardEntry.stepStatus`）。"""
        return {step.name: step.status for step in self._steps}

    def steps_status(self) -> dict[str, dict[str, str]]:
        """确认卡用的状态表：键是步骤 id，值是 `{status}` 对象。"""
        return {step.step_id: {"status": step.status} for step in self._steps}

    def has_progress(self) -> bool:
        return any(step.status != PENDING for step in self._steps)
