"""Approval handlers for AppServer dispatch.

Phase 28.2: Migrates approvals.list, approvals.resolve,
approvals.clear_permanent, approvals.add_permanent, and
approvals.history from bridge legacy handlers to AppServer
async handlers with client/session scoping.

All approval mutation goes through AppServer → RuntimeSession →
ToolOrchestrator. Permanent allowlist manipulation is gated through
the AppServer boundary for audit trail.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from miqi.runtime.app_server import AppServerError


async def _resolve_approval_mode(registry: Any, client_id: str) -> str:
    """Best-effort resolution of the active approval mode for a client.

    Iterates over the client's authorized sessions and returns the first
    non-None approval_policy mode found on the session's services or on
    the session runtime itself. Falls back to 'on_request' when no policy
    is available.
    """
    for sid in registry.list_sessions(client_id):
        runtime = await registry.get_session(client_id, sid)
        if runtime is None:
            continue

        profile = None
        services = getattr(runtime, "services", None)
        if services is not None:
            profile = getattr(services, "permission_profile", None)
        if profile is None:
            profile = getattr(runtime, "permission_profile", None)
        if profile is None:
            continue

        policy = getattr(profile, "approval_policy", None)
        if policy is not None:
            return policy.mode.value

    return "on_request"


async def approvals_list_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """List pending approvals scoped to this client's authorized sessions.

    Returns:
        pending: approvals from active RuntimeSessions owned by this client
        permanent_allowlist: global permanent approval patterns
        permanent_entries: permanent patterns with timestamps
        enabled: whether command approval is enabled
        timeout: approval timeout in seconds
    """
    from miqi.agent.command_approval import (
        get_permanent_allowlist,
        get_permanent_allowlist_meta,
    )

    # Collect pending approvals from all client sessions
    pending: list[dict[str, Any]] = []
    pending_ids: list[str] = []
    for sid in registry.list_sessions(client_id):
        runtime = await registry.get_session(client_id, sid)
        if runtime is None:
            continue
        orchestrator = getattr(runtime.services, "orchestrator", None)
        if orchestrator is None:
            continue
        for entry in orchestrator.list_pending_approvals():
            pending.append(entry)
            pending_ids.append(entry["approval_id"])

    # Permanent allowlist (global, read-only for listing)
    permanent_patterns = sorted(get_permanent_allowlist())
    permanent_meta = get_permanent_allowlist_meta()
    permanent_entries = [
        {"pattern": p, "added_at": permanent_meta.get(p, 0)}
        for p in permanent_patterns
    ]

    # Config info — try from bridge state, fall back to defaults
    enabled = True
    timeout = 60
    try:
        import miqi.bridge.server as bridge_module
        state = getattr(bridge_module, "_state", None)
        if state is not None:
            config = state.load_config()
            enabled = getattr(config.agents.command_approval, "enabled", True)
            timeout = getattr(config.agents.command_approval, "timeout", 60)
    except Exception:
        pass

    # Surface the active approval mode (best-effort from session profiles)
    approval_mode = await _resolve_approval_mode(registry, client_id)

    return {
        "result": {
            "pending": pending,
            "pending_ids": pending_ids,
            "permanent_allowlist": permanent_patterns,
            "permanent_entries": permanent_entries,
            "enabled": enabled,
            "timeout": timeout,
            "approval_mode": approval_mode,
        },
    }


async def approvals_resolve_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Resolve a pending approval — scoped to this client's sessions.

    Only the client that owns the session with the pending approval
    can resolve it. Cross-session resolution returns UNAUTHORIZED.

    Phase 31.4 fix: only emits ApprovalResolvedEvent when the orchestrator
    confirms resolved=True.  The event is routed through
    runtime.services.event_emitter so it enters the RuntimeSession event
    queue and is mirrored to the ledger.
    """
    from miqi.protocol.events import ApprovalResolvedEvent

    approval_id = params.get("approval_id", "")
    decision = params.get("decision", "deny")

    if not approval_id:
        raise AppServerError("approval_id is required", code="INVALID_PARAMS")

    # Phase 31.4: allow legacy "allow" and "allow_permanent" through
    # the handler gate — the orchestrator normalizes them.
    if decision not in ("once", "session", "always", "deny", "allow", "allow_permanent"):
        raise AppServerError(
            f"Invalid decision: {decision}",
            code="INVALID_PARAMS",
        )

    # Find the session that owns this approval
    for sid in registry.list_sessions(client_id):
        runtime = await registry.get_session(client_id, sid)
        if runtime is None:
            continue
        orchestrator = getattr(runtime.services, "orchestrator", None)
        if orchestrator is None:
            continue
        if orchestrator.has_approval(approval_id):
            result = orchestrator.resolve_approval(approval_id, decision)
            if not result.resolved:
                # Approval exists but resolve failed (invalid decision
                # already caught above, so this is a duplicate or
                # internal inconsistency)
                logger.warning(
                    "approvals.resolve: resolve_approval returned "
                    "resolved=False for {} (decision={}, reason={})",
                    approval_id, decision, result.reason,
                )
                raise AppServerError(
                    "Approval could not be resolved",
                    code="INTERNAL",
                )
            # Phase 31.4: emit terminal event through the runtime session's
            # event emitter so it enters the event queue and ledger mirror.
            event_emitter = getattr(runtime.services, "event_emitter", None)
            if event_emitter is not None:
                await event_emitter.emit(ApprovalResolvedEvent(
                    approval_id=result.approval_id,
                    decision=result.normalized_decision,
                    turn_id=result.turn_id,
                ))
            logger.info(
                "approvals.resolve: {} = {} for session {} (client={})",
                approval_id, result.normalized_decision, sid, client_id,
            )
            return {
                "result": {
                    "resolved": True,
                    "approval_id": approval_id,
                    "decision": result.normalized_decision,
                },
            }

    # Approval not found in any of this client's sessions
    # It may belong to another client — don't leak that information
    raise AppServerError(
        "Approval not found or not authorized",
        code="UNAUTHORIZED",
    )


async def approvals_clear_permanent_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Clear permanent approval patterns.

    When pattern is provided, removes only that pattern.
    Otherwise, clears all permanent patterns.
    """
    from miqi.agent.command_approval import (
        _lock,
        _permanent_added_at,
        _permanent_approved,
    )

    pattern = params.get("pattern")
    # Snapshot before mutating so a failed persist can roll the in-memory
    # list back — memory and disk must stay consistent (2026-08-31 review).
    with _lock:
        snapshot_approved = set(_permanent_approved)
        snapshot_added_at = dict(_permanent_added_at)
        if pattern:
            _permanent_approved.discard(pattern)
            _permanent_added_at.pop(pattern, None)
        else:
            _permanent_approved.clear()
            _permanent_added_at.clear()

    # Persist the cleared state (#7 review): clear_permanent used to only
    # touch memory, leaving the removed patterns in config.json — the next
    # permanent-approvals save would resurrect them via the hot-reload
    # allowlist replace.  Persist now so memory and disk stay in sync.
    from miqi.agent.command_approval import _save_permanent_allowlist

    if not _save_permanent_allowlist():
        # Roll back the in-memory change — the on-disk list is unchanged, so
        # reporting cleared:true would lie to the caller and the next hot
        # reload would resurrect the "cleared" patterns.
        with _lock:
            _permanent_approved.clear()
            _permanent_approved.update(snapshot_approved)
            _permanent_added_at.clear()
            _permanent_added_at.update(snapshot_added_at)
        logger.error(
            "approvals.clear_permanent: persist failed, rolled back "
            "(pattern={}, client={})", pattern or "<all>", client_id,
        )
        raise AppServerError(
            "Failed to persist permanent approval changes",
            code="INTERNAL",
        )

    logger.info(
        "approvals.clear_permanent: pattern={} (client={})",
        pattern or "<all>", client_id,
    )
    return {"result": {"cleared": True}}


async def approvals_add_permanent_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Add a permanent approval pattern."""
    from miqi.agent.command_approval import (
        _lock,
        _permanent_added_at,
        _permanent_approved,
        _save_permanent_allowlist,
        approve_permanent,
    )

    pattern = params.get("pattern", "").strip()
    if not pattern:
        raise AppServerError("pattern is required", code="INVALID_PARAMS")

    # Snapshot before mutating — a failed persist rolls the in-memory list
    # back so memory matches the unchanged on-disk state (2026-09-01 review;
    # same contract as clear_permanent).
    with _lock:
        snapshot_approved = set(_permanent_approved)
        snapshot_added_at = dict(_permanent_added_at)

    approve_permanent(pattern)
    if not _save_permanent_allowlist():
        with _lock:
            _permanent_approved.clear()
            _permanent_approved.update(snapshot_approved)
            _permanent_added_at.clear()
            _permanent_added_at.update(snapshot_added_at)
        logger.error(
            "approvals.add_permanent: persist failed, rolled back "
            "(pattern={}, client={})", pattern, client_id,
        )
        raise AppServerError(
            "Failed to persist permanent approval changes",
            code="INTERNAL",
        )

    logger.info(
        "approvals.add_permanent: pattern={} (client={})",
        pattern, client_id,
    )
    return {"result": {"added": True, "pattern": pattern}}


async def approvals_history_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Get approval history, scoped by client and optionally session.

    Returns history entries filtered to this client's session keys.
    When no session_id is provided, returns entries for all of this
    client's sessions.
    """
    from miqi.agent.command_approval import get_approval_history

    limit = params.get("limit", 200)
    history = get_approval_history(limit)

    # Scope: only return entries for this client's sessions
    # session_key in the history entry is the raw session key
    authorized_sids = set(registry.list_sessions(client_id))

    # Also check if a specific session_id was requested
    if session_id:
        authorized_sids = {session_id} & authorized_sids

    # Filter: entries with no session_key are global (include them)
    # entries with session_key must match one of the client's sessions
    filtered = []
    for entry in history:
        entry_session_key = entry.get("session_key", "")
        if not entry_session_key:
            # Global entry — include
            filtered.append(entry)
        elif entry_session_key in authorized_sids:
            # Session-scoped entry for this client — include
            filtered.append(entry)
        # else: other client's session — exclude

    return {"result": {"history": filtered}}
