"""Workbench process state AppServer handlers.

Registers: workbench/process/list, workbench/process/read,
workbench/process/history.

These are MiQi workbench helper APIs (not Codex public API names) that
allow Desktop to query live process state, individual snapshots, and
bounded completion history.
"""

from __future__ import annotations

from typing import Any

from miqi.runtime.app_server import (
    AppServer,
    AppServerError,
    get_bridge_context,
)
from miqi.runtime.workbench_process_runtime import (
    HandleNotFoundError,
    WorkbenchProcessRuntime,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _get_wpr(registry: Any) -> WorkbenchProcessRuntime:
    """Get or lazily create the WorkbenchProcessRuntime from bridge_context."""
    wpr = get_bridge_context(registry, "workbench_process_runtime")
    if wpr is None:
        from pathlib import Path as _Path

        state = get_bridge_context(registry, "state")
        workspace = _Path.cwd()
        if state is not None:
            try:
                config = state.load_config()
                workspace = config.workspace_path
            except Exception:
                pass
        wpr = WorkbenchProcessRuntime(workspace=workspace)
        registry.bridge_context["workbench_process_runtime"] = wpr
    return wpr


def _safe_kind(raw: Any) -> str | None:
    """Validate the optional *kind* filter parameter."""
    if raw is None:
        return None
    if raw == "process":
        return "process"
    if raw == "commandExec":
        return "commandExec"
    raise AppServerError(
        f"kind must be 'process', 'commandExec', or omitted, got: {raw!r}",
        code="INVALID_PARAMS",
    )


# ── Registration ─────────────────────────────────────────────────────────


def register_workbench_process_state_handlers(server: AppServer) -> None:
    """Register workbench/process/* state handlers on an AppServer instance."""

    # ── workbench/process/list ──────────────────────────────────────────

    async def _process_list(request_id, params, client_id, session_id, registry):
        wpr = _get_wpr(registry)
        kind = _safe_kind(params.get("kind"))
        processes = wpr.list_live(client_id, kind=kind)
        return {"result": {"processes": processes}}

    # ── workbench/process/read ──────────────────────────────────────────

    async def _process_read(request_id, params, client_id, session_id, registry):
        wpr = _get_wpr(registry)
        # Accept both processHandle and processId (alias for commandExec)
        handle_id = params.get("processHandle") or params.get("processId")
        if not handle_id:
            raise AppServerError(
                "processHandle or processId is required",
                code="INVALID_PARAMS",
            )
        include_output = params.get("includeOutput", False)
        if not isinstance(include_output, bool):
            raise AppServerError(
                "includeOutput must be a boolean",
                code="INVALID_PARAMS",
            )
        try:
            snapshot = wpr.read_live(
                client_id, handle_id, include_output=include_output,
            )
        except HandleNotFoundError as exc:
            raise AppServerError(exc.args[0], code=exc.code)
        return {"result": {"process": snapshot}}

    # ── workbench/process/history ───────────────────────────────────────

    async def _process_history(request_id, params, client_id, session_id, registry):
        wpr = _get_wpr(registry)
        kind = _safe_kind(params.get("kind"))
        limit = params.get("limit", 50)
        if not isinstance(limit, int) or limit < 1:
            raise AppServerError(
                "limit must be an integer >= 1",
                code="INVALID_PARAMS",
            )
        if limit > 200:
            limit = 200
        result = wpr.history(client_id, kind=kind, limit=limit)
        return {"result": result}

    # ── Register all ────────────────────────────────────────────────────

    server.register_method("workbench/process/list", _process_list)
    server.register_method("workbench/process/read", _process_read)
    server.register_method("workbench/process/history", _process_history)
