from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import TypeVar

T = TypeVar("T")


class ErrorKind(str, Enum):
    TRANSIENT = "transient"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    PAYMENT_REQUIRED = "payment_required"
    CONTENT_BLOCKED = "content_blocked"
    CONTEXT_LENGTH = "context_length"
    INVALID_REQUEST = "invalid_request"
    FATAL = "fatal"


_RETRYABLE = {ErrorKind.TRANSIENT, ErrorKind.RATE_LIMIT}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _status_code(exc: BaseException) -> int | None:
    """Read status_code from exception or its response, then scan message for 3-digit code."""
    # Direct attribute
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code

    # Wrapped response attribute
    response = getattr(exc, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
        code = getattr(response, "status", None)
        if isinstance(code, int):
            return code

    # Fallback: scan message for a 3-digit HTTP code
    message = str(exc).lower()
    matches = re.findall(r"\b(\d{3})\b", message)
    for m in matches:
        code = int(m)
        if 400 <= code < 600:
            return code
    return None


def _header_value(exc: BaseException, name: str) -> str | None:
    """Return a header value from the exception or its response, case-insensitively."""
    exc_headers = getattr(exc, "headers", None)
    if exc_headers is not None:
        if hasattr(exc_headers, "get"):
            try:
                value = exc_headers.get(name)
                if value is not None:
                    return str(value)
            except Exception:
                pass
        if isinstance(exc_headers, dict):
            key_lower = name.lower()
            for k, v in exc_headers.items():
                if k.lower() == key_lower:
                    return str(v)

    response = getattr(exc, "response", None)
    if response is not None:
        response_headers = getattr(response, "headers", None)
        if response_headers is not None:
            if hasattr(response_headers, "get"):
                try:
                    value = response_headers.get(name)
                    if value is not None:
                        return str(value)
                except Exception:
                    pass
            if isinstance(response_headers, dict):
                key_lower = name.lower()
                for k, v in response_headers.items():
                    if k.lower() == key_lower:
                        return str(v)
    return None


def _classify_by_message(exc: BaseException) -> ErrorKind | None:
    """Classify by substrings for generic exceptions without SDK type/status code."""
    message = str(exc).lower()

    if "rate limit" in message or "too many requests" in message:
        return ErrorKind.RATE_LIMIT
    # Issue #528: check billing/quota before AUTH — a 402 body that mentions
    # "forbidden"-ish wording would otherwise be misread as authentication.
    if _is_payment_required_error(exc):
        return ErrorKind.PAYMENT_REQUIRED
    # 平台内容安全拦截同样带 "forbidden"/403 特征（网关实测 403 + security_violation），
    # 必须在 AUTH 之前判定，否则用户被引导去改 API Key（真因是内容被拦）。
    if _is_content_policy_error(exc):
        return ErrorKind.CONTENT_BLOCKED
    if "invalid api key" in message or "unauthorized" in message or "forbidden" in message:
        return ErrorKind.AUTH
    if _is_context_length_error(exc):
        return ErrorKind.CONTEXT_LENGTH
    if "not found" in message or "bad request" in message or "invalid request" in message:
        return ErrorKind.INVALID_REQUEST

    return None


def _is_context_length_error(exc: BaseException) -> bool:
    """Detect context-length / token-limit errors from message text."""
    message = str(exc).lower()
    signals = (
        "context length",
        "context_length",
        "token limit",
        "too many tokens",
        "context window",
        "max_tokens",
        "maximum context",
    )
    return any(s in message for s in signals)


# Issue #528: signal phrases across providers for 402 / balance / quota errors.
# Distinct from AUTH — identity is accepted but the account has no
# balance/quota, so it gets its own non-retryable kind. Covers OpenAI
# ("exceeded your current quota"), Anthropic ("credit balance is too low"),
# and gateway/proxy wording ("insufficient balance", "payment required").
_PAYMENT_REQUIRED_SIGNALS = (
    "insufficient balance",
    "insufficient quota",
    "payment required",
    "payment_required",
    "balance exceeded",
    "quota exceeded",
    "quota exhausted",
    "credit exhausted",
    "out of credits",
    "credit balance is too low",
    "exceeded your current quota",
    # CodeRabbit (#528): no bare "billing" — too broad. It misclassified
    # AUTH-style messages like "Forbidden: billing access denied" as
    # PAYMENT_REQUIRED (checked before the AUTH branch). Only balance/quota-
    # specific phrases remain; a true 402 is still caught by the
    # status-code layer (402 → PAYMENT_REQUIRED) regardless of wording.
)


def _is_payment_required_error(exc: BaseException) -> bool:
    """Detect 402/balance/quota exhaustion from message text."""
    message = str(exc).lower()
    return any(s in message for s in _PAYMENT_REQUIRED_SIGNALS)


# 内容安全/审核拦截的信号词。平台 AI 网关实测返回
# ``403 {"code": "sensitive_word_detected", "type": "security_violation"}``
# —— 403 在 AUTH 之前必须让位给本判定，否则用户看到「模型服务认证失败，
# 请检查 API Key」并被引导去重选模型，而真因是提示词/附件触发了平台的
# 敏感词过滤（改密钥永远修不好）。
# 只收录审核专用词，不含宽泛的 "forbidden"：真正的 403 权限拒绝仍归 AUTH
#（Azure 的 content_filter / OpenAI 的 content_policy_violation 一并覆盖）。
_CONTENT_POLICY_SIGNALS = (
    "sensitive_word_detected",
    "security_violation",
    "content_filter",
    "content_policy",
    "content policy violation",
)


def _is_content_policy_error(exc: BaseException) -> bool:
    """Detect provider/gateway content-moderation rejections from message text."""
    message = str(exc).lower()
    return any(s in message for s in _CONTENT_POLICY_SIGNALS)


def _classify_by_status_code(exc: BaseException) -> ErrorKind | None:
    """Classify a retry error by HTTP status code."""
    code = _status_code(exc)
    if code is None:
        return None

    if code == 429:
        return ErrorKind.RATE_LIMIT
    if code in (401, 403):
        # 平台网关的内容安全拦截同样是 403：先按审核信号分流，否则会被当成
        # 认证失败（用户被引导去改密钥/换模型，真因是内容被拦）。
        if _is_content_policy_error(exc):
            return ErrorKind.CONTENT_BLOCKED
        return ErrorKind.AUTH
    if code == 402:
        # Issue #528: Payment Required — balance/quota exhausted. Distinct
        # from AUTH and non-retryable (retrying won't add balance).
        return ErrorKind.PAYMENT_REQUIRED
    if code == 408 or 500 <= code < 600:
        return ErrorKind.TRANSIENT
    if code == 409:
        return ErrorKind.INVALID_REQUEST
    if code in (400, 404):
        if _is_context_length_error(exc):
            return ErrorKind.CONTEXT_LENGTH
        return ErrorKind.INVALID_REQUEST
    if code == 413:
        if _is_context_length_error(exc):
            return ErrorKind.CONTEXT_LENGTH
        return ErrorKind.INVALID_REQUEST
    return None


def _classify_transient_by_message(exc: BaseException) -> bool:
    """Match the union of transient signal keywords from the previous provider paths."""
    message = str(exc).lower()
    signals = (
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
        # Issue #26: DNS / connectivity "not found" phrasing is transient, not
        # a 404-style invalid request.
        "host not found",
        "server not found",
        "name resolution",
    )
    if any(s in message for s in signals):
        return True
    # "connection ... not found" with arbitrary words/separators in between,
    # e.g. "Connection to server not found", "_connection not found_".
    return bool(re.search(r"connection.{0,40}not found", message))


def classify_error(exc: BaseException) -> ErrorKind:
    """Classify an exception using SDK types (defensively imported) plus a string-match fallback."""
    # Defensive SDK type mapping.
    try:
        import openai

        api_connection_error = getattr(openai, "APIConnectionError", ())
        api_timeout_error = getattr(openai, "APITimeoutError", ())
        timeout_error = getattr(openai, "Timeout", ())
        rate_limit_error = getattr(openai, "RateLimitError", ())
        authentication_error = getattr(openai, "AuthenticationError", ())
        permission_denied_error = getattr(openai, "PermissionDeniedError", ())
        not_found_error = getattr(openai, "NotFoundError", ())
        bad_request_error = getattr(openai, "BadRequestError", ())
        conflict_error = getattr(openai, "ConflictError", ())
        internal_server_error = getattr(openai, "InternalServerError", ())
        api_status_error = getattr(openai, "APIStatusError", ())

        if isinstance(exc, api_connection_error):
            if getattr(exc, "status_code", None) == 429:
                return ErrorKind.RATE_LIMIT
            return ErrorKind.TRANSIENT
        if isinstance(exc, (api_timeout_error, timeout_error)):
            return ErrorKind.TRANSIENT
        if isinstance(exc, rate_limit_error):
            return ErrorKind.RATE_LIMIT
        if isinstance(exc, (authentication_error, permission_denied_error)):
            # PermissionDeniedError 也承载内容审核 403（网关实测）：审核信号
            # 优先，避免把内容拦截报成认证失败。
            if _is_content_policy_error(exc):
                return ErrorKind.CONTENT_BLOCKED
            return ErrorKind.AUTH
        if isinstance(exc, not_found_error):
            if _is_context_length_error(exc):
                return ErrorKind.CONTEXT_LENGTH
            return ErrorKind.INVALID_REQUEST
        if isinstance(exc, bad_request_error):
            if _is_context_length_error(exc):
                return ErrorKind.CONTEXT_LENGTH
            by_code = _classify_by_status_code(exc)
            if by_code is not None:
                return by_code
            return ErrorKind.INVALID_REQUEST
        if isinstance(exc, conflict_error):
            return ErrorKind.INVALID_REQUEST
        if isinstance(exc, internal_server_error):
            return ErrorKind.TRANSIENT
        if isinstance(exc, api_status_error):
            by_code = _classify_by_status_code(exc)
            if by_code is not None:
                return by_code
    except ImportError:
        pass

    # httpx is the shared transport under openai/anthropic SDKs. Mid-stream
    # connection drops surface as raw httpx errors (ReadError etc.) with empty
    # messages that string-matching can't classify — treat them as TRANSIENT so
    # with_retry re-runs instead of surfacing a generic "internal error"
    # (observed: 22-tool turn died on httpx.ReadError with no retry).
    try:
        import httpx

        # httpx 0.28 hierarchy: ReadError→NetworkError→TransportError→…,
        # timeouts share TimeoutException. Both cover the connect/read/write
        # failure modes of a streaming LLM call.
        if isinstance(exc, (httpx.NetworkError, httpx.TimeoutException)):
            return ErrorKind.TRANSIENT
    except (ImportError, AttributeError):
        pass

    try:
        import anthropic

        api_connection_error = getattr(anthropic, "APIConnectionError", ())
        api_timeout_error = getattr(anthropic, "APITimeoutError", ())
        timeout_error = getattr(anthropic, "Timeout", ())
        rate_limit_error = getattr(anthropic, "RateLimitError", ())
        authentication_error = getattr(anthropic, "AuthenticationError", ())
        permission_denied_error = getattr(anthropic, "PermissionDeniedError", ())
        not_found_error = getattr(anthropic, "NotFoundError", ())
        bad_request_error = getattr(anthropic, "BadRequestError", ())
        overloaded_error = getattr(anthropic, "OverloadedError", ())
        internal_server_error = getattr(anthropic, "InternalServerError", ())

        if isinstance(exc, api_connection_error):
            return ErrorKind.TRANSIENT
        if isinstance(exc, (api_timeout_error, timeout_error)):
            return ErrorKind.TRANSIENT
        if isinstance(exc, rate_limit_error):
            return ErrorKind.RATE_LIMIT
        if isinstance(exc, (authentication_error, permission_denied_error)):
            # PermissionDeniedError 也承载内容审核 403（网关实测）：审核信号
            # 优先，避免把内容拦截报成认证失败。
            if _is_content_policy_error(exc):
                return ErrorKind.CONTENT_BLOCKED
            return ErrorKind.AUTH
        if isinstance(exc, not_found_error):
            if _is_context_length_error(exc):
                return ErrorKind.CONTEXT_LENGTH
            return ErrorKind.INVALID_REQUEST
        if isinstance(exc, bad_request_error):
            if _is_context_length_error(exc):
                return ErrorKind.CONTEXT_LENGTH
            by_code = _classify_by_status_code(exc)
            if by_code is not None:
                return by_code
            return ErrorKind.INVALID_REQUEST
        if isinstance(exc, overloaded_error):
            return ErrorKind.TRANSIENT
        if isinstance(exc, internal_server_error):
            return ErrorKind.TRANSIENT
    except ImportError:
        pass

    # Status-code fallback
    by_code = _classify_by_status_code(exc)
    if by_code is not None:
        return by_code

    # Transient keyword fallback (union of provider signal lists).
    if _classify_transient_by_message(exc):
        return ErrorKind.TRANSIENT

    by_message = _classify_by_message(exc)
    if by_message is not None:
        return by_message

    return ErrorKind.FATAL


def is_retryable(kind: ErrorKind) -> bool:
    return kind in _RETRYABLE


class ProviderError(Exception):
    """A terminal provider failure carrying a classified ``ErrorKind``.

    Raised by the runtime when a provider response reports
    ``finish_reason == "error"`` (e.g. after plan/56 retries are exhausted).
    Carries the provider's error category so callers (TaskRunner) can
    surface a useful, user-actionable message and recoverability flag
    without re-inspecting SDK/HTTP details.
    """

    def __init__(self, *, kind: ErrorKind, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message

    @property
    def recoverable(self) -> bool:
        return is_retryable(self.kind)


def retry_after_seconds(exc: BaseException) -> float | None:
    """Parse Retry-After header/attribute/response value.

    Check, in order:
    - exc.response.headers.get("Retry-After") or retry-after / retry_after attr
    - exc.headers if present
    - exc.retry_after or exc.retry_after_ms
    Return float seconds, None if missing/unparseable.
    """
    # Header first (canonical and retry-after variants)
    for header_name in ("Retry-After", "retry-after", "retry_after"):
        value = _header_value(exc, header_name)
        if value:
            parsed = _parse_retry_after_seconds(value)
            if parsed is not None:
                return parsed

    # Direct retry_after attribute (seconds)
    retry_after_attr = getattr(exc, "retry_after", None)
    if retry_after_attr is not None:
        parsed = _parse_retry_after_seconds(str(retry_after_attr))
        if parsed is not None:
            return parsed

    # Direct retry_after_ms attribute (milliseconds)
    retry_after_ms = getattr(exc, "retry_after_ms", None)
    if retry_after_ms is not None:
        try:
            return float(int(retry_after_ms)) / 1000.0
        except (TypeError, ValueError):
            pass

    return None


def _parse_retry_after_seconds(value: str) -> float | None:
    """Parse a Retry-After value expressed in seconds or as an HTTP-date.

    RFC 7231 §7.1.1.1 allows Retry-After to be either an integer number of
    seconds ("120") or an HTTP-date ("Wed, 21 Oct 2015 07:28:00 GMT"). The
    date form is an absolute timestamp; the returned delay is the remaining
    seconds until that moment, clamped to 0 (a date already in the past must
    never produce a negative backoff).
    """
    value = value.strip()
    if not value:
        return None
    try:
        return float(int(value))
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass

    # HTTP-date form (RFC 7231 §7.1.1.1). parsedate_to_datetime returns a
    # naive datetime for dates without tz info; assume GMT per the spec.
    try:
        parsed_date = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed_date is None:
        return None
    if parsed_date.tzinfo is None:
        parsed_date = parsed_date.replace(tzinfo=timezone.utc)
    delay = (parsed_date - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delay)


def compute_backoff(
    attempt: int,
    *,
    retry_after: float | None = None,
    base: float = 0.5,
    cap: float = 30.0,
) -> float:
    """Compute backoff delay in seconds.

    If retry_after set: min(cap*2, retry_after + random()*base).
    Else: min(cap, (2 ** (attempt - 1)) * (base + random() * base)).
    attempt is 1-indexed.
    """
    attempt = max(1, int(attempt))
    if retry_after is not None and retry_after >= 0:
        return min(cap * 2, retry_after + random.random() * base)
    return min(cap, (2 ** (attempt - 1)) * (base + random.random() * base))


async def with_retry(
    factory: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_retry: Callable[[int, ErrorKind, float], None] | None = None,
) -> T:
    """Call factory repeatedly until success or non-retryable/exhausted.

    - On exception, classify it.
    - If attempt < max_attempts and is_retryable(kind): compute delay,
      call on_retry(attempt, kind, delay), await sleep(delay), continue.
    - Else raise the last exception.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await factory()
        except Exception as e:
            last_exc = e
            kind = classify_error(e)
            if attempt < max_attempts and is_retryable(kind):
                retry_after = retry_after_seconds(e)
                delay = compute_backoff(attempt, retry_after=retry_after)
                if on_retry is not None:
                    on_retry(attempt, kind, delay)
                await sleep(delay)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("with_retry exhausted without exception")
