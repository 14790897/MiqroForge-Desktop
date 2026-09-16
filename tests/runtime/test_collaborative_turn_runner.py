from types import SimpleNamespace

import pytest

from miqi.runtime.collaborative_turn_runner import CollaborativeTurnRunner
from miqi.runtime.turn_runner import TurnResult


@pytest.mark.asyncio
async def test_adjustment_restarts_planning_with_user_constraint(monkeypatch):
    runner = object.__new__(CollaborativeTurnRunner)
    seen_contents: list[str] = []
    calls = 0

    async def fake_run(self, *, turn, user_content, **kwargs):
        nonlocal calls
        calls += 1
        seen_contents.append(user_content)
        if calls == 1:
            turn._plan_adjustment_pending = "不要上传 MiqroForge，先生成本地报告"
            turn._plan_gate_blocked = True
            return TurnResult(
                final_content="",
                messages=[],
                tools_used=[],
                token_usage={},
                messages_delta=[],
            )
        return TurnResult(
            final_content="已按新计划完成",
            messages=[],
            tools_used=["write_file"],
            token_usage={},
            messages_delta=[{"role": "assistant", "content": "已按新计划完成"}],
        )

    monkeypatch.setattr("miqi.runtime.turn_runner.TurnRunner.run", fake_run)

    turn = SimpleNamespace(
        _plan_adjustment_pending="",
        _plan_gate_blocked=False,
        _plan_confirm_done=False,
        _plan_phases=["READ"],
        _plan_seen_tools=["web_search"],
        _plan_calls=["web_search"],
        _plan_timeline_shown=True,
        _run_ctx=object(),
    )

    result = await runner.run(turn=turn, user_content="制作一份研究报告并上传")

    assert calls == 2
    assert seen_contents[0] == "制作一份研究报告并上传"
    assert "不要上传 MiqroForge，先生成本地报告" in seen_contents[1]
    assert result.final_content == "已按新计划完成"
    assert turn._plan_confirm_done is False
    assert turn._plan_seen_tools == []
    assert turn._plan_calls == []
    assert turn._run_ctx is None


@pytest.mark.asyncio
async def test_adjustment_replans_are_bounded(monkeypatch):
    runner = object.__new__(CollaborativeTurnRunner)
    calls = 0
    seen_contents: list[str] = []

    async def fake_run(self, *, turn, user_content, **kwargs):
        nonlocal calls
        calls += 1
        seen_contents.append(str(user_content))
        turn._plan_adjustment_pending = f"调整 {calls}"
        return TurnResult(
            final_content="",
            messages=[],
            tools_used=[],
            token_usage={},
            messages_delta=[],
        )

    monkeypatch.setattr("miqi.runtime.turn_runner.TurnRunner.run", fake_run)

    turn = SimpleNamespace()
    result = await runner.run(turn=turn, user_content="复杂任务")

    # CR #1071：初始一轮之外，每次被接受的调整都必须真的执行 → 调用数 =
    # _MAX_REPLANS_PER_TURN + 1。旧实现 range(_MAX_REPLANS_PER_TURN) 会在第 5 次
    # 调整时把新约束准备好就耗尽循环——最后一轮永远不发给模型，用户拿空回答。
    assert calls == 6
    # 回归：最后一次被接受的调整（第 5 次）必须真的出现在发给模型的内容里
    assert "调整 5" in seen_contents[-1]
    assert result.final_content == ""
