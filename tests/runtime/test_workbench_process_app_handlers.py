"""Tests for workbench process/* AppServer handlers (Phase 43.5)."""

from pathlib import Path

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_server():
    """Create an AppServer with WorkbenchProcessRuntime in bridge_context."""
    from unittest.mock import MagicMock

    from miqi.runtime.app_server import AppServer, ClientSessionRegistry
    from miqi.runtime.workbench_process_runtime import WorkbenchProcessRuntime

    registry = ClientSessionRegistry()
    registry.bridge_context = {
        "state": MagicMock(),
        "workbench_process_runtime": WorkbenchProcessRuntime(workspace=Path.cwd()),
    }
    server = AppServer(registry)
    return server, registry


async def _dispatch(server, registry, method, params, client_id="test-client", session_id=None):
    """Dispatch a request and return the response dict."""
    resp = await server.dispatch(
        request_id="req-1",
        method=method,
        params=params,
        client_id=client_id,
        session_id=session_id,
    )
    return resp


@pytest.fixture
def server_and_registry():
    """Fixture providing AppServer with process handlers registered."""
    from miqi.runtime.workbench_process_app_handlers import (
        register_workbench_process_handlers,
    )

    server, registry = _make_server()
    register_workbench_process_handlers(server)
    return server, registry


# ── process/spawn ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_spawn_returns_empty_result_immediately(server_and_registry):
    """process/spawn returns {} immediately — does not wait for exit."""
    import time

    server, registry = server_and_registry

    t0 = time.monotonic()
    resp = await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "import time; time.sleep(30)"],
        "processHandle": "spawn-slow",
        "cwd": str(Path.cwd()),
    })
    elapsed = time.monotonic() - t0

    assert "result" in resp, f"Expected result, got: {resp}"
    assert elapsed < 2.0, f"spawn should return immediately, took {elapsed:.1f}s"

    # Cleanup: kill the spawned process
    wpr = registry.bridge_context["workbench_process_runtime"]
    try:
        await wpr.kill("test-client", "spawn-slow")
    except Exception:
        pass


@pytest.mark.asyncio
async def test_process_spawn_emits_output_delta(server_and_registry):
    """process/spawn emits process/outputDelta events."""
    import asyncio as _asyncio

    server, registry = server_and_registry
    events_received: list = []

    async def fake_sink(envelope):
        events_received.append(envelope)

    server.set_event_sink("test-client", fake_sink)

    resp = await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "print('hello-spawn')"],
        "processHandle": "spawn-stream",
        "cwd": str(Path.cwd()),
    })
    assert "result" in resp, f"Expected result, got: {resp}"

    # Wait for output
    await _asyncio.sleep(0.5)

    output_deltas = [
        e for e in events_received
        if e.get("event") == "process/outputDelta"
    ]
    assert len(output_deltas) > 0, (
        f"Expected process/outputDelta events, got: {events_received}"
    )

    # Wait for exit notification
    await _asyncio.sleep(0.5)
    exit_events = [
        e for e in events_received
        if e.get("event") == "process/exited"
    ]
    assert len(exit_events) > 0, (
        f"Expected process/exited event, got: {events_received}"
    )


@pytest.mark.asyncio
async def test_process_spawn_emits_exited_notification(server_and_registry):
    """process/spawn emits process/exited notification when process finishes."""
    import asyncio as _asyncio

    server, registry = server_and_registry
    events_received: list = []

    async def fake_sink(envelope):
        events_received.append(envelope)

    server.set_event_sink("test-client", fake_sink)

    await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "print('done')"],
        "processHandle": "spawn-exit",
        "cwd": str(Path.cwd()),
    })

    # Wait for exit
    for _ in range(20):
        await _asyncio.sleep(0.1)
        exit_events = [
            e for e in events_received
            if e.get("event") == "process/exited"
        ]
        if exit_events:
            break

    exit_events = [
        e for e in events_received
        if e.get("event") == "process/exited"
    ]
    assert len(exit_events) > 0, (
        f"Expected process/exited event, got: {events_received}"
    )
    exit_data = exit_events[0]["data"]
    assert exit_data["exitCode"] == 0
    assert "done" in exit_data.get("stdout", "")


@pytest.mark.asyncio
async def test_process_spawn_missing_experimental_flag_rejected(server_and_registry):
    """process/spawn without experimentalApi flag returns EXPERIMENTAL_API_REQUIRED."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "process/spawn", {
        "command": ["python", "-c", "print('hi')"],
        "processHandle": "no-exp-flag",
        "cwd": str(Path.cwd()),
    })
    assert "error" in resp, f"Expected error without experimentalApi, got: {resp}"
    assert resp.get("code") == "EXPERIMENTAL_API_REQUIRED", (
        f"Expected EXPERIMENTAL_API_REQUIRED, got: {resp.get('code')}"
    )


@pytest.mark.asyncio
async def test_process_spawn_tty_true_rejected(server_and_registry):
    """process/spawn with tty:true returns UNSUPPORTED_FEATURE."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "print('hi')"],
        "processHandle": "tty-spawn",
        "cwd": str(Path.cwd()),
        "tty": True,
    })
    assert "error" in resp, f"Expected error for tty:true, got: {resp}"
    assert resp.get("code") == "UNSUPPORTED_FEATURE", (
        f"Expected UNSUPPORTED_FEATURE, got: {resp.get('code')}"
    )


@pytest.mark.asyncio
async def test_process_spawn_duplicate_handle_rejected(server_and_registry):
    """Duplicate processHandle is rejected."""
    import asyncio as _asyncio

    server, registry = server_and_registry

    # Spawn a long-running process
    resp1 = await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "import time; time.sleep(10)"],
        "processHandle": "dup-spawn",
        "cwd": str(Path.cwd()),
    })
    assert "result" in resp1

    await _asyncio.sleep(0.1)

    resp2 = await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "print('hi')"],
        "processHandle": "dup-spawn",
        "cwd": str(Path.cwd()),
    })
    assert "error" in resp2, f"Expected error for duplicate handle, got: {resp2}"

    # Cleanup
    wpr = registry.bridge_context["workbench_process_runtime"]
    try:
        await wpr.kill("test-client", "dup-spawn")
    except Exception:
        pass


# ── process/writeStdin ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_write_stdin_works(server_and_registry):
    """process/writeStdin writes to process stdin."""
    import asyncio as _asyncio
    import base64

    server, registry = server_and_registry
    events_received: list = []

    async def fake_sink(envelope):
        events_received.append(envelope)

    server.set_event_sink("test-client", fake_sink)

    await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c",
                    "import sys; data=sys.stdin.read(); print('READ:'+data)"],
        "processHandle": "spawn-stdin",
        "cwd": str(Path.cwd()),
    })
    await _asyncio.sleep(0.1)

    resp = await _dispatch(server, registry, "process/writeStdin", {
        "processHandle": "spawn-stdin",
        "deltaBase64": base64.b64encode(b"hello-spawn-stdin").decode(),
    })
    assert "result" in resp, f"Expected result from writeStdin, got: {resp}"

    # Close stdin
    await _dispatch(server, registry, "process/writeStdin", {
        "processHandle": "spawn-stdin",
        "closeStdin": True,
    })

    # Wait for exit
    for _ in range(20):
        await _asyncio.sleep(0.1)
        exit_events = [
            e for e in events_received
            if e.get("event") == "process/exited"
        ]
        if exit_events:
            break

    exit_events = [
        e for e in events_received
        if e.get("event") == "process/exited"
    ]
    assert len(exit_events) > 0
    assert "READ:hello-spawn-stdin" in exit_events[0]["data"].get("stdout", "")


# ── process/kill ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_kill_terminates_running_process(server_and_registry):
    """process/kill terminates a running process."""
    import asyncio as _asyncio

    server, registry = server_and_registry
    events_received: list = []

    async def fake_sink(envelope):
        events_received.append(envelope)

    server.set_event_sink("test-client", fake_sink)

    await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "import time; time.sleep(30)"],
        "processHandle": "spawn-kill",
        "cwd": str(Path.cwd()),
    })
    await _asyncio.sleep(0.2)

    resp = await _dispatch(server, registry, "process/kill", {
        "processHandle": "spawn-kill",
    })
    assert "result" in resp, f"Expected result from kill, got: {resp}"

    # Should get exited event with non-zero exit code
    await _asyncio.sleep(0.5)
    exit_events = [
        e for e in events_received
        if e.get("event") == "process/exited"
    ]
    assert len(exit_events) > 0, (
        f"Expected process/exited after kill, got: {events_received}"
    )


# ── process/resizePty ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_resize_pty_returns_unsupported_feature(server_and_registry):
    """process/resizePty returns UNSUPPORTED_FEATURE in Phase 43."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "process/resizePty", {
        "processHandle": "any-handle",
        "size": {"rows": 24, "cols": 80},
    })
    assert "error" in resp
    assert resp.get("code") == "UNSUPPORTED_FEATURE", (
        f"Expected UNSUPPORTED_FEATURE, got: {resp.get('code')}"
    )


# ── Cross-client isolation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_client_kill_rejected(server_and_registry):
    """Client A cannot kill client B's process."""
    import asyncio as _asyncio

    server, registry = server_and_registry

    # Client A spawns
    await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "import time; time.sleep(10)"],
        "processHandle": "cross-kill",
        "cwd": str(Path.cwd()),
    }, client_id="client-a")
    await _asyncio.sleep(0.1)

    # Client B tries to kill
    resp = await _dispatch(server, registry, "process/kill", {
        "processHandle": "cross-kill",
    }, client_id="client-b")
    assert "error" in resp, f"Expected error for cross-client kill, got: {resp}"

    # Cleanup
    wpr = registry.bridge_context["workbench_process_runtime"]
    try:
        await wpr.kill("client-a", "cross-kill")
    except Exception:
        pass


@pytest.mark.asyncio
async def test_cross_client_write_rejected(server_and_registry):
    """Client A cannot write to client B's process."""
    import asyncio as _asyncio
    import base64

    server, registry = server_and_registry

    await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c",
                    "import sys; sys.stdin.read(); print('DONE')"],
        "processHandle": "cross-write",
        "cwd": str(Path.cwd()),
    }, client_id="client-a")
    await _asyncio.sleep(0.1)

    resp = await _dispatch(server, registry, "process/writeStdin", {
        "processHandle": "cross-write",
        "deltaBase64": base64.b64encode(b"x").decode(),
    }, client_id="client-b")
    assert "error" in resp, f"Expected error for cross-client write, got: {resp}"

    # Cleanup
    wpr = registry.bridge_context["workbench_process_runtime"]
    try:
        await wpr.kill("client-a", "cross-write")
    except Exception:
        pass


# ── Client disconnect cleanup ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_disconnect_kills_live_process(server_and_registry):
    """When client event sink is removed, live processes are killed."""
    import asyncio as _asyncio

    server, registry = server_and_registry

    # Register client cleanup hook (normally done by BridgeRuntimeLoop)
    wpr = registry.bridge_context["workbench_process_runtime"]

    async def _kill_client_hook(client_id: str) -> None:
        await wpr.kill_client(client_id)

    server.add_client_cleanup_hook(_kill_client_hook)

    # Spawn a long-running process
    await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "import time; time.sleep(30)"],
        "processHandle": "disconnect-test",
        "cwd": str(Path.cwd()),
    }, client_id="client-disc")
    await _asyncio.sleep(0.2)

    # Verify process is alive
    wpr = registry.bridge_context["workbench_process_runtime"]
    assert wpr.get_handle("client-disc", "disconnect-test") is not None

    # Simulate disconnect by removing event sink (triggers cleanup hook)
    await server.remove_event_sink("client-disc")

    await _asyncio.sleep(0.3)

    # Process should be gone
    assert wpr.get_handle("client-disc", "disconnect-test") is None


@pytest.mark.asyncio
async def test_process_handle_reused_after_exit(server_and_registry):
    """After process exits, handle can be reused."""
    server, registry = server_and_registry

    await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "print('first')"],
        "processHandle": "reuse-spawn",
        "cwd": str(Path.cwd()),
    })
    import asyncio as _asyncio
    await _asyncio.sleep(0.3)

    resp = await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "print('second')"],
        "processHandle": "reuse-spawn",
        "cwd": str(Path.cwd()),
    })
    assert "result" in resp, f"Should be able to reuse handle after exit, got: {resp}"


# ── Validation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_handle_rejects_slashes(server_and_registry):
    """processHandle with slashes is rejected."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "print('hi')"],
        "processHandle": "bad/handle",
        "cwd": str(Path.cwd()),
    })
    assert "error" in resp


@pytest.mark.asyncio
async def test_process_handle_rejects_double_dot(server_and_registry):
    """processHandle with '..' is rejected."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "print('hi')"],
        "processHandle": "../escape",
        "cwd": str(Path.cwd()),
    })
    assert "error" in resp


# ── Output cap streaming (Phase 43 hardening) ───────────────────────────


@pytest.mark.asyncio
async def test_process_spawn_output_cap_streaming_cap_reached(server_and_registry):
    """process/spawn with small outputBytesCap emits process/outputDelta capReached=true
    and process/exited stdoutCapReached=true."""
    import asyncio as _asyncio

    server, registry = server_and_registry
    events_received: list = []

    async def fake_sink(envelope):
        events_received.append(envelope)

    server.set_event_sink("test-client", fake_sink)

    resp = await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "print('A' * 500)"],
        "processHandle": "spawn-cap-stream",
        "cwd": str(Path.cwd()),
        "outputBytesCap": 20,
    })
    assert "result" in resp, f"Expected result, got: {resp}"

    # Wait for output and exit
    for _ in range(20):
        await _asyncio.sleep(0.1)
        exit_events = [
            e for e in events_received
            if e.get("event") == "process/exited"
        ]
        if exit_events:
            break

    output_deltas = [
        e for e in events_received
        if e.get("event") == "process/outputDelta"
    ]
    assert len(output_deltas) > 0, (
        f"Expected process/outputDelta events, got: {events_received}"
    )

    # At least one delta must have capReached=true
    cap_reached_deltas = [
        d for d in output_deltas
        if d.get("data", {}).get("capReached")
    ]
    assert len(cap_reached_deltas) > 0, (
        f"Expected at least one delta with capReached=true, "
        f"got deltas: {output_deltas}"
    )

    # stdout capReached must appear exactly once
    stdout_cap_deltas = [
        d for d in output_deltas
        if d.get("data", {}).get("stream") == "stdout"
        and d.get("data", {}).get("capReached")
    ]
    assert len(stdout_cap_deltas) == 1, (
        f"Expected exactly 1 stdout capReached delta, "
        f"got {len(stdout_cap_deltas)}: {stdout_cap_deltas}"
    )

    # process/exited must have stdoutCapReached=true
    exit_events = [
        e for e in events_received
        if e.get("event") == "process/exited"
    ]
    assert len(exit_events) > 0, "Expected process/exited event"
    exit_data = exit_events[0]["data"]
    assert exit_data.get("stdoutCapReached") is True, (
        f"process/exited stdoutCapReached should be True, got: {exit_data}"
    )


# ── Default timeout (Phase 43 hardening) ─────────────────────────────────


@pytest.mark.asyncio
async def test_process_spawn_uses_default_timeout_when_omitted(server_and_registry):
    """When timeoutMs is omitted, DEFAULT_TIMEOUT_MS is passed to runtime."""
    from unittest.mock import patch

    from miqi.runtime.workbench_process_runtime import DEFAULT_TIMEOUT_MS

    server, registry = server_and_registry
    wpr = registry.bridge_context["workbench_process_runtime"]

    with patch.object(wpr, "spawn_background", wraps=wpr.spawn_background) as mock_spawn:
        resp = await _dispatch(server, registry, "process/spawn", {
            "experimentalApi": True,
            "command": ["python", "-c", "pass"],
            "processHandle": "spawn-default-timeout",
            "cwd": str(Path.cwd()),
        })

    assert "result" in resp, f"Expected result, got: {resp}"
    call_kwargs = mock_spawn.call_args.kwargs
    assert call_kwargs["timeout_ms"] == DEFAULT_TIMEOUT_MS, (
        f"Expected timeout_ms={DEFAULT_TIMEOUT_MS}, got {call_kwargs['timeout_ms']}"
    )

    # Cleanup: wait for background process to exit
    import asyncio as _asyncio
    await _asyncio.sleep(0.3)


@pytest.mark.asyncio
async def test_process_spawn_null_timeout_disables_timeout(server_and_registry):
    """timeoutMs=null passes None to the runtime, disabling timeout."""
    from unittest.mock import patch

    server, registry = server_and_registry
    wpr = registry.bridge_context["workbench_process_runtime"]

    with patch.object(wpr, "spawn_background", wraps=wpr.spawn_background) as mock_spawn:
        resp = await _dispatch(server, registry, "process/spawn", {
            "experimentalApi": True,
            "command": ["python", "-c", "pass"],
            "processHandle": "spawn-null-timeout",
            "cwd": str(Path.cwd()),
            "timeoutMs": None,
        })

    assert "result" in resp, f"Expected result, got: {resp}"
    call_kwargs = mock_spawn.call_args.kwargs
    assert call_kwargs["timeout_ms"] is None, (
        f"Expected timeout_ms=None for null timeoutMs, got {call_kwargs['timeout_ms']}"
    )

    # Cleanup
    import asyncio as _asyncio
    await _asyncio.sleep(0.3)


@pytest.mark.asyncio
async def test_process_spawn_rejects_negative_timeout(server_and_registry):
    """timeoutMs < 0 returns INVALID_PARAMS."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "pass"],
        "processHandle": "spawn-neg-timeout",
        "cwd": str(Path.cwd()),
        "timeoutMs": -1,
    })
    assert "error" in resp, f"Expected error for negative timeoutMs, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS", (
        f"Expected INVALID_PARAMS, got: {resp.get('code')}"
    )


# ── Default output cap (Phase 43 hardening) ─────────────────────────────


@pytest.mark.asyncio
async def test_process_spawn_uses_default_output_cap_when_omitted(server_and_registry):
    """When outputBytesCap is omitted, DEFAULT_OUTPUT_BYTES_CAP is passed to runtime."""
    from unittest.mock import patch

    from miqi.runtime.workbench_process_runtime import DEFAULT_OUTPUT_BYTES_CAP

    server, registry = server_and_registry
    wpr = registry.bridge_context["workbench_process_runtime"]

    with patch.object(wpr, "spawn_background", wraps=wpr.spawn_background) as mock_spawn:
        resp = await _dispatch(server, registry, "process/spawn", {
            "experimentalApi": True,
            "command": ["python", "-c", "pass"],
            "processHandle": "spawn-default-cap",
            "cwd": str(Path.cwd()),
        })

    assert "result" in resp, f"Expected result, got: {resp}"
    call_kwargs = mock_spawn.call_args.kwargs
    assert call_kwargs["output_cap"] == DEFAULT_OUTPUT_BYTES_CAP, (
        f"Expected output_cap={DEFAULT_OUTPUT_BYTES_CAP}, got {call_kwargs['output_cap']}"
    )

    # Cleanup
    import asyncio as _asyncio
    await _asyncio.sleep(0.3)


@pytest.mark.asyncio
async def test_process_spawn_rejects_null_output_cap(server_and_registry):
    """outputBytesCap=null returns INVALID_PARAMS."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "pass"],
        "processHandle": "spawn-null-cap",
        "cwd": str(Path.cwd()),
        "outputBytesCap": None,
    })
    assert "error" in resp, f"Expected error for null outputBytesCap, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS", (
        f"Expected INVALID_PARAMS, got: {resp.get('code')}"
    )


@pytest.mark.asyncio
async def test_process_spawn_rejects_negative_output_cap(server_and_registry):
    """outputBytesCap < 0 returns INVALID_PARAMS."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", "pass"],
        "processHandle": "spawn-neg-cap",
        "cwd": str(Path.cwd()),
        "outputBytesCap": -1,
    })
    assert "error" in resp, f"Expected error for negative outputBytesCap, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS", (
        f"Expected INVALID_PARAMS, got: {resp.get('code')}"
    )


# ── Zero output cap & capReached boundary (Phase 43 hardening) ──────────


@pytest.mark.asyncio
async def test_process_spawn_zero_output_cap_immediate_cap_reached(server_and_registry):
    """process/spawn with outputBytesCap=0 immediately triggers capReached.

    Verifies the capReached boundary fix in _read_stream: when buffer is
    already at cap (0 bytes) and the next chunk arrives, an empty
    capReached delta is emitted so the client sees exactly one notification.
    """
    import asyncio as _asyncio

    server, registry = server_and_registry
    events_received: list = []

    async def fake_sink(envelope):
        events_received.append(envelope)

    server.set_event_sink("test-client", fake_sink)

    await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c",
                    "import sys; sys.stdout.buffer.write(b'A' * 100); sys.stdout.flush()"],
        "processHandle": "spawn-cap-zero",
        "cwd": str(Path.cwd()),
        "outputBytesCap": 0,
    })

    # Wait for exit
    for _ in range(20):
        await _asyncio.sleep(0.1)
        exit_events = [
            e for e in events_received
            if e.get("event") == "process/exited"
        ]
        if exit_events:
            break

    output_deltas = [
        e for e in events_received
        if e.get("event") == "process/outputDelta"
    ]
    stdout_cap_deltas = [
        d for d in output_deltas
        if d.get("data", {}).get("stream") == "stdout"
        and d.get("data", {}).get("capReached")
    ]
    assert len(stdout_cap_deltas) == 1, (
        f"Expected exactly 1 stdout capReached delta with cap=0, "
        f"got {len(stdout_cap_deltas)}: {stdout_cap_deltas}"
    )
    # With cap=0 the capReached delta carries empty data
    assert stdout_cap_deltas[0]["data"]["deltaBase64"] == "", (
        f"Expected empty deltaBase64 for cap=0 capReached, "
        f"got: {stdout_cap_deltas[0]['data']['deltaBase64']!r}"
    )

    exit_events = [
        e for e in events_received
        if e.get("event") == "process/exited"
    ]
    assert len(exit_events) > 0, "Expected process/exited event"
    exit_data = exit_events[0]["data"]
    assert exit_data.get("stdoutCapReached") is True, (
        f"process/exited stdoutCapReached should be True with outputBytesCap=0, "
        f"got: {exit_data}"
    )
    assert exit_data.get("stdout", "") == "", (
        f"stdout should be empty with outputBytesCap=0, "
        f"got: {exit_data.get('stdout', '')!r}"
    )


@pytest.mark.asyncio
async def test_process_spawn_output_cap_boundary_fill_then_overflow(server_and_registry):
    """process/spawn: exactly filling cap then overflowing emits exactly one capReached."""
    import asyncio as _asyncio

    server, registry = server_and_registry
    events_received: list = []

    async def fake_sink(envelope):
        events_received.append(envelope)

    server.set_event_sink("test-client", fake_sink)

    await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": ["python", "-c", (
            "import sys; "
            "sys.stdout.buffer.write(b'A' * 20); sys.stdout.flush(); "
            "sys.stdout.buffer.write(b'B' * 50); sys.stdout.flush()"
        )],
        "processHandle": "spawn-cap-boundary",
        "cwd": str(Path.cwd()),
        "outputBytesCap": 20,
    })

    # Wait for exit
    for _ in range(20):
        await _asyncio.sleep(0.1)
        exit_events = [
            e for e in events_received
            if e.get("event") == "process/exited"
        ]
        if exit_events:
            break

    output_deltas = [
        e for e in events_received
        if e.get("event") == "process/outputDelta"
    ]
    stdout_cap_deltas = [
        d for d in output_deltas
        if d.get("data", {}).get("stream") == "stdout"
        and d.get("data", {}).get("capReached")
    ]
    assert len(stdout_cap_deltas) == 1, (
        f"Expected exactly 1 stdout capReached delta (boundary fill+overflow), "
        f"got {len(stdout_cap_deltas)}: {stdout_cap_deltas}"
    )

    exit_events = [
        e for e in events_received
        if e.get("event") == "process/exited"
    ]
    assert len(exit_events) > 0, "Expected process/exited event"
    exit_data = exit_events[0]["data"]
    assert exit_data.get("stdoutCapReached") is True, (
        f"process/exited stdoutCapReached should be True when output exceeds cap, "
        f"got: {exit_data}"
    )


# ── Phase 63 typed validation regressions ───────────────────────────────


@pytest.mark.asyncio
async def test_process_spawn_typed_validation_before_spawn_background(server_and_registry):
    from unittest.mock import AsyncMock

    server, registry = server_and_registry
    wpr = registry.bridge_context["workbench_process_runtime"]
    wpr.spawn_background = AsyncMock()

    resp = await _dispatch(server, registry, "process/spawn", {
        "experimentalApi": True,
        "command": [],
        "processHandle": "bad-spawn",
        "cwd": str(Path.cwd()),
    })

    assert resp["code"] == "INVALID_PARAMS"
    wpr.spawn_background.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_write_stdin_typed_validation_before_write(server_and_registry):
    from unittest.mock import AsyncMock

    server, registry = server_and_registry
    wpr = registry.bridge_context["workbench_process_runtime"]
    wpr.write_stdin = AsyncMock()
    wpr.close_stdin = AsyncMock()

    resp = await _dispatch(server, registry, "process/writeStdin", {
        "processHandle": "proc-1",
    })

    assert resp["code"] == "INVALID_PARAMS"
    wpr.write_stdin.assert_not_awaited()
    wpr.close_stdin.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_kill_typed_validation_before_kill(server_and_registry):
    from unittest.mock import AsyncMock

    server, registry = server_and_registry
    wpr = registry.bridge_context["workbench_process_runtime"]
    wpr.kill = AsyncMock()

    resp = await _dispatch(server, registry, "process/kill", {
        "processHandle": "../bad",
    })

    assert resp["code"] == "INVALID_PARAMS"
    wpr.kill.assert_not_awaited()
