"""#1071 R1 回归：todo_state 推送时序（末轮 completed 必须上屏）。

缺陷：`_emit_todo_state` 在"工具执行后、observed 完成态 merge 前"调用，于是
最后一轮的 completed/blocked 永远推不出去——前端停在 in_progress。

本文件驱动**真实** TurnRunner.run 走完多轮工具循环，把
  - 事件通道（ToolCallEndEvent，EventEmitter）
  - 前端通道（display=todo_state，user_input emitter）
合并进同一条有序日志，从而既能断言"最后一轮是 completed"，也能断言
"emit 发生在完成态 merge 之后"以及"每轮恰好一次 emit（搬移而非双推）"。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.agent.user_input_resolver import (
    clear_thread_session,
    set_thread_session,
    set_user_input_emitter,
)
from miqi.execution.orchestrator import OrchestrationResult
from miqi.providers.base import LLMResponse, LLMStreamEvent
from miqi.runtime.context_runtime import ContextRuntime
from miqi.runtime.task_objects import AgentRunContext
from miqi.runtime.turn_runner import TurnRunner

_THREAD_ID = "thread-todo-1071"
_SESSION_KEY = "sess-todo-1071"


class _FakeToolCall:
    def __init__(self, name: str, args: dict, tc_id: str):
        self.name = name
        self.arguments = args
        self.id = tc_id
        self.arguments_json = json.dumps(args)


class _FakeCtx:
    """Mirror orchestrator output for a successful tool call."""

    def __init__(self, tc: _FakeToolCall):
        self.tool_call_id = tc.id
        self.result = f"result-for-{tc.name}"
        self.status = OrchestrationResult.SUCCESS
        self.duration_ms = 1


class _Harness:
    """Real TurnRunner + ordered log spanning both event channels."""

    def __init__(self, rounds: list[list[_FakeToolCall] | None]):
        # rounds[i] = tool calls for round i, or None for a final text round
        self.timeline: list[str] = []
        self.todo_states: list[dict] = []
        self._rounds = list(rounds)
        self._round_idx = 0

        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        async def _stream_chat(**kwargs):
            calls = self._rounds[self._round_idx]
            self._round_idx += 1
            yield LLMStreamEvent(
                kind="completed",
                response=LLMResponse(
                    content="" if calls else "done after tools",
                    tool_calls=calls or [],
                    finish_reason="tool_calls" if calls else "stop",
                ),
            )

        provider.stream_chat = _stream_chat

        tool_runtime = MagicMock()
        tool_runtime.execute_many = AsyncMock(
            side_effect=lambda turn, calls: [_FakeCtx(c) for c in calls]
        )

        async def _emit(event):
            self.timeline.append(f"event:{type(event).__name__}")

        emitter = MagicMock()
        emitter.emit = _emit

        self.runner = TurnRunner(
            provider=provider,
            tool_runtime=tool_runtime,
            context_runtime=ContextRuntime(),
            event_emitter=emitter,
            max_iterations=10,
            ledger_runtime=None,
        )

        async def _user_input_emitter(payload):
            if payload.get("display") == "todo_state":
                self.todo_states.append(payload)
                self.timeline.append("todo_state")

        set_thread_session(_THREAD_ID, _SESSION_KEY)
        set_user_input_emitter(_SESSION_KEY, _user_input_emitter)

    def make_turn(self) -> SimpleNamespace:
        run_ctx = AgentRunContext(session_key=_SESSION_KEY)
        run_ctx.todo_state.initialize_from_plan([("step-1", "搜集论文")])
        return SimpleNamespace(
            turn_id="turn-todo-1071",
            thread_id=_THREAD_ID,
            model="test-model",
            temperature=0.0,
            max_tokens=100,
            agent_metadata=SimpleNamespace(name="code-agent"),
            user_content="制作一份研究报告",
            _plan_confirm_done=True,  # 跳过计划卡 gate
            _run_ctx=run_ctx,  # 已确认的计划上下文（emit/merge 的前提）
        )

    async def run(self) -> SimpleNamespace:
        turn = self.make_turn()
        await self.runner.run(
            turn=turn,
            user_content="制作一份研究报告",
            system_prompt="sys",
            tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
        )
        return turn


@pytest.fixture(autouse=True)
def _clean_emitter():
    yield
    clear_thread_session(_THREAD_ID)
    set_user_input_emitter(_SESSION_KEY, None)


def _statuses(payload: dict) -> dict[str, str]:
    return {it["id"]: it["status"] for it in payload["items"]}


@pytest.mark.asyncio
async def test_last_tool_round_pushes_completed_not_in_progress():
    """一轮多工具、且该轮就是最后一轮 → 推给前端的那一帧必须是 completed。

    修前：emit 在完成态 merge 之前，items 停在 in_progress。
    """
    tc1 = _FakeToolCall("read_file", {"path": "/tmp/a"}, "tcid-1")
    tc2 = _FakeToolCall("list_dir", {"path": "/tmp"}, "tcid-2")
    harness = _Harness(rounds=[[tc1, tc2], None])
    turn = await harness.run()

    assert harness.todo_states, "应当推送 todo_state"
    last = harness.todo_states[-1]
    statuses = _statuses(last)

    assert statuses["obs-tcid-1"] == "completed"
    assert statuses["obs-tcid-2"] == "completed"
    # 计划条目本身不受 observed 兜底进度影响
    assert statuses["step-1"] == "queued"
    # 上下文里的真实状态与推给前端的一致（同源，无部分提交）
    assert turn._run_ctx.todo_state.item("obs-tcid-1").status == "completed"


@pytest.mark.asyncio
async def test_emit_happens_after_tool_end_and_completion_merge():
    """事件顺序：ToolCallEnd 之后才推 todo_state（即完成态已写入）。"""
    tc1 = _FakeToolCall("read_file", {"path": "/tmp/a"}, "tcid-1")
    harness = _Harness(rounds=[[tc1], None])
    await harness.run()

    end_idx = [i for i, e in enumerate(harness.timeline) if e == "event:ToolCallEndEvent"]
    state_idx = [i for i, e in enumerate(harness.timeline) if e == "todo_state"]

    assert end_idx, "应当发出 ToolCallEndEvent"
    assert state_idx, "应当推送 todo_state"
    assert state_idx[0] > end_idx[-1], (
        f"todo_state 必须在完成态 merge（紧随 ToolCallEnd）之后推送，实际顺序：{harness.timeline}"
    )


@pytest.mark.asyncio
async def test_one_emit_per_tool_round_no_double_push():
    """每轮恰好一次 emit——搬移而非在原位新增一处。"""
    harness = _Harness(rounds=[
        [_FakeToolCall("read_file", {"path": "/a"}, "tcid-1")],
        [_FakeToolCall("list_dir", {"path": "/b"}, "tcid-2")],
        None,
    ])
    await harness.run()

    assert len(harness.todo_states) == 2, (
        f"2 个工具轮 → 2 次 push，实际 {len(harness.todo_states)} 次"
    )
    # 最后一帧包含全部三方的最终状态（含第二轮追加的 observed）
    last = _statuses(harness.todo_states[-1])
    assert last["obs-tcid-1"] == "completed"
    assert last["obs-tcid-2"] == "completed"
    # revision 单调递增（前端据此丢弃乱序旧帧）
    revisions = [p["revision"] for p in harness.todo_states]
    assert revisions == sorted(revisions) and len(set(revisions)) == len(revisions)


@pytest.mark.asyncio
async def test_failed_tool_round_pushes_blocked():
    """失败工具 → blocked 也必须上屏（同一时序缺陷的镜像面）。"""
    tc1 = _FakeToolCall("read_file", {"path": "/tmp/a"}, "tcid-1")
    harness = _Harness(rounds=[[tc1], None])

    async def _execute_many(turn, calls):
        ctx = _FakeCtx(calls[0])
        ctx.status = OrchestrationResult.TOOL_ERROR
        ctx.result = "boom"
        return [ctx]

    harness.runner._tools.execute_many = AsyncMock(side_effect=_execute_many)
    await harness.run()

    last = _statuses(harness.todo_states[-1])
    assert last["obs-tcid-1"] == "blocked"
