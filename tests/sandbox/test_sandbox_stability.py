"""Unit tests for the #472 P0 sandbox stability fixes.

Covers the two "止血" changes (no real WSL/bwrap needed — all subprocess
calls are mocked):

1. ``BwrapSandbox.stop()`` really kills streaming handles before cleanup
   (previously only cleanup() ran, leaving wsl.exe -> bash -> bwrap chains
   alive — the source of orphan WSL processes).
2. State-file self-healing:
   - ``_load_state`` distinguishes missing / corrupt / valid files
   - corrupt state falls back to a directory orphan scan
   - a failed stale-dir cleanup keeps the state file (retried next start)
3. ``_rm_rf_retry`` retries with backoff and verifies the dir is actually gone.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from miqi.sandbox.bwrap import BwrapSandbox
from miqi.sandbox.manager import SandboxManager

# ── Helpers ──────────────────────────────────────────────────────────────

def _make_manager(tmp_path: Path) -> SandboxManager:
    return SandboxManager(
        workspace=tmp_path,
        sandbox_base_dir=tmp_path / "sandboxes",
        wsl_base_dir="/tmp/miqi-sandboxes",
    )


def _make_sandbox() -> BwrapSandbox:
    """Instance without __init__ side effects (no real WSL interaction)."""
    sb = BwrapSandbox.__new__(BwrapSandbox)
    sb._running = True
    sb._streaming_handles = []
    sb._linux_base_dir = "/tmp/miqi-sandboxes/test_key"
    sb._detected_distro = "TestDistro"
    sb.session_key = "test_key"
    sb._log_workspace = None
    return sb


def _fake_proc(returncode: int, out: bytes = b"") -> AsyncMock:
    p = AsyncMock()
    p.returncode = returncode
    p.communicate = AsyncMock(return_value=(out, b""))
    return p


# ── stop(): kill streaming handles before cleanup ───────────────────────

@pytest.mark.asyncio
async def test_stop_kills_streaming_handles_before_cleanup(monkeypatch):
    sb = _make_sandbox()
    handle = MagicMock()
    handle.kill = AsyncMock()
    handle.cleanup = AsyncMock()
    sb._streaming_handles = [handle]
    monkeypatch.setattr(BwrapSandbox, "_rm_rf_retry", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "miqi.sandbox.bwrap.append_workspace_log", lambda *a, **k: None
    )

    await sb.stop()

    handle.kill.assert_awaited_once()
    handle.cleanup.assert_awaited_once()
    # kill must happen before cleanup (kill releases the process; cleanup
    # removes the temp script file).
    assert handle.method_calls[0] == call.kill()
    assert handle.method_calls[1] == call.cleanup()
    assert sb._streaming_handles == []


@pytest.mark.asyncio
async def test_stop_continues_cleanup_when_kill_raises(monkeypatch):
    sb = _make_sandbox()
    handle = MagicMock()
    handle.kill = AsyncMock(side_effect=RuntimeError("boom"))
    handle.cleanup = AsyncMock()
    sb._streaming_handles = [handle]
    monkeypatch.setattr(BwrapSandbox, "_rm_rf_retry", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "miqi.sandbox.bwrap.append_workspace_log", lambda *a, **k: None
    )

    await sb.stop()  # must not raise

    handle.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_reports_rm_failure(monkeypatch):
    sb = _make_sandbox()
    monkeypatch.setattr(BwrapSandbox, "_rm_rf_retry", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "miqi.sandbox.bwrap.append_workspace_log", lambda *a, **k: None
    )

    await sb.stop()  # must not raise; failure is logged, not silent


# ── _load_state: missing / corrupt / valid ───────────────────────────────

@pytest.mark.asyncio
async def test_load_state_missing_file(tmp_path):
    m = _make_manager(tmp_path)
    state, damaged = m._load_state()
    assert state is None
    assert damaged is False


@pytest.mark.asyncio
async def test_load_state_corrupt_file(tmp_path):
    m = _make_manager(tmp_path)
    m._state_file.parent.mkdir(parents=True, exist_ok=True)
    m._state_file.write_text("{ not valid json !!!", encoding="utf-8")
    state, damaged = m._load_state()
    assert state is None
    assert damaged is True


@pytest.mark.asyncio
async def test_load_state_valid_file(tmp_path):
    m = _make_manager(tmp_path)
    m._state_file.parent.mkdir(parents=True, exist_ok=True)
    m._state_file.write_text(json.dumps({"sandboxes": []}), encoding="utf-8")
    state, damaged = m._load_state()
    assert damaged is False
    assert state == {"sandboxes": []}


@pytest.mark.asyncio
async def test_load_state_invalid_schema_treated_as_damaged(tmp_path):
    """A syntactically valid but schema-invalid file (e.g. `[]`) must be
    treated as damaged so orphan recovery runs instead of crashing on
    state.get(...) (CodeRabbit)."""
    m = _make_manager(tmp_path)
    m._state_file.parent.mkdir(parents=True, exist_ok=True)
    m._state_file.write_text("[]", encoding="utf-8")
    state, damaged = m._load_state()
    assert state is None
    assert damaged is True

    # malformed entry (missing linux_base_dir) also counts as damaged
    m._state_file.write_text(
        json.dumps({"sandboxes": [{"session_key": "x"}]}), encoding="utf-8"
    )
    state, damaged = m._load_state()
    assert state is None
    assert damaged is True


# ── cleanup_stale: self-healing ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_cleanup_stale_corrupt_state_uses_orphan_scan(tmp_path, monkeypatch):
    m = _make_manager(tmp_path)
    m._state_file.parent.mkdir(parents=True, exist_ok=True)
    m._state_file.write_text("{corrupt", encoding="utf-8")

    scan = AsyncMock(return_value=(2, True))
    monkeypatch.setattr(m, "_cleanup_orphan_scan", scan)

    cleaned = await m.cleanup_stale()

    scan.assert_awaited_once()
    assert cleaned == 2
    assert not m._state_file.exists()  # corrupt file dropped after complete scan


@pytest.mark.asyncio
async def test_cleanup_stale_keeps_corrupt_state_when_scan_incomplete(tmp_path, monkeypatch):
    """A damaged state file survives an incomplete orphan scan so the next
    startup retries instead of forgetting the orphans (#472 / CodeRabbit)."""
    m = _make_manager(tmp_path)
    m._state_file.parent.mkdir(parents=True, exist_ok=True)
    m._state_file.write_text("{corrupt", encoding="utf-8")

    scan = AsyncMock(return_value=(1, False))  # cleaned 1, but incomplete
    monkeypatch.setattr(m, "_cleanup_orphan_scan", scan)

    cleaned = await m.cleanup_stale()

    assert cleaned == 1
    assert m._state_file.exists()  # kept for retry


@pytest.mark.asyncio
async def test_cleanup_stale_keeps_state_on_failure(tmp_path, monkeypatch):
    m = _make_manager(tmp_path)
    m._state_file.parent.mkdir(parents=True, exist_ok=True)
    m._state_file.write_text(
        json.dumps({"sandboxes": [{"linux_base_dir": "/tmp/miqi-sandboxes/a"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "miqi.sandbox.manager.BwrapSandbox.cleanup_dir",
        AsyncMock(return_value=False),
    )

    cleaned = await m.cleanup_stale()

    assert cleaned == 0
    # State file kept so the failed dir is retried next startup (#472).
    assert m._state_file.exists()


@pytest.mark.asyncio
async def test_cleanup_stale_clears_state_when_all_succeed(tmp_path, monkeypatch):
    m = _make_manager(tmp_path)
    m._state_file.parent.mkdir(parents=True, exist_ok=True)
    m._state_file.write_text(
        json.dumps({"sandboxes": [{"linux_base_dir": "/tmp/miqi-sandboxes/a"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "miqi.sandbox.manager.BwrapSandbox.cleanup_dir",
        AsyncMock(return_value=True),
    )

    cleaned = await m.cleanup_stale()

    assert cleaned == 1
    assert not m._state_file.exists()


@pytest.mark.asyncio
async def test_orphan_scan_removes_dirs(tmp_path, monkeypatch):
    m = _make_manager(tmp_path)
    proc = _fake_proc(0, out=b"/tmp/miqi-sandboxes/a\n/tmp/miqi-sandboxes/b\n")
    monkeypatch.setattr("miqi.sandbox.manager._is_windows", lambda: False)
    with patch(
        "miqi.sandbox.manager._create_subprocess_exec",
        return_value=proc,
    ) as spawn, patch(
        "miqi.sandbox.manager.BwrapSandbox.cleanup_dir",
        AsyncMock(return_value=True),
    ):
        cleaned, completed = await m._cleanup_orphan_scan()

    assert cleaned == 2
    assert completed is True
    # find is invoked via argv (no sh -c string interpolation)
    find_call = spawn.await_args.args
    assert "find" in find_call
    assert "sh" not in find_call


@pytest.mark.asyncio
async def test_orphan_scan_incomplete_on_failed_removal(tmp_path, monkeypatch):
    m = _make_manager(tmp_path)
    proc = _fake_proc(0, out=b"/tmp/miqi-sandboxes/a\n")
    monkeypatch.setattr("miqi.sandbox.manager._is_windows", lambda: False)
    with patch(
        "miqi.sandbox.manager._create_subprocess_exec",
        return_value=proc,
    ), patch(
        "miqi.sandbox.manager.BwrapSandbox.cleanup_dir",
        AsyncMock(return_value=False),
    ):
        cleaned, completed = await m._cleanup_orphan_scan()

    assert cleaned == 0
    assert completed is False


@pytest.mark.asyncio
async def test_orphan_scan_timeout_kills_and_waits(tmp_path, monkeypatch):
    m = _make_manager(tmp_path)
    proc = AsyncMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    monkeypatch.setattr("miqi.sandbox.manager._is_windows", lambda: False)
    with patch(
        "miqi.sandbox.manager._create_subprocess_exec",
        return_value=proc,
    ):
        cleaned, completed = await m._cleanup_orphan_scan()

    assert cleaned == 0
    assert completed is False
    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


# ── _rm_rf_retry: retry + verify ────────────────────────────────────────

@pytest.mark.asyncio
async def test_rm_rf_retry_success(monkeypatch):
    # rm succeeds (rc=0), then test -e fails (rc=1) -> path gone -> True
    procs = [_fake_proc(0), _fake_proc(1)]
    monkeypatch.setattr("miqi.sandbox.bwrap._is_windows", lambda: False)
    monkeypatch.setattr(
        "miqi.sandbox.bwrap._create_subprocess_exec",
        AsyncMock(side_effect=procs),
    )
    assert await BwrapSandbox._rm_rf_retry("/tmp/miqi-sandboxes/x") is True


@pytest.mark.asyncio
async def test_rm_rf_retry_retries_then_succeeds(monkeypatch):
    # rm fails once (rc=2), then succeeds (rc=0), verify gone (test rc=1)
    procs = [_fake_proc(2), _fake_proc(0), _fake_proc(1)]
    monkeypatch.setattr("miqi.sandbox.bwrap._is_windows", lambda: False)
    monkeypatch.setattr(
        "miqi.sandbox.bwrap._create_subprocess_exec",
        AsyncMock(side_effect=procs),
    )
    assert await BwrapSandbox._rm_rf_retry("/tmp/miqi-sandboxes/x") is True


@pytest.mark.asyncio
async def test_rm_rf_retry_gives_up_after_3_attempts(monkeypatch):
    # rm always fails -> False after 3 attempts
    procs = [_fake_proc(2), _fake_proc(2), _fake_proc(2)]
    monkeypatch.setattr("miqi.sandbox.bwrap._is_windows", lambda: False)
    monkeypatch.setattr(
        "miqi.sandbox.bwrap._create_subprocess_exec",
        AsyncMock(side_effect=procs),
    )
    assert await BwrapSandbox._rm_rf_retry("/tmp/miqi-sandboxes/x") is False


@pytest.mark.asyncio
async def test_rm_rf_retry_timeout_kills_and_waits(monkeypatch):
    """A timed-out rm subprocess must be killed and awaited before the retry
    loop moves on — otherwise the WSL wrapper leaks (CodeRabbit)."""
    timed_out = AsyncMock()
    timed_out.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    timed_out.kill = MagicMock()
    timed_out.wait = AsyncMock()

    ok_proc = _fake_proc(0)
    verify_gone = _fake_proc(1)

    monkeypatch.setattr("miqi.sandbox.bwrap._is_windows", lambda: False)
    monkeypatch.setattr(
        "miqi.sandbox.bwrap._create_subprocess_exec",
        AsyncMock(side_effect=[timed_out, ok_proc, verify_gone]),
    )

    # attempt 1 times out (killed+awaited), attempt 2 succeeds
    assert await BwrapSandbox._rm_rf_retry("/tmp/miqi-sandboxes/x") is True
    timed_out.kill.assert_called_once()
    timed_out.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_dir_respects_expected_root(monkeypatch):
    """Paths outside the configured sandbox root are rejected; paths under it
    pass through (CodeRabbit — no fixed /tmp allowlist)."""
    monkeypatch.setattr(
        "miqi.sandbox.bwrap.BwrapSandbox._rm_rf_retry", AsyncMock(return_value=True)
    )

    # custom root accepts children
    assert (
        await BwrapSandbox.cleanup_dir(
            "/data/sandboxes/sess_1", expected_root="/data/sandboxes"
        )
        is True
    )
    # root itself is accepted (boundary)
    assert (
        await BwrapSandbox.cleanup_dir("/data/sandboxes", expected_root="/data/sandboxes")
        is True
    )
    # sibling / parent / unrelated paths are rejected
    assert (
        await BwrapSandbox.cleanup_dir("/data/sandboxes2/sess_1", expected_root="/data/sandboxes")
        is False
    )
    assert (
        await BwrapSandbox.cleanup_dir("/etc/passwd", expected_root="/data/sandboxes")
        is False
    )


@pytest.mark.asyncio
async def test_handle_kill_noop_after_process_reaped(monkeypatch):
    """kill() on an already-reaped process must not signal the stale pgid —
    the pgid may have been recycled for an unrelated process group (#472)."""
    from miqi.sandbox.bwrap import BwrapCommandHandle

    handle = BwrapCommandHandle(_fake_proc(0), pgid=999)
    import os as _os

    killpg = MagicMock()
    monkeypatch.setattr(_os, "killpg", killpg, raising=False)
    terminate = MagicMock()
    monkeypatch.setattr(handle._process, "terminate", terminate)

    await handle.kill()

    killpg.assert_not_called()
    terminate.assert_not_called()


@pytest.mark.asyncio
async def test_handle_kill_signals_pgid_while_running(monkeypatch):
    """kill() on a running process (returncode None) still signals the pgid."""
    import signal as _signal

    from miqi.sandbox.bwrap import BwrapCommandHandle

    proc = _fake_proc(None)  # still running
    handle = BwrapCommandHandle(proc, pgid=999)
    import os as _os

    killpg = MagicMock()
    monkeypatch.setattr(_os, "killpg", killpg, raising=False)
    proc.wait = AsyncMock(return_value=0)

    await handle.kill()

    killpg.assert_called_once_with(999, _signal.SIGTERM)
