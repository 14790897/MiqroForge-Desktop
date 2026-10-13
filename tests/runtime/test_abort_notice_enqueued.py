"""AbortTurn's informational notice is produced on exactly one path.

``TaskRunner.handle(AbortTurn)`` puts an informational ``ErrorEvent``
("Turn aborted for thread X") on the shared session queue, tagged
``ABORT_NOTICE_ERROR_KIND``.  Untagged it was adoptable as the NEXT
chat.send's terminal, so a turn that ran to completion was reported as a
failure (#797 follow-up).  The drain half of the fix is covered by
``tests/bridge/test_issue_797_turn_lock_release.py``.

This file covers the half that *produces* the notice, which until now could
only be read off production logs — the drain tests inject the notice by hand:

* no active turn → ``RuntimeSession._run`` calls ``_runner.handle`` → notice
* turn running   → the cancellation bypass handles the abort inline and
                   ``_runner.handle`` is never reached → no notice at all

That asymmetry is why the bug is rare: it needs a stop pressed *after* the
turn it targets has already ended.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from miqi.protocol.commands import AbortTurn
from miqi.protocol.events import ABORT_NOTICE_ERROR_KIND, ErrorEvent
from miqi.runtime.session import RuntimeSession


def _make_session() -> RuntimeSession:
    """A real RuntimeSession over the smallest services ``_run`` touches.

    ``orchestrator`` / ``ledger_runtime`` are explicit ``None`` rather than a
    MagicMock: ``TaskRunner.handle(AbortTurn)`` reads ``services.orchestrator``
    directly, and a MagicMock would hand back a non-awaitable from the
    ``cancel_approvals_for_thread`` probe instead of skipping it.  ``None``
    also short-circuits ``next_event``'s ledger mirroring.
    """
    return RuntimeSession(services=SimpleNamespace(
        session_id="c:sess",
        orchestrator=None,
        ledger_runtime=None,
    ))


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_abort_with_no_active_turn_enqueues_tagged_notice() -> None:
    """Stopping with nothing running still leaves a terminal-shaped event
    behind — now tagged, so the next chat.send's drain can skip it."""
    session = _make_session()
    dispatch = asyncio.create_task(session._run())
    try:
        await session.submit(AbortTurn(thread_id="sess"))
        notice = await asyncio.wait_for(session._events.get(), timeout=2)
    finally:
        await _cancel(dispatch)

    assert isinstance(notice, ErrorEvent), notice
    assert notice.error_kind == ABORT_NOTICE_ERROR_KIND, (
        "the notice lost its tag — the drain would adopt it as the next "
        "chat.send's terminal"
    )
    assert notice.message == "Turn aborted for thread sess."


@pytest.mark.asyncio
async def test_abort_during_active_turn_enqueues_no_notice() -> None:
    """The mirror image, and the reason the bug is rare: while a turn is
    running the abort goes through the cancellation bypass, which never
    reaches ``TaskRunner.handle`` — nothing lands on the queue."""
    session = _make_session()
    running = asyncio.create_task(asyncio.sleep(30))
    session._active_turn_task = running
    dispatch = asyncio.create_task(session._run())
    try:
        await session.submit(AbortTurn(thread_id="sess"))
        # The bypass cancels the running turn; that cancellation is the signal
        # that the abort was processed.
        await asyncio.wait_for(asyncio.wait([running]), timeout=2)
        assert running.cancelled(), "abort did not cancel the running turn"

        leftover = []
        while not session._events.empty():
            leftover.append(session._events.get_nowait())
        assert not leftover, f"notice enqueued on the active-turn path: {leftover}"
    finally:
        if not running.done():
            await _cancel(running)
        await _cancel(dispatch)
