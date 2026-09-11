"""Tests for workbench command/exec* AppServer handlers (Phase 43.4)."""

import sys

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_server():
    """Create an AppServer with WorkbenchProcessRuntime in bridge_context."""
    from pathlib import Path
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
    """Fixture providing AppServer with workbench handlers registered."""
    from miqi.runtime.workbench_command_app_handlers import (
        register_workbench_command_handlers,
    )

    server, registry = _make_server()
    register_workbench_command_handlers(server)
    return server, registry


# ── command/exec ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_command_exec_buffered_returns_stdout_stderr_exit_code(server_and_registry):
    """Buffered command/exec returns {exitCode, stdout, stderr}."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "print('hello')"],
        "processId": "cmd-buffered",
    })

    assert "result" in resp, f"Expected result, got: {resp}"
    result = resp["result"]
    assert result["exitCode"] == 0
    assert "hello" in result["stdout"]
    assert result.get("stderr", "") == ""


@pytest.mark.asyncio
async def test_command_exec_streaming_emits_output_delta(server_and_registry):
    """Streaming command/exec emits command/exec/outputDelta before response."""

    server, registry = server_and_registry

    events_received: list = []

    async def fake_sink(envelope):
        events_received.append(envelope)

    server.set_event_sink("test-client", fake_sink)

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "print('streamed')"],
        "processId": "cmd-stream",
        "streamStdoutStderr": True,
    })

    assert "result" in resp, f"Expected result, got: {resp}"
    output_deltas = [
        e for e in events_received
        if e.get("event") == "command/exec/outputDelta"
    ]
    assert len(output_deltas) > 0, (
        f"Expected outputDelta events, got events: {events_received}"
    )


@pytest.mark.asyncio
async def test_command_exec_duplicate_process_id_rejected(server_and_registry):
    """Duplicate active processId is rejected."""
    import asyncio as _asyncio

    server, registry = server_and_registry

    # Start a long-running command
    task = _asyncio.create_task(
        _dispatch(server, registry, "command/exec", {
            "command": ["python", "-c", "import time; time.sleep(5)"],
            "processId": "dup-cmd",
        })
    )
    await _asyncio.sleep(0.1)

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "print('hi')"],
        "processId": "dup-cmd",
    })
    assert "error" in resp, f"Expected error for duplicate processId, got: {resp}"

    await task


@pytest.mark.asyncio
async def test_command_exec_unknown_process_id_returns_not_found(server_and_registry):
    """Writing to unknown processId returns NOT_FOUND."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec/write", {
        "processId": "no-such-process",
        "deltaBase64": "aGVsbG8=",
    })
    assert "error" in resp
    assert resp.get("code") in ("NOT_FOUND", "INVALID_REQUEST")


@pytest.mark.asyncio
async def test_command_exec_tty_true_rejected(server_and_registry):
    """tty: true returns UNSUPPORTED_FEATURE."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "print('hi')"],
        "processId": "tty-test",
        "tty": True,
    })
    assert "error" in resp, f"Expected error for tty:true, got: {resp}"
    assert resp.get("code") == "UNSUPPORTED_FEATURE", (
        f"Expected UNSUPPORTED_FEATURE, got: {resp.get('code')}"
    )


@pytest.mark.asyncio
async def test_command_exec_size_without_tty_rejected(server_and_registry):
    """size without tty:true returns INVALID_PARAMS."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "print('hi')"],
        "processId": "size-test",
        "size": {"rows": 24, "cols": 80},
    })
    assert "error" in resp, f"Expected error for size without tty, got: {resp}"


# ── command/exec/resize ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_command_exec_resize_returns_unsupported_feature(server_and_registry):
    """command/exec/resize returns UNSUPPORTED_FEATURE in Phase 43."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec/resize", {
        "processId": "any-process",
        "size": {"rows": 24, "cols": 80},
    })
    assert "error" in resp
    assert resp.get("code") == "UNSUPPORTED_FEATURE", (
        f"Expected UNSUPPORTED_FEATURE, got: {resp.get('code')}"
    )


# ── command/exec/write ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_command_exec_write_writes_to_stdin(server_and_registry):
    """command/exec/write writes data to the process stdin."""
    import asyncio as _asyncio

    server, registry = server_and_registry

    # Start a process that reads from stdin
    task = _asyncio.create_task(
        _dispatch(server, registry, "command/exec", {
            "command": ["python", "-c",
                        "import sys; data=sys.stdin.read(); print('READ:'+data)"],
            "processId": "cmd-stdin-write",
            "streamStdin": True,
        })
    )
    await _asyncio.sleep(0.1)

    # Write to stdin
    import base64
    resp = await _dispatch(server, registry, "command/exec/write", {
        "processId": "cmd-stdin-write",
        "deltaBase64": base64.b64encode(b"hello-stdin").decode(),
    })
    assert "result" in resp, f"Expected result from write, got: {resp}"

    # Close stdin
    await _dispatch(server, registry, "command/exec/write", {
        "processId": "cmd-stdin-write",
        "closeStdin": True,
    })

    result_resp = await task
    assert "result" in result_resp, f"Expected result, got: {result_resp}"
    assert "READ:hello-stdin" in result_resp["result"]["stdout"]


@pytest.mark.asyncio
async def test_command_exec_write_invalid_base64_rejected(server_and_registry):
    """Invalid base64 in deltaBase64 returns error."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec/write", {
        "processId": "any",
        "deltaBase64": "!!!not-base64!!!",
    })
    assert "error" in resp


# ── command/exec/terminate ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_command_exec_terminate_kills_running_process(server_and_registry):
    """command/exec/terminate kills a long-running process."""
    import asyncio as _asyncio

    server, registry = server_and_registry

    task = _asyncio.create_task(
        _dispatch(server, registry, "command/exec", {
            "command": ["python", "-c", "import time; time.sleep(30)"],
            "processId": "cmd-kill-me",
        })
    )
    await _asyncio.sleep(0.2)

    resp = await _dispatch(server, registry, "command/exec/terminate", {
        "processId": "cmd-kill-me",
    })
    assert "result" in resp, f"Expected result from terminate, got: {resp}"

    result_resp = await task
    # Either the main response returned an error (killed), or exitCode != 0
    if "result" in result_resp:
        assert result_resp["result"]["exitCode"] != 0


# ── Validation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_command_exec_rejects_empty_argv(server_and_registry):
    """Empty command list is rejected."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": [],
        "processId": "empty-cmd",
    })
    assert "error" in resp


@pytest.mark.asyncio
async def test_command_exec_rejects_empty_string_arg(server_and_registry):
    """Command with empty string arg is rejected."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", ""],
        "processId": "empty-arg",
    })
    assert "error" in resp


@pytest.mark.asyncio
async def test_command_exec_process_id_rejects_slashes(server_and_registry):
    """processId with slashes is rejected."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "print('hi')"],
        "processId": "bad/id",
    })
    assert "error" in resp


@pytest.mark.asyncio
async def test_command_exec_process_id_rejects_double_dot(server_and_registry):
    """processId with '..' is rejected."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "print('hi')"],
        "processId": "../escape",
    })
    assert "error" in resp


# ── Env validation (security hardening) ─────────────────────────────────


@pytest.mark.asyncio
async def test_env_rejects_ld_preload(server_and_registry):
    """LD_PRELOAD is blocked for security."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "print('hi')"],
        "processId": "env-ld",
        "env": {"LD_PRELOAD": "/tmp/evil.so"},
    })
    assert "error" in resp, f"LD_PRELOAD should be rejected, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS", (
        f"LD_PRELOAD rejection must be INVALID_PARAMS (blocklist), "
        f"not {resp.get('code')}"
    )


@pytest.mark.asyncio
async def test_env_rejects_pythonpath(server_and_registry):
    """PYTHONPATH is blocked for security."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "print('hi')"],
        "processId": "env-py",
        "env": {"PYTHONPATH": "/tmp/evil"},
    })
    assert "error" in resp, f"PYTHONPATH should be rejected, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS", (
        f"PYTHONPATH rejection must be INVALID_PARAMS (blocklist), "
        f"not {resp.get('code')}"
    )


@pytest.mark.asyncio
async def test_env_rejects_java_tool_options(server_and_registry):
    """JAVA_TOOL_OPTIONS is blocked for security."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "print('hi')"],
        "processId": "env-java",
        "env": {"JAVA_TOOL_OPTIONS": "-Djava.security.manager"},
    })
    assert "error" in resp, f"JAVA_TOOL_OPTIONS should be rejected, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS", (
        f"JAVA_TOOL_OPTIONS rejection must be INVALID_PARAMS (blocklist), "
        f"not {resp.get('code')}"
    )


@pytest.mark.asyncio
async def test_env_allows_safe_vars(server_and_registry):
    """Safe env vars like custom vars are allowed."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "import os; print(os.environ.get('MY_VAR',''))"],
        "processId": "env-safe",
        "env": {"MY_VAR": "hello"},
    })
    assert "result" in resp, f"Safe env var should be allowed, got: {resp}"
    assert "hello" in resp["result"]["stdout"]


# ── Env merge / unset (Phase 43 hardening) ──────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="SystemRoot is Windows-only env var")
async def test_env_preserves_inherited_environment(server_and_registry):
    """env={"MY_VAR":"hello"} preserves inherited PATH, SystemRoot, etc."""
    server, registry = server_and_registry

    # The command checks that a custom env var is set AND that basic
    # inherited vars (PATH, SystemRoot) are not cleared.
    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", (
            "import os; "
            "print('MY_VAR=' + os.environ.get('MIQI_TEST_43_VAR','MISSING')); "
            "print('HAS_PATH=' + str('PATH' in os.environ)); "
            "print('HAS_SYSTEM=' + str("
            "    'SystemRoot' in os.environ or 'SYSTEMROOT' in os.environ"
            "))"
        )],
        "processId": "env-inherit",
        "env": {"MIQI_TEST_43_VAR": "hello_from_43"},
    })

    assert "result" in resp, f"Expected result, got: {resp}"
    stdout = resp["result"]["stdout"]
    assert "MY_VAR=hello_from_43" in stdout, (
        f"Custom env var not set, stdout: {stdout}"
    )
    assert "HAS_PATH=True" in stdout, (
        f"Inherited PATH was cleared, stdout: {stdout}"
    )
    assert "HAS_SYSTEM=True" in stdout, (
        f"Inherited SystemRoot was cleared, stdout: {stdout}"
    )


@pytest.mark.asyncio
async def test_env_none_value_unsets_inherited_var(server_and_registry):
    """env={"KEY": None} removes an inherited environment variable."""
    import os as _os

    server, registry = server_and_registry

    # Set a marker in the parent process so the child would inherit it
    _os.environ["MIQI_TEST_DELETE_ME"] = "should_be_gone"
    try:
        resp = await _dispatch(server, registry, "command/exec", {
            "command": ["python", "-c", (
                "import os; "
                "print('GOT:' + os.environ.get('MIQI_TEST_DELETE_ME','DELETED'))"
            )],
            "processId": "env-unset",
            "env": {"MIQI_TEST_DELETE_ME": None},
        })
        assert "result" in resp, f"Expected result, got: {resp}"
        stdout = resp["result"]["stdout"]
        assert "GOT:DELETED" in stdout, (
            f"None should have unset MIQI_TEST_DELETE_ME, got stdout: {stdout}"
        )
    finally:
        del _os.environ["MIQI_TEST_DELETE_ME"]


@pytest.mark.asyncio
async def test_inherited_env_blocked_keys_are_sanitized(server_and_registry):
    """Blocked env vars from parent environment are stripped before child spawns.

    Even when no ``env`` parameter is provided, the inherited environment
    must not leak dangerous variables like JAVA_TOOL_OPTIONS to the child.
    """
    import os as _os

    server, registry = server_and_registry

    # Set a blocked key in the parent environment
    _os.environ["JAVA_TOOL_OPTIONS"] = "-Djava.security.manager"
    try:
        # Spawn WITHOUT any env overrides — inherited env should be sanitized
        resp = await _dispatch(server, registry, "command/exec", {
            "command": ["python", "-c", (
                "import os; "
                "print('GOT:' + os.environ.get('JAVA_TOOL_OPTIONS','SANITIZED'))"
            )],
            "processId": "inherit-blocked",
        })
        assert "result" in resp, f"Expected result, got: {resp}"
        stdout = resp["result"]["stdout"]
        assert "GOT:SANITIZED" in stdout, (
            f"JAVA_TOOL_OPTIONS should be sanitized from inherited env, "
            f"got stdout: {stdout}"
        )
    finally:
        del _os.environ["JAVA_TOOL_OPTIONS"]


# ── Output cap streaming (Phase 43 hardening) ───────────────────────────


@pytest.mark.asyncio
async def test_command_exec_output_cap_streaming_cap_reached(server_and_registry):
    """Streaming command/exec with small outputBytesCap emits capReached=true."""
    server, registry = server_and_registry

    events_received: list = []

    async def fake_sink(envelope):
        events_received.append(envelope)

    server.set_event_sink("test-client", fake_sink)

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "print('A' * 500)"],
        "processId": "cmd-cap-stream",
        "streamStdoutStderr": True,
        "outputBytesCap": 20,
    })

    output_deltas = [
        e for e in events_received
        if e.get("event") == "command/exec/outputDelta"
    ]
    assert len(output_deltas) > 0, (
        f"Expected outputDelta events, got: {events_received}"
    )

    # At least one delta must have capReached=true
    cap_reached_deltas = [d for d in output_deltas if d.get("data", {}).get("capReached")]
    assert len(cap_reached_deltas) > 0, (
        f"Expected at least one delta with capReached=true, got deltas: {output_deltas}"
    )

    # capReached must not repeat — at most 2 (one per stream: stdout, stderr)
    stdout_cap_deltas = [
        d for d in output_deltas
        if d.get("data", {}).get("stream") == "stdout" and d.get("data", {}).get("capReached")
    ]
    assert len(stdout_cap_deltas) == 1, (
        f"Expected exactly 1 stdout capReached delta, got {len(stdout_cap_deltas)}: {stdout_cap_deltas}"
    )

    # Final response must have stdoutCapReached
    assert "result" in resp, f"Expected result, got: {resp}"
    assert resp["result"].get("stdoutCapReached") is True, (
        f"Final response stdoutCapReached should be True, got: {resp['result']}"
    )


# ── Default timeout (Phase 43 hardening) ─────────────────────────────────


@pytest.mark.asyncio
async def test_command_exec_uses_default_timeout_when_omitted(server_and_registry):
    """When timeoutMs is omitted, DEFAULT_TIMEOUT_MS is passed to the runtime."""
    from unittest.mock import patch

    from miqi.runtime.workbench_process_runtime import DEFAULT_TIMEOUT_MS

    server, registry = server_and_registry
    wpr = registry.bridge_context["workbench_process_runtime"]

    with patch.object(wpr, "spawn", wraps=wpr.spawn) as mock_spawn:
        resp = await _dispatch(server, registry, "command/exec", {
            "command": ["python", "-c", "pass"],
            "processId": "cmd-default-timeout",
        })

    assert "result" in resp, f"Expected result, got: {resp}"
    call_kwargs = mock_spawn.call_args.kwargs
    assert call_kwargs["timeout_ms"] == DEFAULT_TIMEOUT_MS, (
        f"Expected timeout_ms={DEFAULT_TIMEOUT_MS}, got {call_kwargs['timeout_ms']}"
    )


@pytest.mark.asyncio
async def test_command_exec_uses_default_timeout_when_null(server_and_registry):
    """When timeoutMs is null, DEFAULT_TIMEOUT_MS is used (command/exec has no null-disable)."""
    from unittest.mock import patch

    from miqi.runtime.workbench_process_runtime import DEFAULT_TIMEOUT_MS

    server, registry = server_and_registry
    wpr = registry.bridge_context["workbench_process_runtime"]

    with patch.object(wpr, "spawn", wraps=wpr.spawn) as mock_spawn:
        resp = await _dispatch(server, registry, "command/exec", {
            "command": ["python", "-c", "pass"],
            "processId": "cmd-null-timeout",
            "timeoutMs": None,
        })

    assert "result" in resp, f"Expected result, got: {resp}"
    call_kwargs = mock_spawn.call_args.kwargs
    assert call_kwargs["timeout_ms"] == DEFAULT_TIMEOUT_MS, (
        f"Expected timeout_ms={DEFAULT_TIMEOUT_MS} for null timeoutMs, "
        f"got {call_kwargs['timeout_ms']}"
    )


@pytest.mark.asyncio
async def test_command_exec_rejects_negative_timeout(server_and_registry):
    """timeoutMs < 0 returns INVALID_PARAMS."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "pass"],
        "processId": "cmd-neg-timeout",
        "timeoutMs": -1,
    })
    assert "error" in resp, f"Expected error for negative timeoutMs, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS", (
        f"Expected INVALID_PARAMS, got: {resp.get('code')}"
    )


# ── Default output cap (Phase 43 hardening) ─────────────────────────────


@pytest.mark.asyncio
async def test_command_exec_uses_default_output_cap_when_omitted(server_and_registry):
    """When outputBytesCap is omitted, DEFAULT_OUTPUT_BYTES_CAP is passed to the runtime."""
    from unittest.mock import patch

    from miqi.runtime.workbench_process_runtime import DEFAULT_OUTPUT_BYTES_CAP

    server, registry = server_and_registry
    wpr = registry.bridge_context["workbench_process_runtime"]

    with patch.object(wpr, "spawn", wraps=wpr.spawn) as mock_spawn:
        resp = await _dispatch(server, registry, "command/exec", {
            "command": ["python", "-c", "pass"],
            "processId": "cmd-default-cap",
        })

    assert "result" in resp, f"Expected result, got: {resp}"
    call_kwargs = mock_spawn.call_args.kwargs
    assert call_kwargs["output_cap"] == DEFAULT_OUTPUT_BYTES_CAP, (
        f"Expected output_cap={DEFAULT_OUTPUT_BYTES_CAP}, got {call_kwargs['output_cap']}"
    )


@pytest.mark.asyncio
async def test_command_exec_rejects_null_output_cap(server_and_registry):
    """outputBytesCap=null returns INVALID_PARAMS."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "pass"],
        "processId": "cmd-null-cap",
        "outputBytesCap": None,
    })
    assert "error" in resp, f"Expected error for null outputBytesCap, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS", (
        f"Expected INVALID_PARAMS, got: {resp.get('code')}"
    )


@pytest.mark.asyncio
async def test_command_exec_rejects_negative_output_cap(server_and_registry):
    """outputBytesCap < 0 returns INVALID_PARAMS."""
    server, registry = server_and_registry

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", "pass"],
        "processId": "cmd-neg-cap",
        "outputBytesCap": -1,
    })
    assert "error" in resp, f"Expected error for negative outputBytesCap, got: {resp}"
    assert resp.get("code") == "INVALID_PARAMS", (
        f"Expected INVALID_PARAMS, got: {resp.get('code')}"
    )


# ── Zero output cap & capReached boundary (Phase 43 hardening) ──────────


@pytest.mark.asyncio
async def test_command_exec_zero_output_cap_immediate_cap_reached(server_and_registry):
    """outputBytesCap=0 allows execution but immediately triggers capReached for any output.

    This also verifies the capReached boundary fix: when buffer is already
    at cap (0 bytes) and the next chunk arrives, an empty capReached delta
    is emitted so the client sees exactly one notification.
    """
    server, registry = server_and_registry

    events_received: list = []

    async def fake_sink(envelope):
        events_received.append(envelope)

    server.set_event_sink("test-client", fake_sink)

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c",
                    "import sys; sys.stdout.buffer.write(b'A' * 100); sys.stdout.flush()"],
        "processId": "cmd-cap-zero",
        "streamStdoutStderr": True,
        "outputBytesCap": 0,
    })

    output_deltas = [
        e for e in events_received
        if e.get("event") == "command/exec/outputDelta"
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
    # With cap=0 the capReached delta carries empty data (buffer was at cap)
    assert stdout_cap_deltas[0]["data"]["deltaBase64"] == "", (
        f"Expected empty deltaBase64 for cap=0 capReached, "
        f"got: {stdout_cap_deltas[0]['data']['deltaBase64']!r}"
    )

    assert "result" in resp, f"Expected result, got: {resp}"
    assert resp["result"].get("stdoutCapReached") is True, (
        f"stdoutCapReached should be True with outputBytesCap=0, got: {resp['result']}"
    )
    assert resp["result"]["stdout"] == "", (
        f"stdout should be empty with outputBytesCap=0, got: {resp['result']['stdout']!r}"
    )


@pytest.mark.asyncio
async def test_command_exec_output_cap_boundary_fill_then_overflow(server_and_registry):
    """When output exactly fills the cap then more arrives, exactly one capReached delta.

    Writes exactly 20 bytes (filling cap), flushes, then writes 50 more bytes.
    Regardless of whether the OS delivers them as one read or two, the client
    must receive exactly one capReached=true notification on stdout.
    """
    server, registry = server_and_registry

    events_received: list = []

    async def fake_sink(envelope):
        events_received.append(envelope)

    server.set_event_sink("test-client", fake_sink)

    resp = await _dispatch(server, registry, "command/exec", {
        "command": ["python", "-c", (
            "import sys; "
            "sys.stdout.buffer.write(b'A' * 20); sys.stdout.flush(); "
            "sys.stdout.buffer.write(b'B' * 50); sys.stdout.flush()"
        )],
        "processId": "cmd-cap-boundary",
        "streamStdoutStderr": True,
        "outputBytesCap": 20,
    })

    output_deltas = [
        e for e in events_received
        if e.get("event") == "command/exec/outputDelta"
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

    assert "result" in resp, f"Expected result, got: {resp}"
    assert resp["result"].get("stdoutCapReached") is True, (
        f"stdoutCapReached should be True when output exceeds cap, "
        f"got: {resp['result']}"
    )


# ── Phase 63 typed validation regressions ───────────────────────────────


@pytest.mark.asyncio
async def test_command_exec_typed_validation_before_spawn(server_and_registry):
    from unittest.mock import AsyncMock

    server, registry = server_and_registry
    wpr = registry.bridge_context["workbench_process_runtime"]
    wpr.spawn = AsyncMock()

    resp = await _dispatch(server, registry, "command/exec", {
        "command": [],
        "processId": "bad-command",
    })

    assert resp["code"] == "INVALID_PARAMS"
    wpr.spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_exec_write_typed_validation_before_write(server_and_registry):
    from unittest.mock import AsyncMock

    server, registry = server_and_registry
    wpr = registry.bridge_context["workbench_process_runtime"]
    wpr.write_stdin = AsyncMock()
    wpr.close_stdin = AsyncMock()

    resp = await _dispatch(server, registry, "command/exec/write", {
        "processId": "cmd-1",
    })

    assert resp["code"] == "INVALID_PARAMS"
    wpr.write_stdin.assert_not_awaited()
    wpr.close_stdin.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_exec_terminate_typed_validation_before_kill(server_and_registry):
    from unittest.mock import AsyncMock

    server, registry = server_and_registry
    wpr = registry.bridge_context["workbench_process_runtime"]
    wpr.kill = AsyncMock()

    resp = await _dispatch(server, registry, "command/exec/terminate", {
        "processId": "../bad",
    })

    assert resp["code"] == "INVALID_PARAMS"
    wpr.kill.assert_not_awaited()
