"""Tests for CLI one-shot via RuntimeSession (Phase 11.4)."""

import pytest


@pytest.mark.asyncio
async def test_cli_one_shot_uses_runtime_session(fake_config, fake_provider):
    """CLI one-shot returns result from RuntimeSession."""
    from miqi.cli.agent_cmd import _run_agent_once_via_runtime

    result = await _run_agent_once_via_runtime(
        fake_config,
        fake_provider,
        "hello",
        "cli:default",
    )

    assert result == "done"


@pytest.mark.asyncio
async def test_cli_one_shot_marks_approval_channel_unavailable(
    fake_config, fake_provider, monkeypatch,
):
    """CLI one-shot must not create approvals that nobody can resolve."""
    from miqi.cli.agent_cmd import _run_agent_once_via_runtime
    from miqi.protocol.events import AgentMessageEvent
    from miqi.runtime.session import RuntimeSession

    captured: dict = {}

    class FakeRuntime:
        async def start(self):
            pass

        async def stop(self):
            pass

        async def submit(self, _submission):
            pass

        async def next_event(self, timeout=None):
            return AgentMessageEvent(
                turn_id="turn-1",
                content="done",
                finish_reason="stop",
            )

    def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeRuntime()

    monkeypatch.setattr(RuntimeSession, "create", fake_create)

    result = await _run_agent_once_via_runtime(
        fake_config,
        fake_provider,
        "hello",
        "cli:default",
    )

    assert result == "done"
    assert captured["approval_channel_available"] is False
