"""#646-v2 确认类工具的共享指令注入表（task_runner 与 kun loop 共用）。

历史事故：两个 runtime 各自手写注入条件导致漂移——「表不存在」让
request_action_confirmation 在 KUN 路径零注入。新增确认类工具只改此表。
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable

TOOL_INSTRUCTION_MAP = {
    "request_action_confirmation": "miqi.agent.tools.request_action_confirmation:REQUEST_ACTION_CONFIRM_INSTRUCTION",
    "ask_user_confirm_card": "miqi.agent.tools.ask_user_confirm:ASK_USER_CONFIRM_INSTRUCTION",
    "ask_user_plan_confirm": "miqi.agent.tools.ask_user_plan_confirm:ASK_PLAN_CONFIRM_INSTRUCTION",
}


def instructions_for_tools(tool_names: Iterable[str]) -> list[str]:
    """按暴露的工具名返回应注入的确认类指令（保序=按表顺序、去重）。"""
    names = set(tool_names)
    out: list[str] = []
    for tool, ref in TOOL_INSTRUCTION_MAP.items():
        if tool in names:
            module_name, attr = ref.split(":", 1)
            out.append(getattr(importlib.import_module(module_name), attr))
    return out
