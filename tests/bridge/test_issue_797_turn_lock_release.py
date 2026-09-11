"""Tests for issue #797 — turn lock must be released promptly on abort.

Bug: clicking stop (chat.abort) only submits AbortTurn to the runtime; the
bridge-side turn lock (`_session_drain_tasks`) is released only when the
drain task COMPLETES, i.e. when it reads a terminal event from the session
queue.  If the runtime is stuck on a blocking tool call (WSL subprocess
ignores asyncio cancellation) no terminal event arrives, so subsequent
chat.send calls are rejected with TURN_IN_PROGRESS ("上一个任务还在进行中")
until the 300s stale-turn guard fires.

Fix: chat.abort RELEASES the lock — pops the drain task from
`_session_drain_tasks` WITHOUT cancelling it.  The detached drain keeps
draining in the background (consuming the old turn's eventual terminal
event), and the next chat.send's drain first awaits that released
predecessor so a stale terminal event can never be misread as the NEW
request's terminal.
"""

import asyncio

import pytest

from miqi.bridge.loop import BridgeRuntimeLoop
from miqi.protocol.events import TurnAbortedEvent, TurnCompleteEvent, TurnStartedEvent


class _FakeAppServer:
    def __init__(self):
        self.emitted: list[tuple[str, dict, str]] = []

    async def emit_event(self, session_id, event_type, data, request_id=None):
        self.emitted.append(
            (event_type, dict(data) if isinstance(data, dict) else data, session_id)
        )


class _QueueRuntime:
    """Blocks on next_event until the test pushes an event."""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    async def next_event(self, timeout=None):
        return await self.queue.get()


def _make_loop():
    sent: list[dict] = []

    def send(msg):
        sent.append(msg)

    loop = BridgeRuntimeLoop(send_func=send)
    loop._app_server = _FakeAppServer()
    return loop, sent


def _terminals(sent, request_id: str) -> list[dict]:
    """Wire lines (terminal events) emitted for *request_id*."""
    return [m for m in sent if m.get("id") == request_id]


def _spawn_drain(loop, request_id: str, runtime):
    return asyncio.create_task(
        loop._drain_chat_events(
            request_id=request_id,
            runtime=runtime,
            thread_id="sess",
            session_id="c:sess",
            client_id="c",
            session_key="sess",
        )
    )


# ── release_turn_lock mechanics ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_release_turn_lock_moves_drain_without_cancelling():
    """The released drain is popped from the session lock map but NOT
    cancelled — it keeps draining so it can consume the old turn's
    terminal event (and emit it for the OLD request)."""
    loop, sent = _make_loop()
    runtime = _QueueRuntime()
    drain = _spawn_drain(loop, "req-old", runtime)
    await asyncio.sleep(0)  # let the drain block in next_event
    loop._session_drain_tasks["c:sess"] = drain
    loop._active_chat_tasks["req-old"] = drain

    assert loop.release_turn_lock("c:sess") is True
    assert "c:sess" not in loop._session_drain_tasks
    assert loop._released_drain_tasks["c:sess"] is drain
    assert not drain.cancelled()

    # The chat.send guard sees no active drain → no TURN_IN_PROGRESS.
    old = loop._session_drain_tasks.get("c:sess")
    assert old is None

    # The detached drain still forwards the old turn's terminal event
    # (with the OLD request id) and then ends on its own.
    runtime.queue.put_nowait(TurnAbortedEvent(
        turn_id="t-old", thread_id="sess", reason="user",
    ))
    await asyncio.wait_for(asyncio.wait([drain]), timeout=5)
    assert not drain.cancelled()
    terminals = _terminals(sent, "req-old")
    assert [t["type"] for t in terminals] == ["aborted"]
    assert "c:sess" not in loop._released_drain_tasks  # self-cleaned


@pytest.mark.asyncio
async def test_release_turn_lock_noop_without_active_drain():
    """Releasing an idle session is a no-op (no lock to free)."""
    loop, _ = _make_loop()
    assert loop.release_turn_lock("c:sess") is False
    assert "c:sess" not in loop._session_drain_tasks
    assert "c:sess" not in loop._released_drain_tasks


@pytest.mark.asyncio
async def test_release_turn_lock_idempotent():
    """A second release (double abort) is a no-op and keeps the first
    released drain draining."""
    loop, _ = _make_loop()
    runtime = _QueueRuntime()
    drain = _spawn_drain(loop, "req-old", runtime)
    await asyncio.sleep(0)
    loop._session_drain_tasks["c:sess"] = drain

    assert loop.release_turn_lock("c:sess") is True
    assert loop.release_turn_lock("c:sess") is False
    assert loop._released_drain_tasks["c:sess"] is drain

    runtime.queue.put_nowait(TurnCompleteEvent(
        turn_id="t-old", thread_id="sess", outcome="success",
    ))
    await asyncio.wait_for(asyncio.wait([drain]), timeout=5)


# ── new drain vs released predecessor ───────────────────────────────────


@pytest.mark.asyncio
async def test_new_drain_waits_for_released_predecessor():
    """#797 regression: while the aborted turn is still draining in the
    background (released, not cancelled), a new chat.send must NOT consume
    the old turn's terminal event as its own.  The new drain awaits the
    released predecessor first; the old event is emitted for the OLD
    request and the new request streams normally."""
    loop, _ = _make_loop()

    # Attach a recording send so we can inspect wire lines by request id.
    recorded: list[dict] = []

    def record_send(msg):
        recorded.append(msg)

    loop._send = record_send

    runtime = _QueueRuntime()

    # Old turn: drain is alive (stuck on a WSL tool call) then released
    # by chat.abort.
    pred = _spawn_drain(loop, "req-old", runtime)
    await asyncio.sleep(0)
    loop._session_drain_tasks["c:sess"] = pred
    loop._active_chat_tasks["req-old"] = pred
    assert loop.release_turn_lock("c:sess") is True

    # User immediately sends a new message → new drain starts.
    new = _spawn_drain(loop, "req-new", runtime)
    await asyncio.sleep(0)

    # The old turn's cleanup finally emits its terminal event.  It must go
    # to the OLD (released) drain and be emitted for the OLD request.
    runtime.queue.put_nowait(TurnAbortedEvent(
        turn_id="t-old", thread_id="sess", reason="user",
    ))
    await asyncio.wait_for(asyncio.wait([pred]), timeout=5)

    # The NEW drain must not have emitted any terminal yet.
    new_terminals = [m for m in recorded if m.get("id") == "req-new"]
    assert new_terminals == [], f"new drain wrongly emitted terminal: {new_terminals}"
    old_terminals = [m for m in recorded if m.get("id") == "req-old"]
    assert [t["type"] for t in old_terminals] == ["aborted"]

    # The new turn runs and completes normally.
    runtime.queue.put_nowait(TurnStartedEvent(
        turn_id="t-new", agent_name="a", thread_id="sess",
    ))
    runtime.queue.put_nowait(TurnCompleteEvent(
        turn_id="t-new", thread_id="sess", outcome="success",
    ))
    await asyncio.wait_for(asyncio.wait([new]), timeout=5)
    new_terminals = [m for m in recorded if m.get("id") == "req-new"]
    assert [t["type"] for t in new_terminals] == ["final"]
