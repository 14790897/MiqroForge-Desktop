"""Tests for TurnRunner (Phase 12.3)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.runtime.turn_runner import TurnRunner


class _FakeTurnContext:
    turn_id = "turn-1"
    thread_id = "thread-1"
    model = "test-model"
    temperature = 0.0
    max_tokens = 100

    class _Meta:
        name = "code-agent"
    agent_metadata = _Meta()


class _FakeResponse:
    def __init__(self, content="", tool_calls=None, finish_reason=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self._has_tool_calls = bool(tool_calls)
        # #1094: 默认 None == 旧行为（getattr 取不到值），需要时显式给 "length"。
        self.finish_reason = finish_reason

    @property
    def has_tool_calls(self):
        return self._has_tool_calls


class _FakeToolCall:
    def __init__(self, name="read_file", args=None, tc_id="tc-1", truncated=False):
        self.name = name
        self.arguments = args or {"path": "/tmp/x"}
        self.id = tc_id
        self.arguments_json = '{"path": "/tmp/x"}'
        # #1094: provider marks calls cut off by the output cap.
        self.truncated = truncated


@pytest.fixture
def fake_turn_context():
    return _FakeTurnContext()


@pytest.fixture
def fake_fast_turn_context():
    """#680 极速模式 turn：_rmode == "fast" 时才启用 FAST 预算门。"""
    turn = _FakeTurnContext()
    turn.reasoning_mode = "fast"
    return turn


@pytest.fixture
def fake_tool_runtime():
    runtime = MagicMock()
    runtime.execute_many = AsyncMock()

    class _Ctx:
        def __init__(self, tc):
            self.tool_call_id = tc.id
            self.result = f"result-for-{tc.name}"
            # Mirror real orchestrator output: a successful tool call.
            from miqi.execution.orchestrator import OrchestrationResult
            self.status = OrchestrationResult.SUCCESS

    async def _fake_execute_many(turn, calls):
        return [_Ctx(c) for c in calls]

    runtime.execute_many.side_effect = _fake_execute_many
    return runtime


@pytest.fixture
def fake_context_runtime():
    from miqi.runtime.context_runtime import ContextRuntime
    return ContextRuntime()


@pytest.fixture
def turn_runner(fake_tool_runtime, fake_context_runtime):
    from miqi.providers.base import LLMStreamEvent

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    # Phase 20: TurnRunner uses stream_chat() — mock it as an async generator.
    async def _default_stream(**kwargs):
        yield LLMStreamEvent(
            kind="completed",
            response=_FakeResponse(content="final answer"),
        )

    provider.stream_chat = _default_stream
    ev = MagicMock()
    ev.emit = AsyncMock()
    return TurnRunner(
        provider=provider,
        tool_runtime=fake_tool_runtime,
        context_runtime=fake_context_runtime,
        event_emitter=ev,
        max_iterations=3,
    ), provider


# ── Tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_response_gets_nudged_and_continues(
    turn_runner, fake_turn_context,
):
    """A round that yields only reasoning (empty content, no tool calls)
    must not end the turn with a blank reply — the model is nudged to
    continue and the next round's answer becomes the final response."""
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner
    calls = []

    class _EmptyOnlyReasoning(_FakeResponse):
        def __init__(self):
            super().__init__(content=None)
            self.reasoning_content = "long thinking only"

    async def _stream(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            yield LLMStreamEvent(kind="completed", response=_EmptyOnlyReasoning())
        else:
            yield LLMStreamEvent(kind="completed", response=_FakeResponse(content="final answer"))

    provider.stream_chat = _stream

    result = await runner.run(
        turn=fake_turn_context,
        user_content="hello",
        system_prompt="system",
        tools=[],
    )

    assert len(calls) == 2, "empty-response round must be followed by a nudge round"
    assert result.final_content == "final answer"


@pytest.mark.asyncio
async def test_empty_response_nudges_are_bounded(
    turn_runner, fake_turn_context,
):
    """Repeated empty-only-reasoning rounds must eventually fail loudly
    (ProviderError) instead of looping forever."""
    import pytest as _pytest

    from miqi.providers.base import LLMStreamEvent
    from miqi.providers.resilience import ProviderError

    runner, provider = turn_runner

    class _EmptyOnlyReasoning(_FakeResponse):
        def __init__(self):
            super().__init__(content=None)
            self.reasoning_content = "long thinking only"

    async def _stream(**kwargs):
        yield LLMStreamEvent(kind="completed", response=_EmptyOnlyReasoning())

    provider.stream_chat = _stream

    with _pytest.raises(ProviderError):
        await runner.run(
            turn=fake_turn_context,
            user_content="hello",
            system_prompt="system",
            tools=[],
        )


@pytest.mark.asyncio
async def test_turn_runner_returns_final_response(turn_runner, fake_turn_context):
    from unittest.mock import AsyncMock

    runner, provider = turn_runner

    # Phase 20: TurnRunner must use stream_chat() — a direct chat() call
    # fails the test loudly instead of being silently tolerated.
    provider.chat = AsyncMock(
        side_effect=AssertionError("TurnRunner must use stream_chat, not chat()"),
    )

    result = await runner.run(
        turn=fake_turn_context,
        user_content="hello",
        system_prompt="system",
        tools=[],
    )

    assert result.final_content == "final answer"
    assert result.messages[-1]["role"] == "assistant"
    provider.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_turn_runner_aborts_mid_stream(turn_runner, fake_turn_context):
    """Abort must stop generation WHILE the stream is flowing, not only at the
    next iteration boundary — a single-shot reply is one iteration, so the
    iteration-start check alone would let the old turn stream to completion
    after an interrupt (#542)."""
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner
    cancel_event = asyncio.Event()

    async def _stream(**kwargs):
        yield LLMStreamEvent(kind="content_delta", delta="chunk-0")
        cancel_event.set()  # abort fires between stream events
        yield LLMStreamEvent(kind="content_delta", delta="chunk-1")
        yield LLMStreamEvent(kind="completed", response=_FakeResponse(content="done"))

    provider.stream_chat = _stream

    with pytest.raises(asyncio.CancelledError):
        await runner.run(
            turn=fake_turn_context,
            user_content="hello",
            system_prompt="system",
            tools=[],
            cancel_event=cancel_event,
        )


@pytest.mark.asyncio
async def test_turn_runner_handles_tool_calls(turn_runner, fake_turn_context, fake_tool_runtime):
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner

    # First response: tool calls
    tc = _FakeToolCall("read_file")

    # Phase 20: stream_chat with side_effect
    call_count = 0

    async def _stream_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield LLMStreamEvent(
                kind="completed",
                response=_FakeResponse(tool_calls=[tc]),
            )
        else:
            yield LLMStreamEvent(
                kind="completed",
                response=_FakeResponse(content="done after tools"),
            )

    provider.stream_chat = _stream_side_effect

    result = await runner.run(
        turn=fake_turn_context,
        user_content="task",
        system_prompt="sys",
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
    )

    assert result.final_content == "done after tools"
    assert "read_file" in result.tools_used
    assert call_count == 2  # stream_chat was called twice
    fake_tool_runtime.execute_many.assert_awaited_once()


@pytest.mark.asyncio
async def test_turn_runner_refuses_truncated_tool_call(
    turn_runner, fake_turn_context, fake_tool_runtime
):
    """#1094：被输出上限截断（truncated=True）的调用不得执行，且模型侧要收到
    「未执行」的显式结果（与 assistant tool_call 成对，不会被 presend 清成孤儿）。"""
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner
    tc = _FakeToolCall("read_file", tc_id="tc-trunc", truncated=True)
    call_count = 0
    seen_messages: list[list[dict]] = []

    async def _stream_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        seen_messages.append(kwargs.get("messages") or [])
        if call_count == 1:
            yield LLMStreamEvent(
                kind="completed",
                response=_FakeResponse(tool_calls=[tc]),
            )
        else:
            yield LLMStreamEvent(
                kind="completed",
                response=_FakeResponse(content="recovered"),
            )

    provider.stream_chat = _stream_side_effect

    result = await runner.run(
        turn=fake_turn_context,
        user_content="task",
        system_prompt="sys",
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
    )

    assert result.final_content == "recovered"
    # 未被当作"用过的工具"
    assert "read_file" not in result.tools_used
    # 工具从未被真实执行（execute_many 至多收到空列表）
    for c in fake_tool_runtime.execute_many.await_args_list:
        calls_arg = c.args[1] if len(c.args) > 1 else c.kwargs.get("calls")
        assert calls_arg == []

    # 第二轮发给模型的消息里带着"未执行"的拒绝理由
    assert call_count == 2
    second_msgs = seen_messages[1]
    tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
    assert tool_msgs
    assert any("未执行" in (m.get("content") or "") for m in tool_msgs)
    # 拒绝结果与 assistant tool_call 严格配对（#753 同类：不留孤儿 tool）
    declared_ids = {
        tc_entry["id"]
        for m in second_msgs
        if m.get("role") == "assistant"
        for tc_entry in (m.get("tool_calls") or [])
    }
    assert all(m.get("tool_call_id") in declared_ids for m in tool_msgs)


@pytest.mark.asyncio
async def test_turn_runner_still_runs_non_truncated_tool_call(
    turn_runner, fake_turn_context, fake_tool_runtime
):
    """对照组：truncated=False 的调用照常执行。"""
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner
    tc = _FakeToolCall("read_file", tc_id="tc-ok", truncated=False)
    call_count = 0

    async def _stream_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield LLMStreamEvent(kind="completed", response=_FakeResponse(tool_calls=[tc]))
        else:
            yield LLMStreamEvent(kind="completed", response=_FakeResponse(content="done"))

    provider.stream_chat = _stream_side_effect

    result = await runner.run(
        turn=fake_turn_context,
        user_content="task",
        system_prompt="sys",
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
    )

    assert "read_file" in result.tools_used
    executed = [
        c.args[1] if len(c.args) > 1 else c.kwargs.get("calls")
        for c in fake_tool_runtime.execute_many.await_args_list
    ]
    assert any(calls for calls in executed)


@pytest.mark.asyncio
async def test_turn_runner_mixed_round_runs_only_intact_call(
    turn_runner, fake_turn_context, fake_tool_runtime
):
    """#1094 审计 F6：单轮多枚混合（1 枚截断 + 1 枚正常）互不牵连。

    正常的照常执行，截断的拒执；两者都要成对回注给模型；tools_used 只记正常的。
    """
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner
    bad = _FakeToolCall("write_file", tc_id="tc-trunc", truncated=True)
    good = _FakeToolCall("read_file", tc_id="tc-ok", truncated=False)
    call_count = 0
    seen_messages: list[list[dict]] = []

    async def _stream_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        seen_messages.append(kwargs.get("messages") or [])
        if call_count == 1:
            yield LLMStreamEvent(
                kind="completed",
                response=_FakeResponse(tool_calls=[bad, good]),
            )
        else:
            yield LLMStreamEvent(kind="completed", response=_FakeResponse(content="done"))

    provider.stream_chat = _stream_side_effect

    result = await runner.run(
        turn=fake_turn_context,
        user_content="task",
        system_prompt="sys",
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
    )

    # 只有完好的那枚被真实下发执行
    executed = [
        c.args[1] if len(c.args) > 1 else c.kwargs.get("calls")
        for c in fake_tool_runtime.execute_many.await_args_list
    ]
    executed_ids = [tc.id for calls in executed for tc in calls]
    assert executed_ids == ["tc-ok"]

    # tools_used 只含正常那枚
    assert result.tools_used == ["read_file"]

    # 成对回注：截断枚拿到拒绝理由，正常枚拿到执行结果，且都在 assistant 里声明过
    assert call_count == 2
    second_msgs = seen_messages[1]
    declared_ids = {
        entry["id"]
        for m in second_msgs
        if m.get("role") == "assistant"
        for entry in (m.get("tool_calls") or [])
    }
    tool_msgs = {m.get("tool_call_id"): (m.get("content") or "")
                 for m in second_msgs if m.get("role") == "tool"}
    assert set(tool_msgs) == {"tc-trunc", "tc-ok"}
    assert set(tool_msgs) <= declared_ids  # 不留孤儿 tool 消息
    assert "未执行" in tool_msgs["tc-trunc"]
    # 拒执文案带真实 max_tokens 数值
    assert f"max_tokens={fake_turn_context.max_tokens}" in tool_msgs["tc-trunc"]
    assert "result-for-read_file" in tool_msgs["tc-ok"]


@pytest.mark.asyncio
async def test_turn_runner_fast_budget_does_not_swallow_truncated_call(
    turn_runner, fake_fast_turn_context, fake_tool_runtime
):
    """CR #1100：fast 模式下截断门必须**先于** FAST 预算门。

    反例（修复前两门顺序颠倒）：同一轮里预算已用尽的 web_search 若同时 truncated，
    会先被预算门吃掉 → 拿到 SUCCESS + "[跳过]"，而预算跳过不走 _echo_calls →
    这条 tool_result 成孤儿被 presend 剪掉：模型既学不到"参数被截断"，也不知该重发。
    修复后：先对全量调用分拣 truncated（拒执 + 成对回注），预算门只处理剩下的完好调用。
    """
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner
    # 迭代1：完好的 web_search 放行，用掉本轮唯一的搜索相位额度（_search_phases=1）
    intact = _FakeToolCall("web_search", tc_id="tc-search-1", truncated=False)
    # 迭代2：同为 web_search，但参数被输出上限截断 → 必须被截断门拒执，而不是被预算门跳过
    truncated = _FakeToolCall("web_search", tc_id="tc-search-trunc", truncated=True)
    call_count = 0
    seen_messages: list[list[dict]] = []

    async def _stream_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        seen_messages.append(kwargs.get("messages") or [])
        if call_count == 1:
            yield LLMStreamEvent(kind="completed", response=_FakeResponse(tool_calls=[intact]))
        elif call_count == 2:
            yield LLMStreamEvent(kind="completed", response=_FakeResponse(tool_calls=[truncated]))
        else:
            yield LLMStreamEvent(kind="completed", response=_FakeResponse(content="recovered"))

    provider.stream_chat = _stream_side_effect

    result = await runner.run(
        turn=fake_fast_turn_context,
        user_content="task",
        system_prompt="sys",
        tools=[{"type": "function", "function": {"name": "web_search", "parameters": {}}}],
    )

    assert result.final_content == "recovered"
    assert call_count == 3

    # c) 截断那枚从未真实下发执行；tools_used 只记迭代1 那枚完好的
    for c in fake_tool_runtime.execute_many.await_args_list:
        calls_arg = c.args[1] if len(c.args) > 1 else c.kwargs.get("calls")
        assert truncated.id not in [tc.id for tc in calls_arg]
    assert result.tools_used == ["web_search"]

    # 落盘证据（messages_delta 不会被 presend 剪裁）：拒执文案带真实 max_tokens 数值。
    # 修复前这里拿到的是 "[跳过] 极速模式搜索预算已用尽（最多一轮搜索）"。
    delta_tools = {
        m["tool_call_id"]: (m.get("content") or "")
        for m in result.messages_delta
        if m.get("role") == "tool"
    }
    assert "未执行" in delta_tools.get(truncated.id, ""), (
        f"截断调用被 FAST 预算门吞掉了：{delta_tools.get(truncated.id)!r}"
    )

    # a) 拒执后发给模型的上下文里，该调用是"未执行/截断"拒执文案，而不是"[跳过]"
    third_msgs = seen_messages[2]
    tool_msgs = {
        m.get("tool_call_id"): (m.get("content") or "")
        for m in third_msgs
        if m.get("role") == "tool"
    }
    assert truncated.id in tool_msgs  # 没被 presend 当孤儿 tool 剪掉
    assert "未执行" in tool_msgs[truncated.id]
    assert "max_tokens" in tool_msgs[truncated.id]
    assert "[跳过]" not in tool_msgs[truncated.id]

    # b) 拒执结果与 assistant tool_call 严格配对（#753 同类：不留孤儿）
    declared_ids = {
        entry["id"]
        for m in third_msgs
        if m.get("role") == "assistant"
        for entry in (m.get("tool_calls") or [])
    }
    assert truncated.id in declared_ids


@pytest.mark.asyncio
async def test_turn_runner_logs_plain_text_truncation(
    turn_runner, fake_turn_context
):
    """#1094 审计 F3：纯文本被 length 截断时零留痕 → 现在必须有 warning。"""
    from loguru import logger as loguru_logger

    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner
    seen_messages: list[list[dict]] = []
    call_count = 0

    async def _stream_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        seen_messages.append(kwargs.get("messages") or [])
        if call_count == 1:
            yield LLMStreamEvent(
                kind="completed",
                response=_FakeResponse(content="这是被砍断的半句", finish_reason="length"),
            )
        else:
            yield LLMStreamEvent(kind="completed", response=_FakeResponse(content="final"))

    provider.stream_chat = _stream_side_effect

    records: list[str] = []
    sink_id = loguru_logger.add(lambda msg: records.append(str(msg)), level="WARNING")
    try:
        await runner.run(
            turn=fake_turn_context,
            user_content="task",
            system_prompt="sys",
            tools=[],
        )
    finally:
        loguru_logger.remove(sink_id)

    hits = [r for r in records if "max_tokens 截断" in r]
    assert hits, records
    assert str(fake_turn_context.max_tokens) in hits[0]


@pytest.mark.asyncio
async def test_turn_runner_passes_reasoning_content_through(turn_runner, fake_turn_context):
    """reasoning_delta + response.reasoning_content reach the result (Issue #539)."""
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner

    async def _stream_with_reasoning(**kwargs):
        yield LLMStreamEvent(kind="reasoning_delta", delta="step 1 ")
        yield LLMStreamEvent(kind="reasoning_delta", delta="step 2")
        resp = _FakeResponse(content="answer")
        resp.reasoning_content = "step 1 step 2 (full)"
        yield LLMStreamEvent(kind="completed", response=resp)

    provider.stream_chat = _stream_with_reasoning

    result = await runner.run(
        turn=fake_turn_context,
        user_content="hello",
        system_prompt="system",
        tools=[],
    )

    # Reasoning is surfaced on the result for the UI.
    # The completed response's value takes priority over streamed deltas.
    assert result.reasoning == "step 1 step 2 (full)"
    # And persisted into the message delta for JSONL storage.
    asst_deltas = [m for m in result.messages_delta if m.get("role") == "assistant"]
    assert asst_deltas and asst_deltas[-1]["reasoning_content"] == "step 1 step 2 (full)"
    # Visible content stays clean — reasoning is a separate field.
    assert result.final_content == "answer"


@pytest.mark.asyncio
async def test_turn_runner_emits_tool_call_lifecycle_events(
    turn_runner, fake_turn_context, fake_tool_runtime
):
    from miqi.protocol.events import ToolCallBeginEvent, ToolCallEndEvent
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner
    emitted = []
    runner._events.emit.side_effect = lambda event: emitted.append(event)

    tc = _FakeToolCall("write_file", {"path": "/tmp/asset.txt"}, "tc-write")
    tc.arguments_json = '{"path": "/tmp/asset.txt"}'

    async def _execute_many(turn, calls):
        emitted.append("execute_many")

        class _Ctx:
            tool_call_id = "tc-write"
            result = "created"
            duration_ms = 12
            # Mirror real orchestrator output: write_file succeeded.
            from miqi.execution.orchestrator import OrchestrationResult
            status = OrchestrationResult.SUCCESS

        return [_Ctx()]

    fake_tool_runtime.execute_many.side_effect = _execute_many

    call_count = 0

    async def _stream_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield LLMStreamEvent(
                kind="completed",
                response=_FakeResponse(tool_calls=[tc]),
            )
        else:
            yield LLMStreamEvent(
                kind="completed",
                response=_FakeResponse(content="done after tools"),
            )

    provider.stream_chat = _stream_side_effect

    result = await runner.run(
        turn=fake_turn_context,
        user_content="create a file",
        system_prompt="sys",
        tools=[{"type": "function", "function": {"name": "write_file", "parameters": {}}}],
    )

    assert result.final_content == "done after tools"
    assert [type(event).__name__ if event != "execute_many" else event for event in emitted] == [
        "ToolCallBeginEvent",
        "execute_many",
        "ToolCallEndEvent",
    ]
    begin = emitted[0]
    end = emitted[2]
    assert isinstance(begin, ToolCallBeginEvent)
    assert begin.tool_name == "write_file"
    assert begin.tool_call_id == "tc-write"
    assert begin.arguments == {"path": "/tmp/asset.txt"}
    assert begin.tool_display == 'write_file("/tmp/asset.txt")'
    assert isinstance(end, ToolCallEndEvent)
    assert end.tool_name == "write_file"
    assert end.success is True
    assert end.output_preview == "created"
    assert end.output_size == len("created")
    assert end.duration_ms == 12


@pytest.mark.asyncio
async def test_turn_runner_exhausts_iterations(turn_runner, fake_turn_context):
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner

    # Always return tool calls — forces exhaustion
    async def _always_tool_calls(**kwargs):
        yield LLMStreamEvent(
            kind="completed",
            response=_FakeResponse(tool_calls=[_FakeToolCall()]),
        )

    provider.stream_chat = _always_tool_calls
    runner._max_iterations = 2  # Small cap for fast test

    result = await runner.run(
        turn=fake_turn_context,
        user_content="endless task",
        system_prompt="sys",
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
    )

    assert "已达到最大迭代次数" in result.final_content


@pytest.mark.asyncio
async def test_turn_runner_exhaustion_diagnosis_structured_failure(
    turn_runner, fake_turn_context, fake_tool_runtime
):
    """Issue #491: exhausted turns surface structured tool failure signals.

    A paper_download failure payload (HTTP 403 + paywall flag) must be
    reflected in the final message instead of a bare generic hint.
    """
    import json

    from miqi.execution.orchestrator import OrchestrationResult
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner
    runner._max_iterations = 2

    async def _failing_execute_many(turn, calls):
        class _Ctx:
            def __init__(self, tc):
                self.tool_call_id = tc.id
                self.result = json.dumps(
                    {
                        "ok": False,
                        "error": "Download failed with HTTP 403",
                        "status_code": 403,
                        "paywall_suspected": True,
                        "signals": ["purchase"],
                    },
                    ensure_ascii=False,
                )
                self.status = OrchestrationResult.SUCCESS
        return [_Ctx(c) for c in calls]

    fake_tool_runtime.execute_many.side_effect = _failing_execute_many

    async def _always_tool_calls(**kwargs):
        yield LLMStreamEvent(
            kind="completed",
            response=_FakeResponse(
                tool_calls=[_FakeToolCall(name="paper_download", args={"paperId": "x"})]
            ),
        )

    provider.stream_chat = _always_tool_calls

    result = await runner.run(
        turn=fake_turn_context,
        user_content="download paper",
        system_prompt="sys",
        tools=[{"type": "function", "function": {"name": "paper_download", "parameters": {}}}],
    )

    assert "已达到最大迭代次数" in result.final_content
    assert "【失败诊断】" in result.final_content
    assert "paper_download" in result.final_content
    assert "HTTP 403" in result.final_content
    assert "付费墙" in result.final_content


@pytest.mark.asyncio
async def test_turn_runner_exhaustion_diagnosis_plain_text_signals(
    turn_runner, fake_turn_context, fake_tool_runtime
):
    """Plain-text tool outputs (web_search) also produce diagnosis signals.

    Also verifies per-tool usage counts are reported.
    """
    from miqi.execution.orchestrator import OrchestrationResult
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner
    runner._max_iterations = 3

    results = iter([
        "No results for: campusconnect pdf",
        "No results for: campusconnect pdf",
        "Error: web search failed: rate limited",
    ])

    async def _failing_execute_many(turn, calls):
        class _Ctx:
            def __init__(self, tc):
                self.tool_call_id = tc.id
                self.result = next(results)
                self.status = OrchestrationResult.SUCCESS
        return [_Ctx(c) for c in calls]

    fake_tool_runtime.execute_many.side_effect = _failing_execute_many

    async def _always_tool_calls(**kwargs):
        yield LLMStreamEvent(
            kind="completed",
            response=_FakeResponse(
                tool_calls=[_FakeToolCall(name="web_search", args={"query": "x"})]
            ),
        )

    provider.stream_chat = _always_tool_calls

    result = await runner.run(
        turn=fake_turn_context,
        user_content="find paper",
        system_prompt="sys",
        tools=[{"type": "function", "function": {"name": "web_search", "parameters": {}}}],
    )

    assert "已达到最大迭代次数" in result.final_content
    assert "web_search×3" in result.final_content
    assert "未找到结果" in result.final_content
    assert "rate limited" in result.final_content


@pytest.mark.asyncio
async def test_turn_runner_tool_call_message_ordering(turn_runner, fake_turn_context):
    """TurnRunner must produce user → assistant(tool_calls) → tool → assistant."""
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner

    tc1 = _FakeToolCall("read_file", {"path": "/tmp/a"}, "tcid-1")
    tc2 = _FakeToolCall("list_dir", {"path": "/tmp"}, "tcid-2")

    # Track the second stream_chat call's messages to verify ordering
    captured_messages: list = []
    call_count = 0

    async def _stream_smart(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield LLMStreamEvent(
                kind="completed",
                response=_FakeResponse(tool_calls=[tc1, tc2]),
            )
        else:
            captured_messages.extend(kwargs["messages"])
            yield LLMStreamEvent(
                kind="completed",
                response=_FakeResponse(content="done after tools"),
            )

    provider.stream_chat = _stream_smart

    result = await runner.run(
        turn=fake_turn_context,
        user_content="task",
        system_prompt="sys",
        tools=[
            {"type": "function", "function": {"name": "read_file", "parameters": {}}},
            {"type": "function", "function": {"name": "list_dir", "parameters": {}}},
        ],
    )

    assert result.final_content == "done after tools"

    # The second provider call should receive: user → assistant(tool_calls) → tool → tool
    roles = [m["role"] for m in captured_messages]
    assert roles == ["system", "user", "assistant", "tool", "tool"], (
        f"Bad tool-call ordering: {roles}"
    )

    # Assistant message must have tool_calls and appear before tool results
    asst_idx = roles.index("assistant")
    tool_indices = [i for i, r in enumerate(roles) if r == "tool"]
    assert asst_idx < tool_indices[0], "assistant(tool_calls) must precede tool results"

    # Tool call IDs must match
    tool_call_ids = [m["tool_call_id"] for m in captured_messages if m["role"] == "tool"]
    assert tool_call_ids == ["tcid-1", "tcid-2"], f"tool_call_ids out of order: {tool_call_ids}"


# ── Phase 20: streaming turn provider ────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_runner_emits_content_deltas():
    """TurnRunner must emit AgentMessageDeltaEvent when the provider
    yields content_delta stream events, then return the final content."""
    from miqi.providers.base import LLMResponse, LLMStreamEvent
    from miqi.runtime.turn_runner import TurnRunner

    class StreamingProvider:
        async def stream_chat(self, **kwargs):
            yield LLMStreamEvent(kind="content_delta", delta="hel")
            yield LLMStreamEvent(kind="content_delta", delta="lo")
            yield LLMStreamEvent(
                kind="completed",
                response=LLMResponse(content="hello", finish_reason="stop"),
            )

    class FakeContext:
        def build_initial_messages(self, **kwargs):
            return [{"role": "user", "content": kwargs["user_content"]}]

        def add_assistant_message(self, *, messages, content, tool_calls=None, reasoning_content=None):
            item = {"role": "assistant", "content": content}
            if tool_calls:
                item["tool_calls"] = tool_calls
            if reasoning_content:
                item["reasoning_content"] = reasoning_content
            return [*messages, item]

        def trim_for_model(self, messages, model):
            return messages

    class EventCollector:
        def __init__(self):
            self.events: list = []

        async def emit(self, event):
            self.events.append(event)

    events = EventCollector()
    runner = TurnRunner(
        provider=StreamingProvider(),
        tool_runtime=MagicMock(),
        context_runtime=FakeContext(),
        event_emitter=events,
        max_iterations=3,
    )
    turn = MagicMock()
    turn.turn_id = "turn-1"
    turn.model = "test-model"
    turn.temperature = 0.1
    turn.max_tokens = 100

    result = await runner.run(
        turn=turn,
        user_content="hi",
        system_prompt="system",
        tools=[],
    )

    assert result.final_content == "hello"

    from miqi.protocol.events import AgentMessageDeltaEvent
    deltas = [e for e in events.events if isinstance(e, AgentMessageDeltaEvent)]
    assert [e.delta for e in deltas] == ["hel", "lo"]
    assert [e.index for e in deltas] == [0, 1]


# ---------------------------------------------------------------------------
# Phase 41: Steering queue consumption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_runner_consumes_steer_queue_before_completing_final_response():
    import asyncio as _asyncio

    from miqi.providers.base import LLMResponse, LLMStreamEvent
    from miqi.runtime.turn_runner import TurnRunner

    class FakeProvider:
        def __init__(self):
            self.calls = 0

        async def stream_chat(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield LLMStreamEvent(kind="completed", response=LLMResponse(
                    content="first",
                    finish_reason="stop",
                ))
            else:
                yield LLMStreamEvent(kind="completed", response=LLMResponse(
                    content="second",
                    finish_reason="stop",
                ))

    class FakeContext:
        def build_initial_messages(self, **kwargs):
            return [{"role": "user", "content": kwargs["user_content"]}]

        def add_assistant_message(self, messages, content, tool_calls=None, reasoning_content=None):
            item = {"role": "assistant", "content": content}
            if tool_calls:
                item["tool_calls"] = tool_calls
            if reasoning_content:
                item["reasoning_content"] = reasoning_content
            return [*messages, item]

        def add_tool_result(self, messages, tool_call_id, name, content):
            return [*messages, {"role": "tool", "content": content}]

        def trim_for_model(self, messages, model):
            return messages

    class FakeTools:
        async def execute_many(self, turn, tool_calls):
            return []

    class FakeEvents:
        async def emit(self, event):
            pass

    turn = type("Turn", (), {})()
    turn.turn_id = "turn-steer"
    turn.thread_id = "thread-steer"
    turn.model = "test-model"
    turn.temperature = 0
    turn.max_tokens = 100

    steer_queue = _asyncio.Queue()
    await steer_queue.put({
        "content": "steer me",
        "input_items": [{"type": "text", "text": "steer me"}],
        "client_user_message_id": "client-steer",
    })

    runner = TurnRunner(
        provider=FakeProvider(),
        tool_runtime=FakeTools(),
        context_runtime=FakeContext(),
        event_emitter=FakeEvents(),
        max_iterations=3,
    )

    result = await runner.run(
        turn=turn,
        user_content="hello",
        system_prompt="system",
        tools=[],
        history=[],
        steer_queue=steer_queue,
    )

    assert result.final_content == "second"
    steer_delta = next(
        d for d in result.messages_delta
        if d.get("role") == "user" and d.get("content") == "steer me"
    )
    assert steer_delta is not None
    assert steer_delta["client_user_message_id"] == "client-steer"


@pytest.mark.parametrize(
    "name,args,expected",
    [
        # Path-like args keep showing the target value (existing behavior).
        ("write_file", {"path": "/tmp/asset.txt"}, 'write_file("/tmp/asset.txt")'),
        ("write_file", {"file_path": "/tmp/x"}, 'write_file("/tmp/x")'),
        ("exec", {"command": "npm test"}, 'exec("npm test")'),
        # Long values are truncated, not dumped in full.
        (
            "write_file",
            {"path": "/very/long/path/" + "a" * 60},
            f'write_file("/very/long/path/{"a" * 34}…")',
        ),
        # Non-path args show only the parameter name — values like paper
        # titles or URLs are long strings that would leak into the hint.
        # (issue #532)
        (
            "paper_download",
            {"paperId": "An Image is Worth 16x16 Words"},
            "paper_download(paperId=…)",
        ),
        ("paper_download", {"url": "https://example.com/paper.pdf"}, "paper_download(url=…)"),
        ("web_fetch", {"url": "https://example.com/page"}, "web_fetch(url=…)"),
        # Empty / non-string args fall back to the bare tool name.
        ("paper_download", {}, "paper_download"),
        ("paper_download", {"overwrite": True}, "paper_download(overwrite=…)"),
    ],
)
def test_format_tool_hint(name, args, expected):
    from miqi.runtime.turn_runner import TurnRunner

    assert TurnRunner._format_tool_hint(name, args) == expected


def test_format_tool_hint_duplicate_matches_agent_control():
    """The two copies of _format_tool_hint must stay in sync."""
    from miqi.runtime.agent_control import AgentControl
    from miqi.runtime.turn_runner import TurnRunner

    samples = [
        ("paper_download", {"paperId": "An Image is Worth 16x16 Words"}),
        ("write_file", {"path": "/tmp/asset.txt"}),
        ("write_file", {"path": "/very/long/path/" + "b" * 60}),
        ("exec", {"command": "npm test"}),
        ("web_search", {"query": "what is the meaning of life"}),
        ("paper_download", {}),
        ("memory", {"action": "remember", "target": "x" * 80}),
    ]
    for name, args in samples:
        assert TurnRunner._format_tool_hint(name, args) == AgentControl._format_tool_hint(name, args)


# ── Tool-call text leak feedback (issue #532) ──────────────────────────

LEAK_CONTENT = 'functions.paper_download(paperId="An Image is Worth 16x16 Words")'


@pytest.mark.asyncio
async def test_turn_runner_feeds_tool_text_leak_back_to_model(
    turn_runner, fake_turn_context
):
    """A leaked text-form tool call is fed back to the model for a retry,
    and never surfaces in the final content as an internal placeholder."""
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner
    call_count = 0
    second_messages: list[dict] = []

    async def _stream_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield LLMStreamEvent(
                kind="completed",
                response=_FakeResponse(content=LEAK_CONTENT),
            )
            return
        second_messages.extend(kwargs.get("messages", []))
        yield LLMStreamEvent(
            kind="completed",
            response=_FakeResponse(content="论文下载链接：https://example.com/a.pdf"),
        )

    provider.stream_chat = _stream_side_effect

    result = await runner.run(
        turn=fake_turn_context,
        user_content="下载论文",
        system_prompt="sys",
        tools=[{"type": "function", "function": {"name": "paper_download", "parameters": {}}}],
    )

    # The leak triggered a retry iteration, not a placeholder final.
    assert call_count == 2
    assert result.final_content == "论文下载链接：https://example.com/a.pdf"
    assert "检测到未被执行的工具调用" not in result.final_content
    # The feedback reached the model in the second call's context.
    feedback = [m for m in second_messages if m.get("role") == "user" and "工具调用" in m.get("content", "")]
    assert feedback, "feedback message missing from the retry context"
    # The feedback must not be persisted as user content.
    persisted_roles = [m.get("role") for m in result.messages_delta]
    assert persisted_roles.count("user") == 0


@pytest.mark.asyncio
async def test_turn_runner_leak_until_exhaustion_gets_friendly_notice(
    turn_runner, fake_turn_context
):
    """If the model keeps leaking tool calls as text, the exhaustion notice
    mentions it in human-readable form instead of the internal placeholder."""
    from miqi.providers.base import LLMStreamEvent

    runner, provider = turn_runner
    runner._max_iterations = 2

    async def _always_leak(**kwargs):
        yield LLMStreamEvent(
            kind="completed",
            response=_FakeResponse(content=LEAK_CONTENT),
        )

    provider.stream_chat = _always_leak

    result = await runner.run(
        turn=fake_turn_context,
        user_content="下载论文",
        system_prompt="sys",
        tools=[{"type": "function", "function": {"name": "paper_download", "parameters": {}}}],
    )

    assert "已达到最大迭代次数" in result.final_content
    assert "工具调用" in result.final_content
    assert "检测到未被执行的工具调用" not in result.final_content


@pytest.mark.asyncio
async def test_running_flag_covers_lifecycle_hooks_and_resets_on_failure():
    """#789: _running must be set before PROMPT_SUBMIT/TURN_START and released
    even when a hook raises (2026-08-31 review) — otherwise a config save
    during hook execution could swap the provider, and a hook failure would
    leave the guard stuck True forever.
    """
    from unittest.mock import AsyncMock

    class _BoomHooks:
        def __init__(self):
            self.run = AsyncMock(
                side_effect=RuntimeError("hook PROMPT_SUBMIT failed")
            )

    hooks = _BoomHooks()
    provider = MagicMock()
    runner = TurnRunner(
        provider=provider,
        tool_runtime=MagicMock(),
        context_runtime=MagicMock(),
        event_emitter=MagicMock(),
        max_iterations=3,
        hooks=hooks,
    )
    assert runner._running is False
    with pytest.raises(RuntimeError, match="hook PROMPT_SUBMIT"):
        await runner.run(
            turn=_FakeTurnContext(),
            user_content="hi",
            system_prompt="sys",
            tools=[],
        )
    # The guard was raised before the hook ran (config saves during the
    # hook were blocked) and released again on the failure.
    assert runner._running is False


@pytest.mark.asyncio
async def test_running_flag_resets_when_turn_end_hook_raises():
    """_running must clear even when the TURN_END hook raises (2026-09-01 review).

    A stuck _running would make every later config save park its provider
    swap forever — the runner would never adopt a new provider again.
    """
    from unittest.mock import AsyncMock, MagicMock

    from miqi.providers.base import LLMStreamEvent

    hooks = MagicMock()

    async def _hook_run(point, ctx):
        if point.name == "TURN_END":
            raise RuntimeError("TURN_END hook failed")

    hooks.run = AsyncMock(side_effect=_hook_run)

    provider = MagicMock()

    async def _default_stream(**kwargs):
        yield LLMStreamEvent(
            kind="completed", response=_FakeResponse(content="final answer"),
        )

    provider.stream_chat = _default_stream
    ev = MagicMock()
    ev.emit = AsyncMock()
    runner = TurnRunner(
        provider=provider,
        tool_runtime=MagicMock(),
        context_runtime=MagicMock(),
        event_emitter=ev,
        max_iterations=3,
        hooks=hooks,
    )

    with pytest.raises(RuntimeError, match="TURN_END hook failed"):
        await runner.run(
            turn=_FakeTurnContext(),
            user_content="hi",
            system_prompt="sys",
            tools=[],
        )
    # The hook exception still propagates, but the guard is released.
    assert runner._running is False


@pytest.mark.asyncio
async def test_refused_truncated_call_gets_paired_ledger_completion(
    fake_turn_context, fake_tool_runtime, fake_context_runtime,
):
    """CR #1100：被拒执（参数截断）的调用不能只在 ledger 里留 `tool_call_started`。

    Replay 靠 started/completed 配对重建工具行，只写 started 的话该行永远 pending。
    本用例断言拒执调用与正常调用一样拿到 `tool_call_completed`，且 payload 与已执行
    调用**同形**（不新造 item 类型）、拒执语义随 `result` 落库。
    """
    from miqi.providers.base import LLMStreamEvent

    class _FakeLedger:
        def __init__(self):
            self.items: list[dict] = []

        async def append_item(self, *, thread_id, turn_id, item_type, payload):
            self.items.append({
                "thread_id": thread_id,
                "turn_id": turn_id,
                "item_type": item_type,
                "payload": payload,
            })

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    bad = _FakeToolCall("write_file", tc_id="tc-trunc", truncated=True)
    good = _FakeToolCall("read_file", tc_id="tc-ok", truncated=False)
    call_count = 0

    async def _stream(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield LLMStreamEvent(
                kind="completed", response=_FakeResponse(tool_calls=[bad, good]),
            )
        else:
            yield LLMStreamEvent(
                kind="completed", response=_FakeResponse(content="recovered"),
            )

    provider.stream_chat = _stream
    ev = MagicMock()
    ev.emit = AsyncMock()
    ledger = _FakeLedger()

    runner = TurnRunner(
        provider=provider,
        tool_runtime=fake_tool_runtime,
        context_runtime=fake_context_runtime,
        event_emitter=ev,
        max_iterations=3,
        ledger_runtime=ledger,
    )

    result = await runner.run(
        turn=fake_turn_context,
        user_content="task",
        system_prompt="sys",
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
    )

    assert result.final_content == "recovered"
    started = [i for i in ledger.items if i["item_type"] == "tool_call_started"]
    completed = [i for i in ledger.items if i["item_type"] == "tool_call_completed"]
    # 两个调用都成对：started / completed 覆盖同一组 id，无 pending 残留
    ids = {"tc-ok", "tc-trunc"}
    assert {i["payload"]["tool_call_id"] for i in started} == ids
    assert {i["payload"]["tool_call_id"] for i in completed} == ids
    assert len(completed) == len(started) == 2

    by_id = {i["payload"]["tool_call_id"]: i["payload"] for i in completed}
    # 拒执那条与已执行那条 payload 同形（字段集一致，未新造 item 类型）
    assert set(by_id["tc-trunc"]) == set(by_id["tc-ok"])
    # 拒执语义沿用既有 TOOL_ERROR 表达：result 即拒执说明
    assert "未执行" in by_id["tc-trunc"]["result"]
    assert by_id["tc-trunc"]["duration_ms"] == 0
    # 对照组：正常调用照旧记真实结果
    assert by_id["tc-ok"]["result"] == "result-for-read_file"
