"""Codex-style thread AppServer handlers.

Registers thread/start, thread/resume, thread/fork, thread/read,
thread/turns/list, thread/turns/items/list, thread/name/set,
thread/rollback, thread/loaded/list, thread/list, thread/export,
and thread/import on an AppServer instance.

Phase 39: thread/read and thread/turns/list now support stored
fallback when no live RuntimeSession is available.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

import miqi.runtime.protocol_specs as protocol_specs
from miqi.runtime.app_server import AppServer, AppServerError, get_bridge_state
from miqi.runtime.stored_runtime import (
    StoredRuntimeReader,
    StoredThreadAmbiguousError,
    StoredThreadBundle,
    StoredThreadError,
    StoredThreadNotFoundError,
    StoredThreadUnauthorizedError,
)
from miqi.runtime.thread_projection import (
    ThreadProjectionRuntime,
    project_stored_thread,
    project_stored_turns,
)
from miqi.runtime.thread_protocol import page_items
from miqi.runtime.thread_request_models import validate_thread_params


def _resolve_session_id_for_stored(
    params: dict[str, Any], client_id: str, session_id: str | None
) -> str | None:
    """Pick the session_id to filter stored threads by, namespaced to client.

    Issue #490: stored ``runtime_threads`` rows carry the fully-namespaced
    ``session_id`` (``{client_id}:{session_key}``, see ``create_session`` in
    app_server.py). The dispatch layer (loop.py) namespaces the
    ``session_key``/``session_id`` *params* but the frontend sends the bare
    session_key under the camelCase ``sessionId`` param, which dispatch does
    NOT namespace — so ``desktop:1739...`` reached ``list_threads`` and
    matched no stored rows (they begin with ``miqi-desktop:``). That made the
    resume path silently return an empty list → every chat.send minted a
    fresh orphaned thread_id.

    Prefer the dispatch-supplied ``session_id`` (already namespaced). When
    falling back to an explicit ``sessionId``/``session_id`` param, add the
    ``{client_id}:`` prefix only when the value is this caller's own bare
    session_key. The desktop frontend's session_keys always start with
    ``desktop:`` (``desktop:default`` or ``desktop:{ts}`` — App.tsx:129) or are
    the literal ``default``; that marker is the signal for "not yet
    namespaced". Any other ``xxx:``-prefixed value is treated as an
    already-namespaced (possibly foreign) session_id and returned UNCHANGED so
    the downstream ownership check (``session_belongs_to_client``) can reject
    it — preserving cross-client isolation.

    Why not namespace every non-``{client_id}:``-prefixed value generically
    (as a prior revision tried): that breaks ``thread/import``'s foreign-
    session rejection. A caller importing with ``sessionId="client-b:default"``
    (foreign) must be rejected UNAUTHORIZED, but a blanket "namespace it under
    the caller" rule would rewrite that to ``client-a:client-b:default`` —
    which the ownership check then accepts (it belongs to client-a), so the
    import would SUCCEED instead of being rejected
    (``test_thread_import_rejects_foreign_session_id``). The only unambiguous
    signal that a value is THIS client's own bare key (vs. a foreign
    already-namespaced id) is the client's known keyspace marker; there is no
    client registry to consult, so the marker stays explicit. Adding a new
    client's keyspace means adding its marker here.
    """
    raw = params.get("sessionId") or params.get("session_id")
    candidate = session_id if session_id is not None else raw
    if candidate is None or not client_id or not isinstance(candidate, str):
        return candidate
    prefix = f"{client_id}:"
    if candidate.startswith(prefix) or candidate == client_id:
        return candidate
    if candidate.startswith("desktop:") or candidate == "default":
        # Desktop frontend's bare session_key — namespace it under this client.
        return f"{prefix}{candidate}"
    # Already namespaced (e.g. another client's "client-b:default"): pass
    # through unchanged so the ownership check rejects it. Do NOT namespace —
    # see docstring (would mask thread/import's foreign-session rejection).
    return candidate


def register_codex_thread_handlers(server: AppServer) -> None:
    """Register all Codex-style thread methods on an AppServer instance."""

    async def _thread_start(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread/start", params)
        # Respect dispatch session_id when provided; otherwise derive from params.
        if session_id is not None:
            session = await _require_session(registry, client_id, session_id)
        else:
            session = await _get_or_create_session(registry, client_id, params)
        threads = session.services.thread_runtime
        projection = _projection(session)
        thread = await threads.create_thread(
            title=typed.title or params.get("name") or "New thread",
            thread_id=params.get("threadId") or params.get("thread_id"),
            ephemeral=typed.ephemeral,
            cwd=typed.cwd,
            metadata={"source": params.get("sessionStartSource", "startup")},
        )
        server.subscribe(client_id, session.session_id)
        view = await projection.read_thread(thread.thread_id, include_turns=True)
        await server.emit_event(
            session.session_id, "thread/started", {"thread": view.to_dict()},
        )
        return {"result": {"thread": view.to_dict()}}

    async def _thread_resume(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread/resume", params)
        thread_id = typed.thread_id
        include_turns = not typed.exclude_turns
        # Respect dispatch session_id when provided; otherwise derive from params.
        if session_id is not None:
            session = await _require_session(registry, client_id, session_id)
        else:
            session = await _get_or_create_session(registry, client_id, params)
        projection = _projection(session)
        view = await projection.read_thread(
            thread_id,
            include_turns=include_turns,
            items_view=typed.items_view or "summary",
        )
        server.subscribe(client_id, session.session_id)
        await server.emit_event(
            session.session_id, "thread/started", {"thread": view.to_dict()},
        )
        return {"result": {"thread": view.to_dict()}}

    async def _thread_fork(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread/fork", params)
        source_id = typed.thread_id
        include_turns = not typed.exclude_turns
        items_view = typed.items_view or "summary"

        # Live-first: use existing session if available.
        if session_id is not None:
            live = await registry.get_session(client_id, session_id)
            if live is not None:
                threads = live.services.thread_runtime
                ledger = live.services.ledger_runtime
                history = getattr(live.services, "history_runtime", None)
                projection = _projection(live)
                child = await threads.fork_thread(
                    source_id,
                    title=typed.title or "Fork",
                )
                if ledger is not None:
                    await ledger.copy_thread_items(source_id, child.thread_id)
                if history is not None and hasattr(history, "copy_thread_items"):
                    await history.copy_thread_items(source_id, child.thread_id)
                view = await projection.read_thread(
                    child.thread_id,
                    include_turns=include_turns,
                    items_view=items_view,
                )
                await server.emit_event(
                    live.session_id, "thread/started", {"thread": view.to_dict()},
                )
                return {"result": {"thread": view.to_dict()}}

        # Stored fallback: fork without a live session.
        reader = _stored_reader(registry, client_id)
        target_session_id = _resolve_session_id_for_stored(params, client_id, session_id)
        try:
            bundle = await reader.fork_stored_thread(
                source_id,
                title=typed.title or "Fork",
                target_session_id=target_session_id,
                new_thread_id=None,  # Always auto-generate to avoid UNIQUE conflicts
                exclude_turns=not include_turns,
            )
        except Exception as exc:
            raise _stored_error(exc) from exc
        view = project_stored_thread(bundle, include_turns=include_turns, items_view=items_view)
        return {"result": {"thread": view.to_dict()}}

    async def _thread_read(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread/read", params)
        thread_id = typed.thread_id
        include_turns = typed.include_turns
        items_view = typed.items_view or "summary"

        # Live-first: if the caller already has a live session, use it.
        if session_id is not None:
            live = await registry.get_session(client_id, session_id)
            if live is not None:
                view = await _projection(live).read_thread(
                    thread_id, include_turns=include_turns, items_view=items_view
                )
                return {"result": {"thread": view.to_dict()}}

        # Stored fallback: read from the runtime DB without a live session.
        reader = _stored_reader(registry, client_id)
        try:
            bundle = await reader.load_bundle(
                thread_id,
                session_id=_resolve_session_id_for_stored(params, client_id, session_id),
            )
        except Exception as exc:
            raise _stored_error(exc) from exc
        view = project_stored_thread(bundle, include_turns=include_turns, items_view=items_view)
        return {"result": {"thread": view.to_dict()}}

    async def _thread_turns_list(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread/turns/list", params)
        thread_id = typed.thread_id
        items_view = typed.items_view or "summary"
        limit = typed.limit
        cursor = typed.cursor
        sort_direction = typed.sort_direction or "desc"

        # Live-first path
        if session_id is not None:
            live = await registry.get_session(client_id, session_id)
            if live is not None:
                projection = _projection(live)
                turns = await projection.list_turns(thread_id, items_view=items_view)
                page = page_items(
                    [turn.to_dict() for turn in turns],
                    limit=limit, cursor=cursor, sort_direction=sort_direction,
                )
                return {"result": page.to_dict()}

        # Stored fallback
        reader = _stored_reader(registry, client_id)
        try:
            bundle = await reader.load_bundle(
                thread_id,
                session_id=_resolve_session_id_for_stored(params, client_id, session_id),
            )
        except Exception as exc:
            raise _stored_error(exc) from exc
        turns = project_stored_turns(thread_id, bundle.ledger_items, items_view=items_view)
        page = page_items(
            [turn.to_dict() for turn in turns],
            limit=limit, cursor=cursor, sort_direction=sort_direction,
        )
        return {"result": page.to_dict()}

    async def _thread_turns_items_list(request_id, params, client_id, session_id, registry):
        validate_thread_params("thread/turns/items/list", params)
        raise AppServerError(
            "thread/turns/items/list is not supported yet",
            code="UNSUPPORTED_METHOD",
            recoverable=False,
        )

    async def _thread_list(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread/list", params)
        reader = _stored_reader(registry, client_id)
        threads = await reader.list_threads(
            include_archived=typed.archived,
            session_id=_resolve_session_id_for_stored(params, client_id, session_id),
            cwd=typed.cwd,
            search_term=typed.search_term,
        )
        views = []
        for thread in threads:
            # Count distinct turns via a COUNT(DISTINCT turn_id) aggregate on
            # runtime_history_items — the SAME table the model reloads on resume
            # (task_runner.load_messages), so the richness signal tracks what the
            # model will actually see. The aggregate avoids materializing row
            # content/payload (load_history_items SELECT *-es every item just to
            # read turn_id); listing N threads is N cheap counts, not N full-
            # content loads. Issue #490: prefer the thread holding the most real
            # history over the merely most-recently-touched one.
            try:
                distinct_turns = await reader.count_distinct_turns(thread)
            except Exception:
                logger.warning(
                    "thread/list: failed to count turns for thread={} in session={}; "
                    "falling back to turn_count=0",
                    thread.thread_id, thread.session_id,
                )
                distinct_turns = 0
            views.append(
                project_stored_thread(
                    StoredThreadBundle(thread=thread, ledger_items=[]),
                    include_turns=False,
                    turn_count=distinct_turns,
                ).to_dict()
            )
        page = page_items(
            views,
            limit=typed.limit,
            cursor=typed.cursor,
            sort_direction=typed.sort_direction or "desc",
        )
        return {"result": page.to_dict()}

    async def _thread_export(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread/export", params)
        thread_id = typed.thread_id
        reader = _stored_reader(registry, client_id)
        try:
            bundle = await reader.load_bundle(
                thread_id,
                session_id=_resolve_session_id_for_stored(params, client_id, session_id),
            )
        except Exception as exc:
            raise _stored_error(exc) from exc
        from miqi.runtime.thread_export import build_export_document
        provider_messages = await reader.load_provider_messages(bundle.thread)
        document = build_export_document(
            thread=bundle.thread,
            ledger_items=bundle.ledger_items,
            provider_messages=provider_messages,
        )
        return {"result": {"document": document.to_dict()}}

    async def _thread_import(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread/import", params)
        document = typed.document
        target_session_id = _resolve_session_id_for_stored(params, client_id, session_id)
        if target_session_id is None:
            session_key = params.get("sessionKey") or params.get("session_key") or "default"
            if str(session_key).startswith(f"{client_id}:"):
                session_key = str(session_key)[len(f"{client_id}:"):]
            target_session_id = f"{client_id}:{session_key}"
        reader = _stored_reader(registry, client_id)
        try:
            imported_thread_id = await reader.import_document(
                document,
                session_id=target_session_id,
                thread_id=params.get("threadId") or params.get("thread_id"),
                overwrite=typed.overwrite,
            )
            bundle = await reader.load_bundle(imported_thread_id, session_id=target_session_id)
        except (StoredThreadError, StoredThreadUnauthorizedError) as exc:
            raise _stored_error(exc) from exc
        except Exception as exc:
            from loguru import logger
            logger.warning("thread/import failed: {}", exc)
            raise AppServerError("Thread import rejected", code="INVALID_PARAMS") from exc
        view = project_stored_thread(bundle, include_turns=typed.include_turns)
        return {"result": {"thread": view.to_dict()}}

    async def _thread_name_set(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread/name/set", params)
        session = await _require_session(registry, client_id, session_id)
        thread = await session.services.thread_runtime.rename_thread(
            typed.thread_id, typed.name,
        )
        view = await _projection(session).read_thread(thread.thread_id, include_turns=False)
        await server.emit_event(
            session.session_id, "thread/name/updated",
            {"threadId": thread.thread_id, "name": thread.title},
        )
        return {"result": {"thread": view.to_dict()}}

    async def _thread_rollback(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread/rollback", params)
        thread_id = typed.thread_id
        drop_last_turns = typed.drop_last_turns

        # Live-first path
        if session_id is not None:
            live = await registry.get_session(client_id, session_id)
            if live is not None:
                marker = await live.services.ledger_runtime.append_rollback_marker(
                    thread_id, drop_last_turns=drop_last_turns,
                )
                removed = list(marker.payload.get("removed_turn_ids", []))
                history = getattr(live.services, "history_runtime", None)
                if history is not None:
                    await history.delete_turn_items(thread_id, removed)
                view = await _projection(live).read_thread(
                    thread_id, include_turns=True,
                    items_view=typed.items_view or "summary",
                )
                await server.emit_event(
                    live.session_id, "thread/rollback",
                    {"threadId": thread_id, "removedTurnIds": removed},
                )
                return {"result": {"thread": view.to_dict()}}

        # Stored fallback
        reader = _stored_reader(registry, client_id)
        try:
            bundle = await reader.rollback_stored_thread(
                thread_id,
                drop_last_turns=drop_last_turns,
                session_id=_resolve_session_id_for_stored(params, client_id, session_id),
            )
        except Exception as exc:
            raise _stored_error(exc) from exc
        view = project_stored_thread(
            bundle, include_turns=True, items_view=typed.items_view or "summary",
        )
        return {"result": {"thread": view.to_dict()}}

    async def _thread_loaded_list(request_id, params, client_id, session_id, registry):
        validate_thread_params("thread/loaded/list", params)
        loaded = []
        for sid, clients in registry._session_clients.items():
            if client_id in clients:
                session = await registry.get_session(client_id, sid)
                if session is None:
                    continue
                threads = await session.services.thread_runtime.list_threads(include_archived=False)
                loaded.extend(t.thread_id for t in threads)
        return {"result": {"threadIds": sorted(set(loaded))}}

    server.register_method("thread/start", _thread_start, spec=protocol_specs.THREAD_START)
    server.register_method("thread/resume", _thread_resume, spec=protocol_specs.THREAD_RESUME)
    server.register_method("thread/fork", _thread_fork, spec=protocol_specs.THREAD_FORK)
    server.register_method("thread/read", _thread_read, spec=protocol_specs.THREAD_READ)
    server.register_method("thread/turns/list", _thread_turns_list, spec=protocol_specs.THREAD_TURNS_LIST)
    server.register_method("thread/turns/items/list", _thread_turns_items_list, spec=protocol_specs.THREAD_TURNS_ITEMS_LIST)
    server.register_method("thread/list", _thread_list, spec=protocol_specs.THREAD_LIST)
    server.register_method("thread/export", _thread_export, spec=protocol_specs.THREAD_EXPORT)
    server.register_method("thread/import", _thread_import, spec=protocol_specs.THREAD_IMPORT)
    server.register_method("thread/name/set", _thread_name_set, spec=protocol_specs.THREAD_NAME_SET)
    server.register_method("thread/rollback", _thread_rollback, spec=protocol_specs.THREAD_ROLLBACK)
    server.register_method("thread/loaded/list", _thread_loaded_list, spec=protocol_specs.THREAD_LOADED_LIST)


def _projection(session: Any) -> ThreadProjectionRuntime:
    return ThreadProjectionRuntime(
        session.services.thread_runtime,
        session.services.ledger_runtime,
        session.services.replay_runtime,
    )


async def _get_or_create_session(registry: Any, client_id: str, params: dict) -> Any:
    """Get or create a RuntimeSession for session-creating handlers.

    Uses the raw session_key (e.g. "default", "project-a") so that
    ClientSessionRegistry.create_session produces the correctly namespaced
    session_id ``f"{client_id}:{session_key}"`` (e.g. "client-1:default").
    If params carries an explicit namespaced sessionId/session_id we check
    for that session first; when it doesn't exist we still create from the
    raw session_key so the registry owns the canonical id.
    """
    session_key = params.get("sessionKey") or params.get("session_key") or "default"
    # Defend against already-namespaced session_key (e.g. "client-1:default").
    # ClientSessionRegistry.create_session builds session_id as
    #   f"{client_id}:{session_key}"
    # so passing an already-namespaced value would produce a double-namespaced
    # id like "client-1:client-1:default". Strip the prefix when present.
    ns_prefix = f"{client_id}:"
    if session_key.startswith(ns_prefix):
        session_key = session_key[len(ns_prefix):]
    # Build the canonical session_id that create_session would produce.
    canonical_id = f"{client_id}:{session_key}"
    # Also accept an explicit sessionId (already-namespaced or bare).
    explicit_id = params.get("sessionId") or params.get("session_id")

    # Prefer the explicit id for existence check, fall back to canonical.
    lookup_id = explicit_id or canonical_id
    session = await registry.get_session(client_id, lookup_id)
    if session is not None:
        return session

    # Create new session using bridge context.
    state = get_bridge_state(registry)
    config = state.load_config()
    from miqi.providers.factory import make_provider
    try:
        provider = make_provider(config)
    except ValueError as exc:
        logger.warning("make_provider failed: {}", exc)
        raise AppServerError(
            "No API key configured — set one in Settings > Models",
            code="NO_API_KEY",
        ) from exc
    workspace = config.workspace_path

    # Read per-session workspace from session metadata if available —
    # the frontend persists it via sessions.get(workspace=...).  Without
    # this, thread/start creates the RuntimeSession with the DEFAULT
    # workspace and chat.send's workspace param is ignored because the
    # runtime already exists.
    from pathlib import Path as _WsPath
    try:
        from miqi.session.manager import SessionManager
        sm = SessionManager(config.workspace_path)
        sess = sm.get_or_create(session_key, client_id=client_id)
        ws_meta = sess.metadata.get("workspace")
        if ws_meta:
            workspace = _WsPath(ws_meta)
    except Exception as exc:
        logger.debug("thread/start: failed to read session workspace metadata: {}", exc)

    _ensure_sm = getattr(state, '_ensure_sandbox_manager', None)
    if _ensure_sm is not None:
        _ensure_sm()

    return await registry.create_session(
        client_id=client_id,
        session_key=session_key,
        config=config,
        provider=provider,
        workspace=workspace,
        sandbox_manager=(
            None if getattr(state, "_sandbox_manager", None) in (None, "disabled")
            else state._sandbox_manager
        ),
    )


async def _require_session(registry: Any, client_id: str, session_id: str | None) -> Any:
    """Require that a session exists and client is authorized."""
    if session_id is None:
        raise AppServerError("session_id is required", code="INVALID_PARAMS")
    session = await registry.get_session(client_id, session_id)
    if session is None:
        raise AppServerError("Not authorized", code="UNAUTHORIZED")
    return session


# ── Stored-thread helpers (Phase 39) ────────────────────────────────────────


def _runtime_db_path_from_registry(registry: Any) -> Path:
    state = get_bridge_state(registry)
    config = state.load_config()
    return config.workspace_path / ".miqi-runtime" / "runtime.db"


def _stored_reader(registry: Any, client_id: str) -> StoredRuntimeReader:
    return StoredRuntimeReader(_runtime_db_path_from_registry(registry), client_id=client_id)


def _stored_error(exc: Exception) -> AppServerError:
    if isinstance(exc, StoredThreadUnauthorizedError):
        return AppServerError("Not authorized", code="UNAUTHORIZED")
    if isinstance(exc, StoredThreadAmbiguousError):
        return AppServerError("Multiple stored threads match; provide sessionId", code="AMBIGUOUS_THREAD")
    if isinstance(exc, StoredThreadNotFoundError):
        return AppServerError("Thread not found", code="NOT_FOUND")
    if isinstance(exc, StoredThreadError):
        return AppServerError("Stored thread read failed", code="INTERNAL")
    return AppServerError("Stored thread read failed", code="INTERNAL")
