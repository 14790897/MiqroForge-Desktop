import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.runtime.tool_runtime import ToolRuntime


class _Turn:
    turn_id = "turn-plan"
    thread_id = "thread-plan"

    class _Meta:
        name = "code-agent"

    agent_metadata = _Meta()
    _plan_gate_blocked = False


class _Call:
    def __init__(self, name: str, call_id: str):
        self.name = name
        self.id = call_id
        self.arguments = {}


@pytest.mark.asyncio
async def test_rejected_plan_blocks_mutation_in_later_round():
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock()

    async def execute(ctx):
        ctx.result = (
            '{"status":"cancelled","plan_confirmed":false,"choice_id":"cancel"}'
            if ctx.tool_name == "ask_user_plan_confirm"
            else "executed"
        )
        return ctx

    orchestrator.execute.side_effect = execute
    runtime = ToolRuntime(orchestrator=orchestrator)
    turn = _Turn()

    await runtime.execute_many(turn, [_Call("ask_user_plan_confirm", "plan-1")])
    assert turn._plan_gate_blocked is True

    result = await runtime.execute_many(turn, [_Call("write_file", "write-1")])

    assert result[0].status.value == "denied_by_user"
    assert "重新提交调整后的计划" in result[0].result
    assert orchestrator.execute.await_count == 1


@pytest.mark.asyncio
async def test_newly_confirmed_plan_releases_mutation_gate():
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock()

    async def execute(ctx):
        if ctx.tool_name == "ask_user_plan_confirm":
            ctx.result = '{"status":"confirmed","plan_confirmed":true,"choice_id":"confirm"}'
        else:
            ctx.result = "executed"
        return ctx

    orchestrator.execute.side_effect = execute
    runtime = ToolRuntime(orchestrator=orchestrator)
    turn = _Turn()
    turn._plan_gate_blocked = True

    results = await runtime.execute_many(
        turn,
        [_Call("ask_user_plan_confirm", "plan-2"), _Call("write_file", "write-2")],
    )

    assert turn._plan_gate_blocked is False
    assert results[1].result == "executed"
    assert orchestrator.execute.await_count == 2


@pytest.mark.asyncio
async def test_confirmed_plan_not_asked_twice_in_same_turn():
    """#1093: 计划确认后模型在同一回合又调 ask_user_plan_confirm —— 不应出现第二张
    计划卡（用户实测"两张内容一致的卡"）。重复调用被短路为"已确认"工具结果，
    兄弟调用照常执行。"""
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock()

    async def execute(ctx):
        if ctx.tool_name == "ask_user_plan_confirm":
            ctx.result = '{"status":"confirmed","plan_confirmed":true,"choice_id":"confirm"}'
        else:
            ctx.result = "executed"
        return ctx

    orchestrator.execute.side_effect = execute
    runtime = ToolRuntime(orchestrator=orchestrator)
    turn = _Turn()

    # 第一次：计划卡正常弹出并确认 → _plan_confirm_done=True
    await runtime.execute_many(turn, [_Call("ask_user_plan_confirm", "plan-1")])
    assert turn._plan_confirm_done is True

    # 第二次：模型又调了一次 —— 不再发卡，兄弟写操作不被拦截
    results = await runtime.execute_many(
        turn,
        [_Call("ask_user_plan_confirm", "plan-dup"), _Call("write_file", "write-3")],
    )

    # plan-1 + write-3 共 2 次编排；plan-dup 没有走到编排器（=没有弹卡）
    assert orchestrator.execute.await_count == 2
    plan_ctx = next(r for r in results if r.tool_call_id == "plan-dup")
    payload = json.loads(plan_ctx.result)
    assert payload["status"] == "confirmed"
    assert payload["choice_id"] == "confirm"
    assert results[1].result == "executed"


@pytest.mark.asyncio
async def test_plan_can_be_asked_again_after_rejection():
    """#1093 反向保障：拒绝/调整后 _plan_confirm_done=False —— 重新提交的计划
    仍然要正常弹卡，不能被短路逻辑吞掉。"""
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock()

    async def execute(ctx):
        ctx.result = '{"status":"cancelled","plan_confirmed":false,"choice_id":"cancel"}'
        return ctx

    orchestrator.execute.side_effect = execute
    runtime = ToolRuntime(orchestrator=orchestrator)
    turn = _Turn()

    await runtime.execute_many(turn, [_Call("ask_user_plan_confirm", "plan-1")])
    assert turn._plan_confirm_done is False

    await runtime.execute_many(turn, [_Call("ask_user_plan_confirm", "plan-2")])

    # 第二张卡仍然弹出（两次都经过编排器）
    assert orchestrator.execute.await_count == 2
