"""OpenAI-compatible provider — covers OpenAI, DeepSeek, Moonshot, Zhipu, DashScope,
MiniMax, Groq, SiliconFlow, VolcEngine, AiHubMix, OpenRouter, vLLM, Ollama, and any
other endpoint that speaks the OpenAI chat-completions API.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import json_repair
from loguru import logger
from openai import AsyncOpenAI

import miqi.providers.resilience as resilience
from miqi.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from miqi.providers.registry import find_by_model, find_by_name, find_gateway
from miqi.providers.resilience import ErrorKind

DEFAULT_REQUEST_TIMEOUT = 120.0
DEFAULT_FIRST_TOKEN_TIMEOUT = 60.0
DEFAULT_STREAM_IDLE_TIMEOUT = 30.0

# Standard OpenAI chat-completion message keys; extras (e.g. reasoning_content) are
# stripped for providers that reject unknown fields.
_ALLOWED_MSG_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name"})


class OpenAIProvider(LLMProvider):
    """
    Provider for any OpenAI-compatible endpoint.

    Routing is driven entirely by registry metadata (providers/registry.py).
    No litellm dependency — uses openai.AsyncOpenAI directly.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "gpt-4o",
        extra_headers: dict[str, str] | None = None,
        provider_name: str | None = None,
        request_timeout: float | None = None,
        stream_idle_timeout: float | None = None,
    ):
        self._selected_spec = find_by_name(provider_name) if provider_name else None
        self._gateway = find_gateway(provider_name, api_key, api_base)

        api_base = self._normalize_api_base(api_base)

        # Resolve effective api_base: user value → spec default
        effective_base = api_base or self._default_api_base()

        super().__init__(api_key, effective_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self._stream_idle_timeout = stream_idle_timeout or DEFAULT_STREAM_IDLE_TIMEOUT

        if api_key:
            self._setup_env(api_key, api_base)

        self._client = AsyncOpenAI(
            api_key=api_key or "no-key",
            base_url=effective_base or None,
            default_headers=self.extra_headers,
            timeout=request_timeout or DEFAULT_REQUEST_TIMEOUT,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _default_api_base(self) -> str | None:
        """Return the spec's default api_base, if any."""
        spec = self._gateway or self._selected_spec
        return spec.default_api_base if spec and spec.default_api_base else None

    def _normalize_api_base(self, api_base: str | None) -> str | None:
        """Normalize provider-specific base URLs."""
        if not api_base:
            return api_base

        api_base = api_base.strip()
        spec = self._gateway or self._selected_spec

        if spec and spec.name in {"ollama_local", "ollama_cloud"}:
            # openai SDK needs /v1 suffix; litellm used the bare host.
            # Strip /api suffix and ensure /v1.
            for suffix in ("/api/", "/api"):
                if api_base.endswith(suffix):
                    api_base = api_base[: -len(suffix)]
                    break
            if not api_base.rstrip("/").endswith("/v1"):
                api_base = api_base.rstrip("/") + "/v1"
            return api_base

        if spec and spec.default_api_base:
            api_base = self._fill_default_base_path(api_base, spec.default_api_base)

        return api_base

    @staticmethod
    def _fill_default_base_path(api_base: str, default_api_base: str) -> str:
        """Fill missing path portion from the spec default.

        Example: user provides 'https://api.moonshot.cn', default is
        'https://api.moonshot.ai/v1' → fills to 'https://api.moonshot.cn/v1'.
        """
        parsed_api = urlparse(api_base)
        parsed_default = urlparse(default_api_base)

        if not parsed_api.scheme or not parsed_api.netloc:
            return api_base
        if parsed_api.path not in {"", "/"}:
            return api_base

        default_path = parsed_default.path.rstrip("/")
        if not default_path:
            return api_base

        return urlunparse(parsed_api._replace(path=default_path))

    def _setup_env(self, api_key: str, api_base: str | None) -> None:
        """Set the primary API-key environment variable if the spec defines one."""
        spec = self._gateway or self._selected_spec
        if not spec or not spec.env_key:
            return
        # Use setdefault for both paths to avoid overwriting already-set
        # keys from other sessions using different API keys.
        os.environ.setdefault(spec.env_key, api_key)

    def _resolve_model(self, model: str) -> str:
        """Strip provider prefix so the downstream API receives the bare model name.

        Rules:
        - Gateway with strip_model_prefix=True (e.g. AiHubMix): keep only the
          last segment ('anthropic/claude-3' → 'claude-3').
        - Gateway without strip (e.g. OpenRouter): strip the gateway's own prefix
          only ('openrouter/anthropic/claude-3' → 'anthropic/claude-3').
        - Standard/local provider: strip model_prefix/ if present
          ('deepseek/deepseek-chat' → 'deepseek-chat').
        """
        if self._gateway:
            if self._gateway.strip_model_prefix:
                return model.split("/")[-1]
            prefix = self._gateway.model_prefix
            if prefix and model.startswith(f"{prefix}/"):
                return model[len(prefix) + 1:]
            return model

        spec = self._selected_spec or find_by_model(model)
        if spec:
            for candidate in (spec.model_prefix, spec.name):
                if candidate and model.startswith(f"{candidate}/"):
                    return model[len(candidate) + 1:]

        return model

    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """Apply per-model parameter overrides from the registry (e.g. kimi-k2.5 temperature)."""
        model_lower = model.lower()
        spec = self._selected_spec or find_by_model(model)
        if spec:
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    return

    def _sanitize_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        keep_reasoning: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip non-standard keys; optionally keep reasoning_content for DeepSeek R1."""
        allowed = _ALLOWED_MSG_KEYS | {"reasoning_content"} if keep_reasoning else _ALLOWED_MSG_KEYS
        sanitized = []
        for msg in messages:
            clean = {k: v for k, v in msg.items() if k in allowed}
            if clean.get("role") == "assistant" and "content" not in clean:
                clean["content"] = None
            # DeepSeek thinking 模式：assistant 消息（含工具调用轮）必须带
            # reasoning_content 键，缺失/null 会 400（"must be passed back"），
            # 空字符串可接受。模型某些轮次不输出 reasoning 时补空串；显式
            # null 同样被拒，setdefault 不覆盖已有键所以要显式替换
            # （实测：缺键→400，""→OK，null→400；CodeRabbit #761）。
            if keep_reasoning and clean.get("role") == "assistant":
                if clean.get("reasoning_content") is None:
                    clean["reasoning_content"] = ""
            sanitized.append(clean)
        return sanitized

    def _is_transient_network_error(self, error: Exception) -> bool:
        """Return True for retryable transient errors."""
        return resilience.classify_error(error) == ErrorKind.TRANSIENT

    def _parse_tool_call_arguments(self, tool_name: str, args: Any) -> dict[str, Any]:
        """Parse tool-call arguments with json_repair fallback."""
        if not isinstance(args, str):
            return args if isinstance(args, dict) else {}

        if not args:
            return {}

        repaired = json_repair.loads(args)
        try:
            parsed = json.loads(args)
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "json_repair fixed malformed tool args for '{}': {}",
                tool_name,
                args[:200],
            )
            parsed = repaired

        if isinstance(parsed, dict):
            return parsed
        return repaired if isinstance(repaired, dict) else {}

    # ------------------------------------------------------------------
    # Main interface
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        original_model = model or self.default_model
        resolved = self._resolve_model(original_model)
        max_tokens = max(1, max_tokens)

        spec = self._selected_spec or find_by_model(original_model)
        keep_reasoning = bool(spec and spec.supports_reasoning_history)

        kwargs: dict[str, Any] = {
            "model": resolved,
            "messages": self._sanitize_messages(
                self._sanitize_empty_content(messages),
                keep_reasoning=keep_reasoning,
            ),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # DeepSeek V4 Flash / V4 Pro require thinking mode to emit
        # reasoning_content. The thinking parameter is non-standard so
        # we pass it via extra_body to avoid the OpenAI SDK rejecting
        # unknown params.
        if keep_reasoning:
            kwargs.setdefault("extra_body", {})
            kwargs["extra_body"].setdefault("thinking", {"type": "enabled"})

        self._apply_model_overrides(resolved, kwargs)

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = await resilience.with_retry(
                lambda: self._client.chat.completions.create(**kwargs),
                max_attempts=3,
            )
            return self._parse_response(response)
        except Exception as e:
            kind = resilience.classify_error(e)
            return LLMResponse(
                content=f"Error calling LLM: {e}",
                finish_reason="error",
                error_kind=kind.value,
            )

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse an OpenAI-compatible response object into LLMResponse."""
        if not response.choices:
            return LLMResponse(content=None, finish_reason="stop")
        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCallRequest] = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=self._parse_tool_call_arguments(
                        tc.function.name,
                        tc.function.arguments,
                    ),
                ))

        if not tool_calls and isinstance(message.content, str):
            fallback = self._parse_tool_call_from_content(message.content)
            if fallback:
                tool_calls.append(fallback)

        usage: dict[str, int] = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        reasoning_content = getattr(message, "reasoning_content", None) or None
        if reasoning_content:
            logger.info(
                "chat: got reasoning len={}",
                len(reasoning_content),
            )
        else:
            logger.debug("chat: no reasoning_content in response")

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=reasoning_content,
        )

    def _parse_tool_call_from_content(self, content: str) -> ToolCallRequest | None:
        """Best-effort parser for models that emit tool calls as plain JSON."""
        decoder = json.JSONDecoder()
        for idx, char in enumerate(content):
            if char != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(content[idx:])
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue

            # Format A: {"name": "...", "arguments": {...}}
            name = obj.get("name")
            arguments = obj.get("arguments")
            if isinstance(name, str) and isinstance(arguments, dict):
                return ToolCallRequest(id="tool_call_fallback_1", name=name, arguments=arguments)

            # Format B: {"function": {"name": "...", "arguments": {...}}}
            function = obj.get("function")
            if isinstance(function, dict):
                func_name = function.get("name")
                func_args = function.get("arguments")
                if isinstance(func_name, str):
                    if isinstance(func_args, str):
                        try:
                            func_args = json.loads(func_args)
                        except Exception:
                            func_args = {"raw": func_args}
                    if isinstance(func_args, dict):
                        return ToolCallRequest(
                            id="tool_call_fallback_1", name=func_name, arguments=func_args
                        )

        return None

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ):
        """OpenAI-compatible streaming chat.

        Yields content_delta for text, reasoning_delta for thinking
        content, and completed with the full LLMResponse.  On error
        yields a completed event with finish_reason="error" so the
        runtime always receives a terminal event.
        """
        from miqi.providers.base import LLMResponse, LLMStreamEvent, ToolCallRequest

        original_model = model or self.default_model
        resolved = self._resolve_model(original_model)
        max_tokens = max(1, max_tokens)

        spec = self._selected_spec or find_by_model(original_model)
        keep_reasoning = bool(spec and spec.supports_reasoning_history)
        # #834 / CodeRabbit: models that STREAM reasoning CoT (Kimi/Qwen/GPT-5)
        # invalidate the request→first-delta thinking proxy up front — do not
        # wait for interleaving to show up in the delta order (a reasoning-first
        # stream would otherwise slip through the content_parts check).
        streams_reasoning = bool(spec and spec.streams_reasoning)

        kwargs: dict[str, Any] = {
            "model": resolved,
            "messages": self._sanitize_messages(
                self._sanitize_empty_content(messages),
                keep_reasoning=keep_reasoning,
            ),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        # stream_options with include_usage is OpenAI-specific — gateways
        # (OpenRouter, etc.) and compatible providers (DeepSeek, Moonshot)
        # reject it with 400.
        if self._gateway is None:
            kwargs["stream_options"] = {"include_usage": True}

        # DeepSeek V4 Flash / V4 Pro require thinking mode to emit
        # reasoning_content.
        if keep_reasoning:
            kwargs.setdefault("extra_body", {})
            kwargs["extra_body"].setdefault("thinking", {"type": "enabled"})

        self._apply_model_overrides(resolved, kwargs)

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            request_started = time.monotonic()

            def _do_create() -> Any:
                """Per-attempt create wrapper: restarts the timing window on retry (#834)."""
                nonlocal request_started
                # Per-attempt start: a retry after a timeout still measures
                # the attempt that actually produced the reasoning stream.
                request_started = time.monotonic()
                return self._client.chat.completions.create(**kwargs)

            stream = await resilience.with_retry(
                _do_create,
                max_attempts=3,
            )
        except Exception as e:
            kind = resilience.classify_error(e)
            # loguru 使用 {} 占位；异常消息 + 分类写进日志行，避免堆栈被吞
            logger.exception(
                "LLM streaming error for model {}: {} (kind={})",
                resolved, e, kind.value,
            )
            yield LLMStreamEvent(
                kind="completed",
                response=LLMResponse(
                    content="An unexpected error occurred while processing your request.",
                    finish_reason="error",
                    error_kind=kind.value,
                ),
            )
            return

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        reasoning_chunks = 0
        # Time from request start to the FIRST reasoning delta — the closest
        # host-side proxy for server-side thinking time (DeepSeek etc. buffer
        # the whole reasoning pass server-side, so the first delta arrives
        # only after thinking finished; transport latency is negligible).
        # Suppressed for streaming CoT models (see interleaved_reasoning).
        first_reasoning_elapsed: float | None = None
        interleaved_reasoning = False
        # Accumulate tool calls incrementally (OpenAI sends index + fragments)
        tool_call_accum: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage: dict[str, int] = {}

        aiter = stream.__aiter__()
        is_first = True
        while True:
            try:
                timeout = DEFAULT_FIRST_TOKEN_TIMEOUT if is_first else self._stream_idle_timeout
                async with asyncio.timeout(timeout):
                    chunk = await anext(aiter)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                if is_first:
                    logger.warning("LLM first-token timeout for model {} ({:.0f}s)", resolved, timeout)
                    yield LLMStreamEvent(
                        kind="completed",
                        response=LLMResponse(
                            content="The model did not respond within the first-token timeout. This may indicate the model is overloaded or stuck in a long reasoning phase. Please try again or use a different model.",
                            finish_reason="error",
                            error_kind=ErrorKind.TRANSIENT.value,
                        ),
                    )
                else:
                    logger.warning("LLM stream idle timeout for model {}", resolved)
                    yield LLMStreamEvent(
                        kind="completed",
                        response=LLMResponse(
                            content="An unexpected error occurred while processing your request.",
                            finish_reason="error",
                            error_kind=ErrorKind.TRANSIENT.value,
                        ),
                    )
                return
            except Exception as e:
                logger.exception("LLM streaming error for model {}", resolved)
                kind = resilience.classify_error(e)
                yield LLMStreamEvent(
                    kind="completed",
                    response=LLMResponse(
                        content="An unexpected error occurred while processing your request.",
                        finish_reason="error",
                        error_kind=kind.value,
                    ),
                )
                return

            if not chunk.choices:
                # usage-only chunk (stream_options: include_usage)
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = {
                        "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0) or 0,
                        "completion_tokens": getattr(chunk.usage, "completion_tokens", 0) or 0,
                        "total_tokens": getattr(chunk.usage, "total_tokens", 0) or 0,
                    }
                continue

            is_first = False  # first real content chunk received

            delta = chunk.choices[0].delta
            choice_finish = chunk.choices[0].finish_reason
            if choice_finish:
                finish_reason = choice_finish

            # Content delta
            content_text = getattr(delta, "content", None) or ""
            if content_text:
                content_parts.append(content_text)
                yield LLMStreamEvent(kind="content_delta", delta=content_text)

            # Reasoning delta (Kimi, DeepSeek-R1, etc.)
            reasoning_text = getattr(delta, "reasoning_content", None) or ""
            if reasoning_text:
                if first_reasoning_elapsed is None:
                    first_reasoning_elapsed = time.monotonic() - request_started
                # #834 / review: request→first-reasoning-delta only equals the
                # thinking duration for BUFFERED reasoning providers (DeepSeek
                # emits reasoning only after the whole pass finished).  Streaming
                # CoT models (Kimi, Qwen, GPT-5) interleave reasoning and content
                # deltas — for them the frontend's local first→last span is the
                # correct total, and our proxy would show a tiny first-token
                # latency instead.  Suppress when the provider capability says
                # streaming (up front, covers reasoning-first streams too) OR
                # when interleaving is observed in the delta order.
                if streams_reasoning:
                    interleaved_reasoning = True
                elif content_parts:
                    interleaved_reasoning = True
                reasoning_parts.append(reasoning_text)
                reasoning_chunks += 1
                if reasoning_chunks % 10 == 0:
                    logger.info(
                        "stream_chat: got reasoning delta #{} len={} for model={}",
                        reasoning_chunks, len(reasoning_text), resolved,
                    )
                yield LLMStreamEvent(kind="reasoning_delta", delta=reasoning_text)

            # Tool calls — incremental accumulation
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_accum:
                        tool_call_accum[idx] = {
                            "id": tc.id or "",
                            "type": "function",
                            "function": {
                                "name": "",
                                "arguments": "",
                            },
                        }
                    acc = tool_call_accum[idx]
                    if tc.id:
                        acc["id"] = tc.id
                    if hasattr(tc, "function") and tc.function:
                        if hasattr(tc.function, "name") and tc.function.name:
                            acc["function"]["name"] += tc.function.name
                        if hasattr(tc.function, "arguments") and tc.function.arguments:
                            acc["function"]["arguments"] += tc.function.arguments

        # Build final response
        full_content = "".join(content_parts) or None
        full_reasoning = "".join(reasoning_parts) or None
        if reasoning_parts:
            logger.info(
                "stream_chat: reasoning complete chunks={} chars={} for model={}",
                reasoning_chunks, len(full_reasoning or ""), resolved,
            )

        # Parse accumulated tool calls
        parsed_tool_calls: list[ToolCallRequest] = []
        for idx in sorted(tool_call_accum.keys()):
            acc = tool_call_accum[idx]
            parsed_tool_calls.append(ToolCallRequest(
                id=acc["id"],
                name=acc["function"]["name"],
                arguments=self._parse_tool_call_arguments(
                    acc["function"]["name"],
                    acc["function"]["arguments"],
                ),
            ))

        yield LLMStreamEvent(
            kind="completed",
            response=LLMResponse(
                content=full_content,
                tool_calls=parsed_tool_calls,
                finish_reason=finish_reason or "stop",
                usage=usage,
                reasoning_content=full_reasoning,
                # Streaming CoT models interleave reasoning/content — the
                # first-delta proxy would under-report badly, so suppress it
                # and let the frontend use its local first→last span.
                reasoning_elapsed_s=(
                    None if interleaved_reasoning else first_reasoning_elapsed
                ),
                reasoning_elapsed_suppressed=interleaved_reasoning,
            ),
        )

    def get_default_model(self) -> str:
        return self.default_model
