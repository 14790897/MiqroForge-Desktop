"""Tests for WorkbenchProcessRuntime (Phase 43.2).

Tests process spawning, output capture, timeout, stdin, kill, cleanup,
and cross-client isolation.
"""

import asyncio

import pytest

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def process_runtime():
    """Create a WorkbenchProcessRuntime for testing."""
    from pathlib import Path

    from miqi.runtime.workbench_process_runtime import WorkbenchProcessRuntime

    return WorkbenchProcessRuntime(workspace=Path.cwd())


# ── Basic spawn ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_python_hello_returns_exit_0_and_stdout(process_runtime):
    """Spawn 'python -c print('hello')' — exit 0, stdout captured."""
    chunks: list = []

    async def on_chunk(handle_id, chunk):
        chunks.append(chunk)

    exit_result = await process_runtime.spawn(
        client_id="test-client",
        handle_id="py-hello",
        kind="commandExec",
        command=["python", "-c", "print('hello')"],
        cwd=process_runtime.workspace,
        on_chunk=on_chunk,
    )

    assert exit_result.exit_code == 0
    assert "hello" in exit_result.stdout
    assert exit_result.stderr == ""


@pytest.mark.asyncio
async def test_spawn_captures_stderr_separately(process_runtime):
    """Stderr output is captured separately from stdout."""
    exit_result = await process_runtime.spawn(
        client_id="test-client",
        handle_id="py-stderr",
        kind="commandExec",
        command=["python", "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
        cwd=process_runtime.workspace,
    )

    assert exit_result.exit_code == 0
    assert "out" in exit_result.stdout
    assert "err" in exit_result.stderr


@pytest.mark.asyncio
async def test_output_cap_per_stream(process_runtime):
    """Output cap limits bytes per stream (stdout + stderr independently)."""
    exit_result = await process_runtime.spawn(
        client_id="test-client",
        handle_id="py-cap",
        kind="commandExec",
        command=["python", "-c", "print('A' * 100); import sys; print('B' * 100, file=sys.stderr)"],
        cwd=process_runtime.workspace,
        output_cap=10,
    )

    assert exit_result.stdout_cap_reached
    assert exit_result.stderr_cap_reached
    assert len(exit_result.stdout) <= 10 + 100  # cap is approximate (per chunk)
    assert len(exit_result.stderr) <= 10 + 100


@pytest.mark.asyncio
async def test_timeout_kills_long_process(process_runtime):
    """Timeout kills a process that runs longer than timeout_ms."""
    import time

    t0 = time.monotonic()
    exit_result = await process_runtime.spawn(
        client_id="test-client",
        handle_id="py-slow",
        kind="commandExec",
        command=["python", "-c", "import time; time.sleep(30)"],
        cwd=process_runtime.workspace,
        timeout_ms=500,
    )
    elapsed = time.monotonic() - t0

    assert elapsed < 5, f"Timeout should kill process quickly, took {elapsed:.1f}s"
    assert exit_result.exit_code != 0


@pytest.mark.asyncio
async def test_duplicate_handle_rejected(process_runtime):
    """Spawning with same handle_id while active raises error."""
    import asyncio as _asyncio

    # Start a long-running process
    task = _asyncio.create_task(
        process_runtime.spawn(
            client_id="test-client",
            handle_id="dup-test",
            kind="commandExec",
            command=["python", "-c", "import time; time.sleep(5)"],
            cwd=process_runtime.workspace,
        )
    )

    await _asyncio.sleep(0.1)  # Let it start

    with pytest.raises(Exception) as exc_info:
        await process_runtime.spawn(
            client_id="test-client",
            handle_id="dup-test",
            kind="commandExec",
            command=["python", "-c", "print('hi')"],
            cwd=process_runtime.workspace,
        )

    assert "already" in str(exc_info.value).lower() or "active" in str(exc_info.value).lower()

    await task  # Let the first one finish


@pytest.mark.asyncio
async def test_same_handle_reusable_after_exit(process_runtime):
    """After a process exits, the same handle_id can be reused."""
    await process_runtime.spawn(
        client_id="test-client",
        handle_id="reuse-me",
        kind="commandExec",
        command=["python", "-c", "print('first')"],
        cwd=process_runtime.workspace,
    )

    # Reuse after exit
    result = await process_runtime.spawn(
        client_id="test-client",
        handle_id="reuse-me",
        kind="commandExec",
        command=["python", "-c", "print('second')"],
        cwd=process_runtime.workspace,
    )
    assert "second" in result.stdout


# ── Stdin ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_stdin_to_process(process_runtime):
    """Write to stdin of a process that reads from stdin."""
    import asyncio as _asyncio

    task = _asyncio.create_task(
        process_runtime.spawn(
            client_id="test-client",
            handle_id="py-stdin",
            kind="commandExec",
            command=["python", "-c", "import sys; print('GOT:' + sys.stdin.read())"],
            cwd=process_runtime.workspace,
            stdin_enabled=True,
        )
    )

    await _asyncio.sleep(0.1)

    await process_runtime.write_stdin(
        client_id="test-client",
        handle_id="py-stdin",
        data=b"hello-stdin",
    )
    await process_runtime.close_stdin(
        client_id="test-client",
        handle_id="py-stdin",
    )

    result = await task
    assert "GOT:hello-stdin" in result.stdout


@pytest.mark.asyncio
async def test_close_stdin_triggers_eof(process_runtime):
    """Closing stdin causes the process to see EOF."""
    import asyncio as _asyncio

    # Spawn in a task so we can close stdin from the main flow
    task = _asyncio.create_task(
        process_runtime.spawn(
            client_id="test-client",
            handle_id="py-eof",
            kind="commandExec",
            command=["python", "-c", "import sys; sys.stdin.read(); print('DONE')"],
            cwd=process_runtime.workspace,
            stdin_enabled=True,
        )
    )
    await _asyncio.sleep(0.1)

    # Close stdin to unblock the process
    await process_runtime.close_stdin("test-client", "py-eof")

    result = await task
    assert "DONE" in result.stdout


# ── Cross-client isolation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_a_cannot_write_to_client_b_handle(process_runtime):
    """Client A cannot write stdin to client B's process."""
    import asyncio as _asyncio

    task = _asyncio.create_task(
        process_runtime.spawn(
            client_id="client-a",
            handle_id="isolated",
            kind="commandExec",
            command=["python", "-c", "import sys; sys.stdin.read(); print('DONE')"],
            cwd=process_runtime.workspace,
            stdin_enabled=True,
        )
    )

    await _asyncio.sleep(0.1)

    with pytest.raises(Exception):
        await process_runtime.write_stdin(
            client_id="client-b",
            handle_id="isolated",
            data=b"intruder",
        )

    # Clean up
    try:
        await process_runtime.close_stdin("client-a", "isolated")
    except Exception:
        pass
    await task


@pytest.mark.asyncio
async def test_client_a_cannot_kill_client_b_handle(process_runtime):
    """Client A cannot kill client B's process."""
    import asyncio as _asyncio

    task = _asyncio.create_task(
        process_runtime.spawn(
            client_id="client-a",
            handle_id="kill-me",
            kind="commandExec",
            command=["python", "-c", "import time; time.sleep(10)"],
            cwd=process_runtime.workspace,
        )
    )

    await _asyncio.sleep(0.1)

    with pytest.raises(Exception):
        await process_runtime.kill("client-b", "kill-me")

    # Clean up
    try:
        await process_runtime.kill("client-a", "kill-me")
    except Exception:
        pass
    await task


# ── Cleanup ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_all_kills_live_processes(process_runtime):
    """stop_all() kills all live processes and leaves no pending tasks."""
    import asyncio as _asyncio

    t1 = _asyncio.create_task(
        process_runtime.spawn(
            client_id="c1",
            handle_id="live-1",
            kind="commandExec",
            command=["python", "-c", "import time; time.sleep(30)"],
            cwd=process_runtime.workspace,
        )
    )
    t2 = _asyncio.create_task(
        process_runtime.spawn(
            client_id="c2",
            handle_id="live-2",
            kind="commandExec",
            command=["python", "-c", "import time; time.sleep(30)"],
            cwd=process_runtime.workspace,
        )
    )

    await _asyncio.sleep(0.2)

    await process_runtime.stop_all()

    # Both tasks should resolve (killed)
    results = await _asyncio.gather(t1, t2, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            pass  # CancelledError is fine
        else:
            assert r.exit_code != 0  # Killed, not clean exit

    # No handles remain
    assert len(process_runtime._handles) == 0


@pytest.mark.asyncio
async def test_kill_client_kills_only_that_clients_processes(process_runtime):
    """kill_client() kills only the specified client's processes."""
    import asyncio as _asyncio

    t1 = _asyncio.create_task(
        process_runtime.spawn(
            client_id="c-a",
            handle_id="a-proc",
            kind="commandExec",
            command=["python", "-c", "import time; time.sleep(30)"],
            cwd=process_runtime.workspace,
        )
    )
    t2 = _asyncio.create_task(
        process_runtime.spawn(
            client_id="c-b",
            handle_id="b-proc",
            kind="commandExec",
            command=["python", "-c", "import time; time.sleep(30)"],
            cwd=process_runtime.workspace,
        )
    )

    await _asyncio.sleep(0.2)

    await process_runtime.kill_client("c-a")

    # c-a process should be gone
    r1 = await t1
    assert r1.exit_code != 0

    # c-b should still be running — kill it to clean up
    t2.cancel()
    try:
        await t2
    except asyncio.CancelledError:
        pass

    await process_runtime.stop_all()


# ── Streaming chunks ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_streaming_output_chunks_emitted(process_runtime):
    """Streaming mode emits output chunks via callback."""
    chunks: list = []

    async def on_chunk(handle_id, chunk):
        chunks.append(chunk)

    await process_runtime.spawn(
        client_id="test-client",
        handle_id="stream-test",
        kind="process",
        command=["python", "-c", "print('hello')"],
        cwd=process_runtime.workspace,
        on_chunk=on_chunk,
    )

    assert len(chunks) > 0
    stdout_chunks = [c for c in chunks if c.stream == "stdout"]
    assert len(stdout_chunks) > 0


@pytest.mark.asyncio
async def test_unknown_handle_write_raises(process_runtime):
    """Writing to unknown handle raises NOT_FOUND."""
    with pytest.raises(Exception):
        await process_runtime.write_stdin(
            client_id="test-client",
            handle_id="nonexistent",
            data=b"data",
        )


@pytest.mark.asyncio
async def test_unknown_handle_kill_raises(process_runtime):
    """Killing unknown handle raises NOT_FOUND."""
    with pytest.raises(Exception):
        await process_runtime.kill("test-client", "nonexistent")


# ── Kill returncode correctness (Phase 43 hardening) ──────────────────────


@pytest.mark.asyncio
async def test_kill_returncode_zero_preserved(process_runtime):
    """Kill() on a process that exited 0 returns exit_code=0, not -1.

    Regression: ``handle.process.returncode or -1`` evaluates 0 as falsy
    and incorrectly returns -1.

    Uses an on_exit callback that blocks cleanup so the handle stays
    registered long enough for kill() to observe the real returncode.
    """
    import asyncio as _asyncio

    exit_seen = _asyncio.Event()
    ok_to_cleanup = _asyncio.Event()
    exit_result_ref: list = []

    async def _on_exit(exit_result):
        exit_result_ref.append(exit_result)
        exit_seen.set()
        # Block until the test is done inspecting
        await ok_to_cleanup.wait()

    await process_runtime.spawn_background(
        client_id="test-client",
        handle_id="kill-returncode-zero",
        kind="process",
        command=["python", "-c", "pass"],  # exits 0 immediately
        cwd=process_runtime.workspace,
        on_exit=_on_exit,
    )

    # Wait for process to exit and on_exit to fire
    await exit_seen.wait()

    # Handle must still be registered (on_exit holds cleanup)
    assert process_runtime.get_handle("test-client", "kill-returncode-zero") is not None

    # on_exit saw correct exit_code
    assert exit_result_ref[0].exit_code == 0

    # kill() on an already-exited process — must return 0, not -1
    result = await process_runtime.kill("test-client", "kill-returncode-zero")
    assert result.exit_code == 0, (
        f"exit_code should be 0 for process that exited 0, got {result.exit_code}. "
        f"The old ``returncode or -1`` bug would coerce 0 → -1."
    )

    # Allow cleanup to proceed
    ok_to_cleanup.set()
    await _asyncio.sleep(0.2)


@pytest.mark.asyncio
async def test_kill_returncode_nonzero_preserved(process_runtime):
    """Kill() on a process that exited 1 returns exit_code=1, not 0."""
    import asyncio as _asyncio

    exit_seen = _asyncio.Event()
    ok_to_cleanup = _asyncio.Event()
    exit_result_ref: list = []

    async def _on_exit(exit_result):
        exit_result_ref.append(exit_result)
        exit_seen.set()
        await ok_to_cleanup.wait()

    await process_runtime.spawn_background(
        client_id="test-client",
        handle_id="kill-returncode-one",
        kind="process",
        command=["python", "-c", "import sys; sys.exit(1)"],
        cwd=process_runtime.workspace,
        on_exit=_on_exit,
    )

    await exit_seen.wait()

    assert process_runtime.get_handle("test-client", "kill-returncode-one") is not None
    assert exit_result_ref[0].exit_code == 1

    result = await process_runtime.kill("test-client", "kill-returncode-one")
    assert result.exit_code == 1, (
        f"exit_code should be 1 for process that exited 1, got {result.exit_code}"
    )

    ok_to_cleanup.set()
    await _asyncio.sleep(0.2)
