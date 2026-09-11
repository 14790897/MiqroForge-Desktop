"""Permission handlers for AppServer dispatch.

Phase 35.2: Migrates permissions.get, permissions.update,
permissions.permanent.add, and permissions.permanent.remove from bridge
legacy handlers to AppServer async handlers.

IMPORTANT — Global control-plane semantics:
  These handlers operate on the SINGLE global ToolOrchestrator.permissions
  instance accessed via _state._orchestrator. All clients share the same
  permission state — this is a GLOBAL control plane, not per-client or
  per-session.

  Design rationale (Phase 35 hardening):
  - The ToolOrchestrator is a process-level singleton by design (one
    approval engine per bridge process).
  - Permission rules (allowlist, deny patterns) are workspace-level
    policy, NOT per-user or per-session settings.
  - Changing to per-client isolation would require a PermissionEngine
    per RuntimeSession, which is a Phase 36+ concern.

  Residual risk:
  - Two Desktop clients connected to the same bridge share one permission
    state. Adding a pattern on client-A immediately affects client-B.
  - No audit log of which client made which permission change.
  - Mitigation: Desktop is typically single-user. Multi-user gateways
    should use a per-session orchestrator (future work).

  Modifying this module: do NOT switch to per-client permission state
  without updating cross-client tests and acceptance docs.
"""

from __future__ import annotations

from typing import Any

from miqi.runtime.app_server import get_bridge_context


def _get_orchestrator(registry: Any) -> Any:
    """Get the global ToolOrchestrator from registry.bridge_context.

    Returns the process-level singleton orchestrator. All clients
    share this instance — see module docstring for rationale.
    """
    return get_bridge_context(registry, "orchestrator", None)


async def permissions_get_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Return current permission engine configuration.

    Returns filesystem rules, network policy, exec approval mode,
    permanent allowlist, and deny patterns.
    """
    orch = _get_orchestrator(registry)
    if orch is not None:
        pe = orch.permissions
        return {"result": {
            "filesystem": {"rules": [], "default_mode": "read"},
            "network": "allow_all",
            "exec_approval": "dangerous",
            "permanent_allowlist": list(pe.permanent_allowlist),
            "deny_patterns": list(pe.deny_patterns),
        }}

    return {"result": {
        "filesystem": {"rules": [], "default_mode": "read"},
        "network": "allow_all",
        "exec_approval": "dangerous",
        "permanent_allowlist": [],
        "deny_patterns": [],
    }}


async def permissions_update_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Update permission engine deny/allow patterns.

    Accepts config.deny_patterns and config.permanent_allowlist as lists.
    Converted to sets internally for efficient lookup.
    """
    cfg = params.get("config", {})
    orch = _get_orchestrator(registry)
    if orch is not None:
        pe = orch.permissions
        if "permanent_allowlist" in cfg:
            pe.permanent_allowlist = set(cfg["permanent_allowlist"])
        if "deny_patterns" in cfg:
            pe.deny_patterns = set(cfg["deny_patterns"])

    return {"result": {"saved": True}}


async def permissions_permanent_add_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Add a pattern to the permanent allowlist."""
    pattern = params.get("pattern", "")
    orch = _get_orchestrator(registry)
    if orch is not None and pattern:
        orch.permissions.permanent_allowlist.add(pattern)

    return {"result": {"added": bool(pattern)}}


async def permissions_permanent_remove_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Remove a pattern from the permanent allowlist."""
    pattern = params.get("pattern", "")
    orch = _get_orchestrator(registry)
    if orch is not None and pattern:
        orch.permissions.permanent_allowlist.discard(pattern)

    return {"result": {"removed": bool(pattern)}}
