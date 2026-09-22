# -*- coding: utf-8 -*-
"""AnthropicProvider.stream_chat 真流式回归。

背景（实机复现）：平台 AI 网关是 Anthropic Messages 兼容，模型的 CoT 走
``thinking`` 块 / ``thinking_delta`` 事件。原实现的 ``stream_chat`` 只是个桩：
把整段 ``chat()`` 包成一个 ``completed`` 事件 → 前端只能在整轮结束后一次性
拿到思考全文，"思考过程"不是边想边显示（Hermes 的做法是边收边发）。本文件锁
改造后的数据流。

口径对齐 Hermes：``agent/chat_completion_helpers.py`` 的
``delta_type == "thinking_delta"`` → ``agent._fire_reasoning_delta(thinking)``。

锁四件事：
1. ``thinking_delta`` → ``reasoning_delta``（思考增量实时外发）；
2. ``text_delta`` → ``content_delta``；
3. ``completed`` 带完整响应（reasoning_content / 工具调用）且带
   ``reasoning_elapsed_s``（request→首个思考 delta 的耗时代理，#834）；
4. 容错：流前失败回退 ``chat()``；流中失败按 chat() 的契约交回错误响应。
"""
from types import SimpleNamespace

from miqi.providers.anthropic_provider import AnthropicProvider
from miqi.providers.base import LLMResponse


def _provider() -> AnthropicProvider:
    return AnthropicProvider(
        api_key="test-key",
        api_base="http://127.0.0.1:9",
        default_model="deepseek/deepseek-v4-flash",
    )


class _FakeStream:
    """假的 SDK MessageStream：可迭代原始事件，并可取 final message。"""

    def __init__(self, events, final=None, raise_after: int | None = None):
        self._events = list(events)
        self._final = final
        self._raise_after = raise_after

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):
        for i, event in enumerate(self._events):
            if self._raise_after is not None and i >= self._raise_after:
                raise RuntimeError("connection reset mid-stream")
            yield event
        if self._raise_after is not None and self._raise_after >= len(self._events):
            raise RuntimeError("connection reset mid-stream")

    async def get_final_message(self):
        return self._final


class _FakeMessages:
    def __init__(self, stream):
        self._stream = stream

    def stream(self, **kwargs):
        self.kwargs = kwargs
        return self._stream


def _attach(provider: AnthropicProvider, stream: _FakeStream) -> _FakeMessages:
    fake = _FakeMessages(stream)
    provider._client = SimpleNamespace(messages=fake)  # type: ignore[assignment]
    return fake


def _delta(delta_type: str, **fields):
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type=delta_type, **fields),
    )


def _final_message():
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="先想 17*23"),
            SimpleNamespace(type="text", text="391"),
        ],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=11, output_tokens=22),
    )


async def test_thinking_deltas_are_relayed_as_reasoning_events():
    provider = _provider()
    stream = _FakeStream(
        [
            _delta("thinking_delta", thinking="先想"),
            _delta("thinking_delta", thinking=" 17*23"),
            _delta("text_delta", text="391"),
        ],
        final=_final_message(),
    )
    _attach(provider, stream)

    events = [
        e
        async for e in provider.stream_chat([{"role": "user", "content": "17*23 等于几"}])
    ]

    assert [e.kind for e in events] == [
        "reasoning_delta",
        "reasoning_delta",
        "content_delta",
        "completed",
    ]
    assert "".join(e.delta for e in events if e.kind == "reasoning_delta") == "先想 17*23"
    assert "".join(e.delta for e in events if e.kind == "content_delta") == "391"
    final = events[-1].response
    assert final.reasoning_content == "先想 17*23"
    assert final.content == "391"
    # #834：request→首个思考 delta 的耗时代理必须报出来（前端 "X 秒" 用它）。
    assert final.reasoning_elapsed_s is not None
    assert final.reasoning_elapsed_s >= 0


async def test_completed_event_carries_tool_calls():
    provider = _provider()
    stream = _FakeStream(
        [_delta("text_delta", text="调用工具")],
        final=SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="调用工具"),
                SimpleNamespace(
                    type="tool_use",
                    id="toolu_1",
                    name="web_search",
                    input={"query": "MOF-5"},
                ),
            ],
            stop_reason="tool_use",
            usage=None,
        ),
    )
    _attach(provider, stream)

    events = [
        e
        async for e in provider.stream_chat(
            [{"role": "user", "content": "查一下"}],
            tools=[{"type": "function", "function": {"name": "web_search"}}],
        )
    ]

    final = events[-1].response
    assert final.finish_reason == "tool_calls"
    assert [tc.name for tc in final.tool_calls] == ["web_search"]
    assert final.tool_calls[0].arguments == {"query": "MOF-5"}


async def test_failure_before_any_output_falls_back_to_chat(monkeypatch):
    provider = _provider()
    _attach(provider, _FakeStream([], raise_after=0))

    async def fake_chat(**kwargs):
        return LLMResponse(content="兜底答复", finish_reason="stop")

    monkeypatch.setattr(provider, "chat", fake_chat)

    events = [e async for e in provider.stream_chat([{"role": "user", "content": "hi"}])]

    assert [e.kind for e in events] == ["completed"]
    assert events[-1].response.content == "兜底答复"


async def test_terminal_error_before_output_skips_chat_fallback(monkeypatch):
    """#1190：流前终态错误（配额耗尽 → PAYMENT_REQUIRED）不再回退 chat()——
    再发一次注定失败的请求纯属浪费；按 chat() 的契约直接交回 error 响应。"""
    import anthropic

    quota_err = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    Exception.__init__(
        quota_err,
        "Error code: 429 - {'error': {'message': 'Token quota exhausted, please contact the administrator', 'code': 'consumer_token_quota_exceeded', 'type': 'quota_exceeded'}}",
    )

    class _QuotaExhaustedStream(_FakeStream):
        async def __aiter__(self):
            raise quota_err
            yield  # pragma: no cover — async generator 语义占位

    provider = _provider()
    _attach(provider, _QuotaExhaustedStream([]))

    async def fake_chat(**kwargs):
        raise AssertionError("终态错误不得回退调用 chat()")

    monkeypatch.setattr(provider, "chat", fake_chat)

    events = [e async for e in provider.stream_chat([{"role": "user", "content": "hi"}])]

    assert [e.kind for e in events] == ["completed"]
    resp = events[-1].response
    assert resp.finish_reason == "error"
    assert resp.error_kind == "payment_required"


async def test_failure_mid_stream_surfaces_error_response():
    provider = _provider()
    _attach(provider, _FakeStream([_delta("text_delta", text="半截")], raise_after=1))

    events = [e async for e in provider.stream_chat([{"role": "user", "content": "hi"}])]

    # 已吐出的增量照常保留，之后是一个 error 语义的 completed（与 chat() 一致）。
    assert [e.kind for e in events] == ["content_delta", "completed"]
    assert events[-1].response.finish_reason == "error"
    assert events[-1].response.error_kind
