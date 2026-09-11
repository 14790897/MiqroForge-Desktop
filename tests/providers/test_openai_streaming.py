"""Tests for OpenAIProvider.stream_chat() fake-client streaming (Phase 20)."""

import pytest

from miqi.providers.base import LLMStreamEvent

# ── Fake OpenAI streaming chunk helpers ──────────────────────────────


class _FakeDelta:
    """Simulates an OpenAI streaming delta with optional content / reasoning."""
    def __init__(self, content="", reasoning_content="", tool_calls=None):
        self.content = content or None
        self.reasoning_content = reasoning_content or None
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, delta, finish_reason=None, index=0):
        self.delta = delta
        self.finish_reason = finish_reason
        self.index = index


class _FakeStream:
    """Async iterable that yields fake OpenAI completion chunks."""

    def __init__(self, choices_by_chunk: list[list[_FakeChoice]]):
        self._chunks = [
            type("Chunk", (), {"choices": choices})()
            for choices in choices_by_chunk
        ]

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


# ── Test helpers ────────────────────────────────────────────────────


async def _stream_events(provider, **kwargs) -> list[LLMStreamEvent]:
    """Collect all LLMStreamEvents from provider.stream_chat()."""
    events: list[LLMStreamEvent] = []
    async for event in provider.stream_chat(**kwargs):
        events.append(event)
    return events


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openai_streaming_emits_content_deltas():
    """Content chunks become content_delta events; stream ends with completed."""
    from miqi.providers.openai_provider import OpenAIProvider

    chunks = [
        [_FakeChoice(_FakeDelta(content="hel"))],
        [_FakeChoice(_FakeDelta(content="lo"))],
        [_FakeChoice(_FakeDelta(), finish_reason="stop")],
    ]

    provider = OpenAIProvider(api_key="sk-test")
    # Replace the internal client's create — must be a coroutine function
    # so that `await client.chat.completions.create()` works.
    async def _fake_create(**kw):
        """Fake create for this test scenario."""
        return _FakeStream(chunks)
    provider._client.chat.completions.create = _fake_create

    events = await _stream_events(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-4o",
    )

    kinds = [e.kind for e in events]
    assert kinds == ["content_delta", "content_delta", "completed"], (
        f"Expected content deltas then completed: {kinds}"
    )
    assert events[0].delta == "hel"
    assert events[1].delta == "lo"
    assert events[-1].response is not None
    assert events[-1].response.content == "hello"


@pytest.mark.asyncio
async def test_openai_streaming_yields_error_on_exception():
    """When the stream raises, a single completed event with error content
    is yielded so the runtime never hangs without a terminal event."""
    from miqi.providers.openai_provider import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test")
    provider._client.chat.completions.create = lambda **kw: (
        __import__("builtins").exec("raise RuntimeError('connection reset')")
    )

    events = await _stream_events(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-4o",
    )

    assert len(events) == 1
    assert events[0].kind == "completed"
    assert events[0].response is not None
    assert events[0].response.finish_reason == "error"
    assert "unexpected error" in events[0].response.content.lower()


@pytest.mark.asyncio
async def test_openai_streaming_emits_reasoning_deltas():
    """Reasoning chunks become reasoning_delta events (Kimi, DeepSeek-R1)."""
    from miqi.providers.openai_provider import OpenAIProvider

    chunks = [
        [_FakeChoice(_FakeDelta(reasoning_content="Let me think"))],
        [_FakeChoice(_FakeDelta(reasoning_content=" about this"))],
        [_FakeChoice(_FakeDelta(content="answer"), finish_reason="stop")],
    ]

    provider = OpenAIProvider(api_key="sk-test")
    async def _fake_create(**kw):
        """Fake create for this test scenario."""
        return _FakeStream(chunks)
    provider._client.chat.completions.create = _fake_create

    events = await _stream_events(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-4o",
    )

    kinds = [e.kind for e in events]
    assert "reasoning_delta" in kinds, f"Expected reasoning_delta in: {kinds}"
    assert "completed" in kinds

    reasoning_events = [e for e in events if e.kind == "reasoning_delta"]
    assert reasoning_events[0].delta == "Let me think"
    assert reasoning_events[1].delta == " about this"

    # Final content should still be in the completed response
    assert events[-1].response is not None
    assert events[-1].response.content == "answer"


@pytest.mark.asyncio
async def test_openai_streaming_enables_thinking_for_deepseek_v4():
    """DeepSeek V4 Flash / V4 Pro get thinking mode via extra_body (#539)."""
    from miqi.providers.openai_provider import OpenAIProvider

    captured: dict = {}
    provider = OpenAIProvider(api_key="sk-test")

    async def _fake_create(**kw):
        """Fake create for this test scenario."""
        captured.update(kw)
        return _FakeStream([[_FakeChoice(_FakeDelta(content="hi"), finish_reason="stop")]])

    provider._client.chat.completions.create = _fake_create

    await _stream_events(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        model="deepseek-v4-flash",
    )

    assert captured.get("extra_body", {}).get("thinking") == {"type": "enabled"}


@pytest.mark.asyncio
async def test_openai_streaming_does_not_force_thinking_for_non_reasoning_models():
    """Non-reasoning providers keep the request body untouched (#539)."""
    from miqi.providers.openai_provider import OpenAIProvider

    captured: dict = {}
    provider = OpenAIProvider(api_key="sk-test")

    async def _fake_create(**kw):
        """Fake create for this test scenario."""
        captured.update(kw)
        return _FakeStream([[_FakeChoice(_FakeDelta(content="hi"), finish_reason="stop")]])

    provider._client.chat.completions.create = _fake_create

    await _stream_events(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-4o",
    )

    assert "thinking" not in captured.get("extra_body", {})


# ── Issue #24: stream tool-call args need json_repair fallback ────────────


class _FakeFunction:
    """Simulates an OpenAI streaming tool-call function delta."""

    def __init__(self, name="", arguments=""):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    """Simulates an OpenAI streaming tool-call delta with an index."""

    def __init__(self, index=0, call_id="call_1", name="", arguments=""):
        self.index = index
        self.id = call_id
        self.type = "function"
        self.function = _FakeFunction(name=name, arguments=arguments)


async def _stream_with_tool_args(provider, arguments_str: str) -> list[LLMStreamEvent]:
    """Stream a single tool call whose accumulated arguments = arguments_str."""
    chunks = [
        [_FakeChoice(_FakeDelta(tool_calls=[_FakeToolCall(
            index=0, call_id="call_1", name="web_search", arguments=arguments_str,
        )]))],
        [_FakeChoice(_FakeDelta(), finish_reason="tool_calls")],
    ]

    async def _fake_create(**kw):
        """Fake create for this test scenario."""
        return _FakeStream(chunks)

    provider._client.chat.completions.create = _fake_create
    return await _stream_events(
        provider,
        messages=[{"role": "user", "content": "search the news"}],
        model="gpt-4o",
    )


@pytest.mark.asyncio
async def test_stream_malformed_tool_args_repaired_not_dropped():
    """Issue #24: slightly malformed tool-call args must be repaired by
    json_repair (matching the non-stream path), not silently fall back to {}."""
    from miqi.providers.openai_provider import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test")
    # Single-quoted JSON: json.loads fails, json_repair recovers the real args.
    events = await _stream_with_tool_args(provider, "{'query': '今日要闻'}")

    completed = events[-1]
    assert completed.kind == "completed"
    assert completed.response is not None
    assert len(completed.response.tool_calls) == 1
    args = completed.response.tool_calls[0].arguments
    # Must recover the real query, not silently become {}.
    assert args == {"query": "今日要闻"}, f"json_repair should recover args, got {args!r}"


@pytest.mark.asyncio
async def test_stream_valid_tool_args_unchanged():
    """Valid tool-call args parse as before (regression guard)."""
    from miqi.providers.openai_provider import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test")
    events = await _stream_with_tool_args(provider, '{"query": "今日要闻"}')

    completed = events[-1]
    assert completed.response.tool_calls[0].arguments == {"query": "今日要闻"}


@pytest.mark.asyncio
async def test_stream_empty_tool_args_resolves_to_empty_dict():
    """Empty streamed tool-call args remain an empty dict."""
    from miqi.providers.openai_provider import OpenAIProvider

    provider = OpenAIProvider(api_key="sk-test")
    events = await _stream_with_tool_args(provider, "")

    completed = events[-1]
    assert completed.response.tool_calls[0].arguments == {}


@pytest.mark.asyncio
async def test_stream_malformed_tool_args_logs_warning():
    """Issue #24: malformed args must also log a warning (parity with non-stream)."""
    from loguru import logger as loguru_logger

    from miqi.providers.openai_provider import OpenAIProvider

    messages: list[str] = []

    def _sink(message):
        messages.append(str(message.record["message"]))

    # loguru uses its own sinks (not stdlib logging), so capture via a test
    # sink instead of pytest's caplog.
    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        provider = OpenAIProvider(api_key="sk-test")
        await _stream_with_tool_args(provider, "{'query': '今日要闻'}")
    finally:
        loguru_logger.remove(handler_id)

    assert any("malformed tool args" in m for m in messages), (
        f"expected a malformed-tool-args warning, got: {messages}"
    )


# ── #834: reasoning_elapsed_s (server-side thinking proxy) ──────────────


@pytest.mark.asyncio
async def test_stream_reasoning_elapsed_s_measured_on_first_delta():
    """#834: completed response carries reasoning_elapsed_s = request-start →
    first reasoning delta (server-side thinking proxy)."""
    import asyncio

    from miqi.providers.openai_provider import OpenAIProvider

    chunks = [
        [_FakeChoice(_FakeDelta(reasoning_content="Let me think"))],
        [_FakeChoice(_FakeDelta(reasoning_content=" about this"))],
        [_FakeChoice(_FakeDelta(content="answer"), finish_reason="stop")],
    ]

    provider = OpenAIProvider(api_key="sk-test")

    async def _fake_create(**kw):
        # Simulate server-side thinking before the reasoning stream starts.
        # 0.2s leaves generous headroom for Windows timer precision (~15.6ms).
        """Fake create: simulates buffered server-side thinking before reasoning."""
        await asyncio.sleep(0.2)
        return _FakeStream(chunks)

    provider._client.chat.completions.create = _fake_create

    events = await _stream_events(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-4o",
    )

    completed = events[-1]
    assert completed.response is not None
    # Measured from monotonic request start — includes the simulated thinking.
    assert completed.response.reasoning_elapsed_s is not None
    assert completed.response.reasoning_elapsed_s >= 0.15


@pytest.mark.asyncio
async def test_stream_no_reasoning_elapsed_is_none():
    """#834: plain content streams (no reasoning) leave reasoning_elapsed_s None."""
    from miqi.providers.openai_provider import OpenAIProvider

    chunks = [
        [_FakeChoice(_FakeDelta(content="hello"), finish_reason="stop")],
    ]

    provider = OpenAIProvider(api_key="sk-test")

    async def _fake_create(**kw):
        """Fake create: content-only stream, no reasoning."""
        return _FakeStream(chunks)

    provider._client.chat.completions.create = _fake_create

    events = await _stream_events(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-4o",
    )

    completed = events[-1]
    assert completed.response is not None
    assert completed.response.reasoning_elapsed_s is None


@pytest.mark.asyncio
async def test_stream_interleaved_reasoning_suppresses_elapsed():
    """#834 / review: streaming CoT models (Kimi/Qwen) interleave reasoning
    with content — the first-delta proxy would under-report badly, so the
    completed response carries reasoning_elapsed_s=None and the frontend
    falls back to its local first→last span."""
    import asyncio

    from miqi.providers.openai_provider import OpenAIProvider

    # Kimi-style: reasoning, content, then MORE reasoning (interleaved).
    chunks = [
        [_FakeChoice(_FakeDelta(reasoning_content="Let me think"))],
        [_FakeChoice(_FakeDelta(content="answer part"))],
        [_FakeChoice(_FakeDelta(reasoning_content=" still thinking"))],
        [_FakeChoice(_FakeDelta(content=" rest"), finish_reason="stop")],
    ]

    provider = OpenAIProvider(api_key="sk-test")

    async def _fake_create(**kw):
        """Fake create for this test scenario."""
        await asyncio.sleep(0.1)  # thinking delay that must be suppressed
        return _FakeStream(chunks)

    provider._client.chat.completions.create = _fake_create

    events = await _stream_events(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-4o",
    )

    completed = events[-1]
    assert completed.response is not None
    # Interleaved ⇒ streaming CoT ⇒ proxy suppressed (frontend local span wins).
    assert completed.response.reasoning_elapsed_s is None
    assert completed.response.reasoning_content == "Let me think still thinking"


@pytest.mark.asyncio
async def test_stream_buffered_reasoning_keeps_elapsed():
    """#834: DeepSeek-style BUFFERED reasoning (all reasoning before any
    content) keeps the request→first-delta proxy — no interleaving detected."""
    import asyncio

    from miqi.providers.openai_provider import OpenAIProvider

    chunks = [
        [_FakeChoice(_FakeDelta(reasoning_content="Let me think"))],
        [_FakeChoice(_FakeDelta(reasoning_content=" more"))],
        [_FakeChoice(_FakeDelta(content="answer"), finish_reason="stop")],
    ]

    provider = OpenAIProvider(api_key="sk-test")

    async def _fake_create(**kw):
        """Fake create for this test scenario."""
        await asyncio.sleep(0.2)
        return _FakeStream(chunks)

    provider._client.chat.completions.create = _fake_create

    events = await _stream_events(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-4o",
    )

    completed = events[-1]
    assert completed.response is not None
    assert completed.response.reasoning_elapsed_s is not None
    assert completed.response.reasoning_elapsed_s >= 0.15


@pytest.mark.asyncio
async def test_stream_reasoning_first_streaming_provider_suppressed_by_capability():
    """#834 / CodeRabbit: a STREAMING-CoT provider (Kimi, spec.streams_reasoning)
    suppresses the proxy even when reasoning arrives BEFORE any content — the
    delta-order check alone would miss this shape."""
    import asyncio

    from miqi.providers.openai_provider import OpenAIProvider

    # Kimi-style but reasoning-first (no interleaving in delta order):
    # capability metadata must still suppress the proxy.
    chunks = [
        [_FakeChoice(_FakeDelta(reasoning_content="Let me think"))],
        [_FakeChoice(_FakeDelta(reasoning_content=" more"))],
        [_FakeChoice(_FakeDelta(content="answer"), finish_reason="stop")],
    ]

    provider = OpenAIProvider(api_key="sk-test")

    async def _fake_create(**kw):
        """Fake create for this test scenario."""
        await asyncio.sleep(0.1)
        return _FakeStream(chunks)

    provider._client.chat.completions.create = _fake_create

    events = await _stream_events(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        model="kimi-k2.5",
    )

    completed = events[-1]
    assert completed.response is not None
    # streams_reasoning=True ⇒ suppressed despite reasoning-first ordering.
    assert completed.response.reasoning_elapsed_s is None
    assert completed.response.reasoning_elapsed_suppressed is True


@pytest.mark.asyncio
async def test_stream_buffered_provider_not_suppressed_by_capability():
    """#834 / CodeRabbit: providers WITHOUT the streaming capability keep the
    proxy (DeepSeek-style buffered reasoning, reasoning-first ordering)."""
    import asyncio

    from miqi.providers.openai_provider import OpenAIProvider

    chunks = [
        [_FakeChoice(_FakeDelta(reasoning_content="Let me think"))],
        [_FakeChoice(_FakeDelta(reasoning_content=" more"))],
        [_FakeChoice(_FakeDelta(content="answer"), finish_reason="stop")],
    ]

    provider = OpenAIProvider(api_key="sk-test")

    async def _fake_create(**kw):
        """Fake create for this test scenario."""
        await asyncio.sleep(0.2)
        return _FakeStream(chunks)

    provider._client.chat.completions.create = _fake_create

    events = await _stream_events(
        provider,
        messages=[{"role": "user", "content": "hi"}],
        model="deepseek-reasoner",
    )

    completed = events[-1]
    assert completed.response is not None
    assert completed.response.reasoning_elapsed_s is not None
    assert completed.response.reasoning_elapsed_s >= 0.15
    assert completed.response.reasoning_elapsed_suppressed is False
