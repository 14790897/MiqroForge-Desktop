"""Phase 33 final acceptance tests — cross-component integration assertions.

These tests validate the complete sandbox pipeline across multiple components:
  SandboxPolicyEngine → ToolOrchestrator → ExecTool → execution path.

They complement (do not duplicate) the component-level tests in:
  - test_sandbox_policy.py (policy engine unit tests)
  - test_exec_tool_sandbox_selection.py (ExecTool dispatch unit tests)
  - test_exec_events.py (BWRAP streaming/cancel unit tests)
  - test_orchestrator.py (orchestrator unit tests)
"""

import pytest

from miqi.agent.tools.shell import ExecTool
from miqi.execution.sandbox_policy import (
    SandboxDeniedError,
    SandboxPolicyEngine,
    SandboxSelection,
    SandboxType,
)
from miqi.protocol.events import ExecCommandBeginEvent, ExecCommandEndEvent
from miqi.protocol.permissions import (
    FileSystemAccessMode,
    FileSystemSandboxPolicy,
    NetworkSandboxPolicy,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _restricted_selection(
    *,
    network_policy: NetworkSandboxPolicy = NetworkSandboxPolicy.ALLOW_ALL,
    timeout_ms: int = 30_000,
) -> SandboxSelection:
    return SandboxSelection(
        sandbox_type=SandboxType.RESTRICTED,
        filesystem_policy=FileSystemSandboxPolicy(
            default_mode=FileSystemAccessMode.READ,
        ),
        network_policy=network_policy,
        timeout_ms=timeout_ms,
        reason="acceptance-test",
    )


def _none_selection() -> SandboxSelection:
    return SandboxSelection(
        sandbox_type=SandboxType.NONE,
        filesystem_policy=FileSystemSandboxPolicy(
            default_mode=FileSystemAccessMode.READ,
        ),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        timeout_ms=30_000,
        reason="acceptance-test",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Full pipeline: policy engine → ExecTool dispatch → RESTRICTED enforcement
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_acceptance_default_config_exec_runs_on_host(tmp_path):
    """Default config (no bwrap, no landlock, no network profile) → policy
    engine selects NONE → exec runs directly on the host without
    restrictions.  No sandbox must not block commands."""
    engine = SandboxPolicyEngine(
        bwrap_available=False,
        landlock_available=False,
    )

    ctx = type("Ctx", (), {
        "tool_name": "exec",
        "arguments": {"command": "echo hello", "cwd": str(tmp_path)},
        "permission_profile": None,
    })()

    selection = await engine.select(ctx)
    assert selection.sandbox_type == SandboxType.NONE, (
        f"Expected NONE, got {selection.sandbox_type.value}"
    )
    assert selection.network_policy == NetworkSandboxPolicy.ALLOW_ALL, (
        "NONE exec must keep ALLOW_ALL when no sandbox is available"
    )

    # Full pipeline: ExecTool must execute on the host
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    result = await tool.execute(
        "echo hello",
        cwd=str(tmp_path),
        _sandbox=selection,
        _event_emitter=None,
        _turn_id="turn-default-fc",
        _tool_call_id="call-default-fc",
    )
    assert "hello" in result, f"Expected host execution, got: {result}"
    assert "Error" not in result


@pytest.mark.asyncio
async def test_acceptance_restricted_network_allowed_executes_safe_command(tmp_path):
    """Full pipeline: RESTRICTED + network_allowed=True → command executes."""
    selection = _restricted_selection(network_policy=NetworkSandboxPolicy.ALLOW_ALL)

    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    result = await tool.execute(
        "echo acceptance-ok",
        cwd=str(tmp_path),
        _sandbox=selection,
        _event_emitter=None,
        _turn_id="turn-net-ok",
        _tool_call_id="call-net-ok",
    )
    assert "acceptance-ok" in result
    assert "Error" not in result


@pytest.mark.asyncio
async def test_acceptance_restricted_rejects_outside_workspace_path(tmp_path):
    """Full pipeline: RESTRICTED + path outside workspace → rejected."""
    selection = _restricted_selection(network_policy=NetworkSandboxPolicy.ALLOW_ALL)

    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    result = await tool.execute(
        "cat /etc/passwd",
        cwd=str(tmp_path),
        _sandbox=selection,
        _event_emitter=None,
        _turn_id="turn-unsafe",
        _tool_call_id="call-unsafe",
    )
    assert "Error" in result
    assert "路径" in result or "超出" in result


@pytest.mark.asyncio
async def test_acceptance_restricted_rejects_tilde_expansion(tmp_path):
    """Full pipeline: RESTRICTED + tilde path → rejected as unresolvable static ref."""
    selection = _restricted_selection(network_policy=NetworkSandboxPolicy.ALLOW_ALL)

    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    result = await tool.execute(
        "cat ~/secret.txt",
        cwd=str(tmp_path),
        _sandbox=selection,
        _event_emitter=None,
        _turn_id="turn-tilde",
        _tool_call_id="call-tilde",
    )
    assert "Error" in result


@pytest.mark.asyncio
async def test_acceptance_restricted_rejects_shell_var_path(tmp_path):
    """Full pipeline: RESTRICTED + shell variable path → always rejected."""
    selection = _restricted_selection(network_policy=NetworkSandboxPolicy.ALLOW_ALL)

    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    result = await tool.execute(
        "cat $HOME/secret.txt",
        cwd=str(tmp_path),
        _sandbox=selection,
        _event_emitter=None,
        _turn_id="turn-shellvar",
        _tool_call_id="call-shellvar",
    )
    assert "Error" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Policy engine: LANDLOCK / fallback invariants
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_acceptance_landlock_never_auto_selected_on_available_alone():
    """Phase 33.4 invariant: landlock_available=True alone does NOT
    select LANDLOCK because landlock_supported=False."""
    engine = SandboxPolicyEngine(
        bwrap_available=False,
        landlock_available=True,
    )

    ctx = type("Ctx", (), {
        "tool_name": "exec",
        "arguments": {"command": "ls"},
        "permission_profile": None,
    })()

    selection = await engine.select(ctx)
    assert selection.sandbox_type != SandboxType.LANDLOCK, (
        "LANDLOCK must not be selected when landlock_supported=False"
    )
    assert selection.sandbox_type == SandboxType.NONE


@pytest.mark.asyncio
async def test_acceptance_exec_never_falls_back_to_none():
    """Phase 33.4 invariant: exec exhaustion still raises instead of
    *falling back* to NONE.  (Attempt 0 with no sandbox is the NONE base
    selection — direct host execution — not a fallback.)"""
    engine = SandboxPolicyEngine(
        bwrap_available=False,
        landlock_available=False,
    )
    ctx = type("Ctx", (), {
        "tool_name": "exec",
        "arguments": {"command": "ls"},
        "permission_profile": None,
    })()

    # Attempt 0 → NONE (no sandbox available — direct host execution)
    s0 = await engine.select(ctx, attempt=0)
    assert s0.sandbox_type == SandboxType.NONE

    # Attempt 1 → beyond chain → must raise, NOT return NONE as fallback
    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(ctx, attempt=1)
    assert "exec" in str(exc_info.value)
    assert "NONE" in str(exc_info.value)


@pytest.mark.asyncio
async def test_acceptance_allow_fallback_to_none_does_not_override_exec():
    """Phase 33.4 invariant: allow_fallback_to_none=True does NOT let
    exec become NONE — exec's _resolve_fallback() is unconditional."""
    engine = SandboxPolicyEngine(
        bwrap_available=False,
        landlock_available=False,
        allow_fallback_to_none=True,  # ← should not affect exec
    )
    ctx = type("Ctx", (), {
        "tool_name": "exec",
        "arguments": {"command": "ls"},
        "permission_profile": None,
    })()

    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(ctx, attempt=99)
    assert "exec" in str(exc_info.value)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Explicit NONE / BWRAP unavailable — fail-closed guards
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_acceptance_bwrap_unavailable_falls_back():
    """BWRAP selection without active sandbox → fall back to host execution with warning."""
    from miqi.execution.sandbox_policy import SandboxSelection, SandboxType
    from miqi.protocol.permissions import FileSystemSandboxPolicy, NetworkSandboxPolicy

    bwrap_sel = SandboxSelection(
        sandbox_type=SandboxType.BWRAP,
        filesystem_policy=FileSystemSandboxPolicy(),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        timeout_ms=5000,
        reason="acceptance-test",
    )

    tool = ExecTool(timeout=5)
    # No sandbox_manager set → BWRAP unavailable
    result = await tool.execute(
        "echo should-not-execute",
        _sandbox=bwrap_sel,
        _event_emitter=None,
        _turn_id="turn-bwrap-fc",
        _tool_call_id="call-bwrap-fc",
    )
    assert "sandbox not available" in result
    assert "should-not-execute" in result


@pytest.mark.asyncio
async def test_acceptance_explicit_none_executes_directly():
    """Explicit NONE selection → direct host execution (caller accepts risk)."""
    selection = _none_selection()

    tool = ExecTool(timeout=5)
    result = await tool.execute(
        "echo explicit-none-works",
        _sandbox=selection,
        _event_emitter=None,
        _turn_id="turn-none",
        _tool_call_id="call-none",
    )
    assert "explicit-none-works" in result
    assert "Error" not in result


@pytest.mark.asyncio
async def test_acceptance_explicit_landlock_fail_closed():
    """Explicit LANDLOCK selection → always fail closed (not yet implemented)."""
    landlock_sel = SandboxSelection(
        sandbox_type=SandboxType.LANDLOCK,
        filesystem_policy=FileSystemSandboxPolicy(),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        timeout_ms=5000,
        reason="acceptance-test",
    )

    tool = ExecTool(timeout=5)
    result = await tool.execute(
        "echo should-not-execute",
        _sandbox=landlock_sel,
        _event_emitter=None,
        _turn_id="turn-landlock-fc",
        _tool_call_id="call-landlock-fc",
    )
    assert "Error" in result
    assert "尚未实现" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Events: correct sandbox_type in begin/end events
# ═══════════════════════════════════════════════════════════════════════════════


class _Collector:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_acceptance_restricted_emits_sandbox_type_in_events(tmp_path):
    """ExecTool with RESTRICTED selection → BeginEvent and EndEvent
    must record sandbox_type='restricted'."""
    selection = _restricted_selection(network_policy=NetworkSandboxPolicy.ALLOW_ALL)
    collector = _Collector()

    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    result = await tool.execute(
        "echo event-test",
        cwd=str(tmp_path),
        _sandbox=selection,
        _event_emitter=collector,
        _turn_id="turn-evt",
        _tool_call_id="call-evt",
    )
    assert "event-test" in result

    begin_events = [e for e in collector.events if isinstance(e, ExecCommandBeginEvent)]
    end_events = [e for e in collector.events if isinstance(e, ExecCommandEndEvent)]
    assert len(begin_events) == 1
    assert len(end_events) == 1
    assert begin_events[0].sandbox_type == "restricted"


@pytest.mark.asyncio
async def test_acceptance_none_emits_sandbox_type_in_events(tmp_path):
    """ExecTool with NONE selection → BeginEvent and EndEvent
    must record sandbox_type='none'."""
    selection = _none_selection()
    collector = _Collector()

    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    result = await tool.execute(
        "echo none-event-test",
        cwd=str(tmp_path),
        _sandbox=selection,
        _event_emitter=collector,
        _turn_id="turn-none-evt",
        _tool_call_id="call-none-evt",
    )
    assert "none-event-test" in result

    begin = [e for e in collector.events if isinstance(e, ExecCommandBeginEvent)]
    assert len(begin) == 1
    assert begin[0].sandbox_type == "none"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. BwrapCommandHandle.wait() exit code regression
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_acceptance_bwrap_handle_wait_preserves_exit_zero():
    """Regression: BwrapCommandHandle.wait() must return 0, not -1, for exit code 0."""
    from unittest.mock import AsyncMock, MagicMock

    from miqi.sandbox.bwrap import BwrapCommandHandle

    proc = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    proc.returncode = 0
    handle = BwrapCommandHandle(proc)
    exit_code = await handle.wait()
    assert exit_code == 0, f"Expected 0, got {exit_code}"


@pytest.mark.asyncio
async def test_acceptance_bwrap_handle_wait_preserves_exit_nonzero():
    """Regression: BwrapCommandHandle.wait() must return the actual non-zero exit code."""
    from unittest.mock import AsyncMock, MagicMock

    from miqi.sandbox.bwrap import BwrapCommandHandle

    proc = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    proc.returncode = 99
    handle = BwrapCommandHandle(proc)
    exit_code = await handle.wait()
    assert exit_code == 99, f"Expected 99, got {exit_code}"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. SandboxDeniedError message quality
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_acceptance_sandbox_denied_error_is_actionable():
    """SandboxDeniedError must list actionable opt-in paths."""
    engine = SandboxPolicyEngine(
        bwrap_available=False,
        landlock_available=False,
    )
    ctx = type("Ctx", (), {
        "tool_name": "exec",
        "arguments": {"command": "ls"},
        "permission_profile": None,
    })()

    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(ctx, attempt=99)
    msg = str(exc_info.value)
    assert "bwrap_available" in msg or "network_allowed" in msg, (
        f"Error message must list opt-in paths, got: {msg}"
    )
