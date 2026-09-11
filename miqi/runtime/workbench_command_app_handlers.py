"""Codex-style command/exec* AppServer handlers.

Registers: command/exec, command/exec/write, command/exec/resize,
command/exec/terminate.

All process execution flows through WorkbenchProcessRuntime, which is
stored in registry.bridge_context["workbench_process_runtime"].
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

import miqi.runtime.protocol_specs as protocol_specs
from miqi.runtime.app_server import (
    AppServer,
    AppServerError,
    get_bridge_context,
)
from miqi.runtime.process_request_models import (
    CommandExecParams,
    CommandExecResizeParams,
    CommandExecTerminateParams,
    CommandExecWriteParams,
    validate_process_params,
)
from miqi.runtime.workbench_process_runtime import (
    HandleNotFoundError,
    OutputChunk,
    WorkbenchProcessError,
    WorkbenchProcessRuntime,
)


def _get_wpr(registry: Any) -> WorkbenchProcessRuntime:
    """Get or lazily create the WorkbenchProcessRuntime from bridge_context."""
    wpr = get_bridge_context(registry, "workbench_process_runtime")
    if wpr is None:
        # Determine workspace from bridge state or fall back to cwd
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


# ── Registration ─────────────────────────────────────────────────────────


def register_workbench_command_handlers(server: AppServer) -> None:
    """Register command/exec* handlers on an AppServer instance."""

    # ── command/exec ────────────────────────────────────────────────────

    async def _command_exec(request_id, params, client_id, session_id, registry):
        wpr = _get_wpr(registry)

        # Preserve UNSUPPORTED_FEATURE for tty before typed validation
        if params.get("tty") is True:
            raise AppServerError(
                "PTY is not supported in this version",
                code="UNSUPPORTED_FEATURE",
            )

        typed = validate_process_params(CommandExecParams, params, workspace=wpr.workspace)

        # Streaming callback
        async def _on_chunk(handle_id: str, chunk: OutputChunk) -> None:
            await server.emit_client_event(
                client_id,
                "command/exec/outputDelta",
                {
                    "processId": handle_id,
                    "stream": chunk.stream,
                    "deltaBase64": base64.b64encode(chunk.data).decode("ascii"),
                    "capReached": chunk.cap_reached,
                },
                request_id=request_id,
            )

        on_chunk = _on_chunk if typed.stream_stdout_stderr else None
        handle_id = typed.process_id or f"cmd-internal-{uuid.uuid4().hex}"

        try:
            exit_result = await wpr.spawn(
                client_id=client_id,
                handle_id=handle_id,
                kind="commandExec",
                command=typed.command,
                cwd=typed.cwd,
                env=typed.env,
                stdin_enabled=typed.stdin_enabled,
                output_cap=typed.output_cap,
                timeout_ms=typed.timeout_ms,
                on_chunk=on_chunk,
                client_visible=typed.client_visible,
            )
        except WorkbenchProcessError as exc:
            raise AppServerError(exc.args[0], code=exc.code)

        return {
            "result": {
                "exitCode": exit_result.exit_code,
                "stdout": exit_result.stdout,
                "stderr": exit_result.stderr,
                "stdoutCapReached": exit_result.stdout_cap_reached,
                "stderrCapReached": exit_result.stderr_cap_reached,
                "durationMs": exit_result.duration_ms,
                "terminationReason": exit_result.termination_reason,
            },
        }

    # ── command/exec/write ──────────────────────────────────────────────

    async def _command_exec_write(request_id, params, client_id, session_id, registry):
        wpr = _get_wpr(registry)
        typed = validate_process_params(CommandExecWriteParams, params)

        if typed.delta_bytes is not None:
            try:
                await wpr.write_stdin(
                    client_id,
                    typed.process_id,
                    typed.delta_bytes,
                    require_client_visible=True,
                )
            except WorkbenchProcessError as exc:
                raise AppServerError(exc.args[0], code=exc.code)

        if typed.close_stdin:
            try:
                await wpr.close_stdin(
                    client_id,
                    typed.process_id,
                    require_client_visible=True,
                )
            except WorkbenchProcessError as exc:
                raise AppServerError(exc.args[0], code=exc.code)

        return {"result": {}}

    # ── command/exec/resize ─────────────────────────────────────────────

    async def _command_exec_resize(request_id, params, client_id, session_id, registry):
        validate_process_params(CommandExecResizeParams, params)
        raise AppServerError(
            "PTY is not supported in this version",
            code="UNSUPPORTED_FEATURE",
        )

    # ── command/exec/terminate ───────────────────────────────────────────

    async def _command_exec_terminate(request_id, params, client_id, session_id, registry):
        wpr = _get_wpr(registry)
        typed = validate_process_params(CommandExecTerminateParams, params)

        try:
            await wpr.kill(client_id, typed.process_id, require_client_visible=True)
        except HandleNotFoundError:
            raise AppServerError(
                f"Process not found: {typed.process_id}",
                code="NOT_FOUND",
            )
        except WorkbenchProcessError as exc:
            raise AppServerError(exc.args[0], code=exc.code)

        return {"result": {}}

    # ── Register all ────────────────────────────────────────────────────

    server.register_method("command/exec", _command_exec, spec=protocol_specs.COMMAND_EXEC)
    server.register_method("command/exec/write", _command_exec_write, spec=protocol_specs.COMMAND_EXEC_WRITE)
    server.register_method("command/exec/resize", _command_exec_resize, spec=protocol_specs.COMMAND_EXEC_RESIZE)
    server.register_method("command/exec/terminate", _command_exec_terminate, spec=protocol_specs.COMMAND_EXEC_TERMINATE)
