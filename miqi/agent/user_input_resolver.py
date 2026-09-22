"""Shared user-input resolver bridging ask_user_confirm_card to the desktop
(issue #646).

The KUN runtime wires its own UserInputGate via the AgentLoop. The legacy
runtime (chat.send path, which the desktop currently uses) executes tools
through ToolRuntime/ToolOrchestrator without a gate. This module provides a
shared gate + injectable emitter so the legacy path can also block on a user
choice and emit user_input_requested events toward the desktop.

Wiring:
  bridge/loop.py (chat.send):
      set_user_input_emitter(lambda payload: asyncio.create_task(_emit("user_input_requested", payload)))
      app_server.register_method("userInput.resolve", user_input_resolve_handler)
"""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from miqi.kun_runtime.user_input_gate import UserInputGate

_gate = UserInputGate()
# One emitter slot per session (thread_id == session_key on the desktop
# path). A single process-global slot misrouted cards across concurrent
# sessions: session B's turn overwrote session A's emitter, and B finishing
# first cleared A's channel (CodeRabbit #711).
_emitters: dict[str, Callable[[dict[str, Any]], Any]] = {}
# thread_id → session_key mapping (desktop path): the runtime mints a
# server-generated thread id per session (threads.start), which differs
# from the session key the emitter is registered under.
_thread_sessions: dict[str, str] = {}
# Turn identity context (legacy path): the tool executes without thread/turn
# args, so the task runner publishes them here and the resolver reads them
# to scope remember + turn cancellation correctly.
_thread_ctx: ContextVar[tuple[str, str]] = ContextVar(
    "miqi_user_input_thread", default=("", "")
)


def set_user_input_emitter(
    session_key: str, emitter: Callable[[dict[str, Any]], Any] | None
) -> None:
    """Register (or clear) the emitter for one session.

    The bridge sets this per chat.send drain task keyed by session_key.
    When absent (headless), the resolver returns a cancelled result instead
    of blocking forever.
    """
    if emitter is None:
        _emitters.pop(session_key, None)
    else:
        _emitters[session_key] = emitter


def user_input_emitter_for(session_key: str) -> Callable[[dict[str, Any]], Any] | None:
    return _emitters.get(session_key)


def set_thread_session(thread_id: str, session_key: str) -> None:
    """Record which session a thread belongs to (bridge drain task)."""
    _thread_sessions[thread_id] = session_key


def clear_thread_session(thread_id: str) -> None:
    _thread_sessions.pop(thread_id, None)


def session_for_thread(thread_id: str) -> str | None:
    return _thread_sessions.get(thread_id)


def set_thread_context(thread_id: str, turn_id: str) -> None:
    """Publish the active legacy turn's identity for the resolver."""
    _thread_ctx.set((thread_id, turn_id))


def clear_thread_context() -> None:
    _thread_ctx.set(("", ""))


def current_thread_context() -> tuple[str, str]:
    return _thread_ctx.get()


def resolve_user_input(
    input_id: str,
    answers: dict[str, Any] | None = None,
    remember: bool = False,
) -> bool:
    """Resolve a pending user-input request (called by the app handler)."""
    return _gate.resolve(input_id, answers or {}, remember=remember)


def pending_thread_for_input(input_id: str) -> str | None:
    """Return the owning thread of a pending card, or None.

    Used by the userInput.resolve handler to authorize the resolving client
    against the session the card belongs to.
    """
    req = _gate.pending_request(input_id)
    return req.thread_id if req is not None else None


def make_resolver() -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the async resolver injected into AskUserConfirmCardTool.

    Blocks on the shared gate until the desktop resolves, times out, or the
    turn is cancelled. Emits user_input_requested so the desktop renders the
    confirm card.
    """

    async def resolver(payload: dict[str, Any]) -> dict[str, Any]:
        # Session identity: the model only supplies card fields, so the turn
        # context comes from the task runner's contextvar. Without it,
        # remember scoping and turn cancellation silently break (CodeRabbit #711).
        ctx_thread, ctx_turn = _thread_ctx.get()
        thread_id = str(payload.get("threadId") or ctx_thread or "")
        turn_id = str(payload.get("turnId") or ctx_turn or "")
        # The runtime's thread id differs from the session key the bridge
        # registers emitters under (threads.start mints it) — map through
        # the thread→session table, falling back to the thread id itself.
        session_key = session_for_thread(thread_id) or thread_id
        # Same remember key as the KUN path so the two runtimes share
        # remember semantics (issue #646 review).
        allow_remember = bool(payload.get("allow_remember_choice"))
        from miqi.kun_runtime.loop import _remember_key

        remember_key = _remember_key(payload) if allow_remember else None
        # Session-level remember: reuse the previous choice WITHOUT popping a
        # card, mirroring the KUN path. Every confirmation is audited,
        # including auto-resolved ones (issue #646 功能描述⑤).
        if remember_key is not None:
            cached = _gate.remembered_choice(thread_id, remember_key)
            if cached is not None:
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
                result = {
                    "status": "submitted",
                    "answers": dict(cached),
                    "remembered": True,
                    "request_id": f"user_input_{__import__('uuid').uuid4().hex[:12]}",
                }
                # Remembered confirm/cancel must carry the same semantic
                # classification as a fresh resolve (choice_role annotation).
                cid = str(cached.get("choice_id", ""))
                for c in payload.get("choices", []):
                    if isinstance(c, dict) and str(c.get("id", "")) == cid and c.get("role"):
                        result["answers"] = dict(cached)
                        result["answers"]["choice_role"] = str(c["role"])
                        break
                return result
        emitter = user_input_emitter_for(session_key)
        if emitter is None:
            return {
                "status": "cancelled",
                "reason": "no user-input channel (desktop bridge not wired)",
            }
        input_id = f"user_input_{__import__('uuid').uuid4().hex[:12]}"
        prompt = str(payload.get("message") or payload.get("title") or "")

        async def announce_pending() -> None:
            # Emit user_input_requested only when the request actually
            # becomes pending: concurrent confirm cards in the same turn
            # queue in the gate (issue #714 follow-up), and a queued card
            # must not surface before the previous one resolved.
            try:
                if asyncio.iscoroutinefunction(emitter):
                    await emitter({**payload, "input_id": input_id, "prompt": prompt})
                else:
                    emitter({**payload, "input_id": input_id, "prompt": prompt})
            except Exception:
                pass  # emitter failure must not block the tool; timeout will cancel

        timeout = payload.get("timeout_seconds")
        result = await _gate.request(
            thread_id=thread_id,
            turn_id=turn_id,
            item_id=f"item_{input_id[-6:]}",
            prompt=prompt,
            timeout=float(timeout) if timeout else None,
            input_id=input_id,
            remember_key=remember_key if allow_remember else None,
            choices=payload.get("choices", []),
            on_pending=announce_pending,
        )
        result["request_id"] = input_id
        return result

    return resolver


def resolver_to_tool_result(gate_result: dict[str, Any]) -> str:
    """Convert a gate result into the ask_user_confirm_card tool-result JSON."""
    from miqi.agent.tools.ask_user_confirm import AskUserConfirmCardTool

    return AskUserConfirmCardTool.build_result(gate_result)


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)
