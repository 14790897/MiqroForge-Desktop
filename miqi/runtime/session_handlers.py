"""Session handlers for AppServer dispatch.

Phase 28.4: Migrates sessions.list, sessions.get, sessions.delete,
sessions.archive, sessions.unarchive, sessions.list_archived,
sessions.get_tracked_files, and sessions.clear_tracked_files from
bridge legacy handlers to AppServer async handlers.

Key semantics:
- sessions.list: merges active (AppServer registry) and inactive (disk)
  sessions for the requesting client. Active sessions show "running" status.
- sessions.get: checks AppServer registry first, falls back to SessionManager.
- sessions.delete: stops RuntimeSession if active, cleans AppServer registry,
  destroys sandbox, removes disk files.
- sessions.archive: stops RuntimeSession if active, cleans sandbox,
  marks archived on disk.
- Pure metadata handlers (unarchive, list_archived, tracked_files) remain
  thin wrappers but are gated through AppServer boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from miqi.runtime.app_server import AppServerError
from miqi.runtime.session_request_models import validate_session_params
from miqi.session.manager import OwnershipError


def _get_session_manager() -> Any:
    """Get a SessionManager for the current workspace."""
    import miqi.bridge.server as bridge_module

    state = getattr(bridge_module, "_state", None)
    if state is None:
        raise AppServerError("Bridge state not available", code="INTERNAL")
    config = state.load_config()
    from miqi.session.manager import SessionManager
    return SessionManager(config.workspace_path)


def _client_session_id(client_id: str, session_key: str) -> str:
    """Compute the namespaced session_id used by AppServer registry."""
    return f"{client_id}:{session_key}"


def _candidate_workspace_roots(
    sm: Any,
    client_id: str,
    *,
    extra: list[str] | None = None,
) -> list[Path]:
    """Candidate folder roots that may hold real session data (#956).

    A folder-bound session writes its conversation under the bound workspace
    root while the app-home root keeps only a stub.  Collect every known
    folder root: the explicitly requested workspace plus every workspace any
    session is bound to.  The default workspace is always excluded.

    Two properties matter here, and both are load-bearing:

    * Uncapped.  A "recent N workspaces" window would drop the root of any
      folder session older than the window, so its history would vanish from
      the sidebar after a restart and a bare sessions.get would find nothing
      even though the conversation is intact on disk.  Discovery walks every
      persisted binding instead.
    * Archived sessions included.  Archive state says nothing about where a
      folder copy lives, and callers routinely discover the root *after* the
      app-home stub changed state: sessions.archive marks the stub archived
      before resolving the folder copy, so an active-only listing would drop
      the very root being searched for.
    """
    from miqi.session.manager import SessionManager

    raw_roots: list[str] = list(extra or [])
    raw_roots.extend(
        sm.list_bound_workspaces(client_id=client_id, include_archived=True)
    )

    default_ws = str(Path(sm.workspace).expanduser().resolve())
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in raw_roots:
        if not raw:
            continue
        try:
            resolved = SessionManager._validate_workspace(Path(raw))
        except Exception:
            continue
        key = str(resolved)
        if key == default_ws or key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def _find_folder_session(
    sm: Any,
    session_key: str,
    client_id: str,
    *,
    extra_workspace: str | None = None,
    require_messages: bool = True,
    require_ledger_in_scan: bool = False,
) -> tuple[Any, Path] | None:
    """Locate the authoritative folder-root copy of a session.

    Returns (folder_session, folder_root) for the first known root that holds
    the session, or None.  Sessions owned by a different client are skipped —
    never leak another client's data.

    ``require_messages`` defaults to True: callers resolving the *conversation*
    need an empty copy rejected, since it is not authoritative for history.
    Callers resolving state that outlives the conversation — tracked files,
    which the runtime writes under its own workspace root whether or not a
    message was ever sent — must pass False, or a session with assets but no
    history resolves back to app-home and reads an empty ledger (#1061).

    ``require_ledger_in_scan`` applies to the *scanned* roots only.  A root
    handed in through ``extra_workspace`` is authoritative by construction (it
    is the live runtime's own workspace, or the folder this session's stub is
    bound to) and is accepted on the session's presence alone.  Roots reached by
    scanning are guesses, and with ``require_messages`` off a message-less copy
    is indistinguishable from the real one: one key can have copies under
    several folders, since rebinding a session through the workspace picker
    leaves the old folder's copy on disk.  Scanning walks roots newest-stub
    first, so that stale copy can come first and shadow the ledger — reads come
    back empty and clears wipe the wrong folder.  Demanding the candidate hold
    this session's tracked_files.json is what tells the real one apart.
    """
    from miqi.session.manager import SessionManager

    seed: Path | None = None
    if extra_workspace:
        try:
            seed = SessionManager._validate_workspace(Path(extra_workspace))
        except Exception:
            seed = None

    for root in _candidate_workspace_roots(
        sm, client_id, extra=[extra_workspace] if extra_workspace else None,
    ):
        try:
            folder_sm = SessionManager(root)
            folder_session = folder_sm.load_existing(session_key)
            if folder_session is None:
                continue
            if require_messages and not folder_session.messages:
                continue
            if require_ledger_in_scan and (seed is None or root != seed):
                if not folder_sm.load_tracked_files(session_key, client_id=client_id):
                    continue
        except Exception:
            continue
        owner = folder_session.metadata.get("owner_client_id")
        if owner is not None and owner != client_id:
            continue
        return folder_session, root
    return None


def _active_runtime_workspace(runtime: Any) -> str | None:
    """Workspace root a live runtime mirrors its conversation into (#1061).

    A session born inside a folder window has its runtime rooted at that folder
    and may carry no app-home stub at all, so the persisted-binding scan cannot
    see it: the binding is only stamped when a runtime is created (or healed on
    a later read), which leaves sessions written by builds before that stamp
    existed with nowhere to look.  The live runtime is authoritative for where
    its own copy lives, so it seeds the folder search.
    """
    if runtime is None:
        return None
    workspace = getattr(getattr(runtime, "services", None), "workspace", None)
    return str(workspace) if workspace else None


def _folder_session_manager(sm: Any, session_key: str, client_id: str) -> Any | None:
    """SessionManager for the authoritative folder-root copy, if one exists."""
    from miqi.session.manager import SessionManager

    found = _find_folder_session(sm, session_key, client_id)
    if found is None:
        return None
    return SessionManager(found[1])


async def _tracked_files_manager(
    sm: Any,
    session_key: str,
    client_id: str,
    registry: Any,
) -> Any:
    """SessionManager holding this session's tracked-files ledger (#1061).

    Falls back to ``sm`` (app-home) when no folder copy holds the ledger.
    Tracked files are written by the runtime into its own workspace root and are
    independent of conversation history, so this resolution deliberately does
    not require messages.

    Two roots are authoritative, and the order between them is the order writes
    follow: a live runtime (also the only handle on the folder when the session
    carries no app-home stub at all), then the folder this session's stub is
    bound to.  Seeding them keeps precedence honest — a scanned root that merely
    *has* a ledger must not outrank the folder the session actually lives in, or
    a stale copy under an old folder would shadow the real ledger and the assets
    panel would show another folder's files.
    """
    from miqi.session.manager import SessionManager

    runtime = None
    if registry is not None:
        try:
            runtime = await registry.get_session(
                client_id, _client_session_id(client_id, session_key),
            )
        except Exception as exc:
            logger.debug(
                "tracked files: runtime lookup failed for {}: {}", session_key, exc,
            )
    authoritative = _active_runtime_workspace(runtime)
    if authoritative is None:
        stub = sm.load_existing(session_key)
        if stub is not None:
            authoritative = stub.metadata.get("workspace") or None
    found = _find_folder_session(
        sm,
        session_key,
        client_id,
        extra_workspace=authoritative,
        require_messages=False,
        require_ledger_in_scan=True,
    )
    return SessionManager(found[1]) if found is not None else sm


def _tracked_files_store_key(session_key: str) -> str:
    """Derive the on-disk key for ``tracked_files.json`` (write/read 同源).

    #1003 finding ①：写端 ``_persist_tracked_file`` 用
    ``_session_files_dir_key`` 派生目录名（``sessions/<derived>/tracked_files.json``），
    读端必须用同一派生，否则三段 namespaced key（``miqi-desktop:desktop:983``）
    会读到 ``sessions/miqi-desktop_desktop_983/``，而条目实际落在
    ``sessions/desktop_983/``。

    两段 key（现网唯一形态，如 ``desktop:1786...``）派生结果与历史 raw 约定
    ``key.replace(":", "_")`` 逐字相同——raw 是 canonical 之前的目录名规则，
    #1014 起 ``get_session_dir`` 的规则是 canonical；两段键下两者等价，故本次
    归一不改变既有行为，只有三段 key 才会分叉。

    归一发生在 ownership 校验之前：读路径由 ``load_tracked_files(key,
    client_id=...)`` 内部、清理路径由 ``clear_tracked_files(key,
    client_id=...)`` 内部的 ``_verify_ownership_for_mutation`` 完成，且落在
    「条目所在的那条会话记录」上（``get_session_dir(key)`` 同一条派生链），
    因此不削弱归属校验。
    """
    from miqi.agent.tools.filesystem import _session_files_dir_key

    return _session_files_dir_key(session_key)


# ── sessions.list ──────────────────────────────────────────────────────────


async def sessions_list_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """List sessions, merging AppServer registry (active) + disk (inactive).

    Active sessions (those running in the AppServer registry) are annotated
    with status: "running". Disk-only sessions get status: "inactive".
    Sessions from other clients are never visible.
    """
    validate_session_params("sessions.list", params)

    # Active sessions from AppServer registry
    active_sids: set[str] = set(registry.list_sessions(client_id))

    # Disk sessions from SessionManager (client-scoped).
    # exclude_empty: 空会话（尚无任何消息）不落盘也不进列表——它们只是打开中
    # 的临时状态，首条消息写入后才成为会话。合并下方"active 未落盘"的真实
    # 运行会话分支不受影响。
    sm = _get_session_manager()
    disk_sessions: list[dict[str, Any]] = sm.list_sessions(
        client_id=client_id, exclude_empty=True
    )

    # Merge: mark each as active, inactive, or unowned
    result_sessions: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for s in disk_sessions:
        key = s.get("key", "")
        if key in seen_keys:
            continue
        seen_keys.add(key)

        sid = _client_session_id(client_id, key)
        is_active = sid in active_sids
        ownership = s.get("ownership")

        status: str
        if is_active:
            status = "running"
        elif ownership == "unowned":
            status = "unowned"  # Legacy session — requires explicit claim
        else:
            status = "inactive"

        result_sessions.append({
            **s,
            "status": status,
        })

    # Add any active sessions not on disk
    for sid in active_sids:
        # extract key from client_id:session_key
        if sid.startswith(f"{client_id}:"):
            key = sid[len(client_id) + 1:]
        else:
            key = sid
        if key not in seen_keys:
            runtime = await registry.get_session(client_id, sid)
            result_sessions.append({
                "key": key,
                "title": key,
                "status": "running",
                "ownership": "owned",
                "created_at": None,
                "updated_at": None,
                "agent_count": len(getattr(getattr(runtime.services, "agent_control", None), "_agents", {})) if runtime else 0,
            })

    # #956: folder-bound sessions write their real conversation under the
    # bound workspace root — their app-home stub is empty, so exclude_empty
    # above hides them and they would vanish from the sidebar after a restart.
    # Surface them from the known folder roots, title/updated_at taken from
    # the authoritative folder copy.
    try:
        from miqi.session.manager import SessionManager

        folder_copies: dict[str, dict[str, Any]] = {}
        for root in _candidate_workspace_roots(sm, client_id):
            try:
                folder_sm = SessionManager(root)
                for entry in folder_sm.list_sessions(
                    client_id=client_id, exclude_empty=True,
                ):
                    fkey = entry.get("key", "")
                    if fkey:
                        entry["workspace"] = str(root)
                        folder_copies.setdefault(fkey, entry)
            except Exception as exc:
                logger.debug(
                    "sessions.list: folder scan failed for {}: {}", root, exc,
                )

        # Enrich active "not on disk" entries (the active loop above used a
        # generic title=key and created_at=None) with the folder copy's real
        # title/updated_at, then consume them so they are not added twice.
        for entry in result_sessions:
            fcopy = folder_copies.get(entry.get("key", ""))
            if fcopy is None:
                continue
            if entry.get("created_at") is None:
                entry["title"] = fcopy["title"]
                entry["created_at"] = fcopy["created_at"]
                entry["updated_at"] = fcopy["updated_at"]
                entry["workspace"] = fcopy["workspace"]
            folder_copies.pop(entry.get("key", ""), None)

        # Remaining folder copies are folder-only sessions — their app-home
        # stub was hidden by exclude_empty and they are not active.  Surface
        # them with the folder copy's title/updated_at.
        for fkey, fcopy in folder_copies.items():
            if fkey in seen_keys:
                continue
            fsid = _client_session_id(client_id, fkey)
            if fsid in active_sids:
                continue  # safety net — already represented by the active loop
            fstatus = (
                "unowned" if fcopy.get("ownership") == "unowned" else "inactive"
            )
            result_sessions.append({**fcopy, "status": fstatus})
            seen_keys.add(fkey)
    except Exception as exc:
        logger.warning("sessions.list: folder-session resolution failed: {}", exc)

    return {"result": {"sessions": result_sessions}}


# ── sessions.get ───────────────────────────────────────────────────────────


async def _default_workspace_path() -> Path | None:
    """Fallback workspace root when a session carries no explicit workspace."""
    try:
        import miqi.bridge.server as bridge_module

        state = getattr(bridge_module, "_state", None)
        if state is None:
            return None
        return Path(state.load_config().workspace_path)
    except Exception:
        return None


async def _load_interrupted_turns(
    *,
    history_runtime: Any | None = None,
    workspace: Path | None = None,
    sid: str,
) -> list[dict[str, Any]]:
    """#740: return recoverable execution snapshots for the session's thread.

    Active sessions use the live HistoryRuntime connection; inactive ones
    (after restart) construct a throwaway HistoryRuntime over the same db.
    Never raises — a snapshot-lookup failure degrades to an empty list.
    """
    try:
        if history_runtime is not None:
            return await history_runtime.get_interrupted_snapshots()
        ws = workspace or await _default_workspace_path()
        if ws is not None:
            from miqi.runtime.history_runtime import HistoryRuntime

            db_path = ws / ".miqi-runtime" / "runtime.db"
            if not db_path.exists():
                return []
            hr = HistoryRuntime(db_path, session_id=sid)
            await hr.initialize()
            try:
                return await hr.get_interrupted_snapshots()
            finally:
                await hr.close()
    except Exception as exc:
        logger.warning("interrupted-turn lookup failed for {}: {}", sid, exc)
    return []


async def sessions_get_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Get session detail from AppServer registry or disk.

    If the session is active in AppServer, returns runtime info.
    Otherwise, falls back to SessionManager disk data.
    """
    typed = validate_session_params("sessions.get", params)
    session_key = typed.session_key

    sid = _client_session_id(client_id, session_key)

    # Check AppServer registry first
    runtime = await registry.get_session(client_id, sid)

    # Always load messages from SessionManager so history is visible even
    # when the session is still active in the AppServer registry.
    messages: list[dict[str, Any]] = []
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = {}
    ownership: str = "owned"
    # #956: folder root holding the authoritative copy (set when adopted).
    authoritative_ws: Path | None = None

    try:
        sm = _get_session_manager()
        ws = Path(typed.workspace) if typed.workspace else None
        # Hold the per-key lock across the get_or_create → save/delete sequence
        # for the same reason as _save_to_session_manager: close the #1050
        # TOCTOU window between the ownership check and the disk write.
        with sm._get_session_lock(session_key):
            disk_session = sm.get_or_create(session_key, client_id=client_id, workspace=ws)

            # #956: folder-bound sessions write their real conversation under the
            # bound workspace root while the app-home copy is an empty stub.  When
            # the stub is empty, resolve the authoritative copy (explicit workspace
            # → recent-workspace scan) and backfill the binding so later reads and
            # sessions.list resolve without another scan.
            if not disk_session.messages:
                # #1061: a live runtime knows its own workspace root.  A folder
                # window's session has no app-home stub, so without this seed a
                # bare get returns an empty conversation even though the runtime
                # is running and the copy on disk is intact.
                found = _find_folder_session(
                    sm,
                    session_key,
                    client_id,
                    extra_workspace=str(ws) if ws else _active_runtime_workspace(runtime),
                    # Reading the conversation: an empty copy is not authoritative.
                    require_messages=True,
                )
                if found is not None:
                    folder_session, authoritative_ws = found
                    disk_session = folder_session
                    # A legacy folder copy carries no owner_client_id.  Mirror the
                    # app-home REQUIRES_CLAIM path exactly: the history stays
                    # readable and is reported as unowned, and no owned binding is
                    # stamped for a session this client never claimed — otherwise
                    # the same session would answer "owned" from the folder root
                    # and "unowned" from app-home.
                    if folder_session.metadata.get("owner_client_id") is None:
                        ownership = "unowned"
                    else:
                        try:
                            stub = sm.get_or_create(
                                session_key, client_id=client_id, workspace=authoritative_ws,
                            )
                            # Binding-only stub: never copy the folder's messages into
                            # the app-home root — the folder stays authoritative.
                            sm.save(stub)
                        except Exception as exc:
                            logger.debug("sessions.get: binding backfill failed: {}", exc)

            # Adopting a folder copy means disk_session is no longer the app-home
            # entry: the GC below writes through `sm` (app-home), so it must not
            # run on a session the folder root owns.
            if authoritative_ws is None:
                # 空会话是临时的：首条消息写入前不进 sessions.list（左端不残留默认会话）。
                # 但显式带 workspace 的空会话仍要落盘——用户先切工作目录、后发首条消息时，
                # workspace 元数据需跨 get/重启存活（workspace E2E 依赖该契约）。
                # 保留条件要覆盖"已落盘的 workspace 绑定"：切目录后的一次裸 get（无 workspace
                # 参数，如历史重载/列表刷新）不能把空会话当残留 GC 掉，否则首条消息会落在
                # 丢失 workspace 的会话上。仅当空会话既无显式 workspace、磁盘上也没有任何
                # workspace 元数据时，才把它当作旧版本无条件 save 留下的空白残留删除。
                if not disk_session.messages:
                    existing_ws = disk_session.metadata.get("workspace")
                    if ws is not None or existing_ws is not None:
                        sm.save(disk_session)
                    elif sm.get_session_dir(session_key).exists():
                        sm.delete(session_key, client_id=client_id)
                        disk_session = sm.get_or_create(
                            session_key, client_id=client_id, workspace=ws
                        )
                else:
                    sm.save(disk_session)

        messages = disk_session.messages
        created_at = disk_session.created_at.isoformat()
        updated_at = disk_session.updated_at.isoformat()
        metadata = disk_session.metadata
    except OwnershipError as exc:
        if exc.code == "REQUIRES_CLAIM":
            # Legacy session with no owner — still allow reading messages.
            # Fall back to get_or_create without client_id so history
            # survives app restarts after runtime migration.
            disk_session = sm.get_or_create(session_key)
            messages = disk_session.messages
            created_at = disk_session.created_at.isoformat()
            updated_at = disk_session.updated_at.isoformat()
            metadata = disk_session.metadata
            ownership = "unowned"
        else:
            raise AppServerError(exc.args[0], code=exc.code) from exc
    except Exception as exc:
        logger.warning("Failed to load session {}: {}", session_key, exc)
        raise AppServerError("Failed to get session", code="INTERNAL") from exc

    ws_result = (
        str(authoritative_ws)
        if authoritative_ws is not None
        else metadata.get("workspace")
    )

    if runtime is not None:
        return {
            "result": {
                "key": session_key,
                "session_id": sid,
                "status": "running",
                "agent_count": len(getattr(getattr(runtime.services, "agent_control", None), "_agents", {})),
                "messages": messages,
                "created_at": created_at,
                "updated_at": updated_at,
                "metadata": metadata,
                "workspace": ws_result,
                "interrupted_turns": await _load_interrupted_turns(
                    history_runtime=getattr(runtime.services, "history_runtime", None),
                    sid=sid,
                ),
            },
        }

    return {
        "result": {
            "key": session_key,
            "messages": messages,
            "created_at": created_at,
            "updated_at": updated_at,
            "metadata": metadata,
            "status": "inactive",
            "ownership": ownership,
            "workspace": ws_result,
            "interrupted_turns": await _load_interrupted_turns(
                workspace=ws or (Path(ws_result) if ws_result else None),
                sid=sid,
            ),
        },
    }


# ── sessions.delete ────────────────────────────────────────────────────────


async def sessions_delete_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Delete a session: stop RuntimeSession, clean sandbox, remove disk files.

    Order:
    1. Stop RuntimeSession in AppServer registry (if active)
    2. Destroy sandbox
    3. Remove disk files via SessionManager
    """
    typed = validate_session_params("sessions.delete", params)
    session_key = typed.session_key

    import miqi.bridge.server as bridge_module

    sid = _client_session_id(client_id, session_key)

    # 1. Stop RuntimeSession if active
    runtime = await registry.get_session(client_id, sid)
    runtime_was_active = runtime is not None
    if runtime is not None:
        try:
            await registry.stop_session(sid)
            logger.info(
                "sessions.delete: stopped RuntimeSession {} (client={})",
                sid, client_id,
            )
        except Exception as exc:
            logger.warning(
                "sessions.delete: error stopping RuntimeSession {}: {}",
                sid, exc,
            )

    # 2. Destroy sandbox (client-scoped: Phase 30)
    state = getattr(bridge_module, "_state", None)
    if state is not None:
        try:
            await state.destroy_sandbox_async(session_key, client_id=client_id)
        except Exception as exc:
            logger.warning(
                "sessions.delete: error destroying sandbox for {} (client={}): {}",
                session_key, client_id, exc,
            )

    # 3. Remove disk files (client-scoped).  A folder-bound session's real
    # conversation lives under its bound workspace root — locate it BEFORE
    # deleting the app-home entry (which carries the binding), then delete both.
    sm = _get_session_manager()
    try:
        folder_sm = _folder_session_manager(sm, session_key, client_id)
        disk_deleted = sm.delete(session_key, client_id=client_id)
        folder_deleted = False
        if folder_sm is not None:
            try:
                folder_deleted = folder_sm.delete(session_key, client_id=client_id)
            except OwnershipError as exc:
                # An unowned legacy folder copy is not deletable by this client —
                # keep it and let the app-home deletion stand.
                logger.debug(
                    "sessions.delete: folder copy {} not deleted: {}",
                    session_key, exc,
                )
    except OwnershipError as exc:
        raise AppServerError(exc.args[0], code=exc.code) from exc

    # Success if runtime was stopped (session may not have been on disk)
    deleted = runtime_was_active or disk_deleted or folder_deleted

    # Clean up AppServer event subscriptions for the deleted session.
    # stop_session() cleans _sessions/_client_sessions/_session_clients but
    # _subscriptions lives on AppServer — clean it here (#327).
    app_server = getattr(registry, "bridge_context", {}).get("app_server")
    if app_server is not None and hasattr(app_server, "_subscriptions"):
        app_server._subscriptions.pop(sid, None)

    logger.info(
        "sessions.delete: {} (key={}, client={})",
        "deleted" if deleted else "not found",
        session_key, client_id,
    )

    return {"result": {"deleted": deleted}}


# ── sessions.archive ───────────────────────────────────────────────────────


async def sessions_archive_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Archive a session: stop RuntimeSession, clean sandbox, mark archived."""
    typed = validate_session_params("sessions.archive", params)
    session_key = typed.session_key

    import miqi.bridge.server as bridge_module

    sid = _client_session_id(client_id, session_key)

    # 1. Stop RuntimeSession if active
    runtime = await registry.get_session(client_id, sid)
    if runtime is not None:
        try:
            await registry.stop_session(sid)
            logger.info(
                "sessions.archive: stopped RuntimeSession {} (client={})",
                sid, client_id,
            )
        except Exception as exc:
            logger.warning(
                "sessions.archive: error stopping RuntimeSession {}: {}",
                sid, exc,
            )

    # 2. Destroy sandbox (client-scoped: Phase 30)
    state = getattr(bridge_module, "_state", None)
    if state is not None:
        try:
            await state.destroy_sandbox_async(session_key, client_id=client_id)
        except Exception as exc:
            logger.warning(
                "sessions.archive: error destroying sandbox for {} (client={}): {}",
                session_key, client_id, exc,
            )

    # 3. Mark archived on disk (client-scoped).  A folder-bound session must be
    # archived under its workspace root too, or the folder scan would keep
    # surfacing it in the active list (#956).
    sm = _get_session_manager()
    try:
        sm.archive(session_key, client_id=client_id)
        folder_sm = _folder_session_manager(sm, session_key, client_id)
        if folder_sm is not None:
            try:
                folder_sm.archive(session_key, client_id=client_id)
            except OwnershipError as exc:
                logger.debug(
                    "sessions.archive: folder copy {} not archived: {}",
                    session_key, exc,
                )
    except OwnershipError as exc:
        raise AppServerError(exc.args[0], code=exc.code) from exc

    return {"result": {"archived": True}}


# ── sessions.unarchive ─────────────────────────────────────────────────────


async def sessions_unarchive_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Unarchive a session — restore it to the default session list."""
    typed = validate_session_params("sessions.unarchive", params)
    session_key = typed.session_key

    sm = _get_session_manager()
    try:
        sm.unarchive(session_key, client_id=client_id)
        folder_sm = _folder_session_manager(sm, session_key, client_id)
        if folder_sm is not None:
            try:
                folder_sm.unarchive(session_key, client_id=client_id)
            except OwnershipError as exc:
                logger.debug(
                    "sessions.unarchive: folder copy {} not unarchived: {}",
                    session_key, exc,
                )
    except OwnershipError as exc:
        raise AppServerError(exc.args[0], code=exc.code) from exc

    return {"result": {"unarchived": True}}


# ── sessions.list_archived ─────────────────────────────────────────────────


async def sessions_list_archived_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """List only archived sessions (client-scoped)."""
    validate_session_params("sessions.list_archived", params)

    from miqi.session.session_keys import session_files_dir_key

    sm = _get_session_manager()
    sessions = sm.list_sessions(
        include_archived=True, client_id=client_id, exclude_empty=True
    )

    # Filter to only archived ones (already client-scoped by list_sessions)
    archived = []
    for s in sessions:
        # Must match where ``SessionManager.archive`` writes the marker —
        # ``get_session_dir`` uses this same canonical derivation (#1005).
        safe_key = session_files_dir_key(s["key"])
        marker = sm.sessions_dir / safe_key / ".archived"
        if marker.exists():
            archived.append(s)

    # #956: folder-bound sessions archived under their workspace root must
    # stay reachable here or unarchive would have no way to find them.
    try:
        from miqi.session.manager import SessionManager

        for root in _candidate_workspace_roots(sm, client_id):
            try:
                folder_sm = SessionManager(root)
                for s in folder_sm.list_sessions(
                    include_archived=True, client_id=client_id,
                ):
                    fkey = s.get("key", "")
                    if any(a.get("key") == fkey for a in archived):
                        continue
                    safe_key = session_files_dir_key(fkey)
                    marker = folder_sm.sessions_dir / safe_key / ".archived"
                    if marker.exists():
                        archived.append({**s, "workspace": str(root)})
            except Exception as exc:
                logger.debug(
                    "sessions.list_archived: folder scan failed for {}: {}",
                    root, exc,
                )
    except Exception as exc:
        logger.warning("sessions.list_archived: folder resolution failed: {}", exc)

    return {"result": {"sessions": archived}}


# ── sessions.get_tracked_files ─────────────────────────────────────────────


async def sessions_get_tracked_files_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Return tracked files for a session from tracked_files.json (client-scoped)."""
    typed = validate_session_params("sessions.get_tracked_files", params)
    session_key = _tracked_files_store_key(typed.session_key)

    # #1061：文件夹绑定会话的资产也写在会话自己的工区；上游 #1040 修了 list/get/
    # delete/archive，唯独漏了 tracked files —— 这里补上，否则右侧「任务资产」为空。
    sm = await _tracked_files_manager(
        _get_session_manager(), typed.session_key, client_id, registry,
    )
    try:
        files = sm.load_tracked_files(session_key, client_id=client_id)
    except OwnershipError as exc:
        raise AppServerError(exc.args[0], code=exc.code) from exc
    result = [
        {"path": path, **info}
        for path, info in files.items()
    ]

    return {"result": {"tracked_files": result}}


# ── sessions.clear_tracked_files ───────────────────────────────────────────


async def sessions_clear_tracked_files_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Remove all tracked file entries for a session (client-scoped)."""
    typed = validate_session_params("sessions.clear_tracked_files", params)
    session_key = _tracked_files_store_key(typed.session_key)

    # #1061：同上，清理也要落到文件夹绑定工区的那份 tracked_files.json。
    sm = await _tracked_files_manager(
        _get_session_manager(), typed.session_key, client_id, registry,
    )
    try:
        sm.clear_tracked_files(session_key, client_id=client_id)
    except OwnershipError as exc:
        raise AppServerError(exc.args[0], code=exc.code) from exc

    return {"result": {"cleared": True}}


# ── sessions.rename ────────────────────────────────────────────────────────


async def sessions_rename_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Set a custom display title for a session (client-scoped)."""
    typed = validate_session_params("sessions.rename", params)
    session_key = typed.session_key
    title = typed.title

    sm = _get_session_manager()
    try:
        effective_title = sm.rename(session_key, title, client_id=client_id)
        folder_sm = _folder_session_manager(sm, session_key, client_id)
        if folder_sm is not None:
            try:
                effective_title = folder_sm.rename(
                    session_key, title, client_id=client_id,
                )
            except OwnershipError as exc:
                logger.debug(
                    "sessions.rename: folder copy {} not renamed: {}",
                    session_key, exc,
                )
    except OwnershipError as exc:
        raise AppServerError(exc.args[0], code=exc.code) from exc

    return {"result": {"renamed": True, "key": session_key, "title": effective_title}}


# ── sessions.claim_legacy ──────────────────────────────────────────────────


async def sessions_claim_legacy_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Explicitly claim an unowned legacy session.

    This is the ONLY way to take ownership of a legacy session that
    lacks owner_client_id metadata. Once claimed, the session is
    permanently owned by the claiming client.

    A session that is already owned by a different client cannot be
    claimed — it will return UNAUTHORIZED.
    """
    typed = validate_session_params("sessions.claim_legacy", params)
    session_key = typed.session_key

    sm = _get_session_manager()
    try:
        claimed = sm.claim_session(session_key, client_id)
        return {"result": {"claimed": True, "was_already_claimed": not claimed}}
    except OwnershipError as exc:
        raise AppServerError(exc.args[0], code=exc.code) from exc


# ── sessions.list_recent_workspaces ─────────────────────────────────────────


async def sessions_list_recent_workspaces_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Return distinct workspace paths from recent sessions."""
    sm = _get_session_manager()
    workspaces = sm.list_recent_workspaces(client_id=client_id)
    return {"result": {"workspaces": workspaces}}
