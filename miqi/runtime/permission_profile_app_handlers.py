"""Codex-style permission profile AppServer handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import miqi.runtime.protocol_specs as protocol_specs
from miqi.runtime.app_server import AppServer, AppServerError, get_bridge_context
from miqi.runtime.core_request_models import validate_core_params
from miqi.runtime.permission_profile_runtime import PermissionProfileRuntime


def _workspace_root(registry: Any) -> Path | None:
    """Resolve workspace root from bridge state, same helper pattern as skills handlers."""
    state = get_bridge_context(registry, "state", None)
    if state is None:
        return None
    try:
        return Path(state.load_config().workspace_path).resolve()
    except Exception:
        return None


def register_permission_profile_app_handlers(server: AppServer) -> None:

    async def _permission_profile_list(request_id, params, client_id, session_id, registry):
        """permissionProfile/list — paginated profile catalog.

        Params:
            cwd: str | None (validated against workspace if provided)
            cursor: str | None
            limit: int = 100
        Response:
            {"data": [...], "nextCursor": str|None}
        """
        typed = validate_core_params("permissionProfile/list", params)
        cwd_raw = typed.cwd
        cursor = typed.cursor
        limit = typed.limit

        # Validate cwd if provided
        cwd: str | None = None
        if cwd_raw is not None:
            workspace = _workspace_root(registry)
            resolved = Path(str(cwd_raw)).resolve()
            if workspace is not None:
                try:
                    resolved.relative_to(workspace)
                except ValueError:
                    raise AppServerError(
                        f"CWD outside workspace: {cwd_raw}",
                        code="INVALID_PARAMS",
                    )
            cwd = str(resolved)

        pr = PermissionProfileRuntime()
        page = pr.list_profiles(cwd=cwd, cursor=cursor, limit=limit)
        return {"result": page}

    server.register_method(
        "permissionProfile/list",
        _permission_profile_list,
        spec=protocol_specs.PERMISSION_PROFILE_LIST,
    )
