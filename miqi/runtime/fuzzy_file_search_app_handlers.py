"""Codex-style fuzzyFileSearch* AppServer handlers (Phase 46).

Registers: fuzzyFileSearch, fuzzyFileSearch/sessionStart,
fuzzyFileSearch/sessionUpdate, fuzzyFileSearch/sessionStop.

Session methods are gated behind ``experimentalApi`` via the shared
``require_experimental_api()`` gate from Phase 46.  One-shot
``fuzzyFileSearch`` does NOT require experimental API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import miqi.runtime.protocol_specs as protocol_specs
from miqi.runtime.app_server import AppServer, AppServerError, get_bridge_context
from miqi.runtime.experimental_api import require_experimental_api
from miqi.runtime.filesystem_request_models import (
    FuzzyFileSearchParams,
    FuzzySessionStartParams,
    FuzzySessionStopParams,
    FuzzySessionUpdateParams,
    validate_filesystem_params,
)
from miqi.runtime.fs_protocol import resolve_workspace_absolute_path

# ── Handler implementations ──────────────────────────────────────────────────


async def fuzzy_file_search_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Handle fuzzyFileSearch — one-shot file search (no experimental gate)."""
    typed = validate_filesystem_params(FuzzyFileSearchParams, params)
    runtime = _get_fuzzy_runtime(registry)

    roots: list[Path] = []
    for raw in typed.roots:
        resolved = resolve_workspace_absolute_path(
            registry, raw, field_name="root",
        )
        if not resolved.exists():
            continue  # Skip missing roots inside workspace
        roots.append(resolved)

    if not roots:
        # No valid roots → empty result
        return {"result": {"files": []}}

    files = runtime.search(typed.query, roots)
    return {"result": {"files": files}}


async def fuzzy_session_start_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Handle fuzzyFileSearch/sessionStart — requires experimentalApi."""
    require_experimental_api(params, registry, client_id, "fuzzyFileSearch/sessionStart")
    typed = validate_filesystem_params(FuzzySessionStartParams, params)
    runtime = _get_fuzzy_runtime(registry)

    roots: list[Path] = []
    for raw in typed.roots:
        resolved = resolve_workspace_absolute_path(
            registry, raw, field_name="root",
        )
        if not resolved.exists():
            continue  # Skip missing roots inside workspace
        roots.append(resolved)

    runtime.session_start(client_id, typed.session_id, roots)
    return {"result": {}}


async def fuzzy_session_update_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Handle fuzzyFileSearch/sessionUpdate — requires experimentalApi."""
    require_experimental_api(params, registry, client_id, "fuzzyFileSearch/sessionUpdate")
    typed = validate_filesystem_params(FuzzySessionUpdateParams, params)
    runtime = _get_fuzzy_runtime(registry)

    try:
        await runtime.session_update(client_id, typed.session_id, typed.query)
    except KeyError:
        raise AppServerError(
            f"Unknown session: {typed.session_id}",
            code="INVALID_PARAMS",
        )

    return {"result": {}}


async def fuzzy_session_stop_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Handle fuzzyFileSearch/sessionStop — requires experimentalApi."""
    require_experimental_api(params, registry, client_id, "fuzzyFileSearch/sessionStop")
    typed = validate_filesystem_params(FuzzySessionStopParams, params)
    runtime = _get_fuzzy_runtime(registry)

    runtime.session_stop(client_id, typed.session_id)
    return {"result": {}}


# ── Runtime lookup ───────────────────────────────────────────────────────────


def _get_fuzzy_runtime(registry: Any):
    """Get or lazily create the FuzzyFileSearchRuntime from bridge_context."""
    from miqi.runtime.fuzzy_file_search_runtime import FuzzyFileSearchRuntime

    runtime = get_bridge_context(registry, "fuzzy_file_search_runtime")
    if runtime is None:
        app_server = get_bridge_context(registry, "app_server")
        if app_server is None:
            raise AppServerError(
                "AppServer not available for fuzzy search runtime",
                code="INTERNAL",
            )
        runtime = FuzzyFileSearchRuntime(app_server)
        registry.bridge_context["fuzzy_file_search_runtime"] = runtime
    return runtime


# ── Registration ─────────────────────────────────────────────────────────────


def register_fuzzy_file_search_handlers(server: AppServer) -> None:
    """Register fuzzyFileSearch* handlers on *server*."""
    server.register_method("fuzzyFileSearch", fuzzy_file_search_handler, spec=protocol_specs.FUZZY_FILE_SEARCH)
    server.register_method("fuzzyFileSearch/sessionStart", fuzzy_session_start_handler, spec=protocol_specs.FUZZY_FILE_SEARCH_SESSION_START)
    server.register_method("fuzzyFileSearch/sessionUpdate", fuzzy_session_update_handler, spec=protocol_specs.FUZZY_FILE_SEARCH_SESSION_UPDATE)
    server.register_method("fuzzyFileSearch/sessionStop", fuzzy_session_stop_handler, spec=protocol_specs.FUZZY_FILE_SEARCH_SESSION_STOP)
