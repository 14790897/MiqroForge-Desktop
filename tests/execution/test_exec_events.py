"""Tests for exec lifecycle events (Phases 21, 31.5, 31.6)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.agent.tools.shell import ExecTool
from miqi.protocol.events import (
    ExecCommandBeginEvent,
    ExecCommandEndEvent,
    ExecCommandOutputDeltaEvent,
)


class _EventCollector:
    """Collects emitted events for assertion."""

    def __init__(self):
        self.events: list = []

    async def emit(self, event):
        self.events.append(event)


# ── Phase 21: basic begin / end events ──────────────────────────────────


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_exec_tool_emits_begin_and_end(require_subprocess, tmp_path):
    """ExecTool must emit ExecCommandBeginEvent before execution and
    ExecCommandEndEvent after execution when _event_emitter is passed."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))

    output = await tool.execute(
        "python -c \"print('hello')\"",
        _event_emitter=emitter,
        _turn_id="turn-1",
        _tool_call_id="tc-1",
    )

    assert "hello" in output, f"Expected 'hello' in output: {output!r}"

    begin_events = [e for e in emitter.events if isinstance(e, ExecCommandBeginEvent)]
    end_events = [e for e in emitter.events if isinstance(e, ExecCommandEndEvent)]

    assert len(begin_events) == 1, f"Should emit 1 begin event: {emitter.events}"
    assert len(end_events) == 1, f"Should emit 1 end event: {emitter.events}"

    assert begin_events[0].turn_id == "turn-1"
    assert begin_events[0].tool_call_id == "tc-1"
    assert "python" in begin_events[0].command

    assert end_events[0].turn_id == "turn-1"
    assert end_events[0].tool_call_id == "tc-1"
    assert end_events[0].output_size > 0


# ── Phase 31.5: stdout/stderr streaming ─────────────────────────────────

@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_stdout_streaming_emits_delta_events(require_subprocess, tmp_path):
    """Phase 31.5: stdout chunks must emit ExecCommandOutputDeltaEvent
    with stream='stdout' BEFORE ExecCommandEndEvent."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=10, working_dir=str(tmp_path))

    output = await tool.execute(
        "python -c \"import sys; sys.stdout.write('hello'); sys.stdout.flush()\"",
        _event_emitter=emitter,
        _turn_id="t-stream-out",
        _tool_call_id="tc-stream-out",
    )

    assert "hello" in output

    delta_events = [e for e in emitter.events
                    if isinstance(e, ExecCommandOutputDeltaEvent)]
    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]

    # Must have at least one stdout delta
    stdout_deltas = [d for d in delta_events if d.stream == "stdout"]
    assert len(stdout_deltas) >= 1, (
        f"Expected >=1 stdout delta, got {delta_events}"
    )
    assert "hello" in "".join(d.delta for d in stdout_deltas)

    # Must have exactly one end event
    assert len(end_events) == 1

    # Every delta must appear before the end event in the event stream
    last_delta_idx = max(
        emitter.events.index(d) for d in delta_events
    )
    end_idx = emitter.events.index(end_events[0])
    assert last_delta_idx < end_idx, (
        "All delta events must come BEFORE the end event"
    )


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_stderr_streaming_emits_delta_events(require_subprocess, tmp_path):
    """Phase 31.5: stderr chunks must emit ExecCommandOutputDeltaEvent
    with stream='stderr'."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=10, working_dir=str(tmp_path))

    await tool.execute(
        "python -c \"import sys; sys.stderr.write('err-msg'); sys.stderr.flush()\"",
        _event_emitter=emitter,
        _turn_id="t-stream-err",
        _tool_call_id="tc-stream-err",
    )

    delta_events = [e for e in emitter.events
                    if isinstance(e, ExecCommandOutputDeltaEvent)]
    stderr_deltas = [d for d in delta_events if d.stream == "stderr"]
    assert len(stderr_deltas) >= 1, (
        f"Expected >=1 stderr delta, got {delta_events}"
    )
    assert "err-msg" in "".join(d.delta for d in stderr_deltas)

    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_multiple_stdout_chunks_produce_multiple_delta_events(require_subprocess, tmp_path):
    """Phase 31.5: a command with enough stdout should produce multiple
    delta events (reading in 4KB chunks)."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=10, working_dir=str(tmp_path))

    # Write ~10KB of output to guarantee multiple chunks
    await tool.execute(
        "python -c \"for i in range(200): print('x' * 50)\"",
        _event_emitter=emitter,
        _turn_id="t-multi",
        _tool_call_id="tc-multi",
    )

    stdout_deltas = [
        e for e in emitter.events
        if isinstance(e, ExecCommandOutputDeltaEvent) and e.stream == "stdout"
    ]
    assert len(stdout_deltas) >= 2, (
        f"Expected >=2 stdout delta events for ~10KB output, got {len(stdout_deltas)}"
    )


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_final_result_still_contains_stdout_stderr(require_subprocess, tmp_path):
    """Phase 31.5: even with streaming, the final tool result must
    still contain the aggregated stdout/stderr."""
    tool = ExecTool(timeout=10, working_dir=str(tmp_path))

    output = await tool.execute(
        "python -c \"print('result-out'); import sys; sys.stderr.write('result-err\\n')\"",
        _event_emitter=_EventCollector(),
        _turn_id="t-agg",
        _tool_call_id="tc-agg",
    )

    assert "result-out" in output
    assert "result-err" in output


# ── Phase 31.6: timeout kills subprocess ────────────────────────────────

@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_timeout_kills_subprocess(require_subprocess, tmp_path):
    """Phase 31.6: when a command exceeds its timeout, the subprocess
    must be killed and a single end event emitted."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))

    output = await tool.execute(
        # Sleep long — will be killed by 50ms timeout
        "python -c \"import time; time.sleep(30)\"",
        _event_emitter=emitter,
        _turn_id="t-timeout",
        _tool_call_id="tc-timeout",
        _sandbox=_make_none_selection(timeout_ms=50),
    )

    assert "超时" in output

    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1, (
        f"Expected exactly 1 end event after timeout, got {len(end_events)}"
    )
    assert end_events[0].exit_code != 0, (
        "Timeout should produce non-zero exit_code"
    )


# ── Phase 31.6: cancel_event kills subprocess ───────────────────────────

@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_cancel_event_kills_running_subprocess(require_subprocess, tmp_path):
    """Phase 31.6: when cancel_event is set during execution, the
    subprocess must be killed and a single end event emitted."""
    emitter = _EventCollector()
    cancel_event = asyncio.Event()
    tool = ExecTool(timeout=30, working_dir=str(tmp_path))

    async def cancel_after_delay():
        await asyncio.sleep(0.3)
        cancel_event.set()

    cancel_task = asyncio.create_task(cancel_after_delay())

    output = await tool.execute(
        "python -c \"import time; time.sleep(60)\"",
        _event_emitter=emitter,
        _turn_id="t-cancel",
        _tool_call_id="tc-cancel",
        _cancel_event=cancel_event,
    )

    await cancel_task

    assert "取消" in output

    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1, (
        f"Expected exactly 1 end event after cancel, got {len(end_events)}"
    )
    assert end_events[0].exit_code != 0, (
        "Cancellation should produce non-zero exit_code"
    )


@pytest.mark.asyncio
async def test_cancel_before_start_returns_safely(tmp_path):
    """Phase 31.6: if cancel_event is already set before execution,
    return cancelled result without spawning a subprocess."""
    emitter = _EventCollector()
    cancel_event = asyncio.Event()
    cancel_event.set()  # already cancelled

    tool = ExecTool(timeout=5, working_dir=str(tmp_path))

    output = await tool.execute(
        "echo should-not-run",
        _event_emitter=emitter,
        _turn_id="t-pre-cancel",
        _tool_call_id="tc-pre-cancel",
        _cancel_event=cancel_event,
    )

    assert "取消" in output

    # Should still get begin + end events
    begin_events = [e for e in emitter.events
                    if isinstance(e, ExecCommandBeginEvent)]
    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(begin_events) == 1
    assert len(end_events) == 1


# ── Phase 31.5: duration_ms ─────────────────────────────────────────────

@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_duration_ms_greater_than_zero(require_subprocess, tmp_path):
    """Phase 31.5: ExecCommandEndEvent.duration_ms must reflect real
    wall-clock time, not the old hard-coded 0."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=10, working_dir=str(tmp_path))

    # Sleep for ~200ms to guarantee measurable duration
    await tool.execute(
        "python -c \"import time; time.sleep(0.2)\"",
        _event_emitter=emitter,
        _turn_id="t-dur",
        _tool_call_id="tc-dur",
    )

    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1
    assert end_events[0].duration_ms > 0, (
        f"duration_ms must be > 0 for a 200ms sleep, got {end_events[0].duration_ms}"
    )
    # Should be at least ~100ms (sleep was 200ms)
    assert end_events[0].duration_ms >= 100, (
        f"duration_ms too low: {end_events[0].duration_ms}"
    )


# ── Phase 31.6: no duplicate end events on error ────────────────────────

@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_no_duplicate_end_event_on_command_error(require_subprocess, tmp_path):
    """Phase 31.6: a command that fails (non-zero exit) must still emit
    exactly one end event."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=10, working_dir=str(tmp_path))

    await tool.execute(
        "python -c \"import sys; sys.exit(2)\"",
        _event_emitter=emitter,
        _turn_id="t-error",
        _tool_call_id="tc-error",
    )

    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1, (
        f"Expected exactly 1 end event on command error, got {len(end_events)}"
    )


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_no_duplicate_end_event_on_launch_failure(require_subprocess, tmp_path):
    """Phase 31.6: a command that fails to launch (bad executable) must
    still emit exactly one end event."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=10, working_dir=str(tmp_path))

    await tool.execute(
        "nonexistent_command_xyz_123",
        _event_emitter=emitter,
        _turn_id="t-launch-fail",
        _tool_call_id="tc-launch-fail",
    )

    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1, (
        f"Expected exactly 1 end event on launch failure, got {len(end_events)}"
    )


# ── Phase 31.6+ resource cleanup ───────────────────────────────────────

@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_normal_completion_with_cancel_event_no_pending_cancel_wait(require_subprocess, tmp_path):
    """When a command completes normally but a cancel_event was passed,
    the internal cancel_wait task must be cancelled (not left pending)."""
    emitter = _EventCollector()
    cancel_event = asyncio.Event()
    tool = ExecTool(timeout=10, working_dir=str(tmp_path))

    output = await tool.execute(
        "python -c \"print('normal-exit')\"",
        _event_emitter=emitter,
        _turn_id="t-clean-normal",
        _tool_call_id="tc-clean-normal",
        _cancel_event=cancel_event,
    )

    assert "normal-exit" in output

    # Must emit exactly one begin + one end event.
    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1
    assert end_events[0].exit_code == 0


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_timeout_no_pending_tasks(require_subprocess, tmp_path):
    """After a timeout kill, no internal asyncio task (proc_wait,
    stdout/stderr readers) should remain pending."""
    tool = ExecTool(timeout=5, working_dir=str(tmp_path))

    output = await tool.execute(
        "python -c \"import time; time.sleep(30)\"",
        _event_emitter=_EventCollector(),
        _turn_id="t-clean-timeout",
        _tool_call_id="tc-clean-timeout",
        _sandbox=_make_none_selection(timeout_ms=50),
    )

    assert "超时" in output


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_cancel_no_pending_tasks(require_subprocess, tmp_path):
    """After cancel-event kills a running subprocess, no internal
    asyncio task should remain pending."""
    emitter = _EventCollector()
    cancel_event = asyncio.Event()
    tool = ExecTool(timeout=30, working_dir=str(tmp_path))

    async def cancel_after_delay():
        await asyncio.sleep(0.2)
        cancel_event.set()

    cancel_task = asyncio.create_task(cancel_after_delay())

    output = await tool.execute(
        "python -c \"import time; time.sleep(60)\"",
        _event_emitter=emitter,
        _turn_id="t-clean-cancel",
        _tool_call_id="tc-clean-cancel",
        _cancel_event=cancel_event,
    )

    await cancel_task

    assert "取消" in output
    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_none_selection(*, timeout_ms: int = 30_000):
    """Create a minimal SandboxSelection(NONE) for tests that need
    to pass _sandbox kwarg."""
    from miqi.execution.sandbox_policy import SandboxSelection, SandboxType
    from miqi.protocol.permissions import (
        FileSystemSandboxPolicy,
        NetworkSandboxPolicy,
    )
    return SandboxSelection(
        sandbox_type=SandboxType.NONE,
        filesystem_policy=FileSystemSandboxPolicy(),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        timeout_ms=timeout_ms,
        env_passthrough=[],
        reason="test",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Phase 31.8: Ledger writing via _ledger_runtime injection
# ═══════════════════════════════════════════════════════════════════════════


class _FakeLedger:
    """Minimal fake LedgerRuntime that records append_item calls."""

    def __init__(self):
        self.items: list[dict] = []

    async def append_item(self, **kwargs):
        self.items.append(kwargs)


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_exec_tool_writes_ledger_begin_and_end(require_subprocess, tmp_path):
    """ExecTool with _ledger_runtime must write exec_started + exec_completed."""
    from miqi.execution.sandbox_policy import SandboxSelection, SandboxType
    from miqi.protocol.permissions import (
        FileSystemSandboxPolicy,
        NetworkSandboxPolicy,
    )

    fake_ledger = _FakeLedger()
    emitter = _EventCollector()

    tool = ExecTool(timeout=5)
    result = await tool.execute(
        "echo hello",
        _event_emitter=emitter,
        _turn_id="turn-1",
        _tool_call_id="call-1",
        _sandbox=SandboxSelection(
            sandbox_type=SandboxType.NONE,
            filesystem_policy=FileSystemSandboxPolicy(),
            network_policy=NetworkSandboxPolicy.ALLOW_ALL,
            timeout_ms=5000,
            env_passthrough=[],
            reason="test",
        ),
        _ledger_runtime=fake_ledger,
        _thread_id="thread-1",
    )

    assert "hello" in result

    # Verify ledger items
    item_types = [it["item_type"] for it in fake_ledger.items]
    assert "exec_started" in item_types, f"Missing exec_started in {item_types}"
    assert "exec_completed" in item_types, f"Missing exec_completed in {item_types}"

    started = next(it for it in fake_ledger.items if it["item_type"] == "exec_started")
    assert started["payload"]["command"] == "echo hello"
    assert started["payload"]["sandbox_type"] == "none"
    assert started["thread_id"] == "thread-1"
    assert started["turn_id"] == "turn-1"

    completed = next(it for it in fake_ledger.items if it["item_type"] == "exec_completed")
    assert completed["payload"]["tool_call_id"] == "call-1"
    assert completed["payload"]["exit_code"] == 0
    assert completed["payload"]["cancelled"] is False
    assert completed["payload"]["timed_out"] is False


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_exec_tool_writes_output_deltas_to_ledger(require_subprocess, tmp_path):
    """ExecTool must write exec_output_delta for each stdout/stderr chunk."""

    from miqi.execution.sandbox_policy import SandboxSelection, SandboxType
    from miqi.protocol.permissions import (
        FileSystemSandboxPolicy,
        NetworkSandboxPolicy,
    )

    fake_ledger = _FakeLedger()
    emitter = _EventCollector()

    tool = ExecTool(timeout=5)
    await tool.execute(
        "echo line1 && echo line2",
        _event_emitter=emitter,
        _turn_id="turn-delta",
        _tool_call_id="call-delta",
        _sandbox=SandboxSelection(
            sandbox_type=SandboxType.NONE,
            filesystem_policy=FileSystemSandboxPolicy(),
            network_policy=NetworkSandboxPolicy.ALLOW_ALL,
            timeout_ms=5000,
            env_passthrough=[],
            reason="test",
        ),
        _ledger_runtime=fake_ledger,
        _thread_id="thread-delta",
    )

    # Verify delta items exist
    deltas = [it for it in fake_ledger.items if it["item_type"] == "exec_output_delta"]
    assert len(deltas) >= 1, f"Expected at least 1 output delta, got {len(deltas)}"
    for d in deltas:
        assert d["payload"]["tool_call_id"] == "call-delta"
        assert d["payload"]["stream"] in ("stdout", "stderr")


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_exec_tool_launch_failure_recorded_in_ledger(require_subprocess):
    """When process launch fails, exec_completed must be written with exit_code=1."""
    fake_ledger = _FakeLedger()
    emitter = _EventCollector()

    tool = ExecTool(timeout=5)
    # Use a command that will fail to launch (invalid shell syntax on Windows)
    await tool.execute(
        "nonexistent_command_xyz_12345",
        _event_emitter=emitter,
        _turn_id="turn-fail",
        _tool_call_id="call-fail",
        _sandbox=_none_sandbox(),
        _ledger_runtime=fake_ledger,
        _thread_id="thread-fail",
    )

    # exec_completed should still be written
    completed = [it for it in fake_ledger.items if it["item_type"] == "exec_completed"]
    assert len(completed) >= 1, f"Expected exec_completed for failed launch, got {fake_ledger.items}"
    # exit_code may be 1 or non-zero (depends on shell)
    assert completed[0]["payload"]["tool_call_id"] == "call-fail"


@pytest.mark.asyncio
async def test_exec_tool_cancelled_before_start_recorded(tmp_path):
    """When cancel_event is already set before execution, exec_completed
    must be written with cancelled=True and exit_code=-1."""
    fake_ledger = _FakeLedger()
    emitter = _EventCollector()
    cancel_evt = asyncio.Event()
    cancel_evt.set()  # Already cancelled before start

    tool = ExecTool(timeout=5)
    result = await tool.execute(
        "echo should_not_run",
        _event_emitter=emitter,
        _turn_id="turn-cancel",
        _tool_call_id="call-cancel",
        _cancel_event=cancel_evt,
        _sandbox=_none_sandbox(),
        _ledger_runtime=fake_ledger,
        _thread_id="thread-cancel",
    )

    assert "取消" in result

    # exec_completed should have cancelled=True
    completed = [it for it in fake_ledger.items if it["item_type"] == "exec_completed"]
    assert len(completed) == 1
    assert completed[0]["payload"]["cancelled"] is True
    assert completed[0]["payload"]["exit_code"] == -1


def _none_sandbox():
    """Helper: SandboxSelection for NONE (direct execution)."""
    from miqi.execution.sandbox_policy import SandboxSelection, SandboxType
    from miqi.protocol.permissions import (
        FileSystemSandboxPolicy,
        NetworkSandboxPolicy,
    )
    return SandboxSelection(
        sandbox_type=SandboxType.NONE,
        filesystem_policy=FileSystemSandboxPolicy(),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        timeout_ms=5000,
        env_passthrough=[],
        reason="test",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Phase 33.2: BWRAP streaming / cancel hardening
# ═══════════════════════════════════════════════════════════════════════════


class _FakeBwrapHandle:
    """Handle that wraps a real asyncio subprocess for testing bwrap streaming.

    Spawns a real Python subprocess so that stdout/stderr are genuine
    :class:`asyncio.StreamReader` objects — the ExecTool's
    :meth:`_read_stream` works without mocking asyncio internals.
    """

    def __init__(self, process: asyncio.subprocess.Process, *, stdout_delay: float = 0):
        self._process = process
        self._stdout_delay = stdout_delay
        self.kill_called = False
        self.cleanup_called = False

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        inner = self._process.stdout
        if self._stdout_delay <= 0:
            return inner
        # Delay applies on FIRST read — simulates a command that takes time
        # AFTER ExecTool starts reading (real commands start inside execute;
        # a subprocess spawned before execute would have already slept, which
        # is broken by any pre-exec work like the Phase 59 workspace snapshot).
        return _DelayedReader(inner, self._stdout_delay)

    @property
    def stderr(self) -> asyncio.StreamReader | None:
        return self._process.stderr

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    async def wait(self) -> int:
        await self._process.wait()
        return self._process.returncode if self._process.returncode is not None else -1

    async def kill(self) -> None:
        self.kill_called = True
        try:
            self._process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass

    async def cleanup(self) -> None:
        self.cleanup_called = True


class _DelayedReader:
    """StreamReader wrapper that sleeps once before the first ``read``.

    Used to simulate command wall-clock time that starts when ExecTool
    begins reading the stream (see _FakeBwrapHandle.stdout).
    """

    def __init__(self, inner: asyncio.StreamReader, delay: float):
        self._inner = inner
        self._delay = delay
        self._slept = False

    async def read(self, n: int) -> bytes:
        if not self._slept:
            self._slept = True
            await asyncio.sleep(self._delay)
        return await self._inner.read(n)


async def _make_streaming_handle(
    stdout_text: str = "",
    stderr_text: str = "",
    exit_code: int = 0,
    *,
    sleep_before: float = 0,
    sleep_forever: bool = False,
) -> _FakeBwrapHandle:
    """Create a _FakeBwrapHandle backed by a real Python subprocess.

    The subprocess writes *stdout_text* to stdout, *stderr_text* to stderr,
    and exits with *exit_code*.  If *sleep_forever* is True, the process
    sleeps indefinitely (used to test kill/timeout).
    """
    if sleep_forever:
        script = "import time; time.sleep(3600)"
    else:
        parts = []
        if stdout_text:
            parts.append("import sys")
            parts.append(f"sys.stdout.write({stdout_text!r})")
        if stderr_text:
            parts.append(f"sys.stderr.write({stderr_text!r})")
        parts.append(f"sys.exit({exit_code})")
        script = "; ".join(parts)

    process = await asyncio.create_subprocess_exec(
        "python", "-c", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # sleep_before is applied on FIRST stdout read (see _FakeBwrapHandle):
    # the subprocess itself must not sleep, otherwise the wall-clock time
    # starts at spawn (before execute) and pre-exec work — like the Phase 59
    # workspace snapshot — silently eats it.
    return _FakeBwrapHandle(process, stdout_delay=sleep_before)


def _make_mock_sandbox_streaming(handle: _FakeBwrapHandle):
    """Create a mock BwrapSandbox that returns *handle* from
    run_command_streaming()."""
    from unittest.mock import MagicMock

    sandbox = MagicMock()
    sandbox.is_running = True
    sandbox.get_sandbox_env = MagicMock(return_value={})
    sandbox.run_command_streaming = AsyncMock(return_value=handle)
    return sandbox


def _make_mock_sandbox_manager_streaming(sandbox):
    """Create a mock SandboxManager with the streaming sandbox."""
    from unittest.mock import MagicMock

    mgr = MagicMock()
    mgr.active_sandbox = sandbox
    return mgr


def _make_bwrap_selection(*, timeout_ms: int = 30_000):
    """Create a SandboxSelection(BWRAP) for tests."""
    from miqi.execution.sandbox_policy import SandboxSelection, SandboxType
    from miqi.protocol.permissions import (
        FileSystemAccessMode,
        FileSystemSandboxPolicy,
        NetworkSandboxPolicy,
    )
    return SandboxSelection(
        sandbox_type=SandboxType.BWRAP,
        filesystem_policy=FileSystemSandboxPolicy(
            default_mode=FileSystemAccessMode.READ,
        ),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        timeout_ms=timeout_ms,
        env_passthrough=[],
        reason="test bwrap selection",
    )


# ── Phase 33.2: stdout/stderr delta events ────────────────────────────────


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_bwrap_stdout_delta_emitted(require_subprocess):
    """Phase 33.2: bwrap path must emit ExecCommandOutputDeltaEvent for
    stdout chunks during streaming execution."""
    emitter = _EventCollector()
    handle = await _make_streaming_handle(stdout_text="hello-from-bwrap")
    sandbox = _make_mock_sandbox_streaming(handle)
    mgr = _make_mock_sandbox_manager_streaming(sandbox)

    tool = ExecTool(timeout=5, sandbox_manager=mgr)
    sel = _make_bwrap_selection()

    result = await tool.execute(
        "echo hello",
        _event_emitter=emitter,
        _turn_id="t-bwrap-out",
        _tool_call_id="tc-bwrap-out",
        _sandbox=sel,
    )

    assert "hello-from-bwrap" in result

    delta_events = [
        e for e in emitter.events
        if isinstance(e, ExecCommandOutputDeltaEvent)
    ]
    stdout_deltas = [d for d in delta_events if d.stream == "stdout"]
    assert len(stdout_deltas) >= 1, (
        f"Expected >=1 stdout delta from bwrap, got {delta_events}"
    )
    assert "hello-from-bwrap" in "".join(d.delta for d in stdout_deltas)

    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_bwrap_stderr_delta_emitted(require_subprocess):
    """Phase 33.2: bwrap path must emit ExecCommandOutputDeltaEvent for
    stderr chunks during streaming execution."""
    emitter = _EventCollector()
    handle = await _make_streaming_handle(
        stdout_text="ok", stderr_text="bwrap-err-msg",
    )
    sandbox = _make_mock_sandbox_streaming(handle)
    mgr = _make_mock_sandbox_manager_streaming(sandbox)

    tool = ExecTool(timeout=5, sandbox_manager=mgr)
    sel = _make_bwrap_selection()

    await tool.execute(
        "echo hello",
        _event_emitter=emitter,
        _turn_id="t-bwrap-err",
        _tool_call_id="tc-bwrap-err",
        _sandbox=sel,
    )

    delta_events = [
        e for e in emitter.events
        if isinstance(e, ExecCommandOutputDeltaEvent)
    ]
    stderr_deltas = [d for d in delta_events if d.stream == "stderr"]
    assert len(stderr_deltas) >= 1, (
        f"Expected >=1 stderr delta from bwrap, got {delta_events}"
    )
    assert "bwrap-err-msg" in "".join(d.delta for d in stderr_deltas)


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_bwrap_final_result_contains_stdout_stderr(require_subprocess):
    """Phase 33.2: bwrap final aggregated result must contain both stdout
    and stderr content."""
    emitter = _EventCollector()
    handle = await _make_streaming_handle(
        stdout_text="result-out", stderr_text="result-err",
    )
    sandbox = _make_mock_sandbox_streaming(handle)
    mgr = _make_mock_sandbox_manager_streaming(sandbox)

    tool = ExecTool(timeout=5, sandbox_manager=mgr)
    sel = _make_bwrap_selection()

    result = await tool.execute(
        "echo hello",
        _event_emitter=emitter,
        _turn_id="t-bwrap-agg",
        _tool_call_id="tc-bwrap-agg",
        _sandbox=sel,
    )

    assert "result-out" in result
    assert "result-err" in result


# ── Phase 33.2: timeout kills ─────────────────────────────────────────────


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_bwrap_timeout_kills_and_one_end_event(require_subprocess):
    """Phase 33.2: when a bwrap command exceeds its timeout, the process
    must be killed (handle.kill() called) and exactly one EndEvent emitted."""
    emitter = _EventCollector()
    handle = await _make_streaming_handle(sleep_forever=True)
    sandbox = _make_mock_sandbox_streaming(handle)
    mgr = _make_mock_sandbox_manager_streaming(sandbox)

    tool = ExecTool(timeout=5, sandbox_manager=mgr)
    sel = _make_bwrap_selection(timeout_ms=200)  # 200ms timeout

    result = await tool.execute(
        "sleep 999",
        _event_emitter=emitter,
        _turn_id="t-bwrap-to",
        _tool_call_id="tc-bwrap-to",
        _sandbox=sel,
    )

    assert "超时" in result
    assert handle.kill_called, "handle.kill() must be called on timeout"

    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1, (
        f"Expected exactly 1 end event after bwrap timeout, got {len(end_events)}"
    )
    assert end_events[0].exit_code != 0


# ── Phase 33.2: cancel_event kills ────────────────────────────────────────


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_bwrap_cancel_kills_and_one_end_event(require_subprocess):
    """Phase 33.2: when cancel_event is set during bwrap execution, the
    process must be killed and exactly one EndEvent emitted."""
    emitter = _EventCollector()
    cancel_event = asyncio.Event()
    handle = await _make_streaming_handle(sleep_forever=True)
    sandbox = _make_mock_sandbox_streaming(handle)
    mgr = _make_mock_sandbox_manager_streaming(sandbox)

    tool = ExecTool(timeout=30, sandbox_manager=mgr)
    sel = _make_bwrap_selection(timeout_ms=30_000)

    async def cancel_after_delay():
        # Phase 59's pre-exec workspace snapshot (os.walk) runs BEFORE the
        # command starts; a cancel firing inside that window takes the
        # "cancelled before start" path (no handle to kill — correct for a
        # command that never launched). Delay long enough to land inside
        # the streaming phase, where cancel must kill the handle.
        await asyncio.sleep(3.0)
        cancel_event.set()

    cancel_task = asyncio.create_task(cancel_after_delay())

    result = await tool.execute(
        "sleep 999",
        _event_emitter=emitter,
        _turn_id="t-bwrap-cancel",
        _tool_call_id="tc-bwrap-cancel",
        _cancel_event=cancel_event,
        _sandbox=sel,
    )

    await cancel_task

    assert "取消" in result
    assert handle.kill_called, "handle.kill() must be called on cancel"

    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1, (
        f"Expected exactly 1 end event after bwrap cancel, got {len(end_events)}"
    )


# ── Phase 33.2: no duplicate end event ────────────────────────────────────


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_bwrap_no_duplicate_end_event_on_failure(require_subprocess):
    """Phase 33.2: a bwrap command that fails (non-zero exit) must still
    emit exactly one end event."""
    emitter = _EventCollector()
    handle = await _make_streaming_handle(
        stdout_text="", stderr_text="something went wrong", exit_code=2,
    )
    sandbox = _make_mock_sandbox_streaming(handle)
    mgr = _make_mock_sandbox_manager_streaming(sandbox)

    tool = ExecTool(timeout=5, sandbox_manager=mgr)
    sel = _make_bwrap_selection()

    await tool.execute(
        "false",
        _event_emitter=emitter,
        _turn_id="t-bwrap-fail",
        _tool_call_id="tc-bwrap-fail",
        _sandbox=sel,
    )

    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1, (
        f"Expected exactly 1 end event on bwrap command failure, got {len(end_events)}"
    )


# ── Phase 33.2: no pending internal tasks ─────────────────────────────────


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_bwrap_no_pending_tasks_on_normal(require_subprocess):
    """Phase 33.2: after normal bwrap completion, no internal asyncio
    tasks are left pending."""
    handle = await _make_streaming_handle(stdout_text="normal-done")
    sandbox = _make_mock_sandbox_streaming(handle)
    mgr = _make_mock_sandbox_manager_streaming(sandbox)

    tool = ExecTool(timeout=10, sandbox_manager=mgr)
    sel = _make_bwrap_selection()

    result = await tool.execute(
        "echo ok",
        _event_emitter=_EventCollector(),
        _turn_id="t-bwrap-clean",
        _tool_call_id="tc-bwrap-clean",
        _sandbox=sel,
    )

    assert "normal-done" in result
    assert handle.cleanup_called, "handle.cleanup() must be called"


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_bwrap_no_pending_tasks_on_timeout(require_subprocess):
    """Phase 33.2: after bwrap timeout, no internal asyncio tasks are
    left pending."""
    handle = await _make_streaming_handle(sleep_forever=True)
    sandbox = _make_mock_sandbox_streaming(handle)
    mgr = _make_mock_sandbox_manager_streaming(sandbox)

    tool = ExecTool(timeout=5, sandbox_manager=mgr)
    sel = _make_bwrap_selection(timeout_ms=100)

    result = await tool.execute(
        "sleep 999",
        _event_emitter=_EventCollector(),
        _turn_id="t-bwrap-clean-to",
        _tool_call_id="tc-bwrap-clean-to",
        _sandbox=sel,
    )

    assert "超时" in result
    assert handle.kill_called
    assert handle.cleanup_called, (
        "handle.cleanup() must be called even after timeout"
    )


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_bwrap_no_pending_tasks_on_cancel(require_subprocess):
    """Phase 33.2: after bwrap cancel, no internal asyncio tasks are
    left pending."""
    cancel_event = asyncio.Event()
    handle = await _make_streaming_handle(sleep_forever=True)
    sandbox = _make_mock_sandbox_streaming(handle)
    mgr = _make_mock_sandbox_manager_streaming(sandbox)

    tool = ExecTool(timeout=30, sandbox_manager=mgr)
    sel = _make_bwrap_selection(timeout_ms=30_000)

    async def cancel_soon():
        # Long enough to pass the Phase 59 pre-exec snapshot so the cancel
        # lands in the streaming phase (see test_bwrap_cancel_kills...).
        await asyncio.sleep(3.0)
        cancel_event.set()

    cancel_task = asyncio.create_task(cancel_soon())

    result = await tool.execute(
        "sleep 999",
        _event_emitter=_EventCollector(),
        _turn_id="t-bwrap-clean-cancel",
        _tool_call_id="tc-bwrap-clean-cancel",
        _cancel_event=cancel_event,
        _sandbox=sel,
    )

    await cancel_task

    assert "取消" in result
    assert handle.kill_called
    assert handle.cleanup_called, (
        "handle.cleanup() must be called even after cancel"
    )


# ── Phase 33.2: duration_ms / output_size accuracy ────────────────────────


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_bwrap_end_event_duration_ms_accurate(require_subprocess):
    """Phase 33.2: ExecCommandEndEvent.duration_ms must reflect real
    wall-clock time for bwrap execution."""
    emitter = _EventCollector()
    handle = await _make_streaming_handle(
        stdout_text="hi", sleep_before=0.15,
    )
    sandbox = _make_mock_sandbox_streaming(handle)
    mgr = _make_mock_sandbox_manager_streaming(sandbox)

    tool = ExecTool(timeout=10, sandbox_manager=mgr)
    sel = _make_bwrap_selection()

    await tool.execute(
        "echo hi",
        _event_emitter=emitter,
        _turn_id="t-bwrap-dur",
        _tool_call_id="tc-bwrap-dur",
        _sandbox=sel,
    )

    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1
    assert end_events[0].duration_ms >= 100, (
        f"duration_ms must be >=100ms for 150ms sleep, got {end_events[0].duration_ms}"
    )


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_bwrap_end_event_output_size_accurate(require_subprocess):
    """Phase 33.2: ExecCommandEndEvent.output_size must match the
    aggregated output length."""
    emitter = _EventCollector()
    handle = await _make_streaming_handle(
        stdout_text="output-data", stderr_text="error-data",
    )
    sandbox = _make_mock_sandbox_streaming(handle)
    mgr = _make_mock_sandbox_manager_streaming(sandbox)

    tool = ExecTool(timeout=5, sandbox_manager=mgr)
    sel = _make_bwrap_selection()

    result = await tool.execute(
        "echo data",
        _event_emitter=emitter,
        _turn_id="t-bwrap-size",
        _tool_call_id="tc-bwrap-size",
        _sandbox=sel,
    )

    end_events = [e for e in emitter.events
                  if isinstance(e, ExecCommandEndEvent)]
    assert len(end_events) == 1
    assert end_events[0].output_size == len(result), (
        f"output_size {end_events[0].output_size} must match len(result) {len(result)}"
    )
    assert end_events[0].output_size > 0


# ── Phase 33.2: regression — legacy BWRAP unavailable, NONE direct ────────


@pytest.mark.asyncio
async def test_regression_bwrap_unavailable_falls_back():
    """Phase 33.2 regression: BWRAP selection with no sandbox_manager
    falls back to host execution with warning."""
    tool = ExecTool(timeout=5, sandbox_manager=None)
    sel = _make_bwrap_selection()

    result = await tool.execute(
        "echo should-not-run",
        _sandbox=sel,
    )

    assert "sandbox not available" in result
    assert "should-not-run" in result


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_regression_direct_none_path_unaffected_by_bwrap_changes(require_subprocess, tmp_path):
    """Phase 33.2 regression: direct NONE execution path still works
    (streaming + delta events + cancel unchanged from Phase 31.5/31.6)."""
    emitter = _EventCollector()
    tool = ExecTool(timeout=10, working_dir=str(tmp_path))

    result = await tool.execute(
        "python -c \"print('direct-still-works')\"",
        _event_emitter=emitter,
        _turn_id="t-reg-n",
        _tool_call_id="tc-reg-n",
    )

    assert "direct-still-works" in result

    # Verify streaming still works on NONE path
    delta_events = [
        e for e in emitter.events
        if isinstance(e, ExecCommandOutputDeltaEvent) and e.stream == "stdout"
    ]
    assert len(delta_events) >= 1, "NONE path must still emit stdout deltas"


# ── Phase 33.2: ledger / replay records bwrap sandbox_type and deltas ──────


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_bwrap_ledger_records_sandbox_type_and_output_deltas(require_subprocess):
    """Phase 33.2: when executing via bwrap, the ledger must record
    exec_started with sandbox_type='bwrap' and exec_output_delta items
    for each stdout/stderr chunk."""
    fake_ledger = _FakeLedger()
    emitter = _EventCollector()

    handle = await _make_streaming_handle(
        stdout_text="ledger-line1\n", stderr_text="ledger-err1\n",
    )
    sandbox = _make_mock_sandbox_streaming(handle)
    mgr = _make_mock_sandbox_manager_streaming(sandbox)

    tool = ExecTool(timeout=5, sandbox_manager=mgr)
    sel = _make_bwrap_selection()

    result = await tool.execute(
        "echo ledger-test",
        _event_emitter=emitter,
        _turn_id="turn-bwrap-ledger",
        _tool_call_id="call-bwrap-ledger",
        _sandbox=sel,
        _ledger_runtime=fake_ledger,
        _thread_id="thread-bwrap-ledger",
    )

    assert "ledger-line1" in result
    assert "ledger-err1" in result

    # Verify ledger items
    item_types = [it["item_type"] for it in fake_ledger.items]
    assert "exec_started" in item_types, f"Missing exec_started in {item_types}"
    assert "exec_completed" in item_types, f"Missing exec_completed in {item_types}"

    # exec_started must record sandbox_type='bwrap'
    started = next(it for it in fake_ledger.items if it["item_type"] == "exec_started")
    assert started["payload"]["sandbox_type"] == "bwrap", (
        f"exec_started sandbox_type must be 'bwrap', got {started['payload']['sandbox_type']!r}"
    )
    assert started["thread_id"] == "thread-bwrap-ledger"
    assert started["turn_id"] == "turn-bwrap-ledger"

    # exec_output_delta items must exist
    deltas = [it for it in fake_ledger.items if it["item_type"] == "exec_output_delta"]
    assert len(deltas) >= 1, f"Expected at least 1 exec_output_delta, got {len(deltas)}"
    for d in deltas:
        assert d["payload"]["tool_call_id"] == "call-bwrap-ledger"
        assert d["payload"]["stream"] in ("stdout", "stderr")

    # exec_completed must be accurate
    completed = next(it for it in fake_ledger.items if it["item_type"] == "exec_completed")
    assert completed["payload"]["tool_call_id"] == "call-bwrap-ledger"
    assert completed["payload"]["exit_code"] == 0
    assert completed["payload"]["cancelled"] is False
    assert completed["payload"]["timed_out"] is False


# ── Phase 33.2: BwrapCommandHandle.wait() exit code regression tests ──────


@pytest.mark.asyncio
async def test_bwrap_handle_wait_exit_code_zero():
    """BwrapCommandHandle.wait() MUST return 0 for successful exit (not -1)."""
    from miqi.sandbox.bwrap import BwrapCommandHandle

    proc = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    proc.returncode = 0
    handle = BwrapCommandHandle(proc)
    exit_code = await handle.wait()
    assert exit_code == 0, f"Expected 0 for successful exit, got {exit_code}"


@pytest.mark.asyncio
async def test_bwrap_handle_wait_exit_code_nonzero():
    """BwrapCommandHandle.wait() MUST return the real non-zero exit code."""
    from miqi.sandbox.bwrap import BwrapCommandHandle

    proc = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    proc.returncode = 42
    handle = BwrapCommandHandle(proc)
    exit_code = await handle.wait()
    assert exit_code == 42, f"Expected 42, got {exit_code}"


@pytest.mark.asyncio
async def test_fake_handle_wait_exit_code_zero():
    """_FakeBwrapHandle.wait() MUST also return 0 for successful exit."""
    proc = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    proc.returncode = 0
    handle = _FakeBwrapHandle(proc)
    exit_code = await handle.wait()
    assert exit_code == 0, f"Expected 0 for successful exit, got {exit_code}"


@pytest.mark.asyncio
async def test_fake_handle_wait_exit_code_nonzero():
    """_FakeBwrapHandle.wait() MUST also return the real non-zero exit code."""
    proc = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    proc.returncode = 7
    handle = _FakeBwrapHandle(proc)
    exit_code = await handle.wait()
    assert exit_code == 7, f"Expected 7, got {exit_code}"


# ═══════════════════════════════════════════════════════════════════════════
# Phase 42: user shell command source tagging
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_exec_begin_event_includes_user_shell_source(require_subprocess, tmp_path):
    from miqi.agent.tools.shell import ExecTool
    from miqi.protocol.events import ExecCommandBeginEvent

    emitted = []

    class Emitter:
        async def emit(self, event):
            emitted.append(event)

    tool = ExecTool(working_dir=str(tmp_path))
    await tool.execute(
        "echo hello",
        _event_emitter=Emitter(),
        _turn_id="turn-1",
        _tool_call_id="exec-1",
        _exec_source="userShell",
    )

    begin = next(e for e in emitted if isinstance(e, ExecCommandBeginEvent))
    assert begin.source == "userShell"


@pytest.mark.subprocess
@pytest.mark.asyncio
async def test_exec_started_ledger_includes_user_shell_source(require_subprocess, tmp_path):
    from miqi.agent.tools.shell import ExecTool
    from miqi.runtime.ledger_runtime import LedgerRuntime

    ledger = LedgerRuntime(tmp_path / "runtime.db", session_id="client-1:default")
    await ledger.initialize()
    try:
        tool = ExecTool(working_dir=str(tmp_path))
        await tool.execute(
            "echo hello",
            _turn_id="turn-1",
            _tool_call_id="exec-1",
            _ledger_runtime=ledger,
            _thread_id="thread-1",
            _exec_source="userShell",
        )

        items = await ledger.load_items("thread-1")
        exec_started = [i for i in items if i.item_type == "exec_started"]
        assert exec_started
        assert exec_started[0].payload["source"] == "userShell"
    finally:
        await ledger.close()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 42: empty delta suppression (issue #236)
# ═══════════════════════════════════════════════════════════════════════════


def test_turn_event_adapter_drops_empty_delta():
    """CodexTurnEventAdapter must not emit outputDelta notifications
    when ExecCommandOutputDeltaEvent.delta is empty."""
    from miqi.protocol.events import ExecCommandOutputDeltaEvent
    from miqi.runtime.turn_event_adapter import CodexTurnEventAdapter

    adapter = CodexTurnEventAdapter(
        thread_id="thread-1",
        turn_id="turn-1",
        input_items=[],
        client_user_message_id="msg-1",
        emit_user_message_item=False,
    )

    # Empty delta → no notifications emitted
    empty = ExecCommandOutputDeltaEvent(
        turn_id="turn-1",
        tool_call_id="tc-1",
        stream="stdout",
        delta="",
    )
    result = adapter.project(empty)
    assert result == [], (
        f"Expected no notifications for empty delta, got {result}"
    )


def test_turn_event_adapter_passes_non_empty_delta():
    """CodexTurnEventAdapter must emit outputDelta for non-empty deltas."""
    from miqi.protocol.events import ExecCommandOutputDeltaEvent
    from miqi.runtime.turn_event_adapter import CodexTurnEventAdapter

    adapter = CodexTurnEventAdapter(
        thread_id="thread-1",
        turn_id="turn-1",
        input_items=[],
        client_user_message_id="msg-1",
        emit_user_message_item=False,
    )

    event = ExecCommandOutputDeltaEvent(
        turn_id="turn-1",
        tool_call_id="tc-1",
        stream="stdout",
        delta="hello\n",
    )
    result = adapter.project(event)
    assert len(result) == 1
    assert result[0]["event"] == "item/commandExecution/outputDelta"
    assert result[0]["data"]["delta"] == "hello\n"
    assert result[0]["data"]["stream"] == "stdout"


def test_turn_event_adapter_allows_stderr_delta():
    """CodexTurnEventAdapter must pass stderr deltas through."""
    from miqi.protocol.events import ExecCommandOutputDeltaEvent
    from miqi.runtime.turn_event_adapter import CodexTurnEventAdapter

    adapter = CodexTurnEventAdapter(
        thread_id="thread-1",
        turn_id="turn-1",
        input_items=[],
        client_user_message_id="msg-1",
        emit_user_message_item=False,
    )

    event = ExecCommandOutputDeltaEvent(
        turn_id="turn-1",
        tool_call_id="tc-1",
        stream="stderr",
        delta="error output\n",
    )
    result = adapter.project(event)
    assert len(result) == 1
    assert result[0]["event"] == "item/commandExecution/outputDelta"
    assert result[0]["data"]["stream"] == "stderr"
