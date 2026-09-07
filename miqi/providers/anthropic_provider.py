"""Anthropic provider — uses the official anthropic SDK directly.

Handles the OpenAI→Anthropic message format conversion internally so the rest of
the codebase can always speak OpenAI-format messages.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import json_repair

import miqi.providers.resilience as resilience
from miqi.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from miqi.providers.registry import find_by_model, find_by_name
from miqi.providers.resilience import ErrorKind

DEFAULT_REQUEST_TIMEOUT = 600.0


class AnthropicProvider(LLMProvider):
    """
    Provider for Anthropic models (claude-*) using the anthropic SDK.

    Accepts the same OpenAI-format messages as the rest of the codebase and
    converts them to Anthropic format internally.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "claude-opus-4-5",
        extra_headers: dict[str, str] | None = None,
        provider_name: str | None = None,
        request_timeout: float | None = None,
        model_prefix: str | None = None,
    ):
        self._selected_spec = find_by_name(provider_name) if provider_name else None
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        # 走非 anthropic 前缀(如 AI 网关承载 deepseek 模型)时,请求前剥掉该前缀。
        self._model_prefix = model_prefix

        client_kwargs: dict[str, Any] = {
            "api_key": api_key or None,
            "default_headers": self.extra_headers,
            "timeout": request_timeout or DEFAULT_REQUEST_TIMEOUT,
        }
        if api_base:
            client_kwargs["base_url"] = api_base

        self._client = anthropic.AsyncAnthropic(**client_kwargs)

    # ------------------------------------------------------------------
    # Model name
    # ------------------------------------------------------------------

    def _resolve_model(self, model: str) -> str:
        """Strip provider prefix if present; Anthropic SDK wants bare model names.

        anthropic 前缀剥 'anthropic/'；经 AI 网关承载其他 provider 的模型时
        (如 deepseek-v4-flash 走 Anthropic 兼容网关)按构造时的 model_prefix 剥。
        """
        for prefix in ("anthropic/", "anthropic-"):
            if model.startswith(prefix):
                return model[len(prefix):]
        mp = self._model_prefix
        if mp:
            for sep in ("/", "-"):
                prefix = f"{mp}{sep}"
                if model.startswith(prefix):
                    return model[len(prefix):]
        return model

    # ------------------------------------------------------------------
    # Message format conversion: OpenAI → Anthropic
    # ------------------------------------------------------------------

    def _extract_system_and_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        use_cache_control: bool = False,
    ) -> tuple[list[dict[str, Any]] | str, list[dict[str, Any]]]:
        """Split out system messages and convert the rest to Anthropic format.

        Returns (system, anthropic_messages) where system is either a plain string
        or a list of content blocks (when use_cache_control=True).
        """
        system_parts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role")

            if role == "system":
                content = msg.get("content") or ""
                if isinstance(content, list):
                    # Already may be a list of blocks — extract text
                    system_parts.append(
                        " ".join(
                            b.get("text", "") for b in content if isinstance(b, dict)
                        )
                    )
                else:
                    system_parts.append(str(content))
                continue

            if role == "assistant":
                anthropic_messages.append(self._convert_assistant_msg(msg))
                continue

            if role == "tool":
                anthropic_messages.append(self._convert_tool_result_msg(msg))
                continue

            if role == "user":
                anthropic_messages.append(self._convert_user_msg(msg))
                continue

        # Merge consecutive same-role messages (Anthropic requires alternating roles)
        anthropic_messages = self._merge_consecutive_same_role(anthropic_messages)

        system_text = "\n\n".join(p for p in system_parts if p)

        if not use_cache_control:
            return system_text, anthropic_messages

        # Prompt caching: wrap system text as a content block with cache_control
        system_blocks: list[dict[str, Any]] = []
        if system_text:
            system_blocks = [
                {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
            ]
        return system_blocks, anthropic_messages

    def _convert_user_msg(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Convert an OpenAI user message to Anthropic format."""
        content = msg.get("content")
        if content is None:
            content = "(empty)"
        if isinstance(content, list):
            # May contain text/image blocks — pass through as-is (already compatible)
            return {"role": "user", "content": content}
        return {"role": "user", "content": str(content)}

    def _convert_assistant_msg(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Convert an OpenAI assistant message (possibly with tool_calls) to Anthropic."""
        content_blocks: list[dict[str, Any]] = []

        text = msg.get("content")
        if text:
            content_blocks.append({"type": "text", "text": str(text)})

        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", "{}")
            if isinstance(raw_args, str):
                try:
                    input_data = json.loads(raw_args)
                except (json.JSONDecodeError, ValueError):
                    input_data = json_repair.loads(raw_args)
            else:
                input_data = raw_args

            if not isinstance(input_data, dict):
                input_data = {}

            content_blocks.append({
                "type": "tool_use",
                "id": tc.get("id", "tc_unknown"),
                "name": fn.get("name", "unknown"),
                "input": input_data,
            })

        if not content_blocks:
            content_blocks = [{"type": "text", "text": ""}]

        return {"role": "assistant", "content": content_blocks}

    def _convert_tool_result_msg(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Convert an OpenAI tool result message to Anthropic user format."""
        tool_use_id = msg.get("tool_call_id", "tc_unknown")
        content = msg.get("content") or ""

        result_block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": str(content) if not isinstance(content, list) else content,
        }
        return {"role": "user", "content": [result_block]}

    def _merge_consecutive_same_role(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Merge consecutive messages with the same role into one.

        Anthropic requires messages to alternate between 'user' and 'assistant'.
        This happens when tool results and a following user message both become
        role='user' after conversion.
        """
        if not messages:
            return messages

        merged: list[dict[str, Any]] = []
        for msg in messages:
            if merged and merged[-1]["role"] == msg["role"]:
                prev = merged[-1]
                # Normalise both contents to lists of blocks
                prev_content = prev["content"]
                new_content = msg["content"]

                if isinstance(prev_content, str):
                    prev_content = [{"type": "text", "text": prev_content}]
                if isinstance(new_content, str):
                    new_content = [{"type": "text", "text": new_content}]

                merged[-1] = {
                    "role": prev["role"],
                    "content": prev_content + new_content,
                }
            else:
                merged.append(msg)

        return merged

    def _convert_tools(
        self,
        tools: list[dict[str, Any]],
        *,
        use_cache_control: bool = False,
    ) -> list[dict[str, Any]]:
        """Convert OpenAI tool definitions to Anthropic format."""
        converted = []
        for tool in tools:
            fn = tool.get("function", tool)
            converted.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })

        if use_cache_control and converted:
            converted[-1] = {**converted[-1], "cache_control": {"type": "ephemeral"}}

        return converted

    # ------------------------------------------------------------------
    # Network error detection
    # ------------------------------------------------------------------

    def _is_transient_network_error(self, error: Exception) -> bool:
        return resilience.classify_error(error) == ErrorKind.TRANSIENT

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
        use_cache = bool(spec and spec.supports_prompt_caching)

        clean_messages = self._sanitize_empty_content(messages)
        system, anthropic_messages = self._extract_system_and_messages(
            clean_messages, use_cache_control=use_cache
        )

        kwargs: dict[str, Any] = {
            "model": resolved,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if system:
            kwargs["system"] = system

        if tools:
            kwargs["tools"] = self._convert_tools(tools, use_cache_control=use_cache)
            kwargs["tool_choice"] = {"type": "auto"}

        try:
            response = await resilience.with_retry(
                lambda: self._client.messages.create(**kwargs),
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
        """Convert an Anthropic Messages response to LLMResponse."""
        tool_calls: list[ToolCallRequest] = []
        text_parts: list[str] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                input_data = block.input
                if isinstance(input_data, str):
                    try:
                        input_data = json.loads(input_data)
                    except (json.JSONDecodeError, ValueError):
                        input_data = json_repair.loads(input_data)

                if not isinstance(input_data, dict):
                    input_data = {}

                tool_calls.append(ToolCallRequest(
                    id=block.id,
                    name=block.name,
                    arguments=input_data,
                ))

        content = "\n".join(text_parts) if text_parts else None

        # Map Anthropic stop reasons to OpenAI-style finish_reason
        stop_map = {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
            "stop_sequence": "stop",
        }
        finish_reason = stop_map.get(response.stop_reason or "", "stop")

        usage: dict[str, int] = {}
        if hasattr(response, "usage") and response.usage:
            input_tokens = getattr(response.usage, "input_tokens", 0) or 0
            output_tokens = getattr(response.usage, "output_tokens", 0) or 0
            usage = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ):
        """Streaming chat via Anthropic SDK native streaming."""
        from miqi.providers.base import LLMStreamEvent

        try:
            response = await self.chat(
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            yield LLMStreamEvent(kind="completed", response=response)
        except Exception:
            raise

    def get_default_model(self) -> str:
        return self.default_model
