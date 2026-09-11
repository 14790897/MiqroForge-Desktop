"""Turn runner — the runtime-owned provider.chat + tool loop.

Historical: Extracted from the legacy AgentLoop._run_agent_loop. Executes a
single turn: calls the provider, routes tool calls through ToolRuntime,
builds messages through ContextRuntime, and returns TurnResult.

Also provides run_agent_job() for AgentJobRuntime — a simplified
single-turn execution path for sub-agent jobs.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger

from miqi.agent.tools.user_roots import extract_user_mentioned_roots
from miqi.execution.hook_runtime import (
    HookPoint,
    HookRuntime,
    LifecycleHookContext,
)
from miqi.execution.orchestrator import OrchestrationResult
from miqi.utils.tool_text_guard import (
    LEAK_NOTICE,
    sanitize_tool_call_text,
    tool_names_from_definitions,
)

# Feedback sent back to the model when it writes a tool call as plain text
# instead of using the tool-calling interface. Never executed — the model
# is asked to retry through the real interface (issue #532).
_TOOL_CALL_TEXT_FEEDBACK = (
    "你刚才把工具调用写成了普通文本（如 functions.xxx(...)），"
    "它没有被执行。请改用工具调用接口重新发起，不要把它写成文字。"
)

# Issue #246: sub-agents get a tight 15-step iteration cap (the legacy
# SubagentManager limit), not the session-wide max_tool_iterations.
SUBAGENT_MAX_ITERATIONS = 15

# DeepSeek 系思考模型在极长推理后偶尔只输出 reasoning、无 content 也无
# 工具调用——直接当最终回答会让回合以空回复静默结束（看门狗/测试都等不到
# 结果）。先推动模型继续，连续超限才作为错误上抛。
_EMPTY_RESPONSE_NUDGE_LIMIT = 2
_EMPTY_RESPONSE_NUDGE = (
    "（系统提示）你上一轮只输出了思考内容，没有给出回答或工具调用。"
    "请直接给出最终回答或下一步工具调用，不要重复思考过程。"
)


def _strip_leak_notice(text: str) -> str:
    """Drop the internal LEAK_NOTICE placeholder before persisting to history.

    The notice is a model-facing signal (tool_text_guard), not user content;
    reconstructing history from a stored copy must not show it.
    """
    return text.replace(LEAK_NOTICE, "").strip() if LEAK_NOTICE in text else text


@dataclass
class TurnResult:
    """Result of a completed turn."""
    final_content: str
    messages: list[dict[str, Any]]
    tools_used: list[str]
    token_usage: dict[str, int] = field(default_factory=dict)
    messages_delta: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str | None = None
    reasoning_elapsed_s: float | None = None  # first-round server-side thinking proxy


class _SnapshotBuffer:
    """#740: per-turn execution-snapshot buffer with throttled flush state.

    Created fresh inside every ``run()`` call so a reused TurnRunner can never
    leak or corrupt snapshot state across concurrent/sequential turns.
    """

    def __init__(self) -> None:
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.reasoning_elapsed_s: float | None = None
        self.last_flush = time.perf_counter()
        self.flushed_len = 0

    def due(self, history: Any) -> bool:
        """Throttle: flush at least once per second or per 4KB of new output."""
        if history is None:
            return False
        total = sum(len(p) for p in self.content) + sum(
            len(p) for p in self.reasoning
        )
        return (
            time.perf_counter() - self.last_flush >= 1.0
            or total - self.flushed_len >= 4096
        )

    async def flush(self, history: Any, turn: Any, *, status: str) -> None:
        """Persist the accumulated content/reasoning as an execution snapshot."""
        if history is None:
            return
        content = "".join(self.content)
        reasoning = "".join(self.reasoning)
        await history.upsert_snapshot(
            turn.turn_id,
            turn.thread_id,
            status=status,
            assistant_content=content,
            reasoning_content=reasoning,
            reasoning_elapsed_s=self.reasoning_elapsed_s,
            # #905 review / CodeRabbit: persist the reasoning mode so an
            # interrupted fast turn restores as 🚀/快速思考, not the ThinkBlock
            # default (🧠/深度思考).
            reasoning_mode=getattr(turn, "reasoning_mode", None),
        )
        self.last_flush = time.perf_counter()
        self.flushed_len = len(content) + len(reasoning)


class TurnRunner:
    """Runs a single model+tool turn.

    Owns provider, tool/context runtimes, event emitter, and iteration cap.
    Stateless per-call — created once per session, reused across turns.
    """

    def __init__(
        self,
        *,
        provider: Any,
        tool_runtime: Any,
        context_runtime: Any,
        event_emitter: Any,
        max_iterations: int,
        capability_resolver: Any | None = None,
        ledger_runtime: Any | None = None,
        history_runtime: Any | None = None,
        hooks: HookRuntime | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self._provider = provider
        self._running = False  # True while run() is executing (hot reload guard)
        # #789: a config save during a running turn cannot swap _provider
        # (the turn captures provider + model at start); the replacement is
        # parked here and adopted at the start of the NEXT run().
        self._pending_provider = None
        self._tools = tool_runtime
        self._context = context_runtime
        self._events = event_emitter
        self._max_iterations = max_iterations
        self._capability_resolver = capability_resolver
        self._ledger = ledger_runtime
        self._history = history_runtime
        self._hooks = hooks
        # 假时钟注入点（#680 跟进）：单测传假时钟即可测 25s/30s 边界，
        # 无需 monkeypatch。
        self._clock = clock or time.monotonic

    def _adopt_pending_provider(self) -> None:
        """Swap in a provider that was hot-applied during a previous turn (#789).

        apply_config_update parks the replacement in ``_pending_provider``
        while a turn is running (a running turn must keep the provider it
        captured); the next turn adopts it here, before any provider call.
        """
        if self._pending_provider is not None:
            self._provider = self._pending_provider
            self._pending_provider = None

    async def run(
        self,
        *,
        turn: Any,
        user_content: str,
        system_prompt: str,
        tools: list[dict[str, Any]] | None,
        history: list[dict[str, Any]] | None = None,
        cancel_event: Any | None = None,
        steer_queue: Any | None = None,
        max_iterations: int | None = None,
    ) -> TurnResult:
        """Execute a full turn: model calls until final response or max iters.

        Phase 14 follow-up: checks cancel_event (asyncio.Event) at each
        iteration and yields with CancelledError when set.
        Phase 41: drains steer_queue at safe boundaries and continues
        the same turn instead of completing immediately.

        Phase 51.3: fires PROMPT_SUBMIT, TURN_START, and TURN_END lifecycle hooks.

        *max_iterations* overrides the session-wide iteration cap for this
        call (e.g. sub-agents get a tighter 15-step limit — issue #246).
        """
        self._adopt_pending_provider()
        # Set the hot-reload guard BEFORE the lifecycle hooks: PROMPT_SUBMIT /
        # TURN_START are async and a config save can land while they run —
        # the running flag must already cover the whole turn, hooks included
        # (2026-08-31 review).  It is released in the finally below (or the
        # hook-failure guard right after).
        self._running = True
        lifecycle_ctx = LifecycleHookContext(
            hook_point=HookPoint.PROMPT_SUBMIT,
            data={
                "turn_id": turn.turn_id,
                "thread_id": turn.thread_id,
                "user_content": user_content,
            },
        )
        try:
            if self._hooks is not None:
                await self._hooks.run(HookPoint.PROMPT_SUBMIT, lifecycle_ctx)
                lifecycle_ctx.hook_point = HookPoint.TURN_START
                await self._hooks.run(HookPoint.TURN_START, lifecycle_ctx)
        except BaseException:
            self._running = False
            raise

        # #740: per-turn snapshot buffer — flush on throttle, on interruption
        # (keep snapshot for resume), and on completion (delete).
        _snap = _SnapshotBuffer()
        try:
            result = await self._run_impl(
                turn=turn,
                user_content=user_content,
                system_prompt=system_prompt,
                tools=tools,
                history=history,
                cancel_event=cancel_event,
                steer_queue=steer_queue,
                max_iterations=max_iterations,
                snapshot_buffer=_snap,
            )
        except BaseException:
            await _snap.flush(self._history, turn, status="interrupted")
            raise
        else:
            await _snap.flush(self._history, turn, status="completed")
            if self._history is not None:
                await self._history.delete_snapshot(turn.turn_id)
            return result
        finally:
            try:
                if self._hooks is not None:
                    end_ctx = LifecycleHookContext(
                        hook_point=HookPoint.TURN_END,
                        data={
                            "turn_id": turn.turn_id,
                            "thread_id": turn.thread_id,
                            "user_content": user_content,
                        },
                    )
                    await self._hooks.run(HookPoint.TURN_END, end_ctx)
            finally:
                # The guard must clear even when TURN_END raises — a stuck
                # _running would defer all future provider swaps forever
                # (2026-09-01 review).  The hook exception still propagates.
                self._running = False

    async def _run_impl(
        self,
        *,
        turn: Any,
        user_content: str,
        system_prompt: str,
        tools: list[dict[str, Any]] | None,
        history: list[dict[str, Any]] | None = None,
        cancel_event: Any | None = None,
        steer_queue: Any | None = None,
        max_iterations: int | None = None,
        snapshot_buffer: _SnapshotBuffer | None = None,
    ) -> TurnResult:
        """Core turn loop implementation."""
        # #729: first-token observability — log latency from turn start to the
        # first streamed delta (content or reasoning)，使端到端首字延迟可观测。
        _turn_started = time.perf_counter()
        _first_token_logged = False
        # #834: server-side thinking proxy — set on the first reasoning delta.
        # Timed from the CURRENT model call start (`_round_started`, reset per
        # round), NOT the turn start: a tool round before thinking (e.g. 60s
        # web_search) or a retry backoff must not inflate the displayed
        # thinking time.  The provider's per-attempt value (request→first
        # delta) takes precedence when available; `provider_established`
        # distinguishes a confirmed provider value from the coarse-clock
        # placeholder so a later suppressed round can't clobber round one's
        # confirmed value (CodeRabbit #856).
        reasoning_elapsed_s: float | None = None
        provider_established = False

        # #821: auto-sense directories the user named in their message
        # (e.g. "输出到 C:\Users\x\Desktop\test_result") so file tools can
        # read/write them.  Extracted once per turn from user-role text only;
        # mid-turn steering messages refresh the roots below.
        # #984: sub-agent turns keep the roots inherited from their parent
        # job — the text here is model-authored, so extracting from it would
        # re-open the injection channel this feature closed.
        _user_texts = [user_content]
        if not getattr(turn, "is_subagent", False):
            turn.user_mentioned_roots = extract_user_mentioned_roots(
                _user_texts, workspace=getattr(turn, "workspace", None),
            )

        messages = self._context.build_initial_messages(
            turn=turn,
            user_content=user_content,
            system_prompt=system_prompt,
            history=history,
        )
        tools_used: list[str] = []
        tool_text_leaked = False
        # Phase 17: accumulate messages added during this turn for persistence.
        # Each entry is a provider-compatible {role, content, ...} dict.
        messages_delta: list[dict[str, Any]] = []
        # Effective iteration cap — caller override wins over the session-wide
        # limit; report the same value the loop actually uses.
        _effective_iterations = max_iterations or self._max_iterations
        # #680 (desktop FAST budget): fast mode caps the decision loop at 3
        # model→tool rounds so the model can't spiral into search→search→search
        # (KUN parity; 方案 2 of desktop-fast-budget-design.md).
        if getattr(turn, "reasoning_mode", None) == "fast":
            _effective_iterations = min(_effective_iterations, 3)

        # Accumulate reasoning across all tool-call cycles within the turn so
        # the frontend can show a single merged ThinkBlock. #539
        turn_level_reasoning_parts: list[str] = []

        # ── #680 desktop FAST budget (方案 1 + 4, desktop-fast-budget-design.md) ──
        # Time fuse: enter finalization at (budget - grace), hard stop at
        # budget — CHECKED BETWEEN iterations (循环级保证: a single long model
        # step may cross the threshold, matching KUN semantics).
        # Search phase budget: fast allows ONE web_search phase per turn.
        _rmode = getattr(turn, "reasoning_mode", None)
        _finalize_at: float | None = None
        _hard_stop_at: float | None = None
        _finalizing = False
        _search_phases = 0
        if _rmode == "fast":
            _turn_t0 = self._clock()
            _budget_s = 30
            _grace_s = 5
            _finalize_at = _turn_t0 + _budget_s - _grace_s
            _hard_stop_at = _turn_t0 + _budget_s

        def _budget_skip_reason(tool_name: str) -> str | None:
            """Unified budget gate (KUN _budget_skip_reason parity): finalizing
            refuses ALL new tools; web_search beyond the phase budget is
            refused with an explicit notice so the model pivots to answering
            from what it has."""
            nonlocal _finalizing, _search_phases
            if _finalizing:
                return "时间预算已到（极速模式收尾阶段，不再调用工具）"
            if tool_name == "web_search" and _rmode == "fast":
                if _search_phases >= 1:
                    return "极速模式搜索预算已用尽（最多一轮搜索）"
                _search_phases += 1
            return None

        async def _drain_steer_messages() -> list[dict[str, Any]]:
            if steer_queue is None:
                return []
            drained: list[dict[str, Any]] = []
            while True:
                try:
                    drained.append(steer_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            return drained

        empty_response_nudges = 0
        for _iteration in range(_effective_iterations):
            # Phase 14 follow-up: check cancellation before expensive work
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError("Turn cancelled via AbortTurn")

            # #680 desktop FAST budget — time fuse (方案 1):
            # hard stop ends the loop; finalization injects a wrap-up prompt
            # once and refuses new tools (via _budget_skip_reason).
            if _rmode == "fast":
                _now = self._clock()
                if _hard_stop_at is not None and _now >= _hard_stop_at:
                    break
                if (
                    _finalize_at is not None
                    and _now >= _finalize_at
                    and not _finalizing
                ):
                    _finalizing = True
                    messages = messages + [{
                        "role": "system",
                        "content": (
                            "[极速模式收尾] 时间预算即将用完：请不要再调用任何工具，"
                            "用已获得的信息完成请求并直接给出最终回答；"
                            "明确说明已完成的部分与未能完成的部分，"
                            "绝不要假装未完成的动作成功。"
                        ),
                    }]

            # Phase 56: hard-trim messages before provider call so we never
            # send a request that exceeds the model's input token limit.
            messages = self._context.trim_for_model(messages, turn.model)

            # Phase 20: prefer streaming. stream_chat() is a base-class
            # method on LLMProvider so every provider supports it — the
            # default wraps chat() and yields a single "completed" event.
            response: Any = None
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            reasoning_chunks = 0
            # Each model call starts a fresh thinking-timer window (CR #856-1).
            _round_started = time.perf_counter()
            async for stream_event in self._provider.stream_chat(
                messages=messages,
                tools=tools,
                model=turn.model,
                temperature=turn.temperature,
                max_tokens=turn.max_tokens,
            ):
                # Phase 14 follow-up: the iteration-start check above only fires
                # BETWEEN iterations.  A single-shot reply is one iteration, so
                # once the stream is flowing an abort would otherwise be ignored
                # until the whole response finishes (#542).  Check on every
                # stream event so an interrupt stops the bubble mid-generation.
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError("Turn cancelled via AbortTurn")
                if stream_event.kind == "content_delta":
                    if not _first_token_logged:
                        _first_token_logged = True
                        logger.info(
                            "turn_runner: first_token_latency_ms={:.0f} for turn={}",
                            (time.perf_counter() - _turn_started) * 1000, turn.turn_id,
                        )
                    content_parts.append(stream_event.delta)
                    if snapshot_buffer is not None:
                        snapshot_buffer.content.append(stream_event.delta)
                        if snapshot_buffer.due(self._history):
                            await snapshot_buffer.flush(self._history, turn, status="running")
                    from miqi.protocol.events import AgentMessageDeltaEvent
                    await self._events.emit(AgentMessageDeltaEvent(
                        turn_id=turn.turn_id,
                        delta=stream_event.delta,
                        index=len(content_parts) - 1,
                    ))
                    if self._ledger is not None:
                        await self._ledger.append_item(
                            thread_id=turn.thread_id,
                            turn_id=turn.turn_id,
                            item_type="assistant_delta",
                            content=stream_event.delta,
                            payload={"index": len(content_parts) - 1},
                        )
                elif stream_event.kind == "reasoning_delta":
                    if not _first_token_logged:
                        _first_token_logged = True
                        logger.info(
                            "turn_runner: first_token_latency_ms={:.0f} for turn={} (reasoning)",
                            (time.perf_counter() - _turn_started) * 1000, turn.turn_id,
                        )
                    # Server-side thinking proxy (#834): the first reasoning
                    # delta arrives only after the provider finished thinking.
                    # Timed from the CURRENT model call (not turn start) so
                    # tool-execution time and retry backoff stay excluded.
                    # The provider's per-attempt measurement (request→first
                    # delta) is preferred; this coarse clock is the fallback
                    # for providers that don't report it.
                    if reasoning_elapsed_s is None:
                        reasoning_elapsed_s = time.perf_counter() - _round_started
                    reasoning_parts.append(stream_event.delta)
                    reasoning_chunks += 1
                    if snapshot_buffer is not None:
                        snapshot_buffer.reasoning.append(stream_event.delta)
                        snapshot_buffer.reasoning_elapsed_s = reasoning_elapsed_s
                        if snapshot_buffer.due(self._history):
                            await snapshot_buffer.flush(self._history, turn, status="running")
                    from miqi.protocol.events import AgentReasoningEvent
                    if reasoning_chunks % 10 == 0:
                        logger.info(
                            "turn_runner: got reasoning_delta #{} len={} for turn={}",
                            reasoning_chunks, len(stream_event.delta), turn.turn_id,
                        )
                    await self._events.emit(AgentReasoningEvent(
                        turn_id=turn.turn_id,
                        content=stream_event.delta,
                    ))
                    if self._ledger is not None:
                        await self._ledger.append_item(
                            thread_id=turn.thread_id,
                            turn_id=turn.turn_id,
                            item_type="reasoning_delta",
                            content=stream_event.delta,
                            payload={},
                        )
                elif stream_event.kind == "completed":
                    response = stream_event.response
                    # #834 / review: the provider's per-attempt measurement
                    # (request→first reasoning delta, retries excluded) is the
                    # most accurate — always prefer it over the coarse round
                    # clock, which includes failed-attempt/backoff time.
                    provider_elapsed = getattr(
                        response, "reasoning_elapsed_s", None
                    )
                    suppressed = getattr(
                        response, "reasoning_elapsed_suppressed", False
                    )
                    if suppressed:
                        # Streaming CoT (interleaved) round: the proxy is
                        # invalid for THIS model — discard the coarse-clock
                        # placeholder unless an earlier round already
                        # established a confirmed provider value.
                        if not provider_established:
                            reasoning_elapsed_s = None
                            if snapshot_buffer is not None:
                                snapshot_buffer.reasoning_elapsed_s = None
                                # #905 review: a coarse value may already have
                                # been flushed to the snapshot table during
                                # streaming — force a flush so the persisted
                                # row is overwritten with NULL instead of
                                # surfacing a bogus thinking time on restore.
                                await snapshot_buffer.flush(
                                    self._history, turn, status="running"
                                )
                    elif provider_elapsed is not None and not provider_established:
                        # #905 review: keep the FIRST confirmed provider value
                        # (first-round proxy, matching the field docstring) —
                        # a later round must not clobber round one's value.
                        reasoning_elapsed_s = float(provider_elapsed)
                        provider_established = True
                        if snapshot_buffer is not None:
                            snapshot_buffer.reasoning_elapsed_s = reasoning_elapsed_s

            if reasoning_parts:
                logger.info(
                    "turn_runner: reasoning complete chunks={} chars={} for turn={}",
                    reasoning_chunks, len("".join(reasoning_parts)), turn.turn_id,
                )

            # Safety net: if the stream never yielded a completed event,
            # synthesize one from the accumulated content parts.
            if response is None:
                from miqi.providers.base import LLMResponse
                response = LLMResponse(
                    content="".join(content_parts),
                    finish_reason="stop",
                )
            # Phase 57: surface provider-reported failures. A terminal
            # response with finish_reason == "error" means the provider hit
            # an unrecoverable error (transient/rate-limit retries already
            # exhausted by plan/56). Treat it as a real failure — raise a
            # classified ProviderError instead of returning the error text
            # as a normal final_content. Invalid/missing error_kind → FATAL.
            if getattr(response, "finish_reason", None) == "error":
                from miqi.providers.resilience import ErrorKind, ProviderError
                raw_kind = getattr(response, "error_kind", None)
                try:
                    kind = ErrorKind(raw_kind) if raw_kind else ErrorKind.FATAL
                except ValueError:
                    kind = ErrorKind.FATAL
                raise ProviderError(
                    kind=kind,
                    message=response.content or "Provider error",
                )

            # Reasoning content from thinking models (DeepSeek-R1 / Kimi).
            # Prefer the provider-assembled full reasoning on the completed
            # response; fall back to deltas we accumulated ourselves.
            reasoning_content = (
                getattr(response, "reasoning_content", None)
                or "".join(reasoning_parts)
                or None
            )
            if reasoning_content:
                logger.info(
                    "turn_runner: reasoning for turn={} len={}",
                    turn.turn_id, len(reasoning_content),
                )
                # Accumulate turn-level reasoning so the UI can show a single
                # merged ThinkBlock across multiple tool-call cycles. #539
                turn_level_reasoning_parts.append(reasoning_content)

            if not response.has_tool_calls:
                # Phase 41: drain steering messages before completing
                steers = await _drain_steer_messages()
                if steers:
                    # Save assistant reply before steering messages
                    content = response.content or ""
                    content, _ = sanitize_tool_call_text(
                        content, tool_names_from_definitions(tools)
                    )
                    messages = self._context.add_assistant_message(
                        messages=messages,
                        content=content,
                        reasoning_content=reasoning_content,
                    )
                    delta_assistant: dict[str, Any] = {
                        "role": "assistant",
                        "content": _strip_leak_notice(content),
                    }
                    if reasoning_content:
                        delta_assistant["reasoning_content"] = reasoning_content
                    if _rmode:
                        delta_assistant["reasoning_mode"] = _rmode
                    messages_delta.append(delta_assistant)
                    for steer in steers:
                        steer_content = steer["content"]
                        messages.append({"role": "user", "content": steer_content})
                        delta: dict[str, Any] = {
                            "role": "user",
                            "content": steer_content,
                        }
                        cid = steer.get("client_user_message_id")
                        if cid is not None:
                            delta["client_user_message_id"] = cid
                        if steer.get("input_items"):
                            delta["input_items"] = steer["input_items"]
                        messages_delta.append(delta)
                    # #821: refresh auto-sensed roots with the steering text
                    # (the user may name a new output dir mid-turn).
                    # #984: never for sub-agent turns — see above.
                    for steer in steers:
                        _user_texts.append(steer["content"])
                    if not getattr(turn, "is_subagent", False):
                        turn.user_mentioned_roots = extract_user_mentioned_roots(
                            _user_texts, workspace=getattr(turn, "workspace", None),
                        )
                    continue

                content = response.content or ""
                # Empty final round (only reasoning, no tool calls) must not
                # silently end the turn with a blank reply — nudge the model
                # to continue, bounded so a stuck model still fails loudly.
                if not content.strip():
                    if empty_response_nudges < _EMPTY_RESPONSE_NUDGE_LIMIT:
                        empty_response_nudges += 1
                        logger.warning(
                            "turn_runner: empty response (reasoning-only) for "
                            "turn={} — nudging model to continue ({}/{})",
                            turn.turn_id,
                            empty_response_nudges,
                            _EMPTY_RESPONSE_NUDGE_LIMIT,
                        )
                        messages = self._context.add_assistant_message(
                            messages=messages,
                            content="",
                            reasoning_content=reasoning_content,
                        )
                        messages.append({"role": "user", "content": _EMPTY_RESPONSE_NUDGE})
                        continue
                    from miqi.providers.resilience import ErrorKind, ProviderError

                    raise ProviderError(
                        kind=ErrorKind.FATAL,
                        message=(
                            "模型连续多轮只输出思考内容、未给出回答。"
                            "请重试，或更换模型/关闭深度思考。"
                        ),
                    )
                content, was_modified = sanitize_tool_call_text(
                    content, tool_names_from_definitions(tools)
                )
                if was_modified:
                    # The model wrote a tool call as plain text instead of
                    # using the tool-calling interface. The text is never
                    # executed (tool_text_guard), so feed the feedback back
                    # to the model and let it retry properly instead of
                    # ending the turn with an internal placeholder. The
                    # feedback message stays in the model context but is not
                    # persisted (messages_delta) — it is not user content.
                    tool_text_leaked = True
                    messages = self._context.add_assistant_message(
                        messages=messages,
                        content=content,
                    )
                    messages_delta.append({
                        "role": "assistant",
                        "content": _strip_leak_notice(content),
                    })
                    messages.append({"role": "user", "content": _TOOL_CALL_TEXT_FEEDBACK})
                    continue

                # Merge all collected turn-level reasoning into one payload for
                # the frontend so tool-call loops don't produce stacked blocks.
                merged_reasoning = (
                    "\n\n---\n\n".join(turn_level_reasoning_parts).strip()
                    or None
                )
                messages = self._context.add_assistant_message(
                    messages=messages,
                    content=content,
                    reasoning_content=merged_reasoning,
                )
                # Append final assistant message to delta
                delta_final: dict[str, Any] = {
                    "role": "assistant",
                    "content": _strip_leak_notice(content),
                }
                if merged_reasoning:
                    delta_final["reasoning_content"] = merged_reasoning
                if _rmode:
                    # Persist the mode so history restore can render the
                    # correct 🚀/🧠 label per message (#905 review).
                    delta_final["reasoning_mode"] = _rmode
                messages_delta.append(delta_final)
                return TurnResult(
                    final_content=content,
                    messages=messages,
                    tools_used=tools_used,
                    token_usage=getattr(response, "usage", {}) or {},
                    messages_delta=messages_delta,
                    reasoning=merged_reasoning,
                    reasoning_elapsed_s=reasoning_elapsed_s,
                )

            # Phase 24: record tool call starts in ledger
            if self._ledger is not None:
                for tc in response.tool_calls:
                    await self._ledger.append_item(
                        thread_id=turn.thread_id,
                        turn_id=turn.turn_id,
                        item_type="tool_call_started",
                        payload={
                            "tool_call_id": tc.id,
                            "name": tc.name,
                            "arguments": getattr(tc, "arguments", None),
                        },
                    )

            from miqi.protocol.events import ToolCallBeginEvent, ToolCallEndEvent

            # #680 desktop FAST budget — refuse budgeted-out tool calls
            # (方案 4 search phase + finalizing gate): skipped calls get a
            # synthetic "跳过" result so the model sees WHY and pivots to
            # answering from what it has.
            _skipped_ctx: list[tuple[Any, Any]] = []
            if _rmode == "fast":
                from types import SimpleNamespace
                _kept: list[Any] = []
                for tc in response.tool_calls:
                    reason = _budget_skip_reason(tc.name)
                    if reason:
                        _skipped_ctx.append((tc, SimpleNamespace(
                            result=f"[跳过] {reason}",
                            status=OrchestrationResult.SUCCESS,
                            duration_ms=0,
                        )))
                    else:
                        _kept.append(tc)
                response.tool_calls = _kept

            for tc in response.tool_calls:
                await self._events.emit(ToolCallBeginEvent(
                    turn_id=turn.turn_id,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    tool_display=self._format_tool_hint(tc.name, tc.arguments),
                    arguments=tc.arguments,
                ))

            # Execute tool calls concurrently through ToolRuntime
            contexts = await self._tools.execute_many(turn, response.tool_calls)

            for tc, ctx in zip(response.tool_calls, contexts):
                result_text = ctx.result or ""
                # paper_search / web_search: keep full result so frontend can
                # render result cards on the live tool row (#539)
                # other tools: truncate to 200 chars for preview
                if tc.name in ("paper_search", "web_search"):
                    output_preview = result_text
                else:
                    output_preview = result_text[:200]
                await self._events.emit(ToolCallEndEvent(
                    turn_id=turn.turn_id,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    success=ctx.status == OrchestrationResult.SUCCESS,
                    output_preview=output_preview,
                    output_size=len(result_text),
                    duration_ms=getattr(ctx, "duration_ms", 0),
                ))

            # Phase 24: record tool call completions in ledger
            if self._ledger is not None:
                for ctx in contexts:
                    await self._ledger.append_item(
                        thread_id=turn.thread_id,
                        turn_id=turn.turn_id,
                        item_type="tool_call_completed",
                        payload={
                            "tool_call_id": getattr(ctx, "tool_call_id", ""),
                            "result": getattr(ctx, "result", None),
                            "duration_ms": getattr(ctx, "duration_ms", 0),
                            "retry_count": getattr(ctx, "retry_count", 0),
                            "permission_verdict": (
                                ctx.permission_decision.verdict.value
                                if getattr(ctx, "permission_decision", None) is not None
                                else None
                            ),
                            "sandbox_type": (
                                ctx.sandbox_selection.sandbox_type.value
                                if getattr(ctx, "sandbox_selection", None) is not None
                                else None
                            ),
                        },
                    )

            # 1. Build assistant tool-call entries (no message mutation yet)
            assistant_tool_calls: list[dict[str, Any]] = []
            for tool_call in response.tool_calls:
                tools_used.append(tool_call.name)
                assistant_tool_calls.append({
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": (
                            tool_call.arguments_json
                            if hasattr(tool_call, "arguments_json")
                            else json.dumps(
                                getattr(tool_call, "arguments", {}) or {},
                                ensure_ascii=False,
                            )
                        ),
                    },
                })

            # 2. Assistant message with tool_calls MUST precede tool results
            _asst_content = response.content or ""
            _asst_content, _content_modified = sanitize_tool_call_text(
                _asst_content, tool_names_from_definitions(tools)
            )
            if _content_modified:
                # The model DID issue the real tool call above — a text-form
                # echo in the content is noise. Drop the internal placeholder
                # entirely instead of rendering it to the user.
                _asst_content = _asst_content.replace(LEAK_NOTICE, "").strip()
            messages = self._context.add_assistant_message(
                messages=messages,
                content=_asst_content,
                tool_calls=assistant_tool_calls,
                reasoning_content=reasoning_content,
            )
            # Persist assistant(tool_calls) in messages_delta
            asst_delta: dict[str, Any] = {
                "role": "assistant",
                "content": _asst_content or None,
                "tool_calls": assistant_tool_calls,
            }
            if reasoning_content:
                asst_delta["reasoning_content"] = reasoning_content
            messages_delta.append(asst_delta)

            # 3. Append tool results in order (assistant → tool → tool → …)
            # Budget-skipped calls (fast) get their synthetic skip results here
            # so the model sees the reason and pivots to answering.
            _all_pairs = list(zip(response.tool_calls, contexts)) + _skipped_ctx
            for tool_call, ctx in _all_pairs:
                messages = self._context.add_tool_result(
                    messages=messages,
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    content=ctx.result or "",
                    arguments=tool_call.arguments,
                )
                # Persist tool result in messages_delta
                messages_delta.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.name,
                    "content": ctx.result or "",
                    "arguments": tool_call.arguments,
                })
        # Exhausted iterations — issue #491: surface why the loop never
        # converged instead of returning a bare generic message.
        # #680 desktop FAST budget: a fast-mode termination (30s hard stop or
        # 3-round cap) is a BUDGET end, not a failure — return the last model
        # content with a mild notice instead of the failure diagnosis
        # (外部审阅 2026-08-24 缺陷 A/B: users saw an error, not an answer).
        if _rmode == "fast":
            _last_asst = ""
            for _m in reversed(messages):
                if isinstance(_m, dict) and _m.get("role") == "assistant" and _m.get("content"):
                    _last_asst = _m["content"]
                    break
            _note = (
                f"【极速模式】已到达轮数/时间预算，本轮到此为止。"
                f"已使用工具：{', '.join(dict.fromkeys(tools_used)) or '无'}。"
                + (f"\n\n{_last_asst}" if _last_asst else "")
            )
            messages_delta.append({"role": "assistant", "content": _note})
            return TurnResult(
                final_content=_note,
                messages=messages,
                tools_used=tools_used,
                messages_delta=messages_delta,
                reasoning_elapsed_s=reasoning_elapsed_s,
            )
        diagnosis = self._build_exhaustion_diagnosis(messages)
        content = (
            f"已达到最大迭代次数（{_effective_iterations}）。"
            f"已使用工具：{', '.join(dict.fromkeys(tools_used)) or '无'}。"
            f"请将任务拆分为更小的步骤重试。\n\n"
            f"【失败诊断】\n{diagnosis}"
        )
        if tool_text_leaked:
            content += (
                "\n\n另外，模型多次把工具调用写成了文本（如 functions.xxx(...)），"
                "这些调用未被执行。请重试或换一种表述。"
            )
        messages_delta.append({"role": "assistant", "content": content})
        return TurnResult(
            final_content=content,
            messages=messages,
            tools_used=tools_used,
            messages_delta=messages_delta,
            reasoning_elapsed_s=reasoning_elapsed_s,
        )

    async def run_agent_job(self, job: Any) -> TurnResult:
        """Run a sub-agent job through TurnRunner.

        Builds a TurnContext from the job metadata, resolves tools
        via the CapabilityResolver if available, and executes a
        single turn. Used by AgentJobRuntime._run().
        """
        from pathlib import Path

        from miqi.runtime.agent_registry import AgentRegistry
        from miqi.runtime.turn_context import TurnContext

        metadata = AgentRegistry().resolve(job.agent_type)
        turn = TurnContext(
            turn_id=job.job_id,
            agent_metadata=metadata,
            thread_id=job.thread_id,
            workspace=getattr(self._provider, "workspace", Path(".")),
            model=self._provider.get_default_model(),
            provider=self._provider,
            execution_policy="edit",  # sub-agents default to normal approval flow
            temperature=0.1,
            max_tokens=8192,
            # #984: sub-agents inherit the parent turn's authorized roots
            # instead of re-extracting them from their own (model-authored)
            # task text — see TurnContext.is_subagent.
            is_subagent=True,
            user_mentioned_roots=[Path(r) for r in (job.user_roots or [])],
        )

        # Resolve capabilities if available (Phase 13)
        if self._capability_resolver is not None:
            capabilities = self._capability_resolver.resolve(agent_metadata=metadata)
            turn.capabilities = capabilities
            tools = capabilities.tool_definitions
        else:
            tools = []

        # Execution policy — controls agent autonomy level
        # Three-layer: system prompt + tool set + approval flags.
        # Plan: strategist — read-only, proposes approach
        # Manual: collaborator — all tools, each step confirmed by user
        # Edit: developer — all tools, safe auto, dangerous ask
        # Auto: agent — all tools, bypass approval entirely

        from miqi.runtime.tool_policy import PLAN_BLOCKED_TOOLS

        if turn.execution_policy == "plan":
            tools = [t for t in tools if t.get("name") not in PLAN_BLOCKED_TOOLS]
            turn.bypass_approval = True  # plan mode tools are safe, deny-list still wins
        elif turn.execution_policy == "ask":
            # Legacy ask mode — filter write/exec tools
            tools = [t for t in tools if t.get("name") not in PLAN_BLOCKED_TOOLS]
        # manual / edit / auto: all tools available,
        # differentiation happens at approval layer

        if turn.execution_policy == "auto":
            turn.bypass_approval = True
        elif turn.execution_policy == "manual":
            turn.bypass_approval = False
            turn.force_approval = True
        # edit: both flags False → normal approval flow
        # plan: bypass_approval already set above

        return await self.run(
            turn=turn,
            user_content=job.task,
            system_prompt=metadata.system_prompt,
            tools=tools,
            max_iterations=SUBAGENT_MAX_ITERATIONS,
        )

    @staticmethod
    def _format_tool_hint(name: str, args: dict) -> str:
        """Format a tool call as a concise display hint.

        Path-like and command args show the target value (truncated at 50
        chars); every other arg shows only the parameter name. Values like
        paper titles, URLs, or queries are long strings that would leak
        into the hint instead of a concise call summary (issue #532).
        """
        if not args:
            return name
        for key in ("path", "file_path", "filename", "outPath", "command"):
            val = args.get(key)
            if isinstance(val, str) and val:
                if len(val) > 50:
                    return f'{name}("{val[:50]}…")'
                return f'{name}("{val}")'
        key = next(iter(args), "")
        return f"{name}({key}=…)" if key else name

    # ── Max-iterations diagnosis (issue #491) ──────────────────────────

    #: How many trailing tool results to scan for failure signals when the
    #: turn loop exhausts its iteration budget.
    _DIAGNOSIS_SCAN_TAIL = 8

    @classmethod
    def _extract_failure_signal(cls, name: str, content: str) -> str | None:
        """Extract a one-line failure signal from a single tool result.

        Returns ``None`` when the result carries no failure signal — e.g.
        a successful response the loop still failed to converge on.
        Understands the structured JSON shapes emitted by the built-in
        tools (paper_download/paper_search/paper_get/web_fetch) and the
        plain-text shapes of web_search/exec.
        """
        text = (content or "").strip()
        if not text:
            return None

        payload: Any = None
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            payload = None

        if isinstance(payload, dict):
            error = str(payload.get("error") or "").strip()
            ok = payload.get("ok", True)
            status = payload.get("status_code")
            paywall = bool(payload.get("paywall_suspected"))
            if error:
                detail = error[:160]
                if paywall:
                    detail += "（疑似付费墙/登录/机构访问限制）"
                elif isinstance(status, int) and status >= 400:
                    detail += f"（HTTP {status}）"
                return f"{name}: {detail}"
            if ok is False:
                return f"{name}: 工具报告失败"
            if paywall:
                return f"{name}: 疑似付费墙/登录拦截页"
            if isinstance(status, int) and status >= 400:
                return f"{name}: HTTP {status}"
            if payload.get("items") == [] or payload.get("count") == 0:
                return f"{name}: 未找到结果"
            return None

        # Plain-text results (web_search / exec)
        lowered = text.lower()
        if text.startswith("Error") or text.startswith("错误"):
            return f"{name}: {text[:160]}"
        if text.startswith("No results for") or "没有结果" in text:
            return f"{name}: 未找到结果"
        if "timed out" in lowered or "timeout" in lowered:
            return f"{name}: 请求超时"
        return None

    @classmethod
    def _build_exhaustion_diagnosis(cls, messages: list[dict[str, Any]]) -> str:
        """Build a structured diagnosis for the max-iterations exit path.

        Summarizes tool usage across the whole turn, then scans the
        trailing tool results for concrete failure signals (paywall,
        HTTP errors, empty results, timeouts) so the final message tells
        the user *why* the task failed instead of a bare generic hint.
        """
        tool_counts: dict[str, int] = {}
        tool_msgs: list[dict[str, Any]] = []
        for m in messages:
            if m.get("role") != "tool":
                continue
            name = str(m.get("name") or "未知工具")
            tool_counts[name] = tool_counts.get(name, 0) + 1
            tool_msgs.append(m)

        usage = "、".join(f"{n}×{c}" for n, c in tool_counts.items()) or "无"
        lines = [f"工具调用概况：{usage}"]
        if tool_msgs:
            signals: list[str] = []
            seen: set[str] = set()
            for m in tool_msgs[-cls._DIAGNOSIS_SCAN_TAIL:]:
                sig = cls._extract_failure_signal(
                    str(m.get("name") or ""),
                    str(m.get("content") or ""),
                )
                if sig and sig not in seen:
                    seen.add(sig)
                    signals.append(sig)
            if signals:
                lines.append("最近失败信号：")
                lines.extend(f"- {s}" for s in signals)
            else:
                lines.append(
                    "最近工具调用未返回明确失败信号——任务可能在持续尝试但未取得进展。"
                    "请告知用户当前状态，并建议拆分为更小的步骤或更换检索途径。"
                )
        return "\n".join(lines)
