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


@pytest.mark.asyncio
async def test_plan_request_restarts_round_so_model_authors_the_plan(monkeypatch):
    """#646-v2（2026-09-15 定稿）：闸门不代笔——先请模型给出计划（_plan_request_pending）
    → 追加一轮 provider；模型下一轮 ask_user_plan_confirm 的内容即卡片内容
    （对齐 Claude Code ExitPlanMode：计划由模型提交、审批由用户完成）。"""
    runner = object.__new__(CollaborativeTurnRunner)
    seen_contents: list[str] = []
    calls = 0

    async def fake_run(self, *, turn, user_content, **kwargs):
        nonlocal calls
        calls += 1
        seen_contents.append(user_content)
        if calls == 1:
            # 第一次：基类闸门判定需要计划卡，但模型没给计划 → 请求模型产出计划
            turn._plan_request_pending = "【系统】请先调用 ask_user_plan_confirm 展示你的执行计划"
            return TurnResult(
                final_content="",
                messages=[],
                tools_used=[],
                token_usage={},
                messages_delta=[],
            )
        return TurnResult(
            final_content="已按模型给出的计划完成",
            messages=[],
            tools_used=["web_search"],
            token_usage={},
            messages_delta=[{"role": "assistant", "content": "已按模型给出的计划完成"}],
        )

    monkeypatch.setattr("miqi.runtime.turn_runner.TurnRunner.run", fake_run)

    turn = SimpleNamespace(
        _plan_request_pending="",
        _plan_adjustment_pending="",
        _plan_gate_blocked=False,
        _plan_confirm_done=False,
        _plan_phases=["READ", "WRITE"],
        _plan_seen_tools=["web_search", "write_file"],
        _plan_calls=["web_search", "write_file"],
        _plan_timeline_shown=False,
        _run_ctx=object(),
    )

    result = await runner.run(turn=turn, user_content="生成报告并上传")

    assert calls == 2
    assert seen_contents[0] == "生成报告并上传"
    assert "请先调用 ask_user_plan_confirm" in seen_contents[1]
    assert result.final_content == "已按模型给出的计划完成"
    # 一次性请求：状态被清空，不会跨轮残留
    assert turn._plan_request_pending == ""
    assert turn._run_ctx is None


def test_supports_plan_replan_flag_is_set():
    """基类闸门据此判断走「请模型给计划」路径，而不是用工具标签代笔拼模板。"""
    assert CollaborativeTurnRunner.supports_plan_replan is True
