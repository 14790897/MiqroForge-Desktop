"""KUN AgentLoop — core execution engine for the desktop workbench runtime.

Aligns with KUN ``loop/agent-loop.ts``.
Orchestrates the full turn pipeline: drain steering → model_step → tool dispatch → loop.

All dependencies are constructor-injected for testability.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from miqi.agent.agent_mode import get_mode_config
from miqi.agent.tools.user_roots import extract_user_mentioned_roots
from miqi.kun_runtime.cancellation import CancellationToken, InflightTracker
from miqi.kun_runtime.compactor import ContextCompactor
from miqi.kun_runtime.event_recorder import RuntimeEventRecorder
from miqi.kun_runtime.model_client import (
    ModelRequest,
    ModelToolSpec,
)
from miqi.kun_runtime.stores import FileSessionStore, FileThreadStore
from miqi.kun_runtime.tool_host import (
    ASK_USER_CONFIRM_TOOL,
    ToolCallLike,
    ToolHostContext,
    ToolHostResult,
)
from miqi.kun_runtime.tool_storm_breaker import ToolStormBreaker
from miqi.kun_runtime.turn_service import TurnService
from miqi.kun_runtime.usage import UsageService

# ═══════════════════════════════════════════════════════════════════════════════
# Options
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class AgentLoopOptions:
    """Dependencies and configuration for the KUN AgentLoop."""

    thread_store: FileThreadStore
    session_store: FileSessionStore
    model: Any  # MiQiModelClient | FakeModelClient
    tool_host: Any  # MiQiToolHost | FakeToolHost
    usage: UsageService
    events: RuntimeEventRecorder
    turns: TurnService
    inflight: InflightTracker
    compactor: ContextCompactor

    now_iso: Any = field(default_factory=lambda: _utc_now_iso)

    # Optional
    approval_gate: Any = None
    user_input_gate: Any = None
    token_economy: dict[str, Any] | None = None
    tool_storm: dict[str, Any] | None = None
    auto_model_router: Any = None


# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

PARALLEL_READ_ONLY_TOOL_NAMES = frozenset({"read", "grep", "find", "ls", "read_file", "list_dir", "web_search", "web_fetch", "paper_search", "paper_get"})
MAX_PARALLEL_TOOL_CALLS = 3


def _remember_key(payload: dict[str, Any]) -> str:
    """Stable session-remember key: full card content (issue #646).

    Title + choices alone is not enough: two different plans can share a
    title, and reusing a remembered choice across different plans would
    auto-approve work the user never saw (CodeRabbit #711).
    """
    import hashlib

    title = str(payload.get("title", ""))
    message = str(payload.get("message", ""))
    steps = [
        (str(s.get("id", "")), str(s.get("title", "")))
        for s in (payload.get("steps") or [])
        if isinstance(s, dict)
    ]
    choices = sorted(
        (str(c.get("id", "")), str(c.get("label", "")))
        for c in (payload.get("choices") or [])
        if isinstance(c, dict)
    )
    raw = f"{title}|{message}|{steps}|{choices}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _turn_user_texts(
    thread: dict[str, Any], items: list[dict[str, Any]], turn_id: str,
) -> list[str]:
    """Collect the current turn's user-message texts (prompt + steering).

    Issue #821: only user-role content is scanned for directory mentions —
    never model output or tool results, so prompt injection via documents
    cannot grant roots.
    """
    texts: list[str] = []
    for turn in thread.get("turns") or []:
        if turn.get("id") == turn_id:
            prompt = str(turn.get("prompt") or "")
            if prompt:
                texts.append(prompt)
            break
    for item in items:
        if item.get("turnId") == turn_id and item.get("kind") == "user_message":
            text = str(item.get("text") or "")
            if text and text not in texts:
                texts.append(text)
    return texts


# ═══════════════════════════════════════════════════════════════════════════════
# AgentLoop
# ═══════════════════════════════════════════════════════════════════════════════


class AgentLoop:
    """Python implementation of the KUN agent loop.

    Usage::

        loop = AgentLoop(opts)
        status = await loop.run_turn("th1", "t1")
    """

    def __init__(self, opts: AgentLoopOptions):
        self._opts = opts
        self._tool_storm_breakers: dict[str, ToolStormBreaker] = {}
        # FAST v2: per-turn runtime budget state (finalizing flag + search
        # phase count), consumed by _model_step / tool dispatch.
        self._turn_state: dict[str, dict] = {}

    # ── Public API ──────────────────────────────────────────────────────

    async def run_turn(
        self, thread_id: str, turn_id: str
    ) -> Literal["completed", "failed", "aborted"]:
        """Run a turn end-to-end. Returns the final turn status."""
        token = self._opts.turns.get_abort_token(turn_id)
        if token is None:
            await self._opts.turns.finish_turn(thread_id, turn_id, "failed", error="no abort token")
            return "failed"
        if token.is_set():
            await self._opts.turns.finish_turn(thread_id, turn_id, "aborted")
            return "aborted"

        try:
            if self._opts.tool_storm and self._opts.tool_storm.get("enabled", True):
                self._tool_storm_breakers[turn_id] = ToolStormBreaker(
                    window_size=self._opts.tool_storm.get("windowSize", 8),
                    threshold=self._opts.tool_storm.get("threshold", 3),
                )

            await self._record_pipeline(thread_id, turn_id, "setup")
            await self._record_pipeline(thread_id, turn_id, "pre_start")
            await self._drain_steering(thread_id, turn_id, token)
            await self._record_pipeline(thread_id, turn_id, "post_start")

            status = await self._loop(thread_id, turn_id, token)
            await self._opts.turns.finish_turn(thread_id, turn_id, status)
            return status
        except Exception as exc:
            message = str(exc)
            logger.exception(f"AgentLoop run_turn failed: {message}")
            await self._opts.turns.finish_turn(thread_id, turn_id, "failed", error=message)
            return "failed"
        finally:
            self._tool_storm_breakers.pop(turn_id, None)

    # ── Loop ────────────────────────────────────────────────────────────

    async def _loop(
        self, thread_id: str, turn_id: str, token: CancellationToken
    ) -> Literal["completed", "failed", "aborted"]:
        # Decision-loop fuse (issue #680): fast mode caps model→tool rounds so
        # the model cannot spiral into search→search→search; think = unlimited.
        thread = await self._opts.thread_store.get(thread_id) or {}
        mode_cfg = get_mode_config((thread.get("metadata") or {}).get("mode"))
        max_rounds = mode_cfg.tool.max_tool_rounds or 100  # safety cap

        # FAST v2 (ChatGPT 评审收敛版): runtime TIME budget — a prompt-level
        # "answer in 30s" is not enforcement; the loop itself must finalize.
        # Enter finalization at (budget - grace), hard stop at budget.
        t0 = time.monotonic()
        budget_s = mode_cfg.tool.time_budget_s
        grace_s = mode_cfg.tool.finalization_grace_s
        finalize_at = (t0 + budget_s - grace_s) if budget_s else None
        hard_stop_at = (t0 + budget_s) if budget_s else None
        # Per-turn state consulted by _model_step (finalize prompt injection,
        # tool refusal, search-phase accounting).
        self._turn_state[turn_id] = {
            "finalizing": False,
            "search_phases": 0,
            "max_search_phase": mode_cfg.tool.max_search_phase or 99,
        }
        try:
            for step in range(max_rounds):
                if token.is_set():
                    return "aborted"
                now = time.monotonic()
                if hard_stop_at is not None and now >= hard_stop_at:
                    # Budget exhausted: stop the loop; the answer produced by
                    # the last (finalize) step stands.
                    logger.warning(f"Fast time budget exhausted for turn {turn_id}")
                    return "completed"
                if (
                    finalize_at is not None
                    and now >= finalize_at
                    and not self._turn_state[turn_id]["finalizing"]
                ):
                    self._turn_state[turn_id]["finalizing"] = True
                    logger.info(f"Fast turn {turn_id} entered finalization (budget {budget_s}s)")
                await self._drain_steering(thread_id, turn_id, token)
                result = await self._model_step(thread_id, turn_id, token, step)
                if result == "stop":
                    return "completed"
                if result in ("failed", "aborted"):
                    return result
            logger.warning(f"Max steps reached for turn {turn_id}")
            return "completed"
        finally:
            self._turn_state.pop(turn_id, None)
    # ── Model Step ──────────────────────────────────────────────────────

    async def _model_step(
        self, thread_id: str, turn_id: str, token: CancellationToken, step_index: int = 0
    ) -> Literal["continue", "stop", "failed", "aborted"]:
        await self._record_pipeline(thread_id, turn_id, "input_received", {"stepIndex": step_index})

        # Load thread and turn
        thread = await self._opts.thread_store.get(thread_id) or {}
        turn = await self._opts.turns.get_turn(thread_id, turn_id) or {}

        # Load and heal history
        loaded_items = await self._opts.session_store.load_items(thread_id)
        await self._record_pipeline(thread_id, turn_id, "input_cached")

        # Resolve model
        model = turn.get("model") or thread.get("model") or getattr(self._opts.model, "model", "deepseek-chat")
        await self._record_pipeline(thread_id, turn_id, "input_routed", {"model": model})

        approval_gate = self._opts.approval_gate
        user_input_gate = self._opts.user_input_gate

        async def await_approval(payload: dict[str, Any]) -> Literal["allow", "deny"]:
            if approval_gate is None:
                return "allow"
            return await approval_gate.request(
                thread_id,
                turn_id,
                str(payload.get("toolName") or ""),
                str(payload.get("summary") or "Approve tool call"),
                payload,
            )

        async def await_user_input(payload: dict[str, Any]) -> dict[str, Any]:
            """Blocking human-in-the-loop confirmation (issue #646).

            Emits user_input_requested, marks the turn waiting_for_user, and
            waits on the user-input gate. Resolution (submitted/cancelled)
            comes from the desktop via UserInputGate.resolve(); timeout or
            turn cancellation resolves as cancelled.
            """
            if user_input_gate is None:
                return {"status": "cancelled", "reason": "user_input_gate unavailable"}

            # Session-level remember (issue #646): same thread + same card
            # (title+choices) reuses the previous choice without popping a card.
            allow_remember = bool(payload.get("allow_remember_choice"))
            remember_key = _remember_key(payload) if allow_remember else None
            if remember_key is not None:
                cached = user_input_gate.remembered_choice(thread_id, remember_key)
                if cached is not None:
                    # Audit the reuse too — issue #646 requires a record of
                    # every confirmation, including auto-resolved ones.
                    try:
                        from miqi.agent.user_input_history import add_user_input_history

                        add_user_input_history(
                            title=str(payload.get("title") or ""),
                            message=str(payload.get("message") or ""),
                            choices=payload.get("choices", []),
                            status="submitted",
                            choice_id=str(cached.get("choice_id", "")),
                            choice_label=str(cached.get("choice_label", "")),
                            reason="remembered",
                            thread_id=thread_id,
                            turn_id=turn_id,
                            input_id="",
                        )
                    except Exception:
                        pass  # audit is best-effort, never blocks the turn
                    return {"status": "submitted", "answers": cached, "remembered": True}

            input_id = f"user_input_{uuid.uuid4().hex[:12]}"
            item_id = f"item_{turn_id}_{input_id[-6:]}"
            prompt = str(payload.get("message") or payload.get("title") or "")

            # Announce the pending item + event + waiting_for_user status
            # only once the request ACTUALLY becomes pending: concurrent
            # confirm cards in the same turn queue in the gate (issue #714
            # follow-up), and a queued request must not surface its card
            # before the previous card resolved.
            async def announce_pending() -> None:
                await self._opts.turns.apply_item(thread_id, {
                    "kind": "user_input",
                    "id": item_id,
                    "turnId": turn_id,
                    "threadId": thread_id,
                    "role": "system",
                    "inputId": input_id,
                    "prompt": prompt,
                    "status": "pending",
                    "title": payload.get("title"),
                    "message": payload.get("message"),
                    "steps": payload.get("steps", []),
                    "choices": payload.get("choices", []),
                    "timeout_seconds": payload.get("timeout_seconds"),
                    "allow_remember_choice": payload.get("allow_remember_choice", False),
                    "createdAt": self._opts.now_iso(),
                })
                await self._opts.events.record({
                    "kind": "user_input_requested",
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "inputId": input_id,
                    "itemId": item_id,
                    "status": "pending",
                    "title": payload.get("title"),
                    "message": payload.get("message"),
                    "steps": payload.get("steps", []),
                    "choices": payload.get("choices", []),
                    "timeoutSeconds": payload.get("timeout_seconds"),
                    "allowRememberChoice": payload.get("allow_remember_choice", False),
                    "createdAt": self._opts.now_iso(),
                })
                await self._opts.turns.update_turn_status(thread_id, turn_id, "waiting_for_user")

            result: dict[str, Any] | None = None
            try:
                timeout = payload.get("timeout_seconds")
                result = await user_input_gate.request(
                    thread_id,
                    turn_id,
                    item_id,
                    prompt,
                    timeout=float(timeout) if timeout else None,
                    # The announced input_id MUST be passed through — a fresh
                    # gate-generated id would make desktop resolve() calls
                    # miss and every card block until timeout (CodeRabbit #711).
                    input_id=input_id,
                    remember_key=_remember_key(payload) if allow_remember else None,
                    choices=payload.get("choices", []),
                    on_pending=announce_pending,
                )
            finally:
                # Resolve the pending item (submitted/cancelled) and restore
                # the turn to running. result may be None when request raised
                # (e.g. turn cancellation) — every downstream read must go
                # through the submitted/answers locals, never result.get()
                # directly, or the cleanup itself raises AttributeError and
                # masks the original exception (CodeRabbit #666).
                submitted = result is not None and result.get("status") == "submitted"
                answers = (result or {}).get("answers") or {}
                item_patch = {
                    "status": "submitted" if submitted else "cancelled",
                    "resolution": answers or (None if submitted else {"status": "cancelled"}),
                    "finishedAt": self._opts.now_iso(),
                }
                if item_patch.get("resolution") is None:
                    item_patch["resolution"] = {}
                await self._opts.turns.update_item(thread_id, item_id, item_patch)
                await self._opts.events.record({
                    "kind": "user_input_resolved",
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "inputId": input_id,
                    "itemId": item_id,
                    "status": "submitted" if submitted else "cancelled",
                    "resolution": item_patch["resolution"],
                    "createdAt": self._opts.now_iso(),
                })
                await self._opts.turns.update_turn_status(thread_id, turn_id, "running")

                # Remember choice for this session (issue #646)
                if remember_key is not None and submitted:
                    user_input_gate.remember(thread_id, remember_key, answers)

                # Observability (issue #646, 功能描述⑤): audit trail.
                # Best-effort and guarded — an audit failure inside finally
                # must never replace the in-flight gate exception
                # (CodeRabbit #711).
                try:
                    from miqi.agent.user_input_history import add_user_input_history

                    add_user_input_history(
                        title=str(payload.get("title") or prompt),
                        message=str(payload.get("message") or ""),
                        choices=payload.get("choices", []),
                        status="submitted" if submitted else "cancelled",
                        choice_id=str(answers.get("choice_id", "")),
                        choice_label=str(answers.get("choice_label", "")),
                        reason="" if submitted else str((result or {}).get("reason", "cancelled")),
                        thread_id=thread_id,
                        turn_id=turn_id,
                        input_id=input_id,
                    )
                except Exception:
                    pass  # audit is best-effort, never blocks the turn

            if result is None:
                # gate.request raised and the exception is propagating — a
                # None result would crash the caller's build_result(); hand
                # back a structured cancelled result instead (never reached
                # when the exception propagates, but safe for gate impls
                # that return None without raising).
                return {"status": "cancelled", "reason": "user-input request failed", "answers": {"status": "cancelled"}}
            return result

        # Reasoning mode (issue #680): fast = Answer-oriented (30s, short
        # generation, parallel search), think = current behavior.
        mode_cfg = get_mode_config((thread.get("metadata") or {}).get("mode"))

        # User-mentioned output dirs (issue #821): auto-sense directories the
        # user named in this turn's messages so file tools can read/write
        # them (e.g. "输出到 C:\Users\x\Desktop\test_result").  Extraction is
        # best-effort — a failure must never kill the turn.
        _user_texts = _turn_user_texts(thread, loaded_items, turn_id)
        _ws_raw = thread.get("workspace") or ""
        try:
            _user_roots = extract_user_mentioned_roots(
                _user_texts, workspace=Path(_ws_raw) if _ws_raw else None,
            )
        except Exception:
            logger.warning("user_roots extraction failed — continuing without extra roots", exc_info=True)
            _user_roots = []
        if _user_roots:
            logger.debug("turn {}: user-mentioned roots: {}", turn_id, _user_roots)

        # List tools
        tool_context = ToolHostContext(
            thread_id=thread_id,
            turn_id=turn_id,
            workspace=thread.get("workspace", ""),
            thread_mode=thread.get("mode"),
            approval_policy=thread.get("approvalPolicy", "auto"),
            autonomy_mode=thread.get("autonomyMode", "supervised"),
            abort_signal=token,
            active_skill_ids=turn.get("activeSkillIds", []),
            await_approval=await_approval if approval_gate is not None else None,
            await_user_input=await_user_input if user_input_gate is not None else None,
            # Reasoning mode (issue #680): parallel breadth search for fast.
            mode=mode_cfg.mode,
            search_strategy=mode_cfg.search,
            parallel_limit=mode_cfg.tool.parallel_limit,
            user_mentioned_roots=[str(r) for r in _user_roots],
        )
        tools = await self._opts.tool_host.list_tools(tool_context)
        tool_specs = [ModelToolSpec(
            name=t["name"],
            description=t.get("description", ""),
            input_schema=t.get("inputSchema", {}),
            tool_kind=t.get("toolKind"),
        ) for t in tools]

        # Compaction
        history = await self._compact_if_needed(loaded_items, model, token, thread_id, turn_id)
        if token.is_set():
            return "aborted"
        await self._record_pipeline(thread_id, turn_id, "input_compressed", {"historyItems": len(history)})

        # Build model request
        # FAST v2 finalization: within the grace window the model must wrap up
        # with what it has — no more tool calls, honest completion report.
        _finalizing = bool(self._turn_state.get(turn_id, {}).get("finalizing"))
        _finalize_snippet = (
            "\n\n[极速模式收尾] 时间预算即将用完：请不要再调用任何工具，"
            "用已获得的信息完成请求并直接给出最终回答；"
            "明确说明已完成的部分与未能完成的部分，绝不要假装未完成的动作成功。"
            if _finalizing
            else ""
        )
        request = ModelRequest(
            thread_id=thread_id,
            turn_id=turn_id,
            model=model,
            system_prompt="You are Kun, a careful and helpful AI assistant."
            + (f"\n\n{mode_cfg.prompt_snippet}" if mode_cfg.prompt_snippet else "")
            + _finalize_snippet,
            history=history,
            tools=tool_specs,
            temperature=0.1,
            max_tokens=mode_cfg.generation.max_tokens,
        )

        # Token economy (optional)
        token_econ = self._opts.token_economy or {}
        if token_econ.get("enabled"):
            from miqi.kun_runtime.token_economy import TOKEN_ECONOMY_INSTRUCTION
            request.context_instructions = request.context_instructions or []
            request.context_instructions.append(TOKEN_ECONOMY_INSTRUCTION)

        # ask_user_confirm_card usage guidance (issue #646, 功能描述④)
        if any(t.name == ASK_USER_CONFIRM_TOOL for t in tool_specs):
            from miqi.agent.tools.ask_user_confirm import ASK_USER_CONFIRM_INSTRUCTION
            request.context_instructions = request.context_instructions or []
            request.context_instructions.append(ASK_USER_CONFIRM_INSTRUCTION)

        # 回答可视化与引用标注（issue #671）
        from miqi.agent.answer_style import VISUAL_ANSWER_INSTRUCTION
        request.context_instructions = request.context_instructions or []
        request.context_instructions.append(VISUAL_ANSWER_INSTRUCTION)

        await self._record_pipeline(thread_id, turn_id, "pre_send", {
            "model": model, "historyItems": len(history), "toolCount": len(tools),
        })
        await self._record_pipeline(thread_id, turn_id, "post_send", {"model": model})

        # Stream model response
        text_accumulator = ""
        reasoning_accumulator = ""
        completed_tool_calls: list[ToolCallLike] = []
        stop_reason = "stop"

        async for chunk in self._opts.model.stream(request):
            if token.is_set():
                return "aborted"

            if chunk.kind == "assistant_text_delta":
                text_accumulator += chunk.text or ""
                await self._opts.events.record({
                    "kind": "assistant_text_delta",
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "itemId": f"item_text_{turn_id}",
                    "item": {
                        "id": f"item_text_{turn_id}",
                        "turnId": turn_id,
                        "threadId": thread_id,
                        "role": "assistant",
                        "status": "running",
                        "kind": "assistant_text",
                        "createdAt": self._opts.now_iso(),
                        "text": chunk.text or "",
                    },
                })

            elif chunk.kind == "assistant_reasoning_delta":
                reasoning_accumulator += chunk.text or ""

            elif chunk.kind == "tool_call_complete":
                call = ToolCallLike(
                    call_id=chunk.callId or "",
                    tool_name=chunk.toolName or "",
                    arguments=chunk.arguments or {},
                )

                # Apply tool storm breaker
                storm = self._tool_storm_breakers.get(turn_id)
                if storm:
                    inspection = storm.inspect(call.tool_name, call.arguments)
                    if inspection["suppress"]:
                        await self._persist_suppressed_tool_call(thread_id, turn_id, call, inspection.get("reason"))
                        continue

                completed_tool_calls.append(call)

                # Persist tool call item
                item_id = f"item_tool_{turn_id}_{call.call_id}"
                await self._opts.turns.apply_item(thread_id, {
                    "id": item_id,
                    "turnId": turn_id,
                    "threadId": thread_id,
                    "role": "assistant",
                    "status": "completed",
                    "kind": "tool_call",
                    "createdAt": self._opts.now_iso(),
                    "toolName": call.tool_name,
                    "callId": call.call_id,
                    "toolKind": "tool_call",
                    "arguments": call.arguments,
                })
                await self._opts.events.record({
                    "kind": "tool_call_ready",
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "itemId": item_id,
                    "callId": call.call_id,
                    "toolName": call.tool_name,
                    "readyCount": len(completed_tool_calls),
                })

            elif chunk.kind == "usage":
                usage_snap = self._opts.usage.record(thread_id, chunk.usage or {})
                await self._opts.events.record({
                    "kind": "usage",
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "model": model,
                    "usage": usage_snap,
                })

            elif chunk.kind == "completed":
                stop_reason = chunk.stopReason or "stop"

            elif chunk.kind == "error":
                await self._opts.events.record({
                    "kind": "error",
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "message": chunk.message or "Unknown error",
                    "code": chunk.code,
                })
                stop_reason = "error"

        await self._record_pipeline(thread_id, turn_id, "response_received", {
            "stopReason": stop_reason,
            "toolCallCount": len(completed_tool_calls),
        })

        # Persist assistant text item
        if text_accumulator:
            await self._opts.turns.apply_item(thread_id, {
                "id": f"item_text_{turn_id}",
                "turnId": turn_id,
                "threadId": thread_id,
                "role": "assistant",
                "status": "completed",
                "kind": "assistant_text",
                "createdAt": self._opts.now_iso(),
                "finishedAt": self._opts.now_iso(),
                "text": text_accumulator,
            })

        # Persist reasoning if present
        if reasoning_accumulator:
            await self._opts.turns.apply_item(thread_id, {
                "id": f"item_reasoning_{turn_id}",
                "turnId": turn_id,
                "threadId": thread_id,
                "role": "assistant",
                "status": "completed",
                "kind": "assistant_reasoning",
                "createdAt": self._opts.now_iso(),
                "text": reasoning_accumulator,
            })

        if stop_reason == "error":
            return "failed"

        # If no tool calls, we're done
        if not completed_tool_calls:
            return "stop"

        # Dispatch tool calls
        dispatched = await self._dispatch_tool_calls(
            thread_id, turn_id, completed_tool_calls, tool_context, token,
        )
        if dispatched == "aborted":
            return "aborted"
        return "continue"

    # ── Tool Dispatch ───────────────────────────────────────────────────

    def _budget_skip_reason(self, turn_id: str, call: ToolCallLike) -> str | None:
        """FAST v2 budget gate: finalizing → refuse ALL new tools; search
        phase budget → refuse extra web_search phases.  Returns the skip
        reason (persisted as a suppressed result) or None to execute."""
        st = self._turn_state.get(turn_id)
        if not st:
            return None
        if st.get("finalizing"):
            return "时间预算已到（极速模式收尾阶段，不再调用工具）"
        if call.tool_name == "web_search":
            if st["search_phases"] >= st["max_search_phase"]:
                return "极速模式搜索预算已用尽（最多一轮搜索，已并行抓取）"
            st["search_phases"] += 1
        return None

    async def _dispatch_tool_calls(
        self,
        thread_id: str,
        turn_id: str,
        calls: list[ToolCallLike],
        context: ToolHostContext,
        token: CancellationToken,
    ) -> Literal["continue", "aborted"]:
        index = 0
        while index < len(calls):
            if token.is_set():
                return "aborted"

            call = calls[index]

            # FAST v2 budget gate (finalize / search phase) — before storm.
            skip_reason = self._budget_skip_reason(turn_id, call)
            if skip_reason:
                await self._persist_suppressed_tool_call(thread_id, turn_id, call, skip_reason)
                index += 1
                continue

            # Storm check
            storm = self._tool_storm_breakers.get(turn_id)
            if storm:
                inspection = storm.inspect(call.tool_name, call.arguments)
                if inspection["suppress"]:
                    await self._persist_suppressed_tool_call(thread_id, turn_id, call, inspection.get("reason"))
                    index += 1
                    continue

            # Check if parallel-safe
            if not _is_parallel_safe(call, context.approval_policy):
                result = await self._opts.tool_host.execute(call, context)
                await self._persist_tool_result(thread_id, turn_id, call, result)
                index += 1
                continue

            # Batch parallel-safe calls
            batch: list[ToolCallLike] = [call]
            index += 1
            max_parallel = context.parallel_limit or MAX_PARALLEL_TOOL_CALLS
            while len(batch) < max_parallel and index < len(calls):
                next_call = calls[index]
                if not _is_parallel_safe(next_call, context.approval_policy):
                    break
                # FAST v2 budget gate for batch members (search phase count).
                skip_reason = self._budget_skip_reason(turn_id, next_call)
                if skip_reason:
                    await self._persist_suppressed_tool_call(thread_id, turn_id, next_call, skip_reason)
                    index += 1
                    continue
                # Storm check for next
                if storm:
                    ins = storm.inspect(next_call.tool_name, next_call.arguments)
                    if ins["suppress"]:
                        await self._persist_suppressed_tool_call(thread_id, turn_id, next_call, ins.get("reason"))
                        index += 1
                        continue
                batch.append(next_call)
                index += 1

            # Execute batch in parallel
            import asyncio as _asyncio
            tasks = [
                self._opts.tool_host.execute(c, context)
                for c in batch
            ]
            results = await _asyncio.gather(*tasks, return_exceptions=True)
            for batch_call, result in zip(batch, results):
                if isinstance(result, BaseException):
                    logger.error(f"Tool {batch_call.tool_name} failed: {result}")
                    result = ToolHostResult(item={
                        "kind": "tool_result",
                        "id": f"item_{turn_id}_{batch_call.call_id}",
                        "turnId": turn_id,
                        "threadId": thread_id,
                        "role": "tool",
                        "status": "failed",
                        "createdAt": self._opts.now_iso(),
                        "toolName": batch_call.tool_name,
                        "callId": batch_call.call_id,
                        "toolKind": "tool_call",
                        "output": f"Tool execution failed: {result}",
                        "isError": True,
                    })
                await self._persist_tool_result(thread_id, turn_id, batch_call, result)

        return "continue"

    # ── Persistence ─────────────────────────────────────────────────────

    async def _persist_tool_result(
        self, thread_id: str, turn_id: str, call: ToolCallLike, result: ToolHostResult
    ) -> None:
        await self._opts.turns.apply_item(thread_id, result.item)

    async def _persist_suppressed_tool_call(
        self, thread_id: str, turn_id: str, call: ToolCallLike, reason: str | None
    ) -> None:
        item = {
            "kind": "tool_result",
            "id": f"item_{call.call_id}_storm",
            "turnId": turn_id,
            "threadId": thread_id,
            "role": "tool",
            "status": "failed",
            "createdAt": self._opts.now_iso(),
            "toolName": call.tool_name,
            "callId": call.call_id,
            "toolKind": "tool_call",
            "output": reason or "duplicate tool call suppressed by repeat-loop guard",
            "isError": True,
        }
        await self._opts.turns.apply_item(thread_id, item)
        await self._opts.events.record({
            "kind": "tool_storm_suppressed",
            "threadId": thread_id,
            "turnId": turn_id,
            "toolName": call.tool_name,
            "callId": call.call_id,
            "message": reason or "duplicate tool call suppressed",
        })

    # ── Helpers ─────────────────────────────────────────────────────────

    async def _drain_steering(self, thread_id: str, turn_id: str, token: CancellationToken) -> None:
        pending = self._opts.turns.drain_steering(thread_id)
        if not pending:
            return
        for text in pending:
            item = {
                "id": f"item_steered_{_new_id_suffix()}",
                "turnId": turn_id,
                "threadId": thread_id,
                "role": "user",
                "status": "completed",
                "kind": "user_message",
                "createdAt": self._opts.now_iso(),
                "finishedAt": self._opts.now_iso(),
                "text": text,
            }
            await self._opts.turns.apply_item(thread_id, item)

    async def _compact_if_needed(
        self, items: list[dict[str, Any]], model: str, token: CancellationToken,
        thread_id: str, turn_id: str,
    ) -> list[dict[str, Any]]:
        plan = self._opts.compactor.plan_compaction(items, model)
        if plan is None:
            return items
        result = self._opts.compactor.compact(
            thread_id=thread_id,
            turn_id=turn_id,
            history=items,
            pinned_constraints=["user: preserve recent turns"],
            keep_recent=plan["keepRecent"],
            reason=plan["reason"],
            mode=plan["mode"],
        )
        if result["replacedTokens"] > 0:
            await self._opts.session_store.append_item(thread_id, result["summaryItem"])
            await self._opts.events.record({
                "kind": "compaction_completed",
                "threadId": thread_id,
                "turnId": turn_id,
                "itemId": result["summaryItem"]["id"],
                "summary": result["summaryItem"]["summary"],
                "replacedTokens": result["replacedTokens"],
            })
        return result["next"]

    async def _record_pipeline(
        self, thread_id: str, turn_id: str, stage: str, details: dict[str, Any] | None = None
    ) -> None:
        labels = {
            "setup": "Setup", "pre_start": "Pre-Start", "post_start": "Post-Start",
            "input_received": "Input Received", "input_cached": "Input Cached",
            "input_routed": "Input Routed", "input_compressed": "Input Compressed",
            "input_remembered": "Input Remembered",
            "pre_send": "Pre-Send", "post_send": "Post-Send",
            "response_received": "Response Received",
        }
        event: dict[str, Any] = {
            "kind": "pipeline_stage",
            "threadId": thread_id,
            "turnId": turn_id,
            "stage": stage,
            "label": labels.get(stage, stage),
        }
        if details:
            event["details"] = details
        await self._opts.events.record(event)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _is_parallel_safe(call: ToolCallLike, approval_policy: str) -> bool:
    if call.tool_name not in PARALLEL_READ_ONLY_TOOL_NAMES:
        return False
    if approval_policy in ("untrusted", "never"):
        return False
    return True


def _new_id_suffix() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
