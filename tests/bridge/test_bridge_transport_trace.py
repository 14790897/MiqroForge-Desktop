"""#1036 transport trace — every segment of the Desktop→bridge request path
must leave a durable, greppable line.

The incident this guards (issue #1036): during one long turn a subset of the
Desktop's requests (`config.get` ×17, `plugins.list` ×3) ran out the full 720 s
client-side timeout while other requests on the same channel (`files.read` ×22)
were answered normally.  The loss can sit in any of four segments — main-side
write, bridge read, bridge reply, main-side match — and two of them left no
durable trace at all, which is why the incident could not be localised.

These tests pin the bridge half of the trace:

    stdin-read         the stdin reader thread took the line off the pipe
    stdin-enqueue      the loop thread put it on the queue (depth afterwards)
    stdin-recv         the drain loop took it off the queue
    dispatch-start     dispatch began (semaphore wait + slot occupancy)
    dispatch-done      the handler returned (elapsed + response byte count)
    bridge-resp sent   the reply line was written to stdout (byte count)

The main-process half (`bridge-req written`, `bridge-resp orphan`,
`bridge-stale-line dropped`, `Error processing stdout line`) is covered by
apps/desktop/src/main/bridge.test.ts.
"""

import asyncio
import contextlib
import io
import json
import os
import sys
from contextlib import contextmanager

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────


class _CaptureSend:
    """Capture _send() calls made by the loop."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    def send(self, data: dict) -> None:
        self.messages.append(data)


@contextmanager
def _loguru_records():
    """Collect the formatted message of every loguru record emitted inside."""
    from loguru import logger

    messages: list[str] = []

    def _sink(message) -> None:
        messages.append(message.record["message"])

    sink_id = logger.add(_sink, level="INFO")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


async def _make_loop(methods: dict | None = None):
    """A BridgeRuntimeLoop whose AppServer is initialized and handshaked.

    Mirrors test_bridge_loop.py's concurrency test: the connection state is
    pre-set to initialized so the dispatch path under test is the normal one.
    """
    from miqi.bridge.loop import BridgeRuntimeLoop
    from miqi.runtime.initialize_protocol import ConnectionState

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(send_func=capturer.send, dispatch_legacy_func=None)
    await loop._init_app_server()

    conn = ConnectionState()
    conn.client_id = "miqi-desktop"
    conn.initialized = True
    loop._connection_state = conn
    loop._stdin_queue = asyncio.Queue(maxsize=256)

    for name, handler in (methods or {}).items():
        loop.app_server._methods[name] = handler

    return loop, capturer


def _trace_lines(messages: list[str], prefix: str) -> list[str]:
    return [m for m in messages if m.startswith(prefix)]


async def _fast_handler(request_id, params, client_id, session_id, registry):
    return {"result": {"fast": True}}


# ── pure helpers ─────────────────────────────────────────────────────────


def test_peek_request_id_reads_string_id_without_full_parse():
    from miqi.bridge.loop import _peek_request_id

    raw = '{"id":"8f3a-2b","method":"config.get","params":{"session_key":"s:1"}}'
    assert _peek_request_id(raw) == "8f3a-2b"
    # Nothing to find (e.g. a garbled line) must not raise.
    assert _peek_request_id("not json at all") == "?"


def test_peek_line_request_falls_back_to_regex_on_bad_json():
    from miqi.bridge.loop import _peek_line_request

    assert _peek_line_request('{"id":"a1","method":"plugins.list"}') == ("a1", "plugins.list")
    # Broken JSON still yields the id (regex) but no method.
    assert _peek_line_request('{"id":"a2","method":"plugins.list"') == ("a2", "")


def test_response_bytes_matches_wire_encoding():
    from miqi.bridge.loop import _response_bytes

    response = {"request_id": "r1", "result": {"中文": True}}
    expected = len((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
    assert _response_bytes(response) == expected


def test_response_bytes_never_raises_on_unserialisable():
    from miqi.bridge.loop import _response_bytes

    assert _response_bytes({"result": object()}) == -1


# ── _send: bridge-resp sent ──────────────────────────────────────────────


def test_send_traces_response_envelopes_only(monkeypatch):
    """Responses are traced; events and the ready handshake are not."""
    from miqi.bridge import server as bridge_server

    # Keep the protocol write out of the real stdout.
    monkeypatch.setattr(bridge_server, "_stdout_buffer", io.BytesIO())

    response = {"request_id": "resp-1", "result": {"ok": True}}
    line_len = len((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))

    with contextlib.redirect_stderr(io.StringIO()) as err:
        bridge_server._send(response)
        bridge_server._send({"id": "evt-1", "type": "progress", "data": {}})
        bridge_server._send({"id": "evt-2", "event": "progress", "data": {}})
        bridge_server._send({"type": "ready"})
        bridge_server._send({"id": "err-1", "error": "boom", "code": "X"})

    lines = [ln for ln in err.getvalue().splitlines() if "bridge-resp sent" in ln]
    assert len(lines) == 2, lines
    assert "id=resp-1" in lines[0]
    assert f"bytes={line_len}" in lines[0]
    assert "id=err-1" in lines[1]
    assert all(f"pid={bridge_server._BRIDGE_PID}" in ln for ln in lines)


def test_is_response_envelope_discriminator():
    from miqi.bridge.server import _is_response_envelope

    assert _is_response_envelope({"request_id": "x", "result": {}})
    assert _is_response_envelope({"id": "x", "error": "boom"})
    assert not _is_response_envelope({"id": "x", "type": "final", "data": {}})
    # `event` is accepted by the receive side too — must be excluded here.
    assert not _is_response_envelope({"id": "x", "event": "final", "data": {}})
    assert not _is_response_envelope({"type": "ready"})


# ── _dispatch_one_line: dispatch-start / dispatch-done ───────────────────


@pytest.mark.asyncio
async def test_dispatch_one_line_traces_start_and_done():
    async def echo_handler(request_id, params, client_id, session_id, registry):
        return {"result": {"echo": params.get("msg", "")}}

    loop, capturer = await _make_loop({"test.echo": echo_handler})

    line = json.dumps({"id": "req-a", "method": "test.echo", "params": {"msg": "你好"}})
    with _loguru_records() as messages:
        await loop._dispatch_one_line(line, queued_ms=12.5, inflight=3)

    starts = _trace_lines(messages, "dispatch-start")
    dones = _trace_lines(messages, "dispatch-done")
    assert len(starts) == 1, messages
    assert len(dones) == 1, messages
    assert "id=req-a" in starts[0] and "method=test.echo" in starts[0]
    # #1036 期望行为 2B: the semaphore wait and the slot occupancy are logged.
    assert "queued_ms=12.5" in starts[0]
    assert "inflight=3" in starts[0]
    assert "id=req-a" in dones[0] and "elapsed_ms=" in dones[0]

    # resp_bytes must equal the size of the response actually written.
    sent = capturer.messages[-1]
    expected_bytes = len((json.dumps(sent, ensure_ascii=False) + "\n").encode("utf-8"))
    assert f"resp_bytes={expected_bytes}" in dones[0]


@pytest.mark.asyncio
async def test_dispatch_one_line_traces_initialize_special_case():
    """The initialize fast path goes through the same trace (both call sites)."""
    from miqi.runtime.initialize_protocol import ConnectionState

    loop, _ = await _make_loop()
    # A fresh connection makes the initialize handshake gate the path taken.
    loop._connection_state = ConnectionState()
    loop._init_lock = asyncio.Lock()

    line = json.dumps({"id": "req-init", "method": "initialize", "params": {}})
    with _loguru_records() as messages:
        await loop._dispatch_one_line(line)

    assert any("dispatch-start" in m and "method=initialize" in m for m in messages), messages
    assert any("dispatch-done" in m and "id=req-init" in m for m in messages), messages


# ── _run: bridge-start + the ready handshake's pid ───────────────────────


@pytest.mark.asyncio
async def test_run_traces_bridge_start_and_announces_its_pid(monkeypatch):
    """The trace is joined across both logs by pid.

    On Windows a venv's python.exe is a launcher that re-execs the real
    interpreter, so `child.pid` (what Electron spawned) is NOT what
    os.getpid() reports inside the bridge.  The handshake therefore has to
    carry the bridge's own pid, and `bridge-start` opens each generation.
    """
    from miqi.bridge.loop import _BRIDGE_PID, BridgeRuntimeLoop

    capturer = _CaptureSend()
    loop = BridgeRuntimeLoop(send_func=capturer.send, dispatch_legacy_func=None)

    async def _noop(*_args, **_kwargs):
        return None

    # Stub everything except the trace/handshake under test.
    monkeypatch.setattr(loop, "_init_app_server", _noop)
    monkeypatch.setattr(loop, "_setup_event_sink", lambda: None)
    monkeypatch.setattr(loop, "_publish_app_server", lambda: None)
    monkeypatch.setattr(loop, "_init_sandbox_manager", _noop)
    monkeypatch.setattr(loop, "_drain_loop", _noop)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    with _loguru_records() as messages:
        await loop._run()
        # Let the reader thread hit EOF and finish before stdin is restored.
        for _ in range(50):
            await asyncio.sleep(0.01)

    assert _BRIDGE_PID == os.getpid()
    starts = _trace_lines(messages, "bridge-start")
    assert len(starts) == 1, messages
    assert f"pid={_BRIDGE_PID}" in starts[0]

    ready = [m for m in capturer.messages if m.get("type") == "ready"]
    assert len(ready) == 1, capturer.messages
    assert ready[0]["pid"] == _BRIDGE_PID


# ── _drain_loop: stdin-recv + semaphore accounting ───────────────────────

@pytest.mark.asyncio
async def test_drain_loop_traces_stdin_recv_with_id_and_method():
    loop, capturer = await _make_loop({"test.fast": _fast_handler})
    await loop._stdin_queue.put(json.dumps({"id": "req-r", "method": "test.fast", "params": {}}))
    await loop._stdin_queue.put(None)

    with _loguru_records() as messages:
        await loop._drain_loop()

    recvs = _trace_lines(messages, "stdin-recv")
    assert len(recvs) == 1, messages
    assert "id=req-r" in recvs[0]
    assert "method=test.fast" in recvs[0]
    assert "qsize=" in recvs[0]
    # …and the request still reached the handler.
    assert capturer.messages


@pytest.mark.asyncio
async def test_dispatch_start_reports_slot_occupancy():
    loop, _ = await _make_loop({"test.fast": _fast_handler})
    await loop._stdin_queue.put(json.dumps({"id": "q1", "method": "test.fast", "params": {}}))
    await loop._stdin_queue.put(None)

    with _loguru_records() as messages:
        await loop._drain_loop()

    starts = _trace_lines(messages, "dispatch-start")
    assert len(starts) == 1, messages
    assert "inflight=1" in starts[0]
    assert "queued_ms=" in starts[0]


# ── _stdin_reader: stdin-read / stdin-enqueue ────────────────────────────


def test_stdin_reader_traces_read_and_enqueue():
    """The reader thread's hand-off is traced on both sides of the queue.

    Without `stdin-read`, "the line never reached the bridge" and "it reached
    the bridge but the loop never ran the put" share one signature: the thread
    drops run_coroutine_threadsafe's Future and swallows its exceptions.
    """
    from miqi.bridge.loop import _BRIDGE_PID, BridgeRuntimeLoop

    raw = json.dumps({"id": "req-s", "method": "config.get", "params": {}})

    async def scenario():
        capturer = _CaptureSend()
        loop = BridgeRuntimeLoop(send_func=capturer.send, dispatch_legacy_func=None)
        loop._loop = asyncio.get_running_loop()
        loop._stdin_queue = asyncio.Queue(maxsize=256)

        original_stdin = sys.stdin
        sys.stdin = io.StringIO(raw + "\n")
        try:
            with _loguru_records() as messages:
                loop._stdin_reader()  # returns at EOF; queues the line + sentinel
                # Let the enqueue coroutines run on this loop.
                for _ in range(5):
                    await asyncio.sleep(0)
        finally:
            sys.stdin = original_stdin
        return messages, loop

    messages, loop = asyncio.run(scenario())

    reads = _trace_lines(messages, "stdin-read")
    enqueues = _trace_lines(messages, "stdin-enqueue")
    assert len(reads) == 1, messages
    assert "id=req-s" in reads[0]
    assert f"len={len(raw)}" in reads[0]
    assert "ctr=1" in reads[0]
    assert all(f"pid={_BRIDGE_PID}" in m for m in reads + enqueues)

    assert len(enqueues) == 1, messages
    assert "id=req-s" in enqueues[0]
    assert "qsize=1" in enqueues[0]
    # The line really landed on the queue (nothing was silently dropped).
    assert loop._stdin_queue.qsize() == 2  # request + EOF sentinel
