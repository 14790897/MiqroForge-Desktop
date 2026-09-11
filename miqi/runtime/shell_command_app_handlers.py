"""Codex-style thread/shellCommand AppServer handler."""

from __future__ import annotations

import uuid
from typing import Any

import miqi.runtime.protocol_specs as protocol_specs
from miqi.protocol.commands import RunUserShellCommand
from miqi.runtime.app_server import AppServer, AppServerError
from miqi.runtime.turn_app_handlers import drain_turn_events


async def _require_session(registry: Any, client_id: str, session_id: str | None) -> Any:
    if session_id is None:
        raise AppServerError("session_id is required", code="INVALID_PARAMS")
    session = await registry.get_session(client_id, session_id)
    if session is None:
        raise AppServerError("Not authorized", code="UNAUTHORIZED")
    return session


def _shell_turn_id() -> str:
    return f"shell-{str(uuid.uuid4())[:12]}"


def _command(params: dict[str, Any]) -> str:
    raw = params.get("command")
    if not isinstance(raw, str) or not raw.strip():
        raise AppServerError("command is required", code="INVALID_PARAMS")
    if len(raw) > 16_384:
        raise AppServerError("command is too long", code="INVALID_PARAMS")
    return raw


def register_shell_command_handlers(server: AppServer) -> None:
    async def _thread_shell_command(request_id, params, client_id, session_id, registry):
        session = await _require_session(registry, client_id, session_id)
        thread_id = params.get("threadId") or params.get("thread_id")
        if not thread_id:
            raise AppServerError("threadId is required", code="INVALID_PARAMS")

        command = _command(params)
        cwd = params.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise AppServerError("cwd must be a string", code="INVALID_PARAMS")

        thread_runtime = getattr(session.services, "thread_runtime", None)
        if thread_runtime is not None:
            thread = await thread_runtime.get_thread(thread_id)
            if thread is None:
                raise AppServerError("Thread not found", code="NOT_FOUND")

        active_turn_id = session.active_turn_id(thread_id)
        if active_turn_id is not None:
            await session.submit(RunUserShellCommand(
                command=command,
                thread_id=thread_id,
                turn_id=active_turn_id,
                cwd=cwd,
                standalone=False,
            ))
            return {"result": {}}

        turn_id = _shell_turn_id()
        if not await session.try_reserve_turn(thread_id, turn_id):
            raise AppServerError(
                "A turn is already running for this thread",
                code="INVALID_REQUEST",
            )

        drain_coro = None
        try:
            await session.submit(RunUserShellCommand(
                command=command,
                thread_id=thread_id,
                turn_id=turn_id,
                cwd=cwd,
                standalone=True,
            ))

            server.subscribe(client_id, session.session_id)
            drain_coro = drain_turn_events(
                server=server,
                session=session,
                request_id=request_id,
                thread_id=thread_id,
                turn_id=turn_id,
                input_items=[],
                client_user_message_id=None,
                emit_user_message_item=False,
            )
            server.create_background_task(
                drain_coro,
                name=f"shell-drain:{session.session_id}:{turn_id}",
            )
        except BaseException:
            # asyncio.CancelledError derives from BaseException (not
            # Exception); a cancelled handler (client disconnect) would
            # otherwise leak the reservation and block future turns (#488).
            # Also close the un-scheduled drain coroutine to avoid a
            # "coroutine was never awaited" RuntimeWarning when task
            # creation fails.
            if drain_coro is not None:
                drain_coro.close()
            await session.release_turn_reservation(thread_id)
            raise

        return {"result": {}}

    server.register_method("thread/shellCommand", _thread_shell_command, spec=protocol_specs.THREAD_SHELL_COMMAND)
