"""Turn-level provider error propagation (Plan 57).

Covers:
- TurnRunner.run raises ``ProviderError`` when the terminal stream response
  has ``finish_reason == "error"`` instead of returning the error text as a
  normal ``final_content`` (the original bug).
- TaskRunner's existing ``except Exception`` turn-failure path recognizes
  ``ProviderError``, sets ``error_kind`` / ``recoverable`` on the emitted
  ``ErrorEvent`` and ledger ``error`` item, surfaces the provider message for
  user-actionable kinds, and keeps the generic message otherwise. The
  ``asyncio.CancelledError`` branch and the success path are untouched.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.providers.base import LLMResponse, LLMStreamEvent
from miqi.providers.resilience import ErrorKind, ProviderError
from miqi.runtime.turn_runner import TurnRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeContext:
    """Minimal context_runtime for TurnRunner tests."""

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


class _FakeEvents:
    def __init__(self):
        self.events: list = []

    async def emit(self, event):
        self.events.append(event)


def _make_turn():
    turn = type("Turn", (), {})()
    turn.turn_id = "turn-err"
    turn.thread_id = "thread-err"
    turn.model = "test-model"
    turn.temperature = 0.0
    turn.max_tokens = 100
    return turn


def _make_runner(provider):
    return TurnRunner(
        provider=provider,
        tool_runtime=MagicMock(),
        context_runtime=_FakeContext(),
        event_emitter=_FakeEvents(),
        max_iterations=3,
    )


class _ErrorProvider:
    """Provider whose stream_chat yields a single terminal 'error' response."""

    def __init__(self, response: LLMResponse):
        self._response = response

    async def stream_chat(self, **kwargs):
        yield LLMStreamEvent(kind="completed", response=self._response)


class _StopProvider:
    """Provider whose stream_chat yields a single normal 'stop' response."""

    def __init__(self, content: str = "all good"):
        self._content = content

    async def stream_chat(self, **kwargs):
        yield LLMStreamEvent(
            kind="completed",
            response=LLMResponse(content=self._content, finish_reason="stop"),
        )


# ---------------------------------------------------------------------------
# Task 57.2: TurnRunner raises ProviderError on finish_reason == "error"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_runner_raises_provider_error_on_finish_reason_error():
    """A terminal `completed` with finish_reason="error" must raise
    ProviderError carrying the response's error_kind and content — it must
    NOT be returned as a successful final_content."""
    provider = _ErrorProvider(
        LLMResponse(content="boom", finish_reason="error", error_kind="rate_limit")
    )
    runner = _make_runner(provider)

    with pytest.raises(ProviderError) as info:
        await runner.run(
            turn=_make_turn(),
            user_content="hi",
            system_prompt="system",
            tools=[],
        )

    err = info.value
    assert err.kind is ErrorKind.RATE_LIMIT
    assert err.message == "boom"
    # Regression guard: the error text is not silently returned as content.
    assert err.recoverable is True


@pytest.mark.asyncio
async def test_turn_runner_happy_path_unchanged():
    """finish_reason="stop" still returns a normal TurnResult (regression guard)."""
    runner = _make_runner(_StopProvider(content="all good"))

    result = await runner.run(
        turn=_make_turn(),
        user_content="hi",
        system_prompt="system",
        tools=[],
    )

    assert result.final_content == "all good"
    assert result.tools_used == []


@pytest.mark.asyncio
async def test_turn_runner_error_defaults_to_fatal_when_error_kind_missing():
    """finish_reason="error" with error_kind=None raises ProviderError(FATAL)."""
    provider = _ErrorProvider(
        LLMResponse(content="kaboom", finish_reason="error", error_kind=None)
    )
    runner = _make_runner(provider)

    with pytest.raises(ProviderError) as info:
        await runner.run(
            turn=_make_turn(),
            user_content="hi",
            system_prompt="system",
            tools=[],
        )

    assert info.value.kind is ErrorKind.FATAL
    assert info.value.recoverable is False


@pytest.mark.asyncio
async def test_turn_runner_invalid_error_kind_falls_back_to_fatal():
    """An unrecognized error_kind string falls back to FATAL rather than crashing."""
    provider = _ErrorProvider(
        LLMResponse(content="weird", finish_reason="error", error_kind="bogus_kind")
    )
    runner = _make_runner(provider)

    with pytest.raises(ProviderError) as info:
        await runner.run(
            turn=_make_turn(),
            user_content="hi",
            system_prompt="system",
            tools=[],
        )

    assert info.value.kind is ErrorKind.FATAL


# ---------------------------------------------------------------------------
# Task 57.3: TaskRunner surfaces ProviderError category
# ---------------------------------------------------------------------------


def _record_ledger_error_payload(ledger: MagicMock) -> dict | None:
    """Return the payload of the ledger `error` item, or None if absent."""
    for call in ledger.append_item.call_args_list:
        if call.kwargs.get("item_type") == "error":
            return call.kwargs.get("payload")
    return None


async def _drain_events(queue: asyncio.Queue) -> list:
    out: list = []
    while True:
        try:
            out.append(await asyncio.wait_for(queue.get(), timeout=0.5))
        except asyncio.TimeoutError:
            break
    return out


@pytest.fixture
def error_services(fake_services):
    """fake_services wired so turn_runner.run raises, with recording
    ledger + history runtimes and auto-compact disabled."""
    history = MagicMock()
    history.start_turn = AsyncMock(return_value=None)
    history.load_messages = AsyncMock(return_value=[])
    history.append_message = AsyncMock(return_value=None)
    history.complete_turn = AsyncMock(return_value=None)
    fake_services.history_runtime = history

    ledger = MagicMock()
    ledger.append_item = AsyncMock(return_value=None)
    fake_services.ledger_runtime = ledger

    # Disable the auto-compact branch (avoids needing a real context_runtime).
    fake_services.context_runtime = None
    return fake_services


async def _run_turn_expect_error(error_services, exc: BaseException):
    from miqi.protocol.commands import UserMessage
    from miqi.runtime.task_runner import TaskRunner

    error_services.turn_runner.run = AsyncMock(side_effect=exc)
    events: asyncio.Queue = asyncio.Queue()
    runner = TaskRunner(services=error_services, event_queue=events)

    await runner.handle(UserMessage(content="hello", thread_id="cli:default"))

    emitted = await _drain_events(events)
    return emitted, error_services.history_runtime, error_services.ledger_runtime


@pytest.mark.asyncio
async def test_task_runner_provider_error_rate_limit_surfaces_kind_and_message(
    error_services,
):
    from miqi.protocol.events import ErrorEvent, TurnCompleteEvent

    emitted, history, ledger = await _run_turn_expect_error(
        error_services,
        ProviderError(kind=ErrorKind.RATE_LIMIT, message="Rate limit exceeded: slow down"),
    )

    err_events = [e for e in emitted if isinstance(e, ErrorEvent)]
    assert len(err_events) == 1
    err = err_events[0]
    assert err.error_kind == "rate_limit"
    assert err.recoverable is True
    # User-actionable kind → provider message is surfaced to the client.
    assert "slow down" in err.message

    # Turn recorded as a real failure, not a success.
    history.complete_turn.assert_awaited()
    status_kwargs = history.complete_turn.call_args
    assert status_kwargs.kwargs.get("status") == "error"
    assert not any(
        isinstance(e, TurnCompleteEvent) and e.outcome == "success" for e in emitted
    )

    # Ledger error item carries the error_kind + recoverable.
    payload = _record_ledger_error_payload(ledger)
    assert payload is not None
    assert payload.get("error_kind") == "rate_limit"
    assert payload.get("recoverable") is True


@pytest.mark.asyncio
async def test_task_runner_provider_error_auth_is_non_recoverable(error_services):
    from miqi.protocol.events import ErrorEvent

    emitted, _history, ledger = await _run_turn_expect_error(
        error_services,
        ProviderError(kind=ErrorKind.AUTH, message="Invalid API key"),
    )

    err = next(e for e in emitted if isinstance(e, ErrorEvent))
    assert err.error_kind == "auth"
    assert err.recoverable is False
    # AUTH is sensitive — surface a fixed, non-leaking message, never the raw
    # provider exception text (Plan 58.2).
    assert err.message == "模型服务认证失败，请检查 Provider 的 API Key、API Base 或当前模型配置。"

    payload = _record_ledger_error_payload(ledger)
    assert payload.get("error_kind") == "auth"
    assert payload.get("recoverable") is False


@pytest.mark.asyncio
async def test_task_runner_provider_error_context_length_surfaces_message(
    error_services,
):
    from miqi.protocol.events import ErrorEvent

    emitted, _history, _ledger = await _run_turn_expect_error(
        error_services,
        ProviderError(
            kind=ErrorKind.CONTEXT_LENGTH,
            message="This model's maximum context length is 8192 tokens.",
        ),
    )

    err = next(e for e in emitted if isinstance(e, ErrorEvent))
    assert err.error_kind == "context_length"
    assert err.recoverable is False
    assert "context length" in err.message.lower()


@pytest.mark.asyncio
async def test_task_runner_provider_error_invalid_request_surfaces_message(
    error_services,
):
    from miqi.protocol.events import ErrorEvent

    emitted, _history, _ledger = await _run_turn_expect_error(
        error_services,
        ProviderError(
            kind=ErrorKind.INVALID_REQUEST,
            message="messages.0.content must be a string",
        ),
    )

    err = next(e for e in emitted if isinstance(e, ErrorEvent))
    assert err.error_kind == "invalid_request"
    assert err.recoverable is False
    assert "must be a string" in err.message


@pytest.mark.asyncio
async def test_task_runner_generic_exception_keeps_generic_message(error_services):
    """A non-ProviderError exception keeps the generic message (FATAL is not
    user-actionable, so internal details are not leaked). After Issue #529 the
    final boundary re-classifies it, so error_kind is now ``"fatal"`` rather
    than ``None`` (regression: "unknown" failures still get a category), while
    recoverable stays False and the message stays generic."""
    from miqi.protocol.events import ErrorEvent, TurnCompleteEvent

    emitted, _history, ledger = await _run_turn_expect_error(
        error_services,
        RuntimeError("unexpected boom"),
    )

    err = next(e for e in emitted if isinstance(e, ErrorEvent))
    assert err.error_kind == "fatal"
    assert err.recoverable is False
    assert err.message == "处理消息时发生内部错误，请重试。"  # noqa: RUF001

    payload = _record_ledger_error_payload(ledger)
    assert payload is not None
    assert payload.get("error_kind") == "fatal"
    assert payload.get("recoverable") is False
    assert payload.get("source") == "task_runner"
    assert not any(
        isinstance(e, TurnCompleteEvent) and e.outcome == "success" for e in emitted
    )


@pytest.mark.asyncio
async def test_task_runner_raw_transient_exception_surfaces_recoverable_message(error_services):
    """Issue #529: a raw SDK-style exception (NOT a ProviderError) that
    classifies as TRANSIENT — e.g. an exhausted 503/overload retry that escaped
    via a streaming path — must surface the transient message and
    recoverable=True, not the generic internal-error fallback. This is the
    core regression the issue fixes."""
    from miqi.protocol.events import ErrorEvent

    emitted, _history, ledger = await _run_turn_expect_error(
        error_services,
        Exception("503 Service Unavailable"),
    )

    err = next(e for e in emitted if isinstance(e, ErrorEvent))
    assert err.error_kind == "transient"
    assert err.recoverable is True
    assert err.message == "模型服务暂时不可用或过载，请稍后重试。"

    payload = _record_ledger_error_payload(ledger)
    assert payload is not None
    assert payload.get("error_kind") == "transient"
    assert payload.get("recoverable") is True


@pytest.mark.asyncio
async def test_task_runner_raw_rate_limit_exception_surfaces_kind(error_services):
    """Issue #529: a raw 429 exception reaching the final boundary is
    re-classified to RATE_LIMIT with recoverable=True. (The provider message
    falls back to the generic one because the raw exception has no clean
    message — that's acceptable; the category + recoverability are what
    matter for this path.)"""
    from miqi.protocol.events import ErrorEvent
    from miqi.providers.resilience import ErrorKind  # noqa: F401  (clarity)

    class _RateLimitError(Exception):
        status_code = 429

    emitted, _history, ledger = await _run_turn_expect_error(
        error_services,
        _RateLimitError("rate limited"),
    )

    err = next(e for e in emitted if isinstance(e, ErrorEvent))
    assert err.error_kind == "rate_limit"
    assert err.recoverable is True

    payload = _record_ledger_error_payload(ledger)
    assert payload.get("error_kind") == "rate_limit"
    assert payload.get("recoverable") is True


@pytest.mark.asyncio
async def test_task_runner_wrapped_transient_recovers_via_cause_chain(error_services):
    """Issue #529 edge case (per review): if an intermediate layer re-wraps a
    TRANSIENT SDK error into a plain RuntimeError with __cause__ set, the outer
    classify_error yields FATAL but __cause__ still classifies as TRANSIENT.
    The final boundary must walk the chain so the recoverable category is not
    lost."""
    from miqi.protocol.events import ErrorEvent

    inner = Exception("503 Service Unavailable")
    wrapped = RuntimeError("processing failed")
    wrapped.__cause__ = inner

    emitted, _history, ledger = await _run_turn_expect_error(error_services, wrapped)

    err = next(e for e in emitted if isinstance(e, ErrorEvent))
    assert err.error_kind == "transient"
    assert err.recoverable is True
    assert err.message == "模型服务暂时不可用或过载，请稍后重试。"

    payload = _record_ledger_error_payload(ledger)
    assert payload.get("error_kind") == "transient"


@pytest.mark.asyncio
async def test_task_runner_raw_auth_exception_surfaces_fixed_message(error_services):
    """Issue #529: a raw 401 exception reaching the final boundary is
    re-classified to AUTH. AUTH stays non-recoverable and surfaces the fixed
    non-leaking message (never the raw exception text)."""
    from miqi.protocol.events import ErrorEvent

    class _AuthError(Exception):
        status_code = 401

    emitted, _history, ledger = await _run_turn_expect_error(
        error_services,
        _AuthError("invalid api key leaked: sk-xxxx"),
    )

    err = next(e for e in emitted if isinstance(e, ErrorEvent))
    assert err.error_kind == "auth"
    assert err.recoverable is False
    assert err.message == "模型服务认证失败，请检查 Provider 的 API Key、API Base 或当前模型配置。"
    assert "sk-xxxx" not in err.message

    payload = _record_ledger_error_payload(ledger)
    assert payload.get("error_kind") == "auth"
    assert payload.get("recoverable") is False


@pytest.mark.asyncio
async def test_task_runner_raw_payment_required_surfaces_billing_message(error_services):
    """Issue #528: a raw 402 error reaching the final boundary is re-classified
    to PAYMENT_REQUIRED. It surfaces the fixed billing hint (never the raw text,
    which may leak account details) and recoverable=False — retrying won't add
    balance."""
    from miqi.protocol.events import ErrorEvent

    class _PaymentRequiredError(Exception):
        status_code = 402

    # Neutral body (no _PAYMENT_REQUIRED_SIGNALS match) so the
    # classification must come from status_code == 402, not the message
    # layer (CodeRabbit #528).
    emitted, _history, ledger = await _run_turn_expect_error(
        error_services,
        _PaymentRequiredError("upstream response"),
    )

    err = next(e for e in emitted if isinstance(e, ErrorEvent))
    assert err.error_kind == "payment_required"
    assert err.recoverable is False
    assert err.message == "模型服务账户余额不足或额度已用尽，请充值或检查账户额度后重试。"
    assert "upstream response" not in err.message

    payload = _record_ledger_error_payload(ledger)
    assert payload.get("error_kind") == "payment_required"
    assert payload.get("recoverable") is False


@pytest.mark.asyncio
async def test_task_runner_raw_quota_exhausted_message_classifies_payment(error_services):
    """Issue #528: even without an explicit 402 status, a quota-exhausted
    message body classifies as PAYMENT_REQUIRED via the message keyword path."""
    from miqi.protocol.events import ErrorEvent

    emitted, _history, ledger = await _run_turn_expect_error(
        error_services,
        Exception("You exceeded your current quota, please check your plan and billing"),
    )

    err = next(e for e in emitted if isinstance(e, ErrorEvent))
    assert err.error_kind == "payment_required"
    assert err.recoverable is False
    assert err.message == "模型服务账户余额不足或额度已用尽，请充值或检查账户额度后重试。"

    payload = _record_ledger_error_payload(ledger)
    assert payload.get("error_kind") == "payment_required"


@pytest.mark.asyncio
async def test_task_runner_provider_error_fatal_keeps_generic_message(error_services):
    """A FATAL ProviderError is NOT user-actionable, so the generic message
    is kept even though error_kind is recorded."""
    from miqi.protocol.events import ErrorEvent

    emitted, _history, ledger = await _run_turn_expect_error(
        error_services,
        ProviderError(kind=ErrorKind.FATAL, message="provider exploded internally"),
    )

    err = next(e for e in emitted if isinstance(e, ErrorEvent))
    assert err.error_kind == "fatal"
    assert err.recoverable is False
    # Generic message kept — internal details are not leaked for fatal errors.
    assert err.message == "处理消息时发生内部错误，请重试。"  # noqa: RUF001

    payload = _record_ledger_error_payload(ledger)
    assert payload.get("error_kind") == "fatal"
    assert payload.get("recoverable") is False
