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
            turn._plan_adjustment_pending = "不要上传 Qraft，先生成本地报告"
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
    assert "不要上传 Qraft，先生成本地报告" in seen_contents[1]
    assert result.final_content == "已按新计划完成"
    assert turn._plan_confirm_done is False
    assert turn._plan_seen_tools == []
    assert turn._plan_calls == []
    assert turn._run_ctx is None


@pytest.mark.asyncio
async def test_adjustment_replans_are_bounded(monkeypatch):
    runner = object.__new__(CollaborativeTurnRunner)
    calls = 0

    async def fake_run(self, *, turn, user_content, **kwargs):
        nonlocal calls
        calls += 1
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

    assert calls == 5
    assert result.final_content == ""
