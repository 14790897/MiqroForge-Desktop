"""#646-v2 R2c 确认类工具注入边界（task_runner 路径）。

回归背景：两个 runtime 各自手写注入条件导致漂移——task_runner 只认
``ask_user_confirm_card`` / ``ask_user_plan_confirm``，
``request_action_confirmation`` 零注入；KUN loop 只认 ``ask_user_confirm_card``。
现在统一走 ``miqi.agent.tools.confirm_instructions``，本文件锁住入口边界。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from miqi.agent.tools.ask_user_confirm import ASK_USER_CONFIRM_INSTRUCTION
from miqi.agent.tools.ask_user_plan_confirm import ASK_PLAN_CONFIRM_INSTRUCTION
from miqi.agent.tools.confirm_instructions import instructions_for_tools
from miqi.agent.tools.request_action_confirmation import (
    REQUEST_ACTION_CONFIRM_INSTRUCTION,
)
from miqi.protocol.commands import UserMessage
from miqi.runtime.capabilities import RuntimeCapabilities
from miqi.runtime.task_runner import TaskRunner

DANGEROUS_ENUM_WORDS = ("upload", "payment", "delete", "external")


def _tool_def(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


class _StubResolver:
    """Deterministic capabilities resolver: returns exactly the given tool defs."""

    def __init__(self, names: list[str]) -> None:
        self._names = names

    def resolve(self, *, agent_metadata: object) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            tool_definitions=[_tool_def(n) for n in self._names]
        )


async def _capture_system_prompt(fake_services, tool_names: list[str]) -> str:
    """Drive the real UserMessage path and return the assembled system prompt."""
    fake_services.capability_resolver = _StubResolver(tool_names)
    captured: dict[str, str] = {}

    async def _capture(**kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt", "")
        result = MagicMock()
        result.final_content = "ok"
        return result

    fake_services.turn_runner.run.side_effect = _capture
    runner = TaskRunner(services=fake_services, event_queue=asyncio.Queue())
    await runner.handle(UserMessage(content="hello", thread_id="cli:default"))
    assert captured, "TurnRunner was not called"
    return captured["system_prompt"]


# ── ①–④ 逐工具注入边界 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_action_card_tool_gets_its_instruction(fake_services) -> None:
    """① 暴露 request_action_confirmation → 注入 ActionCard 指令。"""
    prompt = await _capture_system_prompt(fake_services, ["request_action_confirmation"])
    assert REQUEST_ACTION_CONFIRM_INSTRUCTION in prompt


@pytest.mark.asyncio
async def test_confirm_card_tool_gets_non_dangerous_copy(fake_services) -> None:
    """② 暴露 ask_user_confirm_card → 注入新指令，且不含旧危险文案。"""
    prompt = await _capture_system_prompt(fake_services, ["ask_user_confirm_card"])
    assert ASK_USER_CONFIRM_INSTRUCTION in prompt
    assert "向外部平台上传文件（MiQroForge" not in prompt
    assert REQUEST_ACTION_CONFIRM_INSTRUCTION not in prompt


@pytest.mark.asyncio
async def test_plan_tool_only_gets_plan_instruction(fake_services) -> None:
    """③ 只暴露计划卡工具 → 只注计划指令（不含 confirm / action 卡）。"""
    prompt = await _capture_system_prompt(fake_services, ["ask_user_plan_confirm"])
    assert ASK_PLAN_CONFIRM_INSTRUCTION in prompt
    assert ASK_USER_CONFIRM_INSTRUCTION not in prompt
    assert REQUEST_ACTION_CONFIRM_INSTRUCTION not in prompt


@pytest.mark.asyncio
async def test_no_confirm_tools_injects_nothing(fake_services) -> None:
    """④ 三个确认类工具都不暴露 → 三者都不注入。"""
    prompt = await _capture_system_prompt(fake_services, ["read_file", "exec"])
    assert ASK_USER_CONFIRM_INSTRUCTION not in prompt
    assert ASK_PLAN_CONFIRM_INSTRUCTION not in prompt
    assert REQUEST_ACTION_CONFIRM_INSTRUCTION not in prompt


@pytest.mark.asyncio
async def test_all_confirm_tools_inject_all_instructions(fake_services) -> None:
    """三个都暴露 → 三条指令都在（共享助手保序注入）。"""
    prompt = await _capture_system_prompt(
        fake_services,
        ["ask_user_confirm_card", "ask_user_plan_confirm", "request_action_confirmation"],
    )
    for instruction in (
        REQUEST_ACTION_CONFIRM_INSTRUCTION,
        ASK_USER_CONFIRM_INSTRUCTION,
        ASK_PLAN_CONFIRM_INSTRUCTION,
    ):
        assert instruction in prompt


# ── ⑤ 常量级：文案分工不串味 ────────────────────────────────────────────────


def test_confirm_card_instruction_has_no_dangerous_action_list() -> None:
    """旧卡指令不再列危险清单（危险动作归 request_action_confirmation）。"""
    lowered = ASK_USER_CONFIRM_INSTRUCTION.lower()
    for word in DANGEROUS_ENUM_WORDS:
        assert word not in lowered
    assert "危险动作不要使用本工具" in ASK_USER_CONFIRM_INSTRUCTION
    assert "request_action_confirmation" in ASK_USER_CONFIRM_INSTRUCTION


def test_action_card_instruction_is_exclusive_entry() -> None:
    """ActionCard 指令声明「必须调用」与「唯一」入口。"""
    assert "必须调用" in REQUEST_ACTION_CONFIRM_INSTRUCTION
    assert "唯一" in REQUEST_ACTION_CONFIRM_INSTRUCTION


def test_instructions_for_tools_order_and_dedup() -> None:
    """共享助手：按表顺序注入、去重。"""
    out = instructions_for_tools(
        [
            "ask_user_plan_confirm",
            "request_action_confirmation",
            "ask_user_confirm_card",
            "request_action_confirmation",
        ]
    )
    assert out == [
        REQUEST_ACTION_CONFIRM_INSTRUCTION,
        ASK_USER_CONFIRM_INSTRUCTION,
        ASK_PLAN_CONFIRM_INSTRUCTION,
    ]


def test_instructions_for_tools_ignores_unknown_names() -> None:
    assert instructions_for_tools(["read_file", "exec"]) == []
