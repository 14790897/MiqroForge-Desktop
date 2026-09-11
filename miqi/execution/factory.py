"""Shared orchestrator factory — creates a default ToolOrchestrator.

The single public entry point is create_default_orchestrator, used by
RuntimeServices and runtime-owned execution.
"""

from __future__ import annotations

from typing import Any, Callable


class NoopEmitter:
    """Event emitter that silently discards all events."""

    async def emit(self, event: Any) -> None:
        pass


def create_default_orchestrator(
    tool_registry: Any,
    event_emitter: Any | None = None,
    *,
    bwrap_available: bool | Callable[[], bool] = False,
    allow_fallback_to_none: bool | Callable[[], bool] = True,
    permanent_allowlist: set[str] | None = None,
    approval_bypass: Any | None = None,
    ledger_runtime: Any | None = None,
    exec_timeout_ms: int | None = None,
) -> Any:
    """Create a ToolOrchestrator with sensible defaults.

    Args:
        tool_registry: ToolRegistry instance (or None, wired later).
        event_emitter: EventEmitter for typed events. Uses NoopEmitter if None.
        bwrap_available: Whether bwrap sandboxing is available on this system,
            or a live callable evaluated per selection (#875: the sandbox
            manager initializes asynchronously after the ready signal, so a
            frozen bool silently drops sessions created before init to
            unisolated host execution).
        permanent_allowlist: Set of commands that bypass permission checks.
        ledger_runtime: Phase 31.8 — optional LedgerRuntime for
            replay-persistent event recording.
        exec_timeout_ms: Per-call exec timeout for SandboxSelection.
            Defaults to 30s; pass the configured ``tools.exec.timeout``
            (in ms) so the selection does not silently cap commands below
            the user's setting.
    Returns:
        Configured ToolOrchestrator instance.
    """
    from miqi.execution.hook_runtime import HookRuntime
    from miqi.execution.orchestrator import ToolOrchestrator
    from miqi.execution.permission_engine import PermissionEngine
    from miqi.execution.sandbox_policy import SandboxPolicyEngine

    emitter = event_emitter if event_emitter is not None else NoopEmitter()

    # Read-only shell commands safe to auto-allow in sandbox
    _safe_defaults = {
        "exec:pwd", "exec:whoami", "exec:echo", "exec:ls", "exec:dir",
        "exec:cat", "exec:head", "exec:tail", "exec:env", "exec:uname",
        "exec:which", "exec:type",
        # Commands with common safe arguments
        "exec:uname -s", "exec:uname -a",
        "exec:ls /home/miqi/workspace",
        "exec:echo sandbox_e2e_OK", "exec:echo hello",
    }

    return ToolOrchestrator(
        permission_engine=PermissionEngine(
            permanent_allowlist=_safe_defaults | (permanent_allowlist or set()),
            approval_bypass=approval_bypass,
        ),
        sandbox_engine=SandboxPolicyEngine(
            bwrap_available=bwrap_available,
            allow_fallback_to_none=allow_fallback_to_none,
            default_timeout_ms=exec_timeout_ms or 30_000,
        ),
        hook_runtime=HookRuntime(),
        tool_registry=tool_registry,
        event_emitter=emitter,
        ledger_runtime=ledger_runtime,
    )
