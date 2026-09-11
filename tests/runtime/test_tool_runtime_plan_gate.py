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
