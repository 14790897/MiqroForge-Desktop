"""Tests for ReplayRuntime wiring into RuntimeServices and RuntimeSession (Phase 25.3)."""

import pytest

from miqi.protocol.commands import UserMessage
from miqi.protocol.events import TurnCompleteEvent

# ── RuntimeServices has replay_runtime ────────────────────────────────────


def test_runtime_services_has_replay_runtime(fake_config, fake_provider, tmp_path):
    from miqi.runtime.replay_runtime import ReplayRuntime
    from miqi.runtime.services import RuntimeServices

    services = RuntimeServices.from_config(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-replay-wire",
        workspace=tmp_path,
    )

    assert services.replay_runtime is not None
    assert isinstance(services.replay_runtime, ReplayRuntime)


# ── RuntimeSession list_turns ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_list_turns(fake_config, fake_provider, tmp_path):
    from miqi.runtime.session import RuntimeSession

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-list-turns",
        workspace=tmp_path,
    )
    await runtime.start()
    try:
        # Run a turn to populate the ledger
        await runtime.submit(UserMessage(content="hello", thread_id="thread-replay"))
        while True:
            event = await runtime.next_event(timeout=2)
            if isinstance(event, TurnCompleteEvent):
                break

        turns = await runtime.list_turns("thread-replay")
        assert len(turns) == 1
        assert turns[0].startswith("turn-") or len(turns[0]) > 0
    finally:
        await runtime.stop()


# ── RuntimeSession get_turn_replay ────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_get_turn_replay(fake_config, fake_provider, tmp_path):
    from miqi.runtime.replay_runtime import TurnTimeline
    from miqi.runtime.session import RuntimeSession

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-turn-replay",
        workspace=tmp_path,
    )
    await runtime.start()
    try:
        await runtime.submit(UserMessage(content="hello", thread_id="thread-replay"))
        turn_id = None
        while True:
            event = await runtime.next_event(timeout=2)
            if isinstance(event, TurnCompleteEvent):
                turn_id = event.turn_id
                break

        assert turn_id is not None
        timeline = await runtime.get_turn_replay("thread-replay", turn_id)
        assert isinstance(timeline, TurnTimeline)
        assert timeline.turn_id == turn_id
        assert timeline.status == "completed"
        assert timeline.user_input == "hello"
    finally:
        await runtime.stop()


# ── RuntimeSession get_provider_messages ──────────────────────────────────


@pytest.mark.asyncio
async def test_session_get_provider_messages(fake_config, fake_provider, tmp_path):
    from miqi.runtime.session import RuntimeSession

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-provider-msgs",
        workspace=tmp_path,
    )
    await runtime.start()
    try:
        await runtime.submit(UserMessage(content="hello", thread_id="thread-msgs"))
        while True:
            event = await runtime.next_event(timeout=2)
            if isinstance(event, TurnCompleteEvent):
                break

        messages = await runtime.get_provider_messages("thread-msgs")
        assert len(messages) >= 2  # at least user + assistant
        roles = [m["role"] for m in messages]
        assert "user" in roles
        assert "assistant" in roles
    finally:
        await runtime.stop()


# ── RuntimeSession replay API with empty thread ───────────────────────────


@pytest.mark.asyncio
async def test_session_replay_empty_thread(fake_config, fake_provider, tmp_path):
    from miqi.runtime.session import RuntimeSession

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-empty",
        workspace=tmp_path,
    )
    await runtime.start()
    try:
        turns = await runtime.list_turns("nonexistent")
        assert turns == []

        timeline = await runtime.get_turn_replay("nonexistent", "any-turn")
        assert timeline is None

        msgs = await runtime.get_provider_messages("nonexistent")
        assert msgs == []
    finally:
        await runtime.stop()
