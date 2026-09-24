"""Tests for the shared provider resilience layer (Plan 56)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import miqi.providers.resilience as resilience
from miqi.providers.anthropic_provider import AnthropicProvider
from miqi.providers.base import LLMResponse
from miqi.providers.openai_provider import (
    DEFAULT_FIRST_TOKEN_TIMEOUT,
    DEFAULT_STREAM_IDLE_TIMEOUT,
    OpenAIProvider,
)
from miqi.providers.resilience import (
    ErrorKind,
    ProviderError,
    classify_error,
    compute_backoff,
    is_retryable,
    retry_after_seconds,
    with_retry,
)

# ---------------------------------------------------------------------------
# Exception fixtures
# ---------------------------------------------------------------------------


class _StatusError(Exception):
    """Generic exception carrying an HTTP status code."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class _RateLimitError(Exception):
    status_code = 429


class _AuthError(Exception):
    status_code = 401


class _ContextLength400Error(Exception):
    status_code = 400


class _NotFoundError(Exception):
    status_code = 404


class _FakeHeaders:
    def __init__(self, data: dict[str, str]):
        self._data = {k.lower(): v for k, v in data.items()}

    def get(self, name: str) -> str | None:
        return self._data.get(name.lower())


# ---------------------------------------------------------------------------
# classify_error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("keyword", [
    "apiconnectionerror",
    "connection reset",
    "connection aborted",
    "temporary failure",
    "timed out",
    "timeout",
    "502",
    "503",
    "504",
    "bad gateway",
    "service unavailable",
    "overloaded",
])
def test_classify_error_transient_keywords(keyword: str) -> None:
    assert classify_error(Exception(f"something {keyword} happened")) == ErrorKind.TRANSIENT


def test_classify_error_rate_limit_status_code() -> None:
    assert classify_error(_RateLimitError("rate limited")) == ErrorKind.RATE_LIMIT


def test_classify_error_rate_limit_message() -> None:
    assert classify_error(Exception("you are being rate limit")) == ErrorKind.RATE_LIMIT


@pytest.mark.parametrize("status", [401, 403])
def test_classify_error_auth(status: int) -> None:
    assert classify_error(_StatusError("auth failed", status_code=status)) == ErrorKind.AUTH


def test_classify_error_auth_message() -> None:
    assert classify_error(Exception("invalid api key")) == ErrorKind.AUTH


# ── 平台内容安全拦截：403 但不是认证失败 ──────────────────────────────────


def test_classify_error_content_blocked_from_gateway_403() -> None:
    """平台 AI 网关实测返回 403 sensitive_word_detected —— 认证失败是误报，
    真因是内容被审核拦截（用户改 API Key / 换模型永远修不好）。"""
    exc = _StatusError(
        "Error code: 403 - {'error': {'code': 'sensitive_word_detected', "
        "'message': '我们换一个话题吧', 'type': 'security_violation'}}",
        status_code=403,
    )
    assert classify_error(exc) == ErrorKind.CONTENT_BLOCKED


def test_classify_error_content_blocked_from_message_only() -> None:
    """无 status_code 时也要按审核信号分流（SDK 包装层可能丢掉状态码）。"""
    assert classify_error(
        Exception("content_policy_violation: your request was rejected")
    ) == ErrorKind.CONTENT_BLOCKED


def test_classify_error_content_blocked_not_retryable() -> None:
    """同一份内容重试必然再次被拦，不得进入重试链。"""
    assert is_retryable(ErrorKind.CONTENT_BLOCKED) is False
    assert ProviderError(kind=ErrorKind.CONTENT_BLOCKED, message="blocked").recoverable is False


def test_plain_403_still_auth() -> None:
    """回归护栏：真正的 403 权限拒绝仍归 AUTH，不被审核判定抢走。"""
    assert classify_error(_StatusError("forbidden", status_code=403)) == ErrorKind.AUTH
    assert classify_error(Exception("Forbidden: billing access denied")) == ErrorKind.AUTH


# ── Issue #528: 402 / balance / quota → PAYMENT_REQUIRED ───────────────────


def test_classify_error_payment_required_status_402() -> None:
    # Neutral body (no _PAYMENT_REQUIRED_SIGNALS match) so this test
    # actually exercises the explicit status_code == 402 mapping, not the
    # message-keyword layer (CodeRabbit #528).
    assert classify_error(_StatusError("upstream response", status_code=402)) == ErrorKind.PAYMENT_REQUIRED


def test_classify_error_httpx_transport_errors_transient() -> None:
    """httpx transport errors (mid-stream drops/timeouts) must be TRANSIENT so
    with_retry re-runs instead of surfacing a generic "internal error" (#675)."""
    import httpx

    for exc in (
        httpx.ReadError("connection reset"),
        httpx.ConnectError("connect failed"),
        httpx.WriteError("write failed"),
        httpx.ReadTimeout("read timed out"),
        httpx.WriteTimeout("write timed out"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.PoolTimeout("no free pool"),
    ):
        assert classify_error(exc) == ErrorKind.TRANSIENT, exc


async def test_with_retry_retries_httpx_readerror_then_succeeds() -> None:
    """TRANSIENT httpx.ReadError must actually trigger a retry — the #675
    regression (classified FATAL → no retry → misleading 'internal error')."""
    import httpx

    calls = 0
    retries = []

    async def factory() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadError("connection reset by peer")
        return "ok"

    result = await with_retry(
        factory,
        max_attempts=3,
        sleep=lambda _: asyncio.sleep(0),
        on_retry=lambda attempt, kind, delay: retries.append((attempt, kind)),
    )
    assert result == "ok"
    assert calls == 2  # failed once, retried once, succeeded
    assert retries == [(1, ErrorKind.TRANSIENT)]


async def test_with_retry_does_not_retry_fatal_error() -> None:
    """A FATAL-classified error must NOT be retried (control case)."""

    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        raise ValueError("not retryable")

    with pytest.raises(ValueError):
        await with_retry(factory, max_attempts=3, sleep=lambda _: asyncio.sleep(0))
    assert calls == 1  # raised immediately, no retry


@pytest.mark.parametrize("message", [
    # OpenAI style
    "You exceeded your current quota, please check your plan and billing details",
    # Anthropic style
    "Error: credit balance is too low",
    # Gateway / proxy wording
    "Insufficient balance",
    "payment required",
    "quota exceeded - top up to continue",
    "credit exhausted",
    "out of credits",
    "payment required - top up your account",
    "balance exceeded the limit",
])
def test_classify_error_payment_required_message(message: str) -> None:
    assert classify_error(Exception(message)) == ErrorKind.PAYMENT_REQUIRED


def test_classify_error_payment_required_is_not_retryable() -> None:
    """402 is non-retryable — retrying won't add balance."""
    assert is_retryable(ErrorKind.PAYMENT_REQUIRED) is False
    assert ProviderError(
        kind=ErrorKind.PAYMENT_REQUIRED, message="insufficient balance"
    ).recoverable is False


def test_classify_error_payment_required_preferred_over_auth_wording() -> None:
    """A 402 body that also says 'forbidden' must not be misread as AUTH —
    billing is checked before the auth keyword branch."""
    assert classify_error(
        Exception("Forbidden: insufficient balance, payment required")
    ) == ErrorKind.PAYMENT_REQUIRED


def test_bare_billing_word_is_not_payment_required() -> None:
    """CodeRabbit regression (#528): a bare 'billing' substring in an
    AUTH-style message ("Forbidden: billing access denied") must NOT be
    misclassified as PAYMENT_REQUIRED — only balance/quota-specific phrases
    match the message layer; a bare 'billing' was removed from the signals."""
    assert classify_error(
        Exception("Forbidden: billing access denied")
    ) == ErrorKind.AUTH


# ── Issue #1190: 429 + quota signals → PAYMENT_REQUIRED（非 RATE_LIMIT）──────


@pytest.mark.parametrize("message", [
    # 平台网关实测形态：429 + "Token quota exhausted" + 结构化错误体
    "Error code: 429 - {'error': {'message': 'Token quota exhausted, please contact the administrator', 'code': 'consumer_token_quota_exceeded', 'type': 'quota_exceeded'}}",
    # 只有 code 字段、无可读 message 的形态
    "429 {'error': {'code': 'consumer_token_quota_exceeded'}}",
    # 只有 type 字段
    "429 {'error': {'type': 'quota_exceeded'}}",
])
def test_classify_error_429_quota_exhausted_is_payment_required(message: str) -> None:
    """配额耗尽的 429 必须归 PAYMENT_REQUIRED（终态、不可重试），而不是
    可重试的 RATE_LIMIT——否则会反复重试 + 原始英文错误透出前端。"""
    assert classify_error(_RateLimitError(message)) == ErrorKind.PAYMENT_REQUIRED


def test_classify_error_429_quota_false_field_is_not_payment() -> None:
    """结构化字段的否定形态不是配额耗尽（#1190 CodeRabbit）：裸子串匹配会
    把 {"quota_exceeded": false} 误判为 PAYMENT_REQUIRED 并错误地禁用重试。"""
    assert classify_error(
        _RateLimitError("429 {'error': {'quota_exceeded': False, 'message': 'rate limit'}}")
    ) == ErrorKind.RATE_LIMIT


def test_classify_error_429_not_quota_exceeded_field_is_not_payment() -> None:
    """value=not_quota_exceeded 同样不命中——结构化匹配要求值完整等于配额码。"""
    assert classify_error(
        _RateLimitError("429 {'error': {'type': 'not_quota_exceeded'}}")
    ) == ErrorKind.RATE_LIMIT


def test_classify_error_429_quota_exhausted_not_retryable() -> None:
    kind = classify_error(
        _RateLimitError(
            "429 {'error': {'code': 'consumer_token_quota_exceeded', 'type': 'quota_exceeded'}}"
        )
    )
    assert is_retryable(kind) is False
    assert ProviderError(kind=kind, message="quota").recoverable is False


def test_classify_error_rate_limit_wording_with_quota_signal_is_payment() -> None:
    """429 报错同时含 "rate limit" 措辞与配额耗尽信号：配额是终态，优先判定。"""
    assert classify_error(
        Exception("429 rate limit: token quota exhausted, please contact the administrator")
    ) == ErrorKind.PAYMENT_REQUIRED


def test_classify_error_anthropic_sdk_rate_limit_quota_signal() -> None:
    """#1190 实测路径：anthropic SDK 的 RateLimitError 实例携带配额信号时
    归 PAYMENT_REQUIRED（SDK 类型分支此前先于消息检查短路为 RATE_LIMIT）。"""
    import anthropic

    exc = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    Exception.__init__(
        exc,
        "Error code: 429 - {'error': {'message': 'Token quota exhausted, please contact the administrator', 'code': 'consumer_token_quota_exceeded', 'type': 'quota_exceeded'}}",
    )
    assert classify_error(exc) == ErrorKind.PAYMENT_REQUIRED


def test_classify_error_anthropic_sdk_plain_rate_limit_stays() -> None:
    """对照组：无配额信号的 anthropic RateLimitError 仍归 RATE_LIMIT（可重试）。"""
    import anthropic

    exc = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    Exception.__init__(exc, "Error code: 429 - {'error': {'message': 'rate limited'}}")
    assert classify_error(exc) == ErrorKind.RATE_LIMIT


def test_classify_error_openai_sdk_rate_limit_quota_signal() -> None:
    """openai SDK 的 RateLimitError 实例同样先按配额信号分流。"""
    import openai

    exc = openai.RateLimitError.__new__(openai.RateLimitError)
    Exception.__init__(exc, "429: Token quota exhausted")
    assert classify_error(exc) == ErrorKind.PAYMENT_REQUIRED


def test_classify_error_context_length_message() -> None:
    assert classify_error(Exception("context length exceeded")) == ErrorKind.CONTEXT_LENGTH


def test_classify_error_context_length_status_400() -> None:
    exc = _ContextLength400Error("context length is too large")
    assert classify_error(exc) == ErrorKind.CONTEXT_LENGTH


@pytest.mark.parametrize("status", [400, 404])
def test_classify_error_invalid_request(status: int) -> None:
    assert classify_error(_StatusError("bad", status_code=status)) == ErrorKind.INVALID_REQUEST


def test_classify_error_conflict_409_is_invalid_request() -> None:
    assert classify_error(_StatusError("conflict", status_code=409)) == ErrorKind.INVALID_REQUEST


def test_classify_error_model_not_found() -> None:
    assert classify_error(Exception("NotFoundError: model not found")) == ErrorKind.INVALID_REQUEST


def test_classify_error_unknown_fatal() -> None:
    assert classify_error(Exception("something weird")) == ErrorKind.FATAL


# ── Issue #26: transient network errors must not be caught by "not found" ─


@pytest.mark.parametrize("message", [
    "Connection to server not found",
    "host not found",
    "_connection not found_ while dialing upstream",
    "Temporary failure in name resolution: host not found",
])
def test_classify_error_network_not_found_is_transient(message: str) -> None:
    """A DNS/connection 'not found' is transient and retryable, not invalid."""
    assert classify_error(Exception(message)) == ErrorKind.TRANSIENT


@pytest.mark.parametrize("message", [
    "NotFoundError: model not found",
    "model not found: gpt-x",
    "The model was not found",
])
def test_classify_error_resource_not_found_still_invalid(message: str) -> None:
    """A 404-style resource 'not found' stays invalid (non-retryable)."""
    assert classify_error(Exception(message)) == ErrorKind.INVALID_REQUEST


# ---------------------------------------------------------------------------
# is_retryable
# ---------------------------------------------------------------------------


def test_is_retryable() -> None:
    assert is_retryable(ErrorKind.TRANSIENT) is True
    assert is_retryable(ErrorKind.RATE_LIMIT) is True
    assert is_retryable(ErrorKind.AUTH) is False
    assert is_retryable(ErrorKind.PAYMENT_REQUIRED) is False
    assert is_retryable(ErrorKind.CONTEXT_LENGTH) is False
    assert is_retryable(ErrorKind.INVALID_REQUEST) is False
    assert is_retryable(ErrorKind.FATAL) is False


# ---------------------------------------------------------------------------
# retry_after_seconds
# ---------------------------------------------------------------------------


def test_retry_after_seconds_header() -> None:
    response = SimpleNamespace(headers=_FakeHeaders({"Retry-After": "5"}))
    exc = SimpleNamespace(response=response)
    assert retry_after_seconds(exc) == 5.0


def test_retry_after_seconds_attribute() -> None:
    assert retry_after_seconds(SimpleNamespace(retry_after=7)) == 7.0


def test_retry_after_seconds_ms_attribute() -> None:
    assert retry_after_seconds(SimpleNamespace(retry_after_ms=2500)) == 2.5


# ── Issue #25: Retry-After HTTP-date format (RFC 7231 §7.1.1.1) ────────────


def test_retry_after_seconds_http_date_header() -> None:
    """A future HTTP-date Retry-After must yield a positive delay in seconds."""
    import datetime as _dt

    future = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=30)
    http_date = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
    response = SimpleNamespace(headers=_FakeHeaders({"Retry-After": http_date}))
    exc = SimpleNamespace(response=response)
    parsed = retry_after_seconds(exc)
    assert parsed is not None
    # Allow scheduling jitter; the server asked for ~30s, not 0.
    assert 25.0 <= parsed <= 35.0


def test_parse_retry_after_seconds_http_date_future() -> None:
    from miqi.providers.resilience import _parse_retry_after_seconds

    # Fixed RFC 7231 example. As an absolute timestamp it is in the past, so the
    # remaining delay must clamp to 0 (never a negative backoff).
    assert _parse_retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0


def test_parse_retry_after_seconds_http_date_relative() -> None:
    """Parsing is monotonic: a later target date yields a strictly larger delay."""
    import datetime as _dt

    from miqi.providers.resilience import _parse_retry_after_seconds

    soon = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=10)
    later = soon + _dt.timedelta(seconds=20)
    parse_soon = _parse_retry_after_seconds(soon.strftime("%a, %d %b %Y %H:%M:%S GMT"))
    parse_later = _parse_retry_after_seconds(later.strftime("%a, %d %b %Y %H:%M:%S GMT"))
    assert parse_soon is not None and parse_later is not None
    assert parse_later > parse_soon


def test_parse_retry_after_seconds_seconds_still_preferred() -> None:
    """Plain seconds must keep working unchanged after the HTTP-date addition."""
    from miqi.providers.resilience import _parse_retry_after_seconds

    assert _parse_retry_after_seconds("120") == 120.0
    assert _parse_retry_after_seconds("120.5") == 120.5
    assert _parse_retry_after_seconds("") is None
    assert _parse_retry_after_seconds("not a date") is None


# ---------------------------------------------------------------------------
# compute_backoff
# ---------------------------------------------------------------------------


def test_compute_backoff_with_retry_after() -> None:
    result = compute_backoff(2, retry_after=10.0, base=0.5, cap=30.0)
    assert result >= 10.0
    assert result <= 60.0


def test_compute_backoff_without_retry_after() -> None:
    first = compute_backoff(1, base=0.5, cap=30.0)
    second = compute_backoff(2, base=0.5, cap=30.0)
    third = compute_backoff(5, base=0.5, cap=30.0)

    assert first >= 0.5
    assert second >= 1.0
    assert third <= 30.0  # capped
    assert third >= 8.0   # attempt 5 base growth before cap


# ---------------------------------------------------------------------------
# with_retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_retry_cancelled_error_propagates_untouched() -> None:
    """CancelledError must propagate immediately without classification or sleep.

    Plan 58.1: with_retry catches Exception, not BaseException, so
    asyncio.CancelledError passes through cleanly.
    """
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    class _FakeCancelled(asyncio.CancelledError):
        pass

    async def factory() -> str:
        raise _FakeCancelled()

    with pytest.raises(_FakeCancelled):
        await with_retry(factory, max_attempts=3, sleep=fake_sleep)

    # CancelledError propagates on the first attempt without sleeping.
    assert len(sleeps) == 0


@pytest.mark.asyncio
async def test_with_retry_succeeds_after_failures() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    attempts = 0

    async def factory() -> str:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise Exception("connection reset")
        return "ok"

    result = await with_retry(factory, max_attempts=3, sleep=fake_sleep)
    assert result == "ok"
    assert attempts == 3
    assert len(sleeps) == 2


@pytest.mark.asyncio
async def test_with_retry_no_retry_on_auth() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def factory() -> str:
        raise _AuthError("invalid key")

    with pytest.raises(_AuthError):
        await with_retry(factory, max_attempts=3, sleep=fake_sleep)

    assert len(sleeps) == 0


@pytest.mark.asyncio
async def test_with_retry_no_retry_on_conflict_409() -> None:
    sleeps: list[float] = []
    attempts = 0

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def factory() -> str:
        nonlocal attempts
        attempts += 1
        raise _StatusError("conflict", status_code=409)

    with pytest.raises(_StatusError):
        await with_retry(factory, max_attempts=3, sleep=fake_sleep)

    assert attempts == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_with_retry_exhaustion() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    attempts = 0

    async def factory() -> str:
        nonlocal attempts
        attempts += 1
        raise Exception("connection reset")

    with pytest.raises(Exception, match="connection reset"):
        await with_retry(factory, max_attempts=3, sleep=fake_sleep)

    assert attempts == 3
    assert len(sleeps) == 2


# ---------------------------------------------------------------------------
# OpenAI provider integration
# ---------------------------------------------------------------------------


class _FakeOpenAIResponse:
    def __init__(self, content: str = "hello") -> None:
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    tool_calls=None,
                    reasoning_content=None,
                ),
                finish_reason="stop",
            )
        ]
        self.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)


class _FakeStreamChunk:
    def __init__(
        self,
        content: str = "",
        finish_reason: str | None = None,
        role: str | None = None,
        reasoning: str = "",
        tool_call: str | None = None,
    ) -> None:
        self.choices = [
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=content or None,
                    reasoning_content=reasoning or None,
                    tool_calls=(
                        [
                            SimpleNamespace(
                                index=0,
                                id="call_1",
                                function=SimpleNamespace(name=tool_call, arguments=""),
                            )
                        ]
                        if tool_call
                        else None
                    ),
                    # OpenAI-compatible streams routinely open with a role-only
                    # primer (`delta.role` set, no content at all).
                    role=role,
                ),
                finish_reason=finish_reason,
            )
        ]


class _FakeStream:
    """Async iterable yielding pre-defined chunks.

    ``hang`` stalls before the first chunk (the first-token timeout fires);
    ``hang_after`` yields the chunks and only then stalls (the idle timeout
    fires).
    """

    def __init__(
        self,
        chunks: list[_FakeStreamChunk],
        *,
        hang: bool = False,
        hang_after: bool = False,
    ) -> None:
        self._chunks = chunks
        self._index = 0
        self._hang = hang
        self._hang_after = hang_after

    def __aiter__(self) -> "_FakeStream":
        return self

    async def __anext__(self) -> _FakeStreamChunk:
        if self._index >= len(self._chunks):
            if self._hang or self._hang_after:
                await asyncio.Event().wait()
            raise StopAsyncIteration
        if self._hang:
            await asyncio.Event().wait()
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


def _make_openai_provider() -> OpenAIProvider:
    provider = OpenAIProvider(api_key="sk-test")
    return provider


def _patch_provider_sleep(monkeypatch: Any) -> list[tuple[int, ErrorKind, float]]:
    """Replace the provider module's with_retry so retries happen instantly."""
    retries: list[tuple[int, ErrorKind, float]] = []

    async def no_sleep(seconds: float) -> None:
        pass

    async def with_retry_no_sleep(
        factory,
        *,
        max_attempts: int = 3,
        sleep=None,
        on_retry=None,
    ):
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await factory()
            except BaseException as e:
                last_exc = e
                kind = resilience.classify_error(e)
                if attempt < max_attempts and resilience.is_retryable(kind):
                    delay = 0.0
                    retries.append((attempt, kind, delay))
                    if on_retry:
                        on_retry(attempt, kind, delay)
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("exhausted")

    monkeypatch.setattr("miqi.providers.openai_provider.resilience.with_retry", with_retry_no_sleep)
    monkeypatch.setattr("miqi.providers.anthropic_provider.resilience.with_retry", with_retry_no_sleep)
    return retries


@pytest.mark.asyncio
async def test_openai_chat_retries_rate_limit(monkeypatch: Any) -> None:
    _patch_provider_sleep(monkeypatch)
    provider = _make_openai_provider()
    calls: list[dict[str, Any]] = []

    responses: list[Any] = [_RateLimitError("rate limited"), _RateLimitError("rate limited"), _FakeOpenAIResponse("yay")]

    async def fake_create(**kw: Any) -> Any:
        calls.append(kw)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)),
        timeout=600.0,
    )

    response = await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert response.content == "yay"
    assert response.finish_reason == "stop"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_openai_chat_returns_error_kind_auth(monkeypatch: Any) -> None:
    _patch_provider_sleep(monkeypatch)
    provider = _make_openai_provider()

    async def fake_create(**kw: Any) -> Any:
        raise _AuthError("invalid key")

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)),
        timeout=600.0,
    )

    response = await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert response.finish_reason == "error"
    assert response.error_kind == "auth"


@pytest.mark.asyncio
async def test_openai_stream_preconnect_retry(monkeypatch: Any) -> None:
    _patch_provider_sleep(monkeypatch)
    provider = _make_openai_provider()
    calls: list[bool] = []

    stream = _FakeStream([
        _FakeStreamChunk("hel"),
        _FakeStreamChunk("lo"),
        _FakeStreamChunk("", finish_reason="stop"),
    ])

    async def fake_create(**kw: Any) -> Any:
        calls.append(True)
        if len(calls) == 1:
            raise Exception("connection reset")
        return stream

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)),
        timeout=600.0,
    )

    events = [event async for event in provider.stream_chat(messages=[{"role": "user", "content": "hi"}])]
    kinds = [e.kind for e in events]

    assert "content_delta" in kinds
    assert kinds[-1] == "completed"
    final = events[-1].response
    assert final is not None
    assert final.content == "hello"
    assert final.finish_reason == "stop"


# The two timeout branches of OpenAIProvider.stream_chat are indistinguishable
# by kind / finish_reason / error_kind — all three are identical — so the
# emitted content is the only host-side observable that says WHICH branch
# fired.  Assert on it, or a hard-coded `is_first` leaves the tests below
# green.  The literals are duplicated on purpose: the wording is user-visible
# contract, so a change in miqi/providers/openai_provider.py must be re-read
# here rather than silently absorbed.
_FIRST_TOKEN_TIMEOUT_MSG = (
    "The model did not respond within the first-token timeout. This may "
    "indicate the model is overloaded or stuck in a long reasoning phase. "
    "Please try again or use a different model."
)
_GENERIC_ERROR_MSG = "An unexpected error occurred while processing your request."


def _capture_warnings() -> tuple[list[str], Any]:
    """Return (messages, remove_handler) collecting loguru WARNING+ records.

    loguru keeps its own sinks, so pytest's caplog does not see them.
    """
    from loguru import logger as loguru_logger

    messages: list[str] = []
    handler_id = loguru_logger.add(
        lambda m: messages.append(m.record["message"]), level="WARNING"
    )
    return messages, lambda: loguru_logger.remove(handler_id)


# The two branches of the timeout gate differ on TWO axes: which budget they
# read (first-token vs idle) and which wording/log line they emit.  Asserting
# only on the wording leaves the budget axis invisible — swapping the two
# budgets keeps every assertion here green unless the delays are distinct and
# observed.  These two values must stay different for that reason; the wait
# they cause is short (0.02 s / 0.25 s), and no assertion below is on wall
# clock time.
_FIRST_TOKEN_BUDGET = 0.02
_IDLE_BUDGET = 0.25


def _record_timeouts(monkeypatch: Any) -> list[float]:
    """Record the delay handed to each `asyncio.timeout(...)` in stream_chat."""
    recorded: list[float] = []
    real_timeout = asyncio.timeout

    def recording_timeout(delay: float) -> Any:
        recorded.append(delay)
        return real_timeout(delay)

    monkeypatch.setattr(asyncio, "timeout", recording_timeout)
    return recorded


@pytest.mark.asyncio
async def test_openai_stream_first_token_timeout_yields_terminal_error(monkeypatch: Any) -> None:
    _patch_provider_sleep(monkeypatch)
    recorded = _record_timeouts(monkeypatch)
    # The stream stalls before its first chunk, so it is the FIRST-token
    # timeout that fires here; the idle one is pinned too so neither default
    # (60 s and 30 s) can turn this test into real sleeping on CI.
    provider = OpenAIProvider(
        api_key="sk-test",
        stream_idle_timeout=_IDLE_BUDGET,
        first_token_timeout=_FIRST_TOKEN_BUDGET,
    )

    async def fake_create(**kw: Any) -> Any:
        return _FakeStream([], hang=True)

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)),
        timeout=600.0,
    )

    warnings, remove_handler = _capture_warnings()
    try:
        events = [event async for event in provider.stream_chat(messages=[{"role": "user", "content": "hi"}])]
    finally:
        remove_handler()

    assert len(events) == 1
    assert events[0].kind == "completed"
    assert events[0].response.finish_reason == "error"
    assert events[0].response.error_kind == "transient"
    # ...and it was the first-token branch, not the idle one: both in wording
    # and in the budget the wait was given.
    assert events[0].response.content == _FIRST_TOKEN_TIMEOUT_MSG
    assert recorded == [_FIRST_TOKEN_BUDGET]
    assert any("LLM first-token timeout" in m for m in warnings), warnings


@pytest.mark.asyncio
async def test_openai_stream_idle_timeout_yields_terminal_error(monkeypatch: Any) -> None:
    """A stall AFTER the first chunk is the idle timeout, not the first-token one."""
    _patch_provider_sleep(monkeypatch)
    recorded = _record_timeouts(monkeypatch)
    provider = OpenAIProvider(
        api_key="sk-test",
        stream_idle_timeout=_IDLE_BUDGET,
        first_token_timeout=_FIRST_TOKEN_BUDGET,
    )

    async def fake_create(**kw: Any) -> Any:
        return _FakeStream([_FakeStreamChunk("hi")], hang_after=True)

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)),
        timeout=600.0,
    )

    warnings, remove_handler = _capture_warnings()
    try:
        events = [event async for event in provider.stream_chat(messages=[{"role": "user", "content": "hi"}])]
    finally:
        remove_handler()

    assert [event.kind for event in events] == ["content_delta", "completed"]
    assert events[-1].response.finish_reason == "error"
    assert events[-1].response.error_kind == "transient"
    # The generic wording is shared with two unrelated exception paths, so the
    # branch log line is what pins the IDLE-timeout branch specifically.
    assert events[-1].response.content == _GENERIC_ERROR_MSG
    assert events[-1].response.content != _FIRST_TOKEN_TIMEOUT_MSG
    # First wait opened the window, second wait was the idle budget.
    assert recorded == [_FIRST_TOKEN_BUDGET, _IDLE_BUDGET]
    assert any("LLM stream idle timeout" in m for m in warnings), warnings


@pytest.mark.asyncio
async def test_openai_stream_role_only_primer_keeps_first_token_window(
    monkeypatch: Any,
) -> None:
    """A role-only primer chunk carries no output — it must not end the window.

    OpenAI-compatible streams routinely open with
    ``{"choices":[{"delta":{"role":"assistant","content":""}}]}`` (OpenAI and
    DeepSeek both do).  Counting that as "the model answered" hands the wait
    for the first real token to the 30 s idle budget instead of the 60 s
    first-token one, and reports the generic error — which defeats the
    first-token timeout exactly for the buffered-reasoning providers that need
    it most (CodeRabbit finding on PR #1200).
    """
    _patch_provider_sleep(monkeypatch)
    recorded = _record_timeouts(monkeypatch)
    provider = OpenAIProvider(
        api_key="sk-test",
        stream_idle_timeout=_IDLE_BUDGET,
        first_token_timeout=_FIRST_TOKEN_BUDGET,
    )

    async def fake_create(**kw: Any) -> Any:
        return _FakeStream([_FakeStreamChunk("", role="assistant")], hang_after=True)

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)),
        timeout=600.0,
    )

    warnings, remove_handler = _capture_warnings()
    try:
        events = [event async for event in provider.stream_chat(messages=[{"role": "user", "content": "hi"}])]
    finally:
        remove_handler()

    assert [event.kind for event in events] == ["completed"]
    assert events[-1].response.content == _FIRST_TOKEN_TIMEOUT_MSG
    # The wait AFTER the primer must still be the first-token budget — this is
    # the whole point: the primer must not hand the wait to the idle budget.
    assert recorded == [_FIRST_TOKEN_BUDGET, _FIRST_TOKEN_BUDGET]
    assert any("LLM first-token timeout" in m for m in warnings), warnings


@pytest.mark.parametrize(
    ("chunk", "ends_window"),
    [
        pytest.param(_FakeStreamChunk(""), False, id="empty-delta"),
        pytest.param(_FakeStreamChunk("", role="assistant"), False, id="role-only-primer"),
        pytest.param(_FakeStreamChunk("hi"), True, id="content"),
        pytest.param(_FakeStreamChunk(reasoning="think"), True, id="reasoning"),
        pytest.param(_FakeStreamChunk(tool_call="get_weather"), True, id="tool-call-fragment"),
        pytest.param(_FakeStreamChunk(finish_reason="stop"), True, id="finish-reason-only"),
    ],
)
@pytest.mark.asyncio
async def test_openai_stream_first_token_window_closes_on_output_only(
    monkeypatch: Any, chunk: _FakeStreamChunk, ends_window: bool
) -> None:
    """Pin exactly which chunk shapes end the first-token window.

    The window stands for "the model has produced nothing yet", so it must stay
    open for a role-only primer or an empty delta — those carry no output — and
    close for content, reasoning, tool-call fragments, and a bare
    `finish_reason` (the last one because the model demonstrably answered, so a
    later stall is a stalled tail, not a missing first token).

    Asserted through the budget the NEXT wait is given, so nothing here depends
    on wall-clock time.
    """
    _patch_provider_sleep(monkeypatch)
    recorded = _record_timeouts(monkeypatch)
    provider = OpenAIProvider(
        api_key="sk-test",
        stream_idle_timeout=_IDLE_BUDGET,
        first_token_timeout=_FIRST_TOKEN_BUDGET,
    )

    async def fake_create(**kw: Any) -> Any:
        return _FakeStream([chunk], hang_after=True)

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)),
        timeout=600.0,
    )

    events = [event async for event in provider.stream_chat(messages=[{"role": "user", "content": "hi"}])]

    assert recorded == [
        _FIRST_TOKEN_BUDGET,
        _IDLE_BUDGET if ends_window else _FIRST_TOKEN_BUDGET,
    ]
    assert events[-1].kind == "completed"
    assert events[-1].response.finish_reason == "error"


# ---------------------------------------------------------------------------
# Timeout knobs: an explicit 0.0 is a value, not "unset" (PR #1200)
# ---------------------------------------------------------------------------


def test_openai_provider_preserves_explicit_zero_first_token_timeout() -> None:
    """`first_token_timeout=0.0` means "do not wait at all" and must survive.

    PR #1200 replaced `first_token_timeout or DEFAULT_FIRST_TOKEN_TIMEOUT` with
    an explicit `is None` check for exactly this reason; that behaviour was
    only ever verified by hand, so pin it here.
    """
    provider = OpenAIProvider(api_key="sk-test", first_token_timeout=0.0)
    assert provider._first_token_timeout == 0.0


def test_openai_provider_preserves_explicit_zero_stream_idle_timeout() -> None:
    """Same contract for the sibling knob, which had the identical `or` trap."""
    provider = OpenAIProvider(api_key="sk-test", stream_idle_timeout=0.0)
    assert provider._stream_idle_timeout == 0.0


def test_openai_provider_applies_timeout_defaults_when_unset() -> None:
    """`None` — the documented "unset" — remains the only value that defaults."""
    provider = OpenAIProvider(api_key="sk-test")
    assert provider._first_token_timeout == DEFAULT_FIRST_TOKEN_TIMEOUT
    assert provider._stream_idle_timeout == DEFAULT_STREAM_IDLE_TIMEOUT


# ---------------------------------------------------------------------------
# Anthropic provider integration
# ---------------------------------------------------------------------------


class _FakeAnthropicResponse:
    def __init__(self, content: str = "hello") -> None:
        self.content = [SimpleNamespace(type="text", text=content)]
        self.stop_reason = "end_turn"
        self.usage = SimpleNamespace(input_tokens=1, output_tokens=1)


@pytest.mark.asyncio
async def test_anthropic_chat_retries_rate_limit(monkeypatch: Any) -> None:
    _patch_provider_sleep(monkeypatch)
    provider = AnthropicProvider(api_key="sk-test")
    calls: list[dict[str, Any]] = []

    responses: list[Any] = [_RateLimitError("rate limited"), _RateLimitError("rate limited"), _FakeAnthropicResponse("yay")]

    async def fake_create(**kw: Any) -> Any:
        calls.append(kw)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    provider._client = SimpleNamespace(messages=SimpleNamespace(create=fake_create), timeout=600.0)

    response = await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert response.content == "yay"
    assert response.finish_reason == "stop"
    assert len(calls) == 3


# ---------------------------------------------------------------------------
# Base response
# ---------------------------------------------------------------------------


def test_llm_response_error_kind_default() -> None:
    response = LLMResponse(content="hi")
    assert response.error_kind is None


def test_openai_request_timeout_set() -> None:
    provider = OpenAIProvider(api_key="sk-test")
    assert isinstance(provider._client.timeout, (int, float))
    assert provider._client.timeout > 0


def test_anthropic_request_timeout_set() -> None:
    provider = AnthropicProvider(api_key="sk-test")
    assert isinstance(provider._client.timeout, (int, float))
    assert provider._client.timeout > 0


# ---------------------------------------------------------------------------
# ProviderError (Plan 57)
# ---------------------------------------------------------------------------


def test_provider_error_exposes_kind_message_and_str() -> None:
    err = ProviderError(kind=ErrorKind.RATE_LIMIT, message="slow down")
    assert err.kind is ErrorKind.RATE_LIMIT
    assert err.message == "slow down"
    assert "slow down" in str(err)


def test_provider_error_recoverable_true_for_retryable_kinds() -> None:
    """recoverable mirrors is_retryable: True for TRANSIENT and RATE_LIMIT."""
    assert ProviderError(kind=ErrorKind.RATE_LIMIT, message="x").recoverable is True
    assert ProviderError(kind=ErrorKind.TRANSIENT, message="x").recoverable is True


def test_provider_error_recoverable_false_for_non_retryable_kinds() -> None:
    assert ProviderError(kind=ErrorKind.AUTH, message="x").recoverable is False
    assert ProviderError(kind=ErrorKind.PAYMENT_REQUIRED, message="x").recoverable is False
    assert ProviderError(kind=ErrorKind.CONTEXT_LENGTH, message="x").recoverable is False
    assert (
        ProviderError(kind=ErrorKind.INVALID_REQUEST, message="x").recoverable is False
    )
    assert ProviderError(kind=ErrorKind.FATAL, message="x").recoverable is False


def test_provider_error_recovers_matches_is_retryable() -> None:
    """ProviderError.recoverable must be consistent with is_retryable(kind)."""
    for kind in ErrorKind:
        err = ProviderError(kind=kind, message="m")
        assert err.recoverable is is_retryable(kind)


def test_provider_error_is_an_exception() -> None:
    err = ProviderError(kind=ErrorKind.AUTH, message="bad key")
    assert isinstance(err, Exception)
    with pytest.raises(ProviderError):
        raise err


# ---------------------------------------------------------------------------
# Issue #529: _classify_chain (TaskRunner final-boundary re-classification)
# ---------------------------------------------------------------------------


def test_classify_chain_returns_direct_classification_when_not_fatal() -> None:
    """A directly-classifiable TRANSIENT error is returned without walking
    the cause chain."""
    from miqi.runtime.task_runner import _classify_chain

    assert _classify_chain(Exception("503 service unavailable")) == ErrorKind.TRANSIENT
    assert _classify_chain(_RateLimitError("rate limited")) == ErrorKind.RATE_LIMIT


def test_classify_chain_falls_back_to_cause_when_outer_is_fatal() -> None:
    """A FATAL wrapper whose __cause__ carries a TRANSIENT SDK error unwraps
    to the cause's kind — the leaked metadata is recovered at the boundary."""
    from miqi.runtime.task_runner import _classify_chain

    wrapped = RuntimeError("processing failed")
    wrapped.__cause__ = Exception("503 service unavailable")
    assert _classify_chain(wrapped) == ErrorKind.TRANSIENT


def test_classify_chain_uses_context_when_no_cause() -> None:
    """Implicit __context__ (from a bare raise inside an except) is also
    walked when __cause__ is absent and the outer is FATAL."""
    from miqi.runtime.task_runner import _classify_chain

    wrapped = RuntimeError("processing failed")
    wrapped.__context__ = Exception("overloaded")
    assert _classify_chain(wrapped) == ErrorKind.TRANSIENT


def test_classify_chain_stays_fatal_when_cause_is_also_fatal() -> None:
    """If the cause chain yields no better classification, FATAL is kept
    (no spurious recoverability)."""
    from miqi.runtime.task_runner import _classify_chain

    wrapped = RuntimeError("processing failed")
    wrapped.__cause__ = Exception("something unrelated")
    assert _classify_chain(wrapped) == ErrorKind.FATAL


def test_classify_chain_ignores_self_referential_cause() -> None:
    """A pathological self-referential __cause__ must not loop / misclassify."""
    from miqi.runtime.task_runner import _classify_chain

    wrapped = RuntimeError("processing failed")
    wrapped.__cause__ = wrapped
    assert _classify_chain(wrapped) == ErrorKind.FATAL

