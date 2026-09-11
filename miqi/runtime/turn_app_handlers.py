"""Codex-style turn AppServer handlers.

Registers turn/start, turn/interrupt, turn/steer, thread/compact/start,
and thread/inject_items.  All handlers flow through RuntimeSession.
"""

from __future__ import annotations

import uuid
from typing import Any

import miqi.runtime.protocol_specs as protocol_specs
from miqi.protocol.commands import UserMessage
from miqi.runtime.app_server import AppServer, AppServerError
from miqi.runtime.turn_event_adapter import CodexTurnEventAdapter
from miqi.runtime.turn_protocol import (
    TurnProtocolError,
    injected_message_to_provider_message,
    input_attachments,
    input_media,
    turn_view,
)
from miqi.runtime.turn_request_models import (
    ThreadCompactStartParams,
    ThreadInjectItemsParams,
    TurnInterruptParams,
    TurnStartParams,
    TurnSteerParams,
    validate_turn_params,
)

# ── helpers ────────────────────────────────────────────────────────────────


async def _require_session(registry: Any, client_id: str, session_id: str | None) -> Any:
    if session_id is None:
        raise AppServerError("session_id is required", code="INVALID_PARAMS")
    session = await registry.get_session(client_id, session_id)
    if session is None:
        raise AppServerError("Not authorized", code="UNAUTHORIZED")
    return session


def _turn_id() -> str:
    return f"turn-{str(uuid.uuid4())[:12]}"


# ── event drain ────────────────────────────────────────────────────────────


async def drain_turn_events(
    *,
    server: AppServer,
    session: Any,
    request_id: str,
    thread_id: str,
    turn_id: str,
    input_items: list[dict[str, Any]],
    client_user_message_id: str | None,
    emit_user_message_item: bool = True,
) -> None:
    adapter = CodexTurnEventAdapter(
        thread_id=thread_id,
        turn_id=turn_id,
        input_items=input_items,
        client_user_message_id=client_user_message_id,
        emit_user_message_item=emit_user_message_item,
    )
    try:
        while True:
            event = await session.next_event(timeout=300)
            if event is None:
                await server.emit_event(
                    session.session_id,
                    "error",
                    {"error": {"message": "Turn 超时（300s）"}},
                    request_id=request_id,
                )
                await server.emit_event(
                    session.session_id,
                    "turn/completed",
                    {"turn": turn_view(turn_id, thread_id, "failed", error_message="Turn 超时（300s）")},
                    request_id=request_id,
                )
                return

            for projected in adapter.project(event):
                await server.emit_event(
                    session.session_id,
                    projected["event"],
                    projected["data"],
                    request_id=request_id,
                )

            event_type = getattr(event, "type", "")
            if event_type in {"turn_complete", "turn_aborted"}:
                return
            # Phase 41 hardening: only terminate drain on non-recoverable errors
            if event_type == "error" and not getattr(event, "recoverable", True):
                return
    finally:
        # Phase 41 hardening v2: release the turn reservation so the
        # next turn/start for this thread can proceed.
        release = session.release_turn_reservation
        if callable(release):
            await release(thread_id)


# ── registration ───────────────────────────────────────────────────────────


def register_codex_turn_handlers(server: AppServer) -> None:
    """Register Codex-style turn, compact, and inject handlers on AppServer."""

    # ── turn/start ────────────────────────────────────────────────────────

    async def _turn_start(request_id, params, client_id, session_id, registry):
        typed = validate_turn_params(TurnStartParams, params)
        thread_id = typed.thread_id

        session = await _require_session(registry, client_id, session_id)

        # Phase 41 hardening v2: atomically check-and-reserve a turn slot.
        # This closes the race window between the previous active_turn_id
        # check and the subsequent session.submit(UserMessage).
        turn_id = _turn_id()
        if not await session.try_reserve_turn(thread_id, turn_id):
            raise AppServerError(
                "A turn is already running for this thread",
                code="INVALID_REQUEST",
            )

        drain_coro = None
        try:
            # Phase 41 hardening: validate thread exists before starting a turn
            thread_runtime = session.services.thread_runtime
            if thread_runtime is not None:
                thread = await thread_runtime.get_thread(thread_id)
                if thread is None:
                    raise AppServerError("Thread not found", code="NOT_FOUND")

            input_items = typed.input_items
            content = typed.content
            client_msg_id = typed.client_user_message_id

            server.subscribe(client_id, session.session_id)

            await session.submit(UserMessage(
                content=content,
                thread_id=thread_id,
                media=input_media(input_items),
                attachments=input_attachments(input_items),
                turn_id=turn_id,
                input_items=input_items,
                client_user_message_id=client_msg_id,
                settings_overrides=typed.settings_overrides,
            ))

            # Start the drain INSIDE the protected scope: if background-task
            # creation itself fails (server stopped, etc.), the reservation
            # must still be released — otherwise the thread stays locked for
            # the next turn/start (CodeRabbit #743).
            drain_coro = drain_turn_events(
                server=server,
                session=session,
                request_id=request_id,
                thread_id=thread_id,
                turn_id=turn_id,
                input_items=input_items,
                client_user_message_id=client_msg_id,
            )
            server.create_background_task(
                drain_coro,
                name=f"turn-drain:{session.session_id}:{turn_id}",
            )
        except BaseException:
            # Cover every exit path from the submit phase — including
            # asyncio.CancelledError, which bypasses `except Exception`
            # (issue #488): a cancelled handler must not leak the turn
            # reservation, or the next turn/start on this thread gets
            # INVALID_REQUEST.  release is idempotent (dict.pop), so the
            # drain's finally-block release stays safe too.  Also close the
            # un-scheduled drain coroutine to avoid a "coroutine was never
            # awaited" RuntimeWarning when task creation fails.
            if drain_coro is not None:
                drain_coro.close()
            await session.release_turn_reservation(thread_id)
            raise

        return {"result": {"turn": turn_view(turn_id, thread_id, "inProgress")}}

    # ── turn/interrupt ────────────────────────────────────────────────────

    async def _turn_interrupt(request_id, params, client_id, session_id, registry):
        typed = validate_turn_params(TurnInterruptParams, params)
        session = await _require_session(registry, client_id, session_id)
        accepted = await session.interrupt_turn(
            thread_id=typed.thread_id,
            turn_id=typed.turn_id,
        )
        if not accepted:
            raise AppServerError("Active turn not found", code="INVALID_REQUEST")
        return {"result": {}}

    # ── turn/steer ────────────────────────────────────────────────────────

    async def _turn_steer(request_id, params, client_id, session_id, registry):
        typed = validate_turn_params(TurnSteerParams, params)
        session = await _require_session(registry, client_id, session_id)
        accepted = await session.steer_turn(
            thread_id=typed.thread_id,
            expected_turn_id=typed.expected_turn_id,
            content=typed.content,
            input_items=typed.input_items,
            client_user_message_id=typed.client_user_message_id,
        )
        if not accepted:
            raise AppServerError("Active turn not steerable", code="INVALID_REQUEST")
        return {"result": {"turnId": typed.expected_turn_id}}

    # ── thread/inject_items ───────────────────────────────────────────────

    async def _thread_inject_items(request_id, params, client_id, session_id, registry):
        typed = validate_turn_params(ThreadInjectItemsParams, params)
        thread_id = typed.thread_id
        items = typed.items

        session = await _require_session(registry, client_id, session_id)

        history = session.services.history_runtime
        ledger = session.services.ledger_runtime
        if history is None or ledger is None:
            raise AppServerError("Runtime history is not available", code="INTERNAL")

        turn_id = f"inject-{str(uuid.uuid4())[:12]}"
        for item in items:
            try:
                message = injected_message_to_provider_message(item)
            except TurnProtocolError as exc:
                raise AppServerError("Invalid turn input", code="INVALID_PARAMS") from exc
            role = message["role"]
            content = message["content"]
            fields = message.get("message_fields", {})
            await history.append_message(
                thread_id=thread_id,
                turn_id=turn_id,
                role=role,
                content=content,
                payload={"message_fields": fields},
            )
            await ledger.append_item(
                thread_id=thread_id,
                turn_id=turn_id,
                item_type="message",
                role=role,
                content=content,
                payload={"message_fields": fields},
            )
        return {"result": {}}

    # ── thread/compact/start ──────────────────────────────────────────────

    async def _thread_compact_start(request_id, params, client_id, session_id, registry):
        typed = validate_turn_params(ThreadCompactStartParams, params)
        thread_id = typed.thread_id

        session = await _require_session(registry, client_id, session_id)
        turn_id = f"compact-{str(uuid.uuid4())[:12]}"
        server.subscribe(client_id, session.session_id)
        server.create_background_task(
            _run_thread_compaction(
                server=server,
                session=session,
                request_id=request_id,
                thread_id=thread_id,
                turn_id=turn_id,
            ),
            name=f"compact:{session.session_id}:{thread_id}",
        )
        return {"result": {}}

    # ── register ──────────────────────────────────────────────────────────

    server.register_method("turn/start", _turn_start, spec=protocol_specs.TURN_START)
    server.register_method("turn/interrupt", _turn_interrupt, spec=protocol_specs.TURN_INTERRUPT)
    server.register_method("turn/steer", _turn_steer, spec=protocol_specs.TURN_STEER)
    server.register_method("thread/compact/start", _thread_compact_start, spec=protocol_specs.THREAD_COMPACT_START)
    server.register_method("thread/inject_items", _thread_inject_items, spec=protocol_specs.THREAD_INJECT_ITEMS)


# ── background compaction ──────────────────────────────────────────────────


async def _run_thread_compaction(
    *,
    server: AppServer,
    session: Any,
    request_id: str,
    thread_id: str,
    turn_id: str,
) -> None:
    from miqi.runtime.turn_protocol import context_compaction_item
    from miqi.runtime.turn_protocol import turn_view as _turn_view

    await server.emit_event(
        session.session_id,
        "turn/started",
        {"turn": _turn_view(turn_id, thread_id, "inProgress")},
        request_id=request_id,
    )
    started = context_compaction_item(turn_id, status="inProgress")
    await server.emit_event(
        session.session_id,
        "item/started",
        {"threadId": thread_id, "turnId": turn_id, "item": started},
        request_id=request_id,
    )
    try:
        ctx_runtime = session.services.context_runtime
        history = session.services.history_runtime
        if ctx_runtime is None or history is None:
            raise RuntimeError("Context runtime is not available")
        await ctx_runtime.compact_thread(
            history_runtime=history,
            thread_id=thread_id,
            turn_id=turn_id,
            model=session.services.model_settings.model,
        )
        completed = context_compaction_item(turn_id, status="completed")
        await server.emit_event(
            session.session_id,
            "item/completed",
            {"threadId": thread_id, "turnId": turn_id, "item": completed},
            request_id=request_id,
        )
        await server.emit_event(
            session.session_id,
            "turn/completed",
            {"turn": _turn_view(turn_id, thread_id, "completed")},
            request_id=request_id,
        )
    except Exception:
        from loguru import logger
        logger.exception("thread/compact/start failed for thread {}", thread_id)
        await server.emit_event(
            session.session_id,
            "item/completed",
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": context_compaction_item(turn_id, status="failed"),
            },
            request_id=request_id,
        )
        await server.emit_event(
            session.session_id,
            "turn/completed",
            {
                "turn": _turn_view(
                    turn_id,
                    thread_id,
                    "failed",
                    error_message="上下文压缩失败，请查看运行时日志。",
                )
            },
            request_id=request_id,
        )
