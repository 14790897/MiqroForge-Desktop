"""User input gate for KUN runtime — pause execution for interactive questions.

Aligns with KUN ``ports/user-input-gate.ts`` and ``adapters/in-memory-user-input-gate.ts``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable


class UserInputRequest:
    """A request for user input (e.g. clarifying questions)."""

    def __init__(
        self,
        input_id: str,
        thread_id: str,
        turn_id: str,
        item_id: str,
        prompt: str,
        questions: list[dict[str, Any]] | None = None,
        remember_key: str | None = None,
        choices: list[dict[str, Any]] | None = None,
    ):
        self.id = input_id
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.item_id = item_id
        self.prompt = prompt
        self.questions = questions or []
        # Card choices ({id,label,role?}) so resolve() can annotate the
        # answer with the semantic choice_role (issue #646 review).
        self.choices = choices or []
        # Session-level remember key (issue #646): when the user checks
        # "本次会话不再询问" the resolved choice is stored under this key.
        self.remember_key = remember_key
        self._event = asyncio.Event()
        self._resolution: dict[str, Any] | None = None

    @property
    def resolved(self) -> bool:
        return self._resolution is not None

    @property
    def resolution(self) -> dict[str, Any] | None:
        return self._resolution

    def resolve(self, answers: dict[str, str] | None = None) -> None:
        if self._resolution is not None:
            return
        self._resolution = {
            "status": "submitted",
            "answers": answers or {},
        }
        self._event.set()

    def cancel(self) -> None:
        if self._resolution is not None:
            return
        self._resolution = {"status": "cancelled"}
        self._event.set()

    async def wait(self, timeout: float | None = None) -> dict[str, Any]:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # A resolve() that landed just before the timer expired already
            # set _resolution — overwriting it would discard the user's
            # submitted choice and report cancelled (CodeRabbit #711).
            if self._resolution is None:
                self._resolution = {"status": "cancelled"}
        return self._resolution or {"status": "cancelled"}


class _TurnSlot:
    """Per-turn serialization slot (issue #714 follow-up).

    At most one request per turn holds the slot at a time; concurrent
    requests wait until release() re-signals. ``cancel()`` wakes every
    waiter with a cancelled outcome so a turn abort never leaves a queued
    card to surface after termination.

    All state transitions run synchronously on the single-threaded event
    loop, so the held/waiters bookkeeping is race-free by construction.
    """

    def __init__(self) -> None:
        self._free = asyncio.Event()
        self._free.set()  # the slot starts available
        self._held = False
        self._waiters = 0
        self._cancelled = False

    async def acquire(self) -> bool:
        """Wait for the slot. Returns False when the turn was cancelled."""
        self._waiters += 1
        try:
            while not self._cancelled:
                await self._free.wait()
                if self._cancelled:
                    break
                if not self._held:
                    self._held = True
                    self._free.clear()
                    return True
                # Another waiter grabbed the slot first — go back to sleep.
            return False
        finally:
            self._waiters -= 1

    def release(self) -> None:
        self._held = False
        self._free.set()

    def cancel(self) -> None:
        self._cancelled = True
        self._free.set()  # wakes every waiter; they observe _cancelled

    @property
    def idle(self) -> bool:
        """No holder and no waiter — safe for the gate to drop the entry."""
        return not self._held and self._waiters == 0


class UserInputGate:
    """Manages interactive user input requests during agent execution."""

    def __init__(self) -> None:
        self._pending: dict[str, UserInputRequest] = {}
        # Per-turn FIFO serialization (issue #714 follow-up): turn_id → slot.
        # Requests for the same turn wait their turn instead of stacking
        # concurrent live cards; entries are dropped when idle.
        self._turn_slots: dict[str, _TurnSlot] = {}
        # Session-level remember (issue #646): thread_id → remember_key → choice
        self._remembered: dict[str, dict[str, dict[str, Any]]] = {}

    def remembered_choice(self, thread_id: str, key: str) -> dict[str, Any] | None:
        """Return a remembered choice for this thread+key, or None."""
        return self._remembered.get(thread_id, {}).get(key)

    def remember(self, thread_id: str, key: str, answers: dict[str, Any]) -> None:
        """Persist a choice for this thread+key (session-scoped, not permanent)."""
        self._remembered.setdefault(thread_id, {})[key] = dict(answers)

    async def request(
        self,
        thread_id: str,
        turn_id: str,
        item_id: str,
        prompt: str,
        questions: list[dict[str, Any]] | None = None,
        timeout: float | None = None,
        input_id: str | None = None,
        remember_key: str | None = None,
        choices: list[dict[str, Any]] | None = None,
        on_pending: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Submit a user input request and wait for resolution.

        Args:
            timeout: Optional seconds to wait before auto-cancelling.
                None means wait indefinitely (e.g. CLI interactive).
            input_id: Optional explicit request id (must match the id already
                announced to the caller/desktop). Defaults to a fresh one.
            choices: Card choices so resolve() can annotate the answer with
                the semantic choice_role.
            on_pending: Optional async callback invoked once the request
                actually becomes pending (acquired its per-turn slot). The
                caller announces the card to the desktop here, so queued
                requests never surface a card before their turn.
        """
        if input_id is None:
            input_id = f"user_input_{uuid.uuid4().hex[:12]}"
        # At most one pending request per turn (issue #714 follow-up): a
        # concurrent request for the same turn QUEUES behind the active one
        # instead of stacking or being rejected. When the active card
        # resolves, the next queued request becomes pending and on_pending
        # fires so the desktop shows exactly one interactive card at a time.
        slot = self._turn_slots.setdefault(turn_id, _TurnSlot())
        acquired = await slot.acquire()
        if not acquired:
            # The turn was cancelled while this request waited in the queue.
            # The last cancelled waiter drops the now-unused slot entry.
            if slot.idle:
                self._turn_slots.pop(turn_id, None)
            return {
                "status": "cancelled",
                "reason": "turn cancelled while the request was queued",
            }
        try:
            req = UserInputRequest(
                input_id=input_id,
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=item_id,
                prompt=prompt,
                questions=questions,
                remember_key=remember_key,
                choices=choices,
            )
            self._pending[input_id] = req
            if on_pending is not None:
                await on_pending()
            try:
                return await req.wait(timeout=timeout)
            finally:
                self._pending.pop(input_id, None)
        finally:
            slot.release()
            # Drop the per-turn entry once nobody uses it, so long-lived
            # gate instances don't accumulate one slot per finished turn
            # (CodeRabbit #718).
            if slot.idle:
                self._turn_slots.pop(turn_id, None)

    def resolve(self, input_id: str, answers: dict[str, str] | None = None, remember: bool = False) -> bool:
        """Resolve a pending user input request. Returns True if it existed.

        When *remember* is True and the request carries a remember key, the
        submitted choice is stored for this thread so the same card is
        auto-resolved on later calls (issue #646).
        """
        req = self._pending.get(input_id)
        if req is None:
            return False
        if answers and req.choices:
            # Annotate the answer with the semantic choice_role so tool
            # results can classify cancel/adjust without hard-coding ids
            # (issue #646 review).
            cid = str(answers.get("choice_id", ""))
            for c in req.choices:
                if isinstance(c, dict) and str(c.get("id", "")) == cid:
                    role = c.get("role")
                    if role:
                        answers = dict(answers)
                        answers["choice_role"] = str(role)
                    break
        req.resolve(answers)
        if remember and req.remember_key and answers:
            self.remember(req.thread_id, req.remember_key, dict(answers))
        return True

    def cancel_all(self, turn_id: str) -> None:
        """Cancel all pending AND queued input requests for a turn.

        Queued requests (waiting for their per-turn slot) resolve as
        cancelled without ever becoming pending or announcing a card, so a
        turn abort never leaves a card to surface after termination
        (CodeRabbit #718).
        """
        for req in list(self._pending.values()):
            if req.turn_id == turn_id:
                req.cancel()
                self._pending.pop(req.id, None)
        slot = self._turn_slots.get(turn_id)
        if slot is not None:
            slot.cancel()
            if slot.idle:
                self._turn_slots.pop(turn_id, None)

    def get_pending(self, turn_id: str | None = None) -> list[UserInputRequest]:
        """Return pending input requests, optionally filtered by turn."""
        if turn_id is None:
            return list(self._pending.values())
        return [r for r in self._pending.values() if r.turn_id == turn_id]

    def pending_request(self, input_id: str) -> UserInputRequest | None:
        """Return the pending request for *input_id*, or None."""
        return self._pending.get(input_id)

    @property
    def pending_count(self) -> int:
        return len(self._pending)
