"""Context runtime — manages turn message history and context compaction.

Handles building initial messages, adding assistant messages, adding
tool result messages, and compacting long conversation history.
Centralizes message manipulation that was previously scattered across
the legacy AgentLoop and ContextBuilder.
(Historical: AgentLoop removed in Phase 48.)

Phase 19: adds CompactionResult, estimate_tokens, compress_messages,
compact_thread, and should_auto_compact for runtime-owned context
compaction.

Phase 19 follow-up: wires real ContextCompressor via llm_call_fn
injection so compress_messages() actually compresses through the
5-phase algorithm from miqi.agent.context_compressor.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from loguru import logger

from miqi.execution.hook_runtime import (
    HookPoint,
    HookRuntime,
    LifecycleHookContext,
)


def _count_cjk_ascii(text: str) -> tuple[int, int]:
    """Count (CJK/full-width chars, ASCII chars) in *text*.

    East-Asian wide/full-width characters (CJK, full-width punctuation)
    cost ~1 token each in CJK-capable tokenizers; ASCII ~0.25 token/char.
    """
    cjk = 0
    ascii_chars = 0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            cjk += 1
        else:
            ascii_chars += 1
    return cjk, ascii_chars


def _prune_unpaired_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop tool messages without a preceding assistant(tool_calls) counterpart.

    OpenAI-compatible APIs reject unpaired messages with 400:
      "Messages with role 'tool' must be a response to a preceding
       message with 'tool_calls'"
    (and symmetrically, an assistant tool_calls message with no tool
    response). trim_for_model's group deletion — guarded by "always keep
    the last message" — can delete an assistant(tool_calls) while leaving
    its trailing tool message (or vice versa). This pass restores a valid
    sequence by dropping orphans and un-responded tool_calls groups.
    """
    fixed: list[dict[str, Any]] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            tc_ids = {tc.get("id") for tc in msg["tool_calls"] if isinstance(tc, dict)}
            j = i + 1
            kept: list[dict[str, Any]] = [msg]
            seen: set[str] = set()
            while j < n and messages[j].get("role") == "tool":
                tid = messages[j].get("tool_call_id")
                if tid in tc_ids and tid not in seen:
                    kept.append(messages[j])  # 匹配且未重复的响应 → 保留
                    seen.add(tid)
                # 孤儿 / 重复 tool → 跳过丢弃
                j += 1
            if tc_ids and {m.get("tool_call_id") for m in kept[1:]} == tc_ids:
                fixed.extend(kept)  # 完整工具调用组 → 保留
            # 响应不完整（或 tool_calls 空）→ 整组丢弃
            i = j
            continue
        if role == "tool":
            # 孤儿 tool（前面没有配对 assistant）→ 丢弃
            i += 1
            continue
        fixed.append(msg)
        i += 1
    return fixed


@dataclass(frozen=True)
class CompactionResult:
    """Result of a context compaction operation."""

    thread_id: str
    messages_before: int
    messages_after: int
    tokens_saved: int
    replacement_messages: list[dict[str, Any]]


class ContextRuntime:
    """Message builder and context compactor for turn execution.

    Phase 12: basic message building (build_initial_messages,
    add_assistant_message, add_tool_result).

    Phase 19: runtime-owned context compaction (estimate_tokens,
    compress_messages, compact_thread, should_auto_compact).

    When llm_call_fn is provided, compress_messages() delegates to
    ContextCompressor (5-phase algorithm) for real compression.
    Without it, compress_messages() is an explicit no-op.
    """

    def __init__(
        self,
        *,
        llm_call_fn: Callable[
            [list[dict[str, Any]], str], Awaitable[str]
        ] | None = None,
        context_limit_chars: int = 0,
        compression_threshold_chars: int = 0,
        hooks: HookRuntime | None = None,
    ):
        self._compressor: Any = None
        self._compression_threshold_chars = compression_threshold_chars
        self._context_limit_chars = context_limit_chars
        self._hooks = hooks
        if llm_call_fn is not None:
            self.set_llm_call_fn(llm_call_fn)

    def set_llm_call_fn(
        self,
        llm_call_fn: Callable[[list[dict[str, Any]], str], Awaitable[str]] | None,
        *,
        context_limit_chars: int | None = None,
    ) -> None:
        """(Re)wire the context compressor's LLM call closure.

        Issue #789 hot reload: after the provider is rebuilt at runtime, the
        compressor must use the NEW provider — the closure captures the
        provider at build time, so it is rebuilt here instead of mutating the
        old closure.  *context_limit_chars* may be supplied to apply a new
        compression threshold (defaults to the construction-time value).
        """
        if context_limit_chars is not None:
            self._context_limit_chars = context_limit_chars
        if llm_call_fn is None:
            self._compressor = None
            return
        from miqi.agent.context_compressor import ContextCompressor

        self._compressor = ContextCompressor(
            llm_call_fn=llm_call_fn,
            context_limit_chars=self._context_limit_chars,
        )

    # ── Phase 12: message building ──────────────────────────────────────

    def build_initial_messages(
        self,
        *,
        turn: Any,
        user_content: str,
        system_prompt: str,
        history: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the initial message list for a turn.

        Returns [system, *history, user] list suitable for provider.chat.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_content})
        return messages

    def add_assistant_message(
        self,
        *,
        messages: list[dict[str, Any]],
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]:
        """Append an assistant message, optionally with tool_calls and reasoning.

        reasoning_content carries the model's chain-of-thought (DeepSeek-R1 /
        Kimi thinking models). It is preserved as a separate field so the UI
        can render a thinking block without polluting the visible content,
        and so providers that support reasoning history can feed it back.
        """
        item: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            item["tool_calls"] = tool_calls
        if reasoning_content:
            item["reasoning_content"] = reasoning_content
        return [*messages, item]

    def add_tool_result(
        self,
        *,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        name: str,
        content: str,
        arguments: Any = None,
    ) -> list[dict[str, Any]]:
        """Append a tool result message."""
        msg: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        }
        if arguments is not None:
            msg["arguments"] = arguments
        return [*messages, msg]

    # ── Phase 19: context compaction ────────────────────────────────────

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Estimate token count from messages (CJK-aware heuristic).

        Counts content, tool_calls, and reasoning_content for each message.
        Weights: CJK/full-width chars ≈ 1.0 token, ASCII ≈ 0.25 token/char
        (deepseek/OpenAI tokenizers encode CJK at ~0.7-1.0 token/char and
        English at ~3.5-4 chars/token). The old flat chars/2.5 heuristic
        severely underestimated CJK-heavy conversations, so trim_for_model
        stopped too late and the API rejected the request (issue #715 现场:
        est 101178 passed the guard but the real payload exceeded the
        model limit). Returns at least 1.
        """
        cjk_chars = 0
        ascii_chars = 0
        for message in messages:
            for text in (
                str(message.get("content") or ""),
                str(message.get("reasoning_content") or ""),
            ):
                cjk, asc = _count_cjk_ascii(text)
                cjk_chars += cjk
                ascii_chars += asc
            if message.get("tool_calls"):
                cjk, asc = _count_cjk_ascii(str(message["tool_calls"]))
                cjk_chars += cjk
                ascii_chars += asc
        return max(1, int(cjk_chars * 1.0 + ascii_chars * 0.25))

    async def compress_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        """Compress messages using ContextCompressor when configured.

        When _compressor is None (no llm_call_fn provided), returns
        messages unchanged as an explicit no-op.
        When _compressor is set, delegates to the 5-phase compression
        algorithm from miqi.agent.context_compressor.

        Phase 51.3: fires PRE_COMPACT and POST_COMPACT lifecycle hooks. A
        PRE_COMPACT block outcome skips the actual compression but still
        fires POST_COMPACT.
        """
        if self._hooks is not None:
            pre_ctx = LifecycleHookContext(
                hook_point=HookPoint.PRE_COMPACT,
                data={
                    "session_id": session_id,
                    "model": model,
                    "message_count": len(messages),
                },
            )
            outcome = await self._hooks.run_with_outcome(
                HookPoint.PRE_COMPACT, pre_ctx
            )
            if outcome.action == "block":
                await self._hooks.run(
                    HookPoint.POST_COMPACT,
                    LifecycleHookContext(
                        hook_point=HookPoint.POST_COMPACT,
                        data={
                            "session_id": session_id,
                            "model": model,
                            "message_count": len(messages),
                            "blocked": True,
                        },
                    ),
                )
                return messages

        if self._compressor is None:
            result = messages
        elif self._compression_threshold_chars > 0:
            total_chars = sum(len(str(m.get("content") or "")) for m in messages)
            if total_chars < self._compression_threshold_chars:
                result = messages
            else:
                result = await self._compressor.compress(
                    messages, model=model, session_id=session_id,
                )
        else:
            result = await self._compressor.compress(
                messages, model=model, session_id=session_id,
            )

        if self._hooks is not None:
            await self._hooks.run(
                HookPoint.POST_COMPACT,
                LifecycleHookContext(
                    hook_point=HookPoint.POST_COMPACT,
                    data={
                        "session_id": session_id,
                        "model": model,
                        "message_count": len(result),
                    },
                ),
            )
        return result

    async def compact_thread(
        self,
        *,
        history_runtime: Any,
        thread_id: str,
        turn_id: str,
        model: str,
    ) -> CompactionResult:
        """Load thread history, compress it, and persist replacement.

        Returns a CompactionResult with before/after counts and token savings.
        Calls history_runtime.replace_messages_with_compaction() to persist
        with full audit metadata.
        """
        messages = await history_runtime.load_messages(thread_id)
        before_tokens = self.estimate_tokens(messages)
        replacement = await self.compress_messages(
            messages,
            model=model,
        )
        after_tokens = self.estimate_tokens(replacement)
        await history_runtime.replace_messages_with_compaction(
            thread_id,
            turn_id,
            replacement,
            messages_before=len(messages),
            messages_after=len(replacement),
            tokens_saved=max(0, before_tokens - after_tokens),
        )
        return CompactionResult(
            thread_id=thread_id,
            messages_before=len(messages),
            messages_after=len(replacement),
            tokens_saved=max(0, before_tokens - after_tokens),
            replacement_messages=replacement,
        )

    def should_auto_compact(
        self,
        messages: list[dict[str, Any]],
        token_limit: int,
    ) -> bool:
        """Return True when estimated tokens exceed the configured limit."""
        return self.estimate_tokens(messages) >= token_limit

    # ── Phase 56: pre-send context guard ───────────────────────────────

    # Per-model maximum input tokens. Conservative defaults for models that
    # don't explicitly advertise their limit. When the model isn't listed,
    # we fall back to 128K — safe for most modern models.
    _MODEL_MAX_INPUT_TOKENS: dict[str, int] = {
        "gpt-4o": 128_000,
        "gpt-4o-mini": 128_000,
        "gpt-4-turbo": 128_000,
        "gpt-4": 8_192,
        "gpt-3.5-turbo": 16_385,
        "o1": 200_000,
        "o1-mini": 128_000,
        "o3": 200_000,
        "o3-mini": 200_000,
        "o4-mini": 200_000,
        "claude-3.5-sonnet": 200_000,
        "claude-3.5-haiku": 200_000,
        "claude-3-opus": 200_000,
        "claude-3-haiku": 200_000,
        "claude-3-sonnet": 200_000,
        "claude-opus-4": 200_000,
        "claude-opus-4-5": 200_000,
        "claude-sonnet-4": 200_000,
        "claude-sonnet-4-5": 200_000,
        "claude-haiku-4-5": 200_000,
        "deepseek-chat": 128_000,
        "deepseek-reasoner": 128_000,
        # v4-flash 实测硬上限 102400（#715 现场记录）；登记后 hard_limit
        # = 102400×0.8 = 81920，避免 fallback 128K×0.8=102400 恰好贴线上限、
        # 0.8 安全系数被抵消（#775）。
        "deepseek-v4-flash": 102_400,
        "gemini-2.5-flash": 1_048_576,
        "gemini-2.5-pro": 1_048_576,
        "gemini-2.0-flash": 1_048_576,
        "qwen-max": 131_072,
        "qwen-plus": 131_072,
        "qwen-turbo": 1_000_000,
        "kimi-k2.5": 128_000,
        "kimi-k2": 128_000,
        "glm-4": 128_000,
        "minimax-m1": 1_000_000,
    }

    # Fraction of model max to use as hard limit (80% leaves headroom for
    # the response tokens, tool definitions, and estimation error).
    _CONTEXT_SAFETY_FACTOR = 0.80

    def trim_for_model(
        self,
        messages: list[dict[str, Any]],
        model: str,
    ) -> list[dict[str, Any]]:
        """Hard-trim messages to fit within the model's input token limit.

        This is the LAST-RESORT safety net — it runs right before the
        provider call and discards the oldest complete turns
        (user→assistant→tool(s)) until the estimated token count is
        under 80% of the model's maximum.

        Always keeps the system prompt (index 0 if role=='system') and
        one extra message after it.  Groups are removed as atomic units
        (user→assistant→tool(s)), so a trailing tool is never orphaned and
        a headless assistant turn is never left behind.  Returns messages
        unchanged when they already fit.
        """
        max_input = self._resolve_model_max_input(model)
        hard_limit = int(max_input * self._CONTEXT_SAFETY_FACTOR)
        est = self.estimate_tokens(messages)

        if est <= hard_limit:
            # 上下文未超限也可能含未配对 tool/assistant(tool_calls) 消息
            # （turn 中断、steering 残留）——统一成对裁剪（CodeRabbit #761）
            return _prune_unpaired_tool_messages(messages)

        logger.warning(
            "Pre-send guard: estimated {} tokens exceeds {} limit for {} "
            "(model max={}); trimming oldest turns",
            est, hard_limit, model, max_input,
        )

        work = list(messages)
        system_idx = 0 if work and work[0].get("role") == "system" else -1
        head_protect = max(system_idx + 1, 0) + 1  # system prompt + 1 extra

        while len(work) > head_protect + 1:
            est = self.estimate_tokens(work)
            if est <= hard_limit:
                break

            # Find the oldest group to remove. A full turn starts with
            # 'user' — preferred. But a session with a SINGLE user turn
            # and a long assistant/tool loop has no second user turn to
            # cut: after the one user turn is gone, trimming stalled and
            # the request still exceeded the limit (real MOF skill
            # session: 112939 est vs 102400 limit, #607). Fall back to
            # cutting the oldest assistant + its trailing tool message(s)
            # as a group (structure-preserving: never splits a group).
            # We skip any leading 'assistant' or 'tool' messages whose
            # corresponding user message sits inside the protected head.
            cut_start = None
            group_role = "user"
            for i in range(head_protect, len(work) - 1):
                if work[i].get("role") == "user":
                    cut_start = i
                    break
            if cut_start is None:
                for i in range(head_protect, len(work) - 1):
                    if work[i].get("role") == "assistant":
                        cut_start = i
                        group_role = "assistant"
                        break
            if cut_start is None:
                break

            # Remove the whole group: the group start, then all following
            # messages until the next turn start (next 'user'; for an
            # assistant group also stop at the next 'assistant').  The group
            # is removed as an atomic unit — never split it.  In particular,
            # do NOT protect the tail here: if the trimmed group is the LAST
            # user turn, keeping its assistant+tool replies while dropping
            # the user question would leave a headless assistant turn (the
            # model keeps generating on an orphaned tool round and the
            # context drifts on every cycle — review #752).  Deleting the
            # whole group never orphans a trailing tool (its assistant goes
            # with it), so the original #753 failure stays fixed too.
            work.pop(cut_start)  # remove group start (user or assistant)
            while cut_start < len(work):
                role = work[cut_start].get("role")
                if role == "user" or (group_role == "assistant" and role == "assistant"):
                    break  # next turn starts here — stop
                work.pop(cut_start)  # remove tool / assistant messages

        est_after = self.estimate_tokens(work)
        logger.info(
            "Pre-send guard: messages {} -> {} (est tokens {} -> {})",
            len(messages), len(work), est, est_after,
        )
        # 结构修复：trim 的组删除受「保留最后一条」保护，可能删掉
        # assistant(tool_calls) 却残留其末尾 tool 消息（或反之），产生
        # 未配对消息 → API 400 "Messages with role 'tool' must be a
        # response to a preceding message with 'tool_calls'"（实测 8/19）。
        # 这里成对裁剪：孤儿 tool 丢弃；无 tool 响应的 assistant(tool_calls)
        # 整组丢弃。
        return _prune_unpaired_tool_messages(work)

    def _resolve_model_max_input(self, model: str) -> int:
        """Return the maximum input tokens for a model name.

        Matches by substring against the known model table, falling back
        to 128K for models not in the table.
        """
        model_lower = model.lower()
        for key, limit in self._MODEL_MAX_INPUT_TOKENS.items():
            if key in model_lower:
                return limit
        return 128_000
