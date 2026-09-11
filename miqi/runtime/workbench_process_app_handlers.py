"""Codex-style process/* AppServer handlers.

Registers: process/spawn, process/writeStdin, process/resizePty,
process/kill.

process/spawn is gated behind experimentalApi.  All process output
flows through client-scoped events (process/outputDelta, process/exited).

Phase 43: no PTY support. tty:true and resizePty return UNSUPPORTED_FEATURE.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import miqi.runtime.protocol_specs as protocol_specs
from miqi.runtime.app_server import (
    AppServer,
    AppServerError,
    get_bridge_context,
)
from miqi.runtime.experimental_api import require_experimental_api
from miqi.runtime.process_request_models import (
    ProcessKillParams,
    ProcessResizePtyParams,
    ProcessSpawnParams,
    ProcessWriteStdinParams,
    validate_process_params,
)
from miqi.runtime.workbench_process_runtime import (
    HandleNotFoundError,
    OutputChunk,
    ProcessExit,
    WorkbenchProcessError,
    WorkbenchProcessRuntime,
)

# ── Helpers ──────────────────────────────────────────────────────────────




def _get_wpr(registry: Any) -> WorkbenchProcessRuntime:
    """Get or lazily create the WorkbenchProcessRuntime from bridge_context."""
    wpr = get_bridge_context(registry, "workbench_process_runtime")
    if wpr is None:
        state = get_bridge_context(registry, "state")
        workspace = Path.cwd()
        if state is not None:
            try:
                config = state.load_config()
                workspace = config.workspace_path
            except Exception:
                pass
        wpr = WorkbenchProcessRuntime(workspace=workspace)
        registry.bridge_context["workbench_process_runtime"] = wpr
    return wpr


# ── Registration ─────────────────────────────────────────────────────────


def register_workbench_process_handlers(server: AppServer) -> None:
    """Register process/* handlers on an AppServer instance."""

    # ── process/spawn ───────────────────────────────────────────────────

    async def _process_spawn(request_id, params, client_id, session_id, registry):
        # Experimental gate
        require_experimental_api(params, registry, client_id, "process/spawn")
        wpr = _get_wpr(registry)

        # Preserve UNSUPPORTED_FEATURE for tty before typed validation
        if params.get("tty") is True:
            raise AppServerError(
                "PTY is not supported in this version",
                code="UNSUPPORTED_FEATURE",
            )

        typed = validate_process_params(ProcessSpawnParams, params, workspace=wpr.workspace)

        # Streaming callback
        async def _on_chunk(handle_id: str, chunk: OutputChunk) -> None:
            if typed.stream_stdout_stderr:
                await server.emit_client_event(
                    client_id,
                    "process/outputDelta",
                    {
                        "processHandle": handle_id,
                        "stream": chunk.stream,
                        "deltaBase64": base64.b64encode(chunk.data).decode("ascii"),
                        "capReached": chunk.cap_reached,
                    },
                    request_id=request_id,
                )

        # Exit callback — emits process/exited notification
        async def _on_exit(exit_result: ProcessExit) -> None:
            await server.emit_client_event(
                client_id,
                "process/exited",
                {
                    "processHandle": exit_result.handle_id,
                    "exitCode": exit_result.exit_code,
                    "stdout": exit_result.stdout,
                    "stderr": exit_result.stderr,
                    "stdoutCapReached": exit_result.stdout_cap_reached,
                    "stderrCapReached": exit_result.stderr_cap_reached,
                    "durationMs": exit_result.duration_ms,
                    "terminationReason": exit_result.termination_reason,
                },
                request_id=request_id,
            )

        try:
            await wpr.spawn_background(
                client_id=client_id,
                handle_id=typed.process_handle,
                kind="process",
                command=typed.command,
                cwd=typed.cwd,
                env=typed.env,
                stdin_enabled=typed.stdin_enabled,
                output_cap=typed.output_cap,
                timeout_ms=typed.timeout_ms,
                on_chunk=_on_chunk if typed.stream_stdout_stderr else None,
                on_exit=_on_exit,
            )
        except WorkbenchProcessError as exc:
            raise AppServerError(exc.args[0], code=exc.code)

        return {"result": {}}

    # ── process/writeStdin ──────────────────────────────────────────────

    async def _process_write_stdin(request_id, params, client_id, session_id, registry):
        wpr = _get_wpr(registry)
        typed = validate_process_params(ProcessWriteStdinParams, params)

        if typed.delta_bytes is not None:
            try:
                await wpr.write_stdin(client_id, typed.process_handle, typed.delta_bytes)
            except WorkbenchProcessError as exc:
                raise AppServerError(exc.args[0], code=exc.code)

        if typed.close_stdin:
            try:
                await wpr.close_stdin(client_id, typed.process_handle)
            except WorkbenchProcessError as exc:
                raise AppServerError(exc.args[0], code=exc.code)

        return {"result": {}}

    # ── process/resizePty ───────────────────────────────────────────────

    async def _process_resize_pty(request_id, params, client_id, session_id, registry):
        validate_process_params(ProcessResizePtyParams, params)
        raise AppServerError(
            "PTY is not supported in this version",
            code="UNSUPPORTED_FEATURE",
        )

    # ── process/kill ────────────────────────────────────────────────────

    async def _process_kill(request_id, params, client_id, session_id, registry):
        wpr = _get_wpr(registry)
        typed = validate_process_params(ProcessKillParams, params)

        try:
            await wpr.kill(client_id, typed.process_handle)
        except HandleNotFoundError:
            raise AppServerError(
                f"Process handle not found: {typed.process_handle}",
                code="NOT_FOUND",
            )
        except WorkbenchProcessError as exc:
            raise AppServerError(exc.args[0], code=exc.code)

        return {"result": {}}

    # ── Register all ────────────────────────────────────────────────────

    server.register_method("process/spawn", _process_spawn, spec=protocol_specs.PROCESS_SPAWN)
    server.register_method("process/writeStdin", _process_write_stdin, spec=protocol_specs.PROCESS_WRITE_STDIN)
    server.register_method("process/resizePty", _process_resize_pty, spec=protocol_specs.PROCESS_RESIZE_PTY)
    server.register_method("process/kill", _process_kill, spec=protocol_specs.PROCESS_KILL)
