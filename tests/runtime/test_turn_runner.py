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
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self._has_tool_calls = bool(tool_calls)

    @property
    def has_tool_calls(self):
        return self._has_tool_calls


class _FakeToolCall:
    def __init__(self, name="read_file", args=None, tc_id="tc-1"):
        self.name = name
        self.arguments = args or {"path": "/tmp/x"}
        self.id = tc_id
        self.arguments_json = '{"path": "/tmp/x"}'


@pytest.fixture
def fake_turn_context():
    return _FakeTurnContext()


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
