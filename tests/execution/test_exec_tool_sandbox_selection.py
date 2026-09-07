"""Tests for Phase 31.2: ExecTool consumes SandboxSelection from ToolOrchestrator.

These tests verify that ExecTool no longer makes independent sandbox decisions
and instead follows the SandboxSelection injected by ToolOrchestrator.
"""

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.agent.tools.shell import ExecTool
from miqi.execution.sandbox_policy import SandboxSelection, SandboxType
from miqi.protocol.events import ExecCommandBeginEvent, ExecCommandEndEvent
from miqi.protocol.permissions import (
    FileSystemAccessMode,
    FileSystemSandboxPolicy,
    NetworkSandboxPolicy,
)

# ── helpers ────────────────────────────────────────────────────────────

def _make_selection(
    sandbox_type: SandboxType = SandboxType.NONE,
    *,
    timeout_ms: int = 30_000,
    env_passthrough: list[str] | None = None,
) -> SandboxSelection:
    """Create a SandboxSelection for testing."""
    return SandboxSelection(
        sandbox_type=sandbox_type,
        filesystem_policy=FileSystemSandboxPolicy(
            default_mode=FileSystemAccessMode.READ,
        ),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        env_passthrough=env_passthrough or [],
        timeout_ms=timeout_ms,
        reason=f"Test selection: {sandbox_type.value}",
    )


async def _make_mock_sandbox(*, is_running: bool = True, run_result=None):
    """Create a mock BwrapSandbox with both legacy and streaming APIs."""
    import asyncio as _asyncio

    sandbox = MagicMock()
    sandbox.is_running = is_running
    sandbox.get_sandbox_env = MagicMock(return_value={})
    if run_result is None:
        run_result = (0, "hello from sandbox", "")

    # Legacy batch API (used by non-streaming code paths)
    sandbox.run_command = AsyncMock(return_value=run_result)

    # Phase 33.2 streaming API — spawn a real subprocess so
    # _read_stream() gets genuine asyncio.StreamReader objects.
    exit_code, stdout_text, stderr_text = run_result
    script_parts = ["import sys"]
    if stdout_text:
        script_parts.append(f"sys.stdout.write({stdout_text!r})")
    if stderr_text:
        script_parts.append(f"sys.stderr.write({stderr_text!r})")
    script_parts.append(f"sys.exit({exit_code})")
    script = "; ".join(script_parts)

    process = await _asyncio.create_subprocess_exec(
        "python", "-c", script,
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
    )

    # Inline minimal handle (same shape as BwrapCommandHandle)
    handle = MagicMock()
    handle.stdout = process.stdout
    handle.stderr = process.stderr
    handle.returncode = None
    handle._process = process

    async def _wait():
        await process.wait()
        return process.returncode if process.returncode is not None else -1

    async def _kill():
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await _asyncio.wait_for(process.wait(), timeout=5.0)
        except (_asyncio.TimeoutError, ProcessLookupError):
            pass

    async def _cleanup():
        pass

    handle.wait = _wait
    handle.kill = _kill
    handle.cleanup = _cleanup

    sandbox.run_command_streaming = AsyncMock(return_value=handle)
    return sandbox


def _make_mock_sandbox_manager(*, active_sandbox=None):
    """Create a mock SandboxManager.

    After Phase 31.2b auto-activate cascade, ExecTool tries:
      1. active_sandbox
      2. list_sandboxes() → get_sandbox() for any running sandbox
      3. get_or_create("_auto_exec")

    Mocks are wired to return empty/none so tests that expect "fail closed"
    still work without hitting a non-async MagicMock.
    """
    mgr = MagicMock()
    mgr.active_sandbox = active_sandbox
    mgr.list_sandboxes = MagicMock(return_value=[])
    mgr.get_sandbox = MagicMock(return_value=None)
    mgr.get_or_create = AsyncMock(return_value=None)
    return mgr


class _EventCollector:
    """Collects emitted events for assertion."""

    def __init__(self):
        self.events: list = []

    async def emit(self, event):
        self.events.append(event)


# ── 31.2: SandboxSelection consumption ─────────────────────────────────

@pytest.mark.asyncio
async def test_sandbox_selection_none_allows_direct_execution():
    """When _sandbox is NONE, the orchestrator explicitly allows direct execution."""
    tool = ExecTool(timeout=5)
    sel = _make_selection(SandboxType.NONE)

    result = await tool.execute(
        "python -c \"print('direct-ok')\"",
        _sandbox=sel,
    )

    assert "direct-ok" in result


@pytest.mark.asyncio
async def test_sandbox_selection_none_overrides_active_sandbox():
    """Phase 31.2b blocker: when orchestrator passes _sandbox=NONE,
    ExecTool MUST do direct execution — even if an active sandbox_manager
    with a running sandbox exists.  The orchestrator's SandboxSelection
    is the single source of truth; ExecTool must not second-guess it."""
    mock_sandbox = await _make_mock_sandbox(is_running=True)
    mock_mgr = _make_mock_sandbox_manager(active_sandbox=mock_sandbox)

    tool = ExecTool(timeout=5, sandbox_manager=mock_mgr)
    sel = _make_selection(SandboxType.NONE)

    result = await tool.execute(
        "python -c \"print('direct-not-sandbox')\"",
        _sandbox=sel,
    )

    assert "direct-not-sandbox" in result
    # The sandbox must NOT have been used — orchestrator said NONE.
    mock_sandbox.run_command.assert_not_called()
    mock_sandbox.run_command_streaming.assert_not_called()


@pytest.mark.asyncio
async def test_sandbox_selection_bwrap_with_active_sandbox():
    """When _sandbox is BWRAP and sandbox is active, use sandbox execution."""
    mock_sandbox = await _make_mock_sandbox(is_running=True)
    mock_mgr = _make_mock_sandbox_manager(active_sandbox=mock_sandbox)

    tool = ExecTool(timeout=5, sandbox_manager=mock_mgr)
    sel = _make_selection(SandboxType.BWRAP)

    result = await tool.execute(
        "echo hello",
        _sandbox=sel,
    )

    assert "hello from sandbox" in result
    mock_sandbox.run_command_streaming.assert_called_once()


@pytest.mark.asyncio
async def test_sandbox_selection_bwrap_unavailable_no_manager_falls_back():
    """BWRAP selected but no sandbox_manager → fall back to host execution."""
    tool = ExecTool(timeout=5, sandbox_manager=None)
    sel = _make_selection(SandboxType.BWRAP)

    result = await tool.execute(
        "echo should-not-run",
        _sandbox=sel,
    )

    assert "sandbox not available" in result
    assert "should-not-run" in result


@pytest.mark.asyncio
async def test_sandbox_selection_bwrap_no_active_sandbox_falls_back():
    """BWRAP selected, sandbox_manager exists but no active sandbox → fall back to host."""
    mock_mgr = _make_mock_sandbox_manager(active_sandbox=None)

    tool = ExecTool(timeout=5, sandbox_manager=mock_mgr)
    sel = _make_selection(SandboxType.BWRAP)

    result = await tool.execute(
        "echo should-not-run",
        _sandbox=sel,
    )

    assert "sandbox not available" in result
    assert "should-not-run" in result


@pytest.mark.asyncio
async def test_sandbox_selection_bwrap_sandbox_not_running_falls_back():
    """BWRAP selected, sandbox exists but is_running=False → fall back to host."""
    mock_sandbox = await _make_mock_sandbox(is_running=False)
    mock_mgr = _make_mock_sandbox_manager(active_sandbox=mock_sandbox)

    tool = ExecTool(timeout=5, sandbox_manager=mock_mgr)
    sel = _make_selection(SandboxType.BWRAP)

    result = await tool.execute(
        "echo should-not-run",
        _sandbox=sel,
    )

    assert "sandbox not available" in result
    assert "should-not-run" in result


@pytest.mark.asyncio
async def test_sandbox_selection_landlock_unsupported():
    """LANDLOCK is not implemented → fail closed with clear message."""
    tool = ExecTool(timeout=5)
    sel = _make_selection(SandboxType.LANDLOCK)

    result = await tool.execute(
        "echo should-not-run",
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "LANDLOCK" in result


@pytest.mark.asyncio
async def test_sandbox_selection_restricted_allows_direct(tmp_path):
    """RESTRICTED sandbox type allows direct execution with enforcement
    when cwd is within workspace."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        "python -c \"print('restricted-ok')\"",
        _sandbox=sel,
        working_dir=str(tmp_path),
    )

    assert "restricted-ok" in result


# ── 31.2: Legacy path (no SandboxSelection) ────────────────────────────

@pytest.mark.asyncio
async def test_legacy_path_direct_execution():
    """Without _sandbox and without sandbox_manager → legacy direct execution."""
    tool = ExecTool(timeout=5)

    result = await tool.execute("python -c \"print('legacy')\"")

    assert "legacy" in result


@pytest.mark.asyncio
async def test_legacy_path_with_sandbox_manager():
    """Without _sandbox but with sandbox_manager and active sandbox → legacy sandbox path."""
    mock_sandbox = await _make_mock_sandbox(is_running=True)
    mock_mgr = _make_mock_sandbox_manager(active_sandbox=mock_sandbox)

    tool = ExecTool(timeout=5, sandbox_manager=mock_mgr)

    result = await tool.execute("echo hello")

    assert "hello from sandbox" in result
    mock_sandbox.run_command_streaming.assert_called_once()


# ── 31.2: Event correctness ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_begin_event_sandbox_type_from_selection():
    """Begin event sandbox_type must come from SandboxSelection, not sandbox_manager."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=5, sandbox_manager=None)  # no sandbox_manager
    sel = _make_selection(SandboxType.BWRAP)  # but selection says BWRAP

    await tool.execute(
        "python -c \"print('ok')\"",
        _event_emitter=emitter,
        _turn_id="t1",
        _tool_call_id="c1",
        _sandbox=sel,
    )

    begin = [e for e in emitter.events if isinstance(e, ExecCommandBeginEvent)]
    assert len(begin) == 1
    # Must use the SandboxSelection type, NOT "none" from missing sandbox_manager
    assert begin[0].sandbox_type == "bwrap"


@pytest.mark.asyncio
async def test_begin_event_sandbox_type_none_selection():
    """Begin event reflects NONE when SandboxSelection type is NONE."""
    emitter = _EventCollector()
    mock_sandbox = await _make_mock_sandbox(is_running=True)
    mock_mgr = _make_mock_sandbox_manager(active_sandbox=mock_sandbox)
    tool = ExecTool(timeout=5, sandbox_manager=mock_mgr)

    sel = _make_selection(SandboxType.NONE)

    await tool.execute(
        "python -c \"print('ok')\"",
        _event_emitter=emitter,
        _turn_id="t1",
        _tool_call_id="c1",
        _sandbox=sel,
    )

    begin = [e for e in emitter.events if isinstance(e, ExecCommandBeginEvent)]
    assert len(begin) == 1
    assert begin[0].sandbox_type == "none"


@pytest.mark.asyncio
async def test_legacy_begin_event_no_selection():
    """Without _sandbox, begin event uses legacy sandbox_type logic."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=5, sandbox_manager=None)

    await tool.execute(
        "python -c \"print('ok')\"",
        _event_emitter=emitter,
        _turn_id="t1",
        _tool_call_id="c1",
    )

    begin = [e for e in emitter.events if isinstance(e, ExecCommandBeginEvent)]
    assert len(begin) == 1
    assert begin[0].sandbox_type == "none"


# ── 31.2: Existing event tests compatibility ───────────────────────────

@pytest.mark.asyncio
async def test_exec_tool_emits_begin_and_end_no_sandbox_selection(tmp_path):
    """Existing behavior: ExecTool emits begin+end events without _sandbox."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))

    output = await tool.execute(
        "python -c \"print('hello')\"",
        _event_emitter=emitter,
        _turn_id="turn-1",
        _tool_call_id="tc-1",
    )

    assert "hello" in output

    begin_events = [e for e in emitter.events if isinstance(e, ExecCommandBeginEvent)]
    end_events = [e for e in emitter.events if isinstance(e, ExecCommandEndEvent)]

    assert len(begin_events) == 1
    assert len(end_events) == 1
    assert begin_events[0].turn_id == "turn-1"
    assert end_events[0].turn_id == "turn-1"
    assert end_events[0].output_size > 0


@pytest.mark.asyncio
async def test_exec_tool_emits_begin_and_end_with_sandbox_selection(tmp_path):
    """With _sandbox=NONE, ExecTool still emits begin+end events."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.NONE)

    output = await tool.execute(
        "python -c \"print('hello-selection')\"",
        _event_emitter=emitter,
        _turn_id="turn-2",
        _tool_call_id="tc-2",
        _sandbox=sel,
    )

    assert "hello-selection" in output

    begin_events = [e for e in emitter.events if isinstance(e, ExecCommandBeginEvent)]
    end_events = [e for e in emitter.events if isinstance(e, ExecCommandEndEvent)]

    assert len(begin_events) == 1
    assert len(end_events) == 1
    assert begin_events[0].turn_id == "turn-2"
    assert begin_events[0].sandbox_type == "none"
    assert end_events[0].turn_id == "turn-2"
    assert end_events[0].output_size > 0


# ── 31.3: SandboxSelection.timeout_ms enforcement ──────────────────────

@pytest.mark.asyncio
async def test_sandbox_selection_timeout_ms_consumed():
    """SandboxSelection.timeout_ms must be used as the actual exec timeout."""
    tool = ExecTool(timeout=60)  # Default 60s — would NOT timeout normally
    sel = _make_selection(SandboxType.NONE, timeout_ms=50)  # 50ms timeout

    result = await tool.execute(
        "python -c \"import time; time.sleep(10)\"",
        _sandbox=sel,
    )

    assert "超时" in result


@pytest.mark.asyncio
async def test_sandbox_selection_timeout_ms_not_exceeded():
    """When timeout_ms is long enough, command completes normally."""
    tool = ExecTool(timeout=1)  # 1s default — too short for slow commands
    sel = _make_selection(SandboxType.NONE, timeout_ms=30_000)  # 30s from selection

    result = await tool.execute(
        "python -c \"print('completed')\"",
        _sandbox=sel,
    )

    assert "completed" in result
    assert "超时" not in result


# ── 31.3: SandboxSelection.env_passthrough enforcement ─────────────────

@pytest.mark.asyncio
async def test_sandbox_selection_env_passthrough_consumed():
    """SandboxSelection.env_passthrough must allow otherwise-filtered env vars."""
    import os

    test_var = "_MIQI_PHASE31_ENV_TEST_313"
    os.environ[test_var] = "phase31-env-value"
    try:
        tool = ExecTool(timeout=5)

        # Without env_passthrough → the var should still be present
        # (it doesn't match any sensitive pattern)
        sel_no_passthrough = _make_selection(SandboxType.NONE)
        result_no = await tool.execute(
            f"python -c \"import os; print(os.environ.get('{test_var}', 'MISSING'))\"",
            _sandbox=sel_no_passthrough,
        )
        assert "phase31-env-value" in result_no

        # With env_passthrough containing the var → also present
        sel_with = _make_selection(
            SandboxType.NONE, env_passthrough=[test_var],
        )
        result_with = await tool.execute(
            f"python -c \"import os; print(os.environ.get('{test_var}', 'MISSING'))\"",
            _sandbox=sel_with,
        )
        assert "phase31-env-value" in result_with
    finally:
        os.environ.pop(test_var, None)


@pytest.mark.asyncio
async def test_sandbox_selection_env_passthrough_overrides_filter():
    """env_passthrough from SandboxSelection must bypass the credential filter."""
    import os

    # Use a var name that WOULD be filtered by _build_safe_env
    # (starts with a sensitive prefix like OPENAI_)
    test_var = "OPENAI_MIQI_TEST_313"
    os.environ[test_var] = "bypass-value"
    try:
        tool = ExecTool(timeout=5)

        # Without env_passthrough → filtered out (default behavior)
        sel_default = _make_selection(SandboxType.NONE)
        result_default = await tool.execute(
            f"python -c \"import os; print(os.environ.get('{test_var}', 'MISSING'))\"",
            _sandbox=sel_default,
        )
        assert "MISSING" in result_default, (
            f"Expected var to be filtered, got: {result_default!r}"
        )

        # With env_passthrough → must be present
        sel_passthrough = _make_selection(
            SandboxType.NONE, env_passthrough=[test_var],
        )
        result_passthrough = await tool.execute(
            f"python -c \"import os; print(os.environ.get('{test_var}', 'MISSING'))\"",
            _sandbox=sel_passthrough,
        )
        assert "bypass-value" in result_passthrough, (
            f"Expected var to be present via env_passthrough, got: {result_passthrough!r}"
        )
    finally:
        os.environ.pop(test_var, None)


# ── 31.3: RESTRICTED enforcement ───────────────────────────────────────

@pytest.mark.asyncio
async def test_restricted_cwd_outside_workspace_fails(tmp_path):
    """RESTRICTED with cwd outside workspace must fail closed."""
    tool = ExecTool(
        timeout=5,
        working_dir=str(tmp_path),
        restrict_to_workspace=True,
    )
    sel = _make_selection(SandboxType.RESTRICTED)

    # Use a cwd outside the workspace (parent of tmp_path)
    outside_dir = str(tmp_path.parent)

    result = await tool.execute(
        "echo should-not-run",
        working_dir=outside_dir,
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_restricted_cwd_within_workspace_succeeds(tmp_path):
    """RESTRICTED with cwd inside workspace must succeed."""
    tool = ExecTool(
        timeout=5,
        working_dir=str(tmp_path),
        restrict_to_workspace=True,
    )
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        "python -c \"print('within-workspace')\"",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "within-workspace" in result


# ── 31.3: SandboxSelection policy metadata presence ────────────────────

def test_sandbox_selection_includes_filesystem_policy():
    """SandboxSelection.filesystem_policy must be populated."""
    sel = _make_selection(SandboxType.BWRAP)
    assert sel.filesystem_policy is not None
    assert sel.filesystem_policy.default_mode == FileSystemAccessMode.READ


def test_sandbox_selection_includes_network_policy():
    """SandboxSelection.network_policy must be populated."""
    sel = _make_selection(SandboxType.RESTRICTED)
    assert sel.network_policy == NetworkSandboxPolicy.ALLOW_ALL


def test_sandbox_selection_includes_timeout_ms():
    """SandboxSelection.timeout_ms must be populated."""
    sel = _make_selection(SandboxType.NONE, timeout_ms=45_000)
    assert sel.timeout_ms == 45_000


def test_sandbox_selection_includes_env_passthrough():
    """SandboxSelection.env_passthrough must be populated."""
    sel = _make_selection(SandboxType.NONE, env_passthrough=["PATH", "HOME"])
    assert "PATH" in sel.env_passthrough
    assert "HOME" in sel.env_passthrough


# ── 31.3: SandboxPolicyEngine allow_fallback_to_none ───────────────────

@pytest.mark.asyncio
async def test_sandbox_policy_dynamic_bwrap_available_live():
    """#875 产品发现：bwrap_available 传 callable 时每次 select() 实时求值。

    沙箱管理器初始化是 ready 信号后的异步后台任务——会话创建早于初始化时，
    冻结的 False 会让该会话的 exec 永远落到 NONE（宿主机无隔离直连），
    沙箱就绪后也拿不到 BWRAP 保护。callable 一旦翻转为 True，既有会话的
    下一次 exec 立即获得 BWRAP 选择。
    """
    from miqi.execution.sandbox_policy import (
        SandboxPolicyEngine,
    )

    state = {"initialized": False}

    def _provider() -> bool:
        return state["initialized"]

    engine = SandboxPolicyEngine(bwrap_available=_provider)

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "apt-get install -y x"}

    # 沙箱未就绪 → NONE（无隔离直连，此时沙箱确实不可用）
    sel1 = await engine.select(FakeCtx())
    assert sel1.sandbox_type == SandboxType.NONE

    # 沙箱异步初始化完成（模拟 _init_sandbox_manager 结束）→ 既有会话立即
    # 获得 BWRAP 保护——无需重建会话/重启
    state["initialized"] = True
    sel2 = await engine.select(FakeCtx())
    assert sel2.sandbox_type == SandboxType.BWRAP


@pytest.mark.asyncio
async def test_sandbox_enabled_no_silent_host_fallback():
    """#875 第二轮评估（B7 不变量）：沙箱开启但 bwrap 不可用 → 拒绝，绝不 NONE。

    NONE（宿主机执行）只允许来自显式的沙箱关闭——allow_fallback_to_none
    传 callable 表达用户意图：开启 → False（拒绝）；关闭 → True（允许）。
    """
    from miqi.execution.sandbox_policy import (
        SandboxDeniedError,
        SandboxPolicyEngine,
    )

    state = {"enabled": True}

    def _fallback() -> bool:
        return not state["enabled"]

    engine = SandboxPolicyEngine(
        bwrap_available=False,  # 沙箱不可用（初始化窗口/失败）
        allow_fallback_to_none=_fallback,
    )

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "apt-get install -y x"}

    # 沙箱开启 + bwrap 不可用 → 绝不静默降级到宿主机
    with pytest.raises(SandboxDeniedError):
        await engine.select(FakeCtx())

    # 用户显式关闭沙箱 → NONE（宿主机模式是显式选择，护栏兜底）
    state["enabled"] = False
    sel = await engine.select(FakeCtx())
    assert sel.sandbox_type == SandboxType.NONE


# ── 31.3: PermissionProfile fields in SandboxSelection path ────────────

@pytest.mark.asyncio
async def test_sandbox_policy_no_fallback_fails_on_exhaustion():
    """SandboxPolicyEngine with allow_fallback_to_none=False raises SandboxDeniedError."""
    from miqi.execution.sandbox_policy import (
        SandboxDeniedError,
        SandboxPolicyEngine,
    )

    engine = SandboxPolicyEngine(
        bwrap_available=True,
        allow_fallback_to_none=False,
    )

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "npm test"}

    # After exhausting all sandbox types (BWRAP→LANDLOCK→RESTRICTED→NONE),
    # the next attempt should raise SandboxDeniedError
    with pytest.raises(SandboxDeniedError):
        await engine.select(FakeCtx(), attempt=4)


# ── 31.3: PermissionProfile fields in SandboxSelection path ────────────

@pytest.mark.asyncio
async def test_permission_profile_filesystem_mode_in_sandbox_selection():
    """When PermissionProfile has filesystem_mode, it should be reflected
    in or at least compatible with the SandboxSelection."""
    from miqi.execution.sandbox_policy import SandboxPolicyEngine
    from miqi.runtime.permission_profile import PermissionProfile

    profile = PermissionProfile(
        workspace=None,  # type: ignore
        filesystem_mode="workspace-readonly",
        network_allowed=False,
    )

    engine = SandboxPolicyEngine()

    class FakeCtx:
        tool_name = "write_file"
        arguments = {"path": "test.txt"}
        permission_profile = profile

    sel = await engine.select(FakeCtx())
    # The selection exists with a valid type
    assert sel.sandbox_type is not None
    assert sel.filesystem_policy is not None
    assert sel.network_policy is not None


# ═══════════════════════════════════════════════════════════════════════════
# Phase 33.3: RESTRICTED policy enforcement — cwd
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_restricted_cwd_enforced_even_without_restrict_config(tmp_path):
    """RESTRICTED must reject cwd outside workspace even when
    restrict_to_workspace=False."""
    tool = ExecTool(
        timeout=5,
        working_dir=str(tmp_path),
        restrict_to_workspace=False,  # ← disabled, but RESTRICTED ignores it
    )
    sel = _make_selection(SandboxType.RESTRICTED)
    outside_dir = str(tmp_path.parent)

    result = await tool.execute(
        "echo should-not-run",
        working_dir=outside_dir,
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_restricted_cwd_inside_workspace_allows_safe_command(tmp_path):
    """RESTRICTED with cwd inside workspace must allow a simple safe command."""
    tool = ExecTool(
        timeout=5,
        working_dir=str(tmp_path),
        restrict_to_workspace=True,
    )
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        "python -c \"print('safe-command-ok')\"",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "safe-command-ok" in result


@pytest.mark.asyncio
async def test_restricted_no_working_dir_fails_closed():
    """RESTRICTED without a configured working_dir must fail closed."""
    tool = ExecTool(timeout=5, working_dir=None)
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        "echo should-not-run",
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "工作区" in result


# ═══════════════════════════════════════════════════════════════════════════
# Phase 33.3: RESTRICTED policy enforcement — file paths
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_restricted_rejects_outside_windows_absolute_path(tmp_path):
    """RESTRICTED must reject commands referencing Windows absolute paths
    outside the workspace."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    outside = str(tmp_path.parent / "outside.txt")
    result = await tool.execute(
        f"type {outside}",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_restricted_rejects_outside_posix_absolute_path(tmp_path):
    """RESTRICTED must reject commands with POSIX absolute paths
    (/etc/passwd, /tmp/x) which are outside the workspace."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        "cat /etc/passwd",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_restricted_rejects_wsl_absolute_path(tmp_path):
    """RESTRICTED must reject /mnt/c/... paths outside workspace."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        "cat /mnt/c/Windows/System32/drivers/etc/hosts",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_restricted_allows_workspace_relative_path(tmp_path):
    """RESTRICTED must allow relative paths that stay inside workspace."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    # Create a test file inside workspace
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello-workspace")

    result = await tool.execute(
        "python -c \"print(open('test.txt').read())\"",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "hello-workspace" in result


@pytest.mark.asyncio
async def test_restricted_allows_inside_workspace_absolute_path(tmp_path):
    """RESTRICTED must allow Windows absolute paths that stay inside
    workspace."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    test_file = tmp_path / "inside.txt"
    test_file.write_text("inside-workspace")

    result = await tool.execute(
        f"python -c \"print(open(r'{test_file}').read())\"",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "inside-workspace" in result


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows path traversal syntax (backslash separators)")
async def test_restricted_rejects_traversal_path(tmp_path):
    """RESTRICTED must reject commands using ../ traversal."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        "type ..\\..\\Windows\\System32\\drivers\\etc\\hosts",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_restricted_rejects_redirect_to_outside(tmp_path):
    """RESTRICTED must reject redirect that writes outside workspace."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)
    outside = str(tmp_path.parent / "out.txt")

    result = await tool.execute(
        f"echo data > {outside}",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    # Issue #811: the capability guard now intercepts this BEFORE the
    # RESTRICTED enforcement, with a structured refusal (沙箱护栏拦截);
    # the RESTRICTED path ("命令未执行") remains as the fallback.
    assert "未执行" in result or "沙箱护栏拦截" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_restricted_rejects_append_redirect_to_outside(tmp_path):
    """RESTRICTED must reject append redirect outside workspace."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)
    outside = str(tmp_path.parent / "append.txt")

    result = await tool.execute(
        f"echo more >> {outside}",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    # Issue #811: structured guard refusal before RESTRICTED enforcement.
    assert "未执行" in result or "沙箱护栏拦截" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_restricted_rejects_input_redirect_from_outside(tmp_path):
    """RESTRICTED must reject input redirect from outside workspace."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)
    outside = str(tmp_path.parent / "input.txt")

    result = await tool.execute(
        f"sort < {outside}",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_bwrap_fallback_requards_with_host_semantics(tmp_path):
    """BWRAP selection with no live sandbox → the host fallback must
    re-check the guard with HOST path semantics (issue #811 review).

    The pre-flight guard ran with sandbox semantics (BWRAP selected) and
    allowed the sandbox-internal path /home/miqi/...; on the host that
    is a REAL path, so the fallback must refuse it instead of executing
    against the wrong filesystem.
    """
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.BWRAP)

    result = await tool.execute(
        "rm -rf /home/miqi/workspace/x",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )
    assert "沙箱护栏拦截" in result


class _FakeActiveSandbox:
    is_running = True


class _FakeManagerWithActiveSandbox:
    def __init__(self):
        self.active_sandbox = _FakeActiveSandbox()


@pytest.mark.asyncio
async def test_none_selection_keeps_host_semantics_with_active_sandbox(tmp_path):
    """NONE/RESTRICTED selections execute on the HOST — the guard must
    use host path semantics even when the manager holds an active
    sandbox (issue #811 review)."""
    tool = ExecTool(
        timeout=5, working_dir=str(tmp_path),
        sandbox_manager=_FakeManagerWithActiveSandbox(),
    )
    for st in (SandboxType.NONE, SandboxType.RESTRICTED):
        result = await tool.execute(
            "rm -rf /home/miqi/workspace/x",
            working_dir=str(tmp_path),
            _sandbox=_make_selection(st),
        )
        assert "沙箱护栏拦截" in result, f"{st} must keep host semantics"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 33.3: RESTRICTED policy enforcement — network
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_restricted_network_block_all_fails_closed(tmp_path):
    """RESTRICTED with BLOCK_ALL network policy must fail closed —
    direct host execution cannot enforce network isolation."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    from miqi.protocol.permissions import NetworkSandboxPolicy

    sel = _make_selection(SandboxType.RESTRICTED)
    sel.network_policy = NetworkSandboxPolicy.BLOCK_ALL  # type: ignore[assignment]

    result = await tool.execute(
        "python -c \"print('should-not-run')\"",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "network" in result.lower()


@pytest.mark.asyncio
async def test_restricted_network_allow_all_proceeds(tmp_path):
    """RESTRICTED with ALLOW_ALL network policy must proceed."""
    from miqi.protocol.permissions import NetworkSandboxPolicy

    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)
    sel.network_policy = NetworkSandboxPolicy.ALLOW_ALL  # type: ignore[assignment]

    result = await tool.execute(
        "python -c \"print('network-allowed')\"",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "network-allowed" in result


# ═══════════════════════════════════════════════════════════════════════════
# Phase 33.3: SandboxPolicyEngine RESTRICTED network default
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_engine_exec_no_sandbox_selects_none_with_network_allowed():
    """SandboxPolicyEngine selects NONE for exec when no stronger sandbox
    is available — direct host execution, network allowed, no restrictions."""
    from miqi.execution.sandbox_policy import SandboxPolicyEngine
    from miqi.runtime.permission_profile import PermissionProfile

    engine = SandboxPolicyEngine(bwrap_available=False, landlock_available=False)

    profile = PermissionProfile(
        workspace=None,  # type: ignore
        network_allowed=False,
    )

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "curl example.com"}
        permission_profile = profile

    sel = await engine.select(FakeCtx())
    # No bwrap, no landlock — NONE: direct host execution
    assert sel.sandbox_type == SandboxType.NONE
    # Network stays ALLOW_ALL so exec can run
    assert sel.network_policy == "allow_all"


@pytest.mark.asyncio
async def test_engine_restricted_exec_with_sandbox_available_blocks_network():
    """When bwrap is available but the selection was downgraded to
    RESTRICTED (escalation past BWRAP/LANDLOCK), network stays BLOCK_ALL
    unless permission_profile.network_allowed=True."""
    from miqi.execution.sandbox_policy import SandboxPolicyEngine
    from miqi.runtime.permission_profile import PermissionProfile

    engine = SandboxPolicyEngine(bwrap_available=True, landlock_available=False)

    profile = PermissionProfile(
        workspace=None,  # type: ignore
        network_allowed=False,
    )

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "curl example.com"}
        permission_profile = profile

    # attempt=2 escalates past BWRAP and LANDLOCK down to RESTRICTED
    sel = await engine.select(FakeCtx(), attempt=2)
    assert sel.sandbox_type == SandboxType.RESTRICTED
    assert sel.network_policy == "block_all"


@pytest.mark.asyncio
async def test_engine_restricted_exec_network_none_profile_blocks_without_sandbox():
    """A PermissionProfile with network="none" is an explicit denial —
    RESTRICTED exec gets BLOCK_ALL even when no stronger sandbox is
    available, so the no-sandbox fallback cannot bypass the denial."""
    from miqi.execution.sandbox_policy import SandboxPolicyEngine
    from miqi.runtime.permission_profile import PermissionProfile

    engine = SandboxPolicyEngine(bwrap_available=False, landlock_available=False)

    profile = PermissionProfile(
        workspace=None,  # type: ignore
        network="none",
        network_allowed=False,
    )

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "curl example.com"}
        permission_profile = profile

    sel = await engine.select(FakeCtx())
    assert sel.sandbox_type == SandboxType.RESTRICTED
    assert sel.network_policy == "block_all"


@pytest.mark.asyncio
async def test_engine_restricted_exec_network_none_denial_wins_over_allow():
    """network="none" is a hard denial: it blocks even when
    network_allowed=True (deny wins over allow)."""
    from miqi.execution.sandbox_policy import SandboxPolicyEngine
    from miqi.runtime.permission_profile import PermissionProfile

    engine = SandboxPolicyEngine(bwrap_available=False, landlock_available=False)

    profile = PermissionProfile(
        workspace=None,  # type: ignore
        network="none",
        network_allowed=True,
    )

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "curl example.com"}
        permission_profile = profile

    sel = await engine.select(FakeCtx())
    assert sel.sandbox_type == SandboxType.RESTRICTED
    assert sel.network_policy == "block_all"


@pytest.mark.asyncio
async def test_engine_restricted_exec_network_allowed_overrides_to_allow_all():
    """When permission_profile.network_allowed=True, RESTRICTED exec
    (downgraded from an available bwrap) keeps ALLOW_ALL network policy."""
    from miqi.execution.sandbox_policy import SandboxPolicyEngine
    from miqi.runtime.permission_profile import PermissionProfile

    engine = SandboxPolicyEngine(bwrap_available=True, landlock_available=False)

    profile = PermissionProfile(
        workspace=None,  # type: ignore
        network_allowed=True,
    )

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "curl example.com"}
        permission_profile = profile

    # attempt=2 escalates past BWRAP and LANDLOCK down to RESTRICTED
    sel = await engine.select(FakeCtx(), attempt=2)
    assert sel.sandbox_type == SandboxType.RESTRICTED
    assert sel.network_policy == "allow_all"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 33.3: event integrity — RESTRICTED failure
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_restricted_fail_produces_no_output_delta_events(tmp_path):
    """When RESTRICTED fails closed (e.g. cwd outside workspace), no
    ExecCommandOutputDeltaEvent should be emitted — the command never
    ran."""
    from miqi.protocol.events import ExecCommandOutputDeltaEvent

    emitter = _EventCollector()
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)
    outside_dir = str(tmp_path.parent)

    result = await tool.execute(
        "echo should-not-run",
        working_dir=outside_dir,
        _event_emitter=emitter,
        _turn_id="t-fail",
        _tool_call_id="c-fail",
        _sandbox=sel,
    )

    assert "未执行" in result
    deltas = [
        e for e in emitter.events
        if isinstance(e, ExecCommandOutputDeltaEvent)
    ]
    assert len(deltas) == 0, (
        f"Expected 0 output delta events for failed command, got {len(deltas)}"
    )


@pytest.mark.asyncio
async def test_restricted_fail_still_emits_begin_and_end_events(tmp_path):
    """When RESTRICTED fails closed, begin + end events must still be
    emitted so the frontend can see the command was attempted and
    rejected."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)
    outside_dir = str(tmp_path.parent)

    await tool.execute(
        "echo should-not-run",
        working_dir=outside_dir,
        _event_emitter=emitter,
        _turn_id="t-fail2",
        _tool_call_id="c-fail2",
        _sandbox=sel,
    )

    begin_events = [e for e in emitter.events
                    if isinstance(e, ExecCommandBeginEvent)]
    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(begin_events) == 1
    assert len(end_events) == 1
    assert begin_events[0].sandbox_type == "restricted"
    # Exit code must be non-zero on failure
    assert end_events[0].exit_code != 0


# ═══════════════════════════════════════════════════════════════════════════
# Phase 33.3: regression — NONE / BWRAP / LANDLOCK unchanged
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_none_path_unaffected_by_restricted_changes(tmp_path):
    """NONE sandbox type must still allow direct execution of any
    command (Phase 33.3 changes should not affect NONE path)."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.NONE)
    outside_dir = str(tmp_path.parent)

    result = await tool.execute(
        "python -c \"print('none-ok')\"",
        working_dir=outside_dir,  # outside workspace — NONE allows it
        _sandbox=sel,
    )

    assert "none-ok" in result


@pytest.mark.asyncio
async def test_bwrap_unavailable_falls_back_unchanged():
    """Regression: BWRAP selection with no active sandbox falls back to
    host execution with warning (Phase 31)."""
    tool = ExecTool(timeout=5, sandbox_manager=None)
    sel = _make_selection(SandboxType.BWRAP)

    result = await tool.execute(
        "echo should-not-run",
        _sandbox=sel,
    )

    assert "sandbox not available" in result
    assert "should-not-run" in result


@pytest.mark.asyncio
async def test_landlock_unsupported_fail_closed_unchanged():
    """Regression: LANDLOCK selection still fails closed (Phase 31 test)."""
    tool = ExecTool(timeout=5)
    sel = _make_selection(SandboxType.LANDLOCK)

    result = await tool.execute(
        "echo should-not-run",
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "LANDLOCK" in result


# ═══════════════════════════════════════════════════════════════════════════
# Phase 33.3-REVIEW: shell variable / tilde expansion bypass detection
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_restricted_rejects_shell_var_with_path(tmp_path):
    """$VAR/path is statically unknowable — must be rejected as unsafe."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        "cat $HOME/.ssh/id_rsa",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_restricted_rejects_braced_shell_var_with_path(tmp_path):
    """${VAR}/path is statically unknowable — must be rejected."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        "cat ${HOME}/.ssh/id_rsa",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_restricted_rejects_tilde_expansion(tmp_path):
    """~/path expands to home dir outside workspace — must be rejected."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        "cat ~/outside_file.txt",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_restricted_rejects_tilde_user_expansion(tmp_path):
    """~user/path expands to another user's home — must be rejected."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        "cat ~root/.bashrc",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_restricted_rejects_shell_var_with_traversal(tmp_path):
    """$UNKNOWN_VAR/../../../etc/passwd — shell var + traversal, must reject."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        "cat $UNKNOWN_VAR/../../../etc/passwd",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "超出" in result or "工作区" in result


# ═══════════════════════════════════════════════════════════════════════════
# Phase 33.4: LANDLOCK / fallback hardening
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_landlock_explicit_selection_still_fail_closed():
    """Phase 33.4: Even with LANDLOCK hardening in the policy engine,
    explicit SandboxSelection(SandboxType.LANDLOCK) passed to ExecTool
    must still fail closed (second line of defense)."""
    tool = ExecTool(timeout=5)
    sel = _make_selection(SandboxType.LANDLOCK)

    result = await tool.execute(
        "echo should-not-run",
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "LANDLOCK" in result
    assert "尚未实现" in result


@pytest.mark.asyncio
async def test_exec_escalation_exhausted_raises_not_none():
    """Phase 33.4: When the SandboxPolicyEngine exhausts all sandbox types
    for exec, it raises SandboxDeniedError — NEVER returns NONE."""
    from miqi.execution.sandbox_policy import (
        SandboxDeniedError,
        SandboxPolicyEngine,
    )

    engine = SandboxPolicyEngine(
        bwrap_available=True,
        allow_fallback_to_none=True,
    )

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "npm test"}

    # BWRAP→LANDLOCK→RESTRICTED = 3 items. Attempt 3 is exhausted.
    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(FakeCtx(), attempt=3)
    assert "NONE fallback is disabled for exec" in str(exc_info.value)


@pytest.mark.asyncio
async def test_allow_fallback_to_none_true_does_not_let_exec_become_none():
    """allow_fallback_to_none=True must NOT weaken exec retry semantics.
    Attempt 0 with no sandbox is the NONE base selection (direct host
    execution), but exhaustion beyond it still raises — exec never
    *falls back* to NONE."""
    from miqi.execution.sandbox_policy import (
        SandboxDeniedError,
        SandboxPolicyEngine,
    )

    engine = SandboxPolicyEngine(
        bwrap_available=False,
        landlock_available=False,
        allow_fallback_to_none=True,
    )

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "npm test"}

    # base=NONE, chain=[NONE]. attempt=0→NONE, attempt=1→exhausted
    s0 = await engine.select(FakeCtx(), attempt=0)
    assert s0.sandbox_type == SandboxType.NONE

    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(FakeCtx(), attempt=1)
    assert "NONE fallback is disabled for exec" in str(exc_info.value)


@pytest.mark.asyncio
async def test_read_only_tools_still_none_with_exec_policy_unchanged():
    """Phase 33.4: read-only tools are unaffected by exec fallback hardening.
    They still get NONE even when allow_fallback_to_none=False."""
    from miqi.execution.sandbox_policy import SandboxPolicyEngine

    engine = SandboxPolicyEngine(allow_fallback_to_none=False)

    class FakeCtx:
        tool_name = "read_file"
        arguments = {"path": "test.py"}

    sel = await engine.select(FakeCtx())
    assert sel.sandbox_type == SandboxType.NONE
    assert "sandbox not required" in sel.reason


@pytest.mark.asyncio
async def test_sandbox_policy_reason_includes_landlock_unsupported():
    """Phase 33.4: reason explains LANDLOCK configured but unsupported."""
    from miqi.execution.sandbox_policy import SandboxPolicyEngine

    engine = SandboxPolicyEngine(bwrap_available=False, landlock_available=True)

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "npm test"}

    sel = await engine.select(FakeCtx())
    assert sel.sandbox_type == SandboxType.NONE
    assert "landlock_supported=False" in sel.reason
    assert "no Landlock adapter" in sel.reason


@pytest.mark.asyncio
async def test_sandbox_policy_reason_includes_fallback_info():
    """Phase 33.4: reason for NONE exec explains why no sandbox is
    available and that execution is unrestricted."""
    from miqi.execution.sandbox_policy import SandboxPolicyEngine

    engine = SandboxPolicyEngine(bwrap_available=False, landlock_available=False)

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "npm test"}

    sel = await engine.select(FakeCtx())
    assert "bwrap unavailable" in sel.reason
    assert "direct host execution without restrictions" in sel.reason


@pytest.mark.asyncio
async def test_sandbox_policy_reason_for_bwrap_selection():
    """Phase 33.4: reason for BWRAP selection is clean (no spurious
    landlock-unsupported noise)."""
    from miqi.execution.sandbox_policy import SandboxPolicyEngine

    engine = SandboxPolicyEngine(bwrap_available=True)

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "npm test"}

    sel = await engine.select(FakeCtx())
    assert sel.sandbox_type == SandboxType.BWRAP
    # BWRAP was selected as the strongest option — no landlock noise
    assert "bwrap" in sel.reason
    # Should NOT contain landlock_unsupported noise since landlock_available=False
    assert "landlock_supported=False" not in sel.reason


@pytest.mark.asyncio
async def test_sandbox_denied_error_for_exec_is_clear():
    """Phase 33.4: SandboxDeniedError for exec gives actionable message."""
    from miqi.execution.sandbox_policy import (
        SandboxDeniedError,
        SandboxPolicyEngine,
    )

    engine = SandboxPolicyEngine(
        bwrap_available=False,
        landlock_available=False,
        allow_fallback_to_none=True,
    )

    class FakeCtx:
        tool_name = "exec"
        arguments = {"command": "npm test"}

    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(FakeCtx(), attempt=1)

    msg = str(exc_info.value)
    assert "NONE fallback is disabled for exec" in msg
    assert "bwrap_available=True" in msg
    assert "network_allowed=True" in msg


# ═══════════════════════════════════════════════════════════════════════════
# Phase 33.4: regression — Phase 33.3 tests must still pass
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_regression_restricted_enforcement_unchanged(tmp_path):
    """Phase 33.4 regression: RESTRICTED cwd enforcement still works."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        "python -c \"print('restricted-ok')\"",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "restricted-ok" in result


@pytest.mark.asyncio
async def test_regression_restricted_rejects_outside_workspace_unchanged(tmp_path):
    """Phase 33.4 regression: RESTRICTED still rejects outside-workspace paths."""
    outside = str(tmp_path) + "-outside"

    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)

    result = await tool.execute(
        f"cat {outside}/secret.txt",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "超出" in result or "工作区" in result


@pytest.mark.asyncio
async def test_regression_restricted_network_block_all_unchanged(tmp_path):
    """Phase 33.4 regression: RESTRICTED with BLOCK_ALL still fails closed."""
    from miqi.protocol.permissions import NetworkSandboxPolicy

    tool = ExecTool(timeout=5, working_dir=str(tmp_path))
    sel = _make_selection(SandboxType.RESTRICTED)
    sel.network_policy = NetworkSandboxPolicy.BLOCK_ALL  # type: ignore[assignment]

    result = await tool.execute(
        "python -c \"print('should-not-run')\"",
        working_dir=str(tmp_path),
        _sandbox=sel,
    )

    assert "未执行" in result
    assert "network" in result.lower()
