"""Tests for Codex-style turn AppServer handlers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.protocol.events import TurnCompleteEvent
from miqi.runtime.app_server import AppServer, ClientSessionRegistry


class _FakeRuntime:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.submissions = []
        self.services = MagicMock()
        self.services.thread_runtime = MagicMock()
        self.services.thread_runtime.get_thread = AsyncMock(return_value=MagicMock(thread_id="thread-1"))
        self.submit = AsyncMock(side_effect=self._submit)
        self.interrupt_turn = AsyncMock(return_value=True)
        self.steer_turn = AsyncMock(return_value=True)
        # Phase 41 hardening v2: per-thread turn reservation
        self._reservations: dict[str, str] = {}
        # Controlled event queue — drain tasks block here instead of hot-looping.
        # A bare AsyncMock for next_event causes infinite hot loops because
        # _drain_turn_events is `while True: await next_event(...)` and an
        # AsyncMock without return_value/side_effect returns a non-terminal
        # mock immediately.  The queue makes next_event block until a test
        # feeds a terminal event or the task is cancelled.
        self._drain_events: asyncio.Queue = asyncio.Queue()

    async def _submit(self, submission):
        self.submissions.append(submission)

    def active_turn_id(self, thread_id: str) -> str | None:
        return self._reservations.get(thread_id)

    async def try_reserve_turn(self, thread_id: str, turn_id: str) -> bool:
        if thread_id in self._reservations:
            return False
        self._reservations[thread_id] = turn_id
        return True

    async def release_turn_reservation(self, thread_id: str) -> None:
        self._reservations.pop(thread_id, None)

    async def next_event(self, timeout=None):
        """Block until a test feeds an event, or CancelledError on shutdown."""
        return await self._drain_events.get()

    def feed_event(self, event):
        """Feed a typed event into the drain loop (non-blocking)."""
        self._drain_events.put_nowait(event)


def _register_runtime(registry, runtime):
    registry._sessions[runtime.session_id] = runtime
    registry._client_sessions.setdefault("client-1", set()).add(runtime.session_id)
    registry._session_clients.setdefault(runtime.session_id, set()).add("client-1")
    registry._last_activity[runtime.session_id] = 0


# ── turn/start ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_start_returns_initial_turn_and_submits_user_message():
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "turn/start",
        {
            "threadId": "thread-1",
            "clientUserMessageId": "client-msg-1",
            "input": [{"type": "text", "text": "hello"}],
        },
        "client-1",
        runtime.session_id,
    )

    assert "result" in response, response
    turn = response["result"]["turn"]
    assert turn["threadId"] == "thread-1"
    assert turn["status"] == "inProgress"
    assert turn["items"] == []
    assert turn["error"] is None

    submission = runtime.submissions[0]
    assert submission.thread_id == "thread-1"
    assert submission.content == "hello"
    assert submission.input_items == [{"type": "text", "text": "hello"}]
    assert submission.client_user_message_id == "client-msg-1"
    assert submission.turn_id == turn["id"]

    await server.stop()


@pytest.mark.asyncio
async def test_turn_start_rejects_missing_input():
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-1", "input": []},
        "client-1",
        runtime.session_id,
    )

    assert response["code"] == "INVALID_PARAMS"
    await server.stop()


# ── turn/interrupt ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_interrupt_delegates_to_runtime_interrupt_turn():
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "turn/interrupt",
        {"threadId": "thread-1", "turnId": "turn-1"},
        "client-1",
        runtime.session_id,
    )

    assert response["result"] == {}
    runtime.interrupt_turn.assert_awaited_once_with(thread_id="thread-1", turn_id="turn-1")
    await server.stop()


@pytest.mark.asyncio
async def test_turn_interrupt_rejects_turn_mismatch():
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    runtime.interrupt_turn = AsyncMock(return_value=False)
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "turn/interrupt",
        {"threadId": "thread-1", "turnId": "wrong"},
        "client-1",
        runtime.session_id,
    )

    assert response["code"] == "INVALID_REQUEST"
    await server.stop()


# ── turn/steer ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_steer_delegates_to_runtime_steer_turn():
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "turn/steer",
        {
            "threadId": "thread-1",
            "expectedTurnId": "turn-1",
            "clientUserMessageId": "client-msg-2",
            "input": [{"type": "text", "text": "steer"}],
        },
        "client-1",
        runtime.session_id,
    )

    assert response["result"] == {"turnId": "turn-1"}
    runtime.steer_turn.assert_awaited_once()
    args = runtime.steer_turn.await_args.kwargs
    assert args["thread_id"] == "thread-1"
    assert args["expected_turn_id"] == "turn-1"
    assert args["content"] == "steer"
    assert args["client_user_message_id"] == "client-msg-2"
    await server.stop()


# ── thread/inject_items ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_inject_items_persists_history_and_ledger(tmp_path):
    from miqi.runtime.history_runtime import HistoryRuntime
    from miqi.runtime.ledger_runtime import LedgerRuntime

    db_path = tmp_path / "runtime.db"
    history = HistoryRuntime(db_path, session_id="client-1:default")
    ledger = LedgerRuntime(db_path, session_id="client-1:default")
    await history.initialize()
    await ledger.initialize()

    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    runtime.services.history_runtime = history
    runtime.services.ledger_runtime = ledger
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "thread/inject_items",
        {
            "threadId": "thread-1",
            "items": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "context"}],
                }
            ],
        },
        "client-1",
        runtime.session_id,
    )

    assert response["result"] == {}
    messages = await history.load_messages("thread-1")
    assert messages == [{"role": "assistant", "content": "context", "raw_item": {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "context"}],
    }}]
    ledger_messages = await ledger.load_provider_messages("thread-1")
    assert ledger_messages[0]["role"] == "assistant"
    assert ledger_messages[0]["content"] == "context"

    await history.close()
    await ledger.close()
    await server.stop()


# ── thread/compact/start ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_compact_start_returns_immediately_and_owns_background_task():
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    runtime.services.context_runtime = MagicMock()
    runtime.services.context_runtime.compact_thread = AsyncMock()
    runtime.services.history_runtime = MagicMock()
    from miqi.runtime.services import RuntimeModelSettings
    runtime.services.model_settings = RuntimeModelSettings(
        model="test-model",
        temperature=0.0,
        max_tokens=100,
        max_tool_result_chars=12000,
        context_limit_chars=600000,
    )
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "thread/compact/start",
        {"threadId": "thread-1"},
        "client-1",
        runtime.session_id,
    )

    assert response["result"] == {}
    assert server._background_tasks

    await server.stop()


# ── Phase 41 hardening: active-turn gate ──────────────────────────────────


@pytest.mark.asyncio
async def test_turn_start_rejects_when_active_turn_exists_for_thread():
    """Fix 1: turn/start must return INVALID_REQUEST when a turn is already running."""
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    # Pre-reserve the thread to simulate an active turn
    runtime._reservations["thread-1"] = "turn-running"
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-1", "input": [{"type": "text", "text": "hello"}]},
        "client-1",
        runtime.session_id,
    )

    assert response["code"] == "INVALID_REQUEST"
    assert not runtime.submissions  # No UserMessage submitted
    await server.stop()


# ── Phase 41 hardening: thread existence validation ──────────────────────


@pytest.mark.asyncio
async def test_turn_start_rejects_unknown_thread_id():
    """Fix 2: turn/start must return NOT_FOUND for unknown threadId."""
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    runtime.services.thread_runtime.get_thread = AsyncMock(return_value=None)
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-nonexistent", "input": [{"type": "text", "text": "hello"}]},
        "client-1",
        runtime.session_id,
    )

    assert response["code"] == "NOT_FOUND"
    assert not runtime.submissions  # No UserMessage submitted
    # Phase 41 hardening v2: reservation must be released on validation failure
    assert "thread-nonexistent" not in runtime._reservations
    await server.stop()


# ── Phase 41 hardening v2: per-thread reservation race-condition fix ─────


@pytest.mark.asyncio
async def test_concurrent_turn_start_same_thread_only_one_succeeds():
    """Two concurrent turn/start for the same threadId — exactly one wins."""
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    async def do_turn_start(req_id: str) -> dict:
        return await server.dispatch(
            req_id,
            "turn/start",
            {"threadId": "thread-1", "input": [{"type": "text", "text": "concurrent"}]},
            "client-1",
            runtime.session_id,
        )

    r1, r2 = await asyncio.gather(do_turn_start("req-1"), do_turn_start("req-2"))

    # Exactly one success
    success = [r for r in (r1, r2) if "result" in r]
    rejected = [r for r in (r1, r2) if r.get("code") == "INVALID_REQUEST"]
    assert len(success) == 1, f"Expected 1 success, got {len(success)}: {r1}, {r2}"
    assert len(rejected) == 1, f"Expected 1 INVALID_REQUEST, got {len(rejected)}: {r1}, {r2}"

    # Exactly one UserMessage submitted
    assert len(runtime.submissions) == 1
    assert runtime.submissions[0].content == "concurrent"

    # Only one drain background task created
    assert len(server._background_tasks) == 1, (
        f"Expected 1 drain task, got {len(server._background_tasks)}"
    )

    await server.stop()


@pytest.mark.asyncio
async def test_sequential_immediate_second_turn_start_rejected():
    """Second sequential turn/start (without waiting) must be rejected."""
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    # First turn/start — succeeds, sets reservation
    r1 = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-1", "input": [{"type": "text", "text": "first"}]},
        "client-1",
        runtime.session_id,
    )
    assert "result" in r1
    assert r1["result"]["turn"]["status"] == "inProgress"
    assert len(runtime.submissions) == 1

    # Second turn/start immediately — rejected by reservation
    r2 = await server.dispatch(
        "req-2",
        "turn/start",
        {"threadId": "thread-1", "input": [{"type": "text", "text": "second"}]},
        "client-1",
        runtime.session_id,
    )
    assert r2.get("code") == "INVALID_REQUEST", f"Expected INVALID_REQUEST, got {r2}"
    # Second request must NOT submit a UserMessage
    assert len(runtime.submissions) == 1
    # No second drain task
    assert len(server._background_tasks) == 1

    await server.stop()


@pytest.mark.asyncio
async def test_turn_start_succeeds_after_reservation_released():
    """After drain ends (releases reservation), a new turn/start succeeds."""
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    # First turn/start — succeeds, drain task blocks on empty queue
    r1 = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-1", "input": [{"type": "text", "text": "first"}]},
        "client-1",
        runtime.session_id,
    )
    assert "result" in r1
    assert len(runtime.submissions) == 1

    # Feed TurnCompleteEvent so the drain loop terminates naturally.
    # The drain's finally block will call release_turn_reservation.
    r1_turn_id = r1["result"]["turn"]["id"]
    runtime.feed_event(TurnCompleteEvent(
        turn_id=r1_turn_id,
        thread_id="thread-1",
        outcome="success",
    ))

    # Wait for the drain task to finish (it removes itself via done callback)
    for _ in range(100):
        if not server._background_tasks:
            break
        await asyncio.sleep(0.01)
    assert not server._background_tasks, "Drain task should have finished"

    # Reservation must be released now
    assert runtime.active_turn_id("thread-1") is None
    assert "thread-1" not in runtime._reservations

    # Second turn/start — must succeed because reservation was released
    r2 = await server.dispatch(
        "req-2",
        "turn/start",
        {"threadId": "thread-1", "input": [{"type": "text", "text": "second"}]},
        "client-1",
        runtime.session_id,
    )
    assert "result" in r2, f"Expected success, got {r2}"
    assert r2["result"]["turn"]["status"] == "inProgress"
    assert len(runtime.submissions) == 2

    await server.stop()


# ── Phase 41 hardening v2: regression — no hot loop ──────────────────────


@pytest.mark.asyncio
async def test_drain_task_blocks_not_hot_loops():
    """Regression: _FakeRuntime.next_event must block, not return bare mocks.

    A bare AsyncMock for next_event causes _drain_turn_events to never
    block and loop infinitely.  This test verifies that the drain task
    actually blocks on the event queue and that server.stop() safely
    cancels it without leaving orphan tasks.
    """
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    # Start a turn — creates a drain background task
    response = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-1", "input": [{"type": "text", "text": "regression"}]},
        "client-1",
        runtime.session_id,
    )
    assert "result" in response

    # The drain task should exist and be alive (blocked on queue)
    assert len(server._background_tasks) == 1
    drain_task = next(iter(server._background_tasks))
    assert not drain_task.done(), "Drain task should be blocked, not completed"

    # Verify we can feed an event without blocking
    runtime.feed_event(TurnCompleteEvent(
        turn_id=response["result"]["turn"]["id"],
        thread_id="thread-1",
        outcome="success",
    ))

    # Drain should finish after consuming the event
    for _ in range(100):
        if drain_task.done():
            break
        await asyncio.sleep(0.01)
    assert drain_task.done(), "Drain task should have completed"
    # background_tasks should be empty (auto-removed via done callback)
    assert not server._background_tasks

    # Reservation must be released after drain finishes
    assert runtime.active_turn_id("thread-1") is None

    # We should be able to start a second turn
    r2 = await server.dispatch(
        "req-2",
        "turn/start",
        {"threadId": "thread-1", "input": [{"type": "text", "text": "second"}]},
        "client-1",
        runtime.session_id,
    )
    assert "result" in r2

    # server.stop() must clean up the second drain task without hanging
    assert len(server._background_tasks) == 1
    await server.stop()
    assert not server._background_tasks, "All background tasks should be cleaned up"


# ── Phase 62 typed validation regressions ───────────────────────────────


@pytest.mark.asyncio
async def test_turn_start_typed_validation_happens_before_reservation():
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-1", "input": [{"type": "image", "url": "https://example.com/a.png"}]},
        "client-1",
        runtime.session_id,
    )

    assert response["code"] == "INVALID_PARAMS"
    assert runtime.submissions == []
    assert runtime._reservations == {}
    await server.stop()


@pytest.mark.asyncio
async def test_turn_interrupt_rejects_non_string_turn_id_before_runtime_call():
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "turn/interrupt",
        {"threadId": "thread-1", "turnId": 123},
        "client-1",
        runtime.session_id,
    )

    assert response["code"] == "INVALID_PARAMS"
    runtime.interrupt_turn.assert_not_awaited()
    await server.stop()


@pytest.mark.asyncio
async def test_turn_steer_rejects_invalid_input_before_runtime_call():
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "turn/steer",
        {"threadId": "thread-1", "expectedTurnId": "turn-1", "input": "bad"},
        "client-1",
        runtime.session_id,
    )

    assert response["code"] == "INVALID_PARAMS"
    runtime.steer_turn.assert_not_awaited()
    await server.stop()


@pytest.mark.asyncio
async def test_thread_inject_items_rejects_empty_items_before_writes():
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    runtime.services.history_runtime = MagicMock()
    runtime.services.ledger_runtime = MagicMock()
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    response = await server.dispatch(
        "req-1",
        "thread/inject_items",
        {"threadId": "thread-1", "items": []},
        "client-1",
        runtime.session_id,
    )

    assert response["code"] == "INVALID_PARAMS"
    runtime.services.history_runtime.append_message.assert_not_called()
    runtime.services.ledger_runtime.append_item.assert_not_called()
    await server.stop()


@pytest.mark.asyncio
async def test_turn_start_cancelled_submit_releases_reservation():
    """#488: a CancelledError during submit must not leak the reservation —
    `except BaseException` releases it, unlike the old `except Exception`
    which CancelledError bypasses."""
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    # Simulate the handler being cancelled while submitting the message.
    async def _cancelled_submit(submission):
        raise asyncio.CancelledError()

    runtime.submit = AsyncMock(side_effect=_cancelled_submit)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    with pytest.raises(asyncio.CancelledError):
        await server.dispatch(
            "req-1",
            "turn/start",
            {"threadId": "thread-1", "input": [{"type": "text", "text": "hi"}]},
            "client-1",
            runtime.session_id,
        )

    # Reservation must have been released on the cancelled path.
    assert runtime.active_turn_id("thread-1") is None
    assert "thread-1" not in runtime._reservations


@pytest.mark.asyncio
async def test_turn_start_background_task_failure_releases_reservation():
    """CodeRabbit #743: if drain background-task creation fails, the
    reservation must still be released so the thread isn't locked forever."""
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    # Make background-task creation fail (e.g. server shutting down).
    # dispatch() swallows handler exceptions into an error result, so no
    # exception propagates — but the reservation MUST be released either way.
    def _boom(*args, **kwargs):
        raise RuntimeError("server stopped")

    server.create_background_task = _boom

    result = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-1", "input": [{"type": "text", "text": "hi"}]},
        "client-1",
        runtime.session_id,
    )
    assert "error" in result or "exception" in str(result).lower()

    # Reservation released despite background-task failure.
    assert runtime.active_turn_id("thread-1") is None
    assert "thread-1" not in runtime._reservations


@pytest.mark.asyncio
async def test_turn_start_releases_reservation_on_base_exception():
    """#488: non-CancelledError BaseExceptions (e.g. KeyboardInterrupt while
    awaiting submit) must also release the reservation before re-raising."""
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")

    async def _interrupted_submit(_submission):
        raise KeyboardInterrupt()
    runtime.submit = AsyncMock(side_effect=_interrupted_submit)
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    with pytest.raises(KeyboardInterrupt):
        await server.dispatch(
            "req-1",
            "turn/start",
            {"threadId": "thread-1", "input": [{"type": "text", "text": "hello"}]},
            "client-1",
            runtime.session_id,
        )

    assert runtime._reservations == {}
    await server.stop()


@pytest.mark.asyncio
async def test_turn_start_closes_drain_coro_when_task_creation_fails():
    """#488: if create_background_task raises after drain_turn_events coroutine
    was created, the coroutine must be closed (no 'was never awaited' warning)
    and the reservation released."""
    registry = ClientSessionRegistry()
    runtime = _FakeRuntime("client-1:default")
    _register_runtime(registry, runtime)

    from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
    server = AppServer(registry)
    register_codex_turn_handlers(server)

    created_coro = []
    def _fail_create_background_task(coro, *, name=None):
        created_coro.append(coro)
        raise RuntimeError("event loop closing")

    server.create_background_task = _fail_create_background_task

    response = await server.dispatch(
        "req-1",
        "turn/start",
        {"threadId": "thread-1", "input": [{"type": "text", "text": "hello"}]},
        "client-1",
        runtime.session_id,
    )
    # dispatch converts the RuntimeError into an INTERNAL error envelope.
    assert response["code"] == "INTERNAL", response

    # The drain coroutine was created but never scheduled → must be closed.
    assert created_coro, "drain coroutine should have been created"
    assert created_coro[0].cr_frame is None, "drain coroutine was not closed"
    # Reservation must be released so a later turn/start can proceed.
    assert runtime._reservations == {}, (
        f"reservation leaked after task creation failure: {runtime._reservations}"
    )
    await server.stop()
