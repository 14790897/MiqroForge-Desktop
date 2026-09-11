"""Replay/debug AppServer handlers.

Phase 40: legacy replay.* handlers are live-first/stored-fallback and
debug/replay/* handlers expose deterministic documents for Desktop.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import miqi.runtime.protocol_specs as protocol_specs
from miqi.runtime.app_server import AppServer, AppServerError, get_bridge_state
from miqi.runtime.replay_document import diff_replay_documents
from miqi.runtime.replay_inspector import ReplayInspector
from miqi.runtime.stored_runtime import (
    StoredThreadAmbiguousError,
    StoredThreadError,
    StoredThreadNotFoundError,
    StoredThreadUnauthorizedError,
)


def register_replay_handlers(server: AppServer) -> None:
    async def _replay_turns(request_id, params, client_id, session_id, registry):
        thread_id = _thread_id(params)
        live = await _live_session(registry, client_id, session_id)
        if live is not None:
            turns = await live.list_turns(thread_id)
            return {"result": {"turns": turns}}
        inspector = _inspector(registry, client_id)
        try:
            turns = await inspector.list_turn_ids(thread_id, session_id=_param_session_id(params, session_id))
        except Exception as exc:
            raise _stored_error(exc) from exc
        return {"result": {"turns": turns}}

    async def _replay_timeline(request_id, params, client_id, session_id, registry):
        thread_id = _thread_id(params)
        turn_id = _turn_id(params)
        live = await _live_session(registry, client_id, session_id)
        if live is not None:
            timeline = await live.get_turn_replay(thread_id, turn_id)
            return {"result": {"timeline": asdict(timeline) if timeline is not None else None}}
        inspector = _inspector(registry, client_id)
        try:
            timeline = await inspector.turn_timeline(
                thread_id, turn_id, session_id=_param_session_id(params, session_id),
            )
        except Exception as exc:
            raise _stored_error(exc) from exc
        return {"result": {"timeline": asdict(timeline) if timeline is not None else None}}

    async def _replay_messages(request_id, params, client_id, session_id, registry):
        thread_id = _thread_id(params)
        live = await _live_session(registry, client_id, session_id)
        if live is not None:
            messages = await live.get_provider_messages(thread_id)
            return {"result": {"messages": messages}}
        inspector = _inspector(registry, client_id)
        try:
            report = await inspector.provider_messages_report(
                thread_id, session_id=_param_session_id(params, session_id),
            )
        except Exception as exc:
            raise _stored_error(exc) from exc
        return {"result": {"messages": report["historyMessages"]}}

    async def _debug_thread(request_id, params, client_id, session_id, registry):
        inspector = _inspector(registry, client_id)
        try:
            document = await inspector.build_thread_document(
                _thread_id(params),
                session_id=_param_session_id(params, session_id),
                include_raw_ledger=bool(params.get("includeRawLedger", False)),
            )
        except Exception as exc:
            raise _stored_error(exc) from exc
        return {"result": {"document": document}}

    async def _debug_turn(request_id, params, client_id, session_id, registry):
        inspector = _inspector(registry, client_id)
        try:
            response = await inspector.build_turn_response(
                _thread_id(params),
                _turn_id(params),
                session_id=_param_session_id(params, session_id),
                include_raw_ledger=bool(params.get("includeRawLedger", False)),
            )
        except Exception as exc:
            raise _stored_error(exc) from exc
        return {"result": response}

    async def _debug_messages(request_id, params, client_id, session_id, registry):
        inspector = _inspector(registry, client_id)
        try:
            report = await inspector.provider_messages_report(
                _thread_id(params),
                session_id=_param_session_id(params, session_id),
            )
        except Exception as exc:
            raise _stored_error(exc) from exc
        return {"result": report}

    async def _debug_integrity(request_id, params, client_id, session_id, registry):
        inspector = _inspector(registry, client_id)
        try:
            report = await inspector.integrity_report(
                _thread_id(params),
                session_id=_param_session_id(params, session_id),
            )
        except Exception as exc:
            raise _stored_error(exc) from exc
        return {"result": {"integrity": report.to_dict()}}

    async def _debug_export(request_id, params, client_id, session_id, registry):
        return await _debug_thread(request_id, params, client_id, session_id, registry)

    async def _debug_diff(request_id, params, client_id, session_id, registry):
        left = params.get("leftDocument")
        right = params.get("rightDocument")
        if not isinstance(left, dict) or not isinstance(right, dict):
            raise AppServerError("leftDocument and rightDocument are required", code="INVALID_PARAMS")
        diff = diff_replay_documents(left, right)
        return {"result": {"diff": diff.to_dict()}}

    server.register_method("replay.turns", _replay_turns, spec=protocol_specs.REPLAY_TURNS)
    server.register_method("replay.timeline", _replay_timeline, spec=protocol_specs.REPLAY_TIMELINE)
    server.register_method("replay.messages", _replay_messages, spec=protocol_specs.REPLAY_MESSAGES)
    server.register_method("debug/replay/thread", _debug_thread)
    server.register_method("debug/replay/turn", _debug_turn)
    server.register_method("debug/replay/messages", _debug_messages)
    server.register_method("debug/replay/integrity", _debug_integrity)
    server.register_method("debug/replay/export", _debug_export)
    server.register_method("debug/replay/diff", _debug_diff)


async def _live_session(registry: Any, client_id: str, session_id: str | None) -> Any | None:
    if session_id is None:
        return None
    return await registry.get_session(client_id, session_id)


def _thread_id(params: dict[str, Any]) -> str:
    value = params.get("threadId") or params.get("thread_id")
    if not value:
        raise AppServerError("threadId is required", code="INVALID_PARAMS")
    return str(value)


def _turn_id(params: dict[str, Any]) -> str:
    value = params.get("turnId") or params.get("turn_id")
    if not value:
        raise AppServerError("turnId is required", code="INVALID_PARAMS")
    return str(value)


def _param_session_id(params: dict[str, Any], dispatch_session_id: str | None) -> str | None:
    return params.get("sessionId") or params.get("session_id") or dispatch_session_id


def _runtime_db_path(registry: Any) -> Path:
    state = get_bridge_state(registry)
    config = state.load_config()
    return config.workspace_path / ".miqi-runtime" / "runtime.db"


def _inspector(registry: Any, client_id: str) -> ReplayInspector:
    return ReplayInspector(_runtime_db_path(registry), client_id=client_id)


def _stored_error(exc: Exception) -> AppServerError:
    if isinstance(exc, AppServerError):
        raise exc
    if isinstance(exc, StoredThreadUnauthorizedError):
        return AppServerError("Not authorized", code="UNAUTHORIZED")
    if isinstance(exc, StoredThreadAmbiguousError):
        return AppServerError("Multiple stored threads match; provide sessionId", code="AMBIGUOUS_THREAD")
    if isinstance(exc, StoredThreadNotFoundError):
        return AppServerError("Thread not found", code="NOT_FOUND")
    if isinstance(exc, StoredThreadError):
        return AppServerError("Stored replay failed", code="INTERNAL")
    return AppServerError("Stored replay failed", code="INTERNAL")
