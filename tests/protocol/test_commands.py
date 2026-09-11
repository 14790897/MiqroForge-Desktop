"""Tests for miqi.protocol.commands."""

import json

from miqi.protocol.commands import (
    AbortTurn,
    ApprovalResponse,
    ConfigUpdate,
    ThreadCommand,
    UserMessage,
)


def test_user_message():
    msg = UserMessage(content="Hello, MiQi!")
    assert msg.type == "user_message"
    assert msg.content == "Hello, MiQi!"
    assert msg.thread_id is None


def test_user_message_with_thread():
    msg = UserMessage(content="Continue", thread_id="thread_abc")
    assert msg.thread_id == "thread_abc"


def test_user_message_with_media():
    msg = UserMessage(
        content="Check this image",
        media=[{"type": "image_url", "image_url": {"url": "https://example.com/img.png"}}],
    )
    assert len(msg.media) == 1


def test_approval_response():
    resp = ApprovalResponse(approval_id="appr_001", decision="allow")
    assert resp.type == "approval_response"
    assert resp.decision == "allow"


def test_approval_response_deny():
    resp = ApprovalResponse(approval_id="appr_002", decision="deny")
    assert resp.decision == "deny"


def test_abort_turn():
    cmd = AbortTurn(thread_id="thread_abc")
    assert cmd.type == "abort_turn"


def test_abort_turn_no_thread():
    cmd = AbortTurn()
    assert cmd.thread_id is None


def test_config_update():
    cmd = ConfigUpdate(path="permissions.filesystem", value={"default_mode": "read"})
    assert cmd.path == "permissions.filesystem"


def test_thread_command():
    cmd = ThreadCommand(action="new", thread_id="thread_abc")
    assert cmd.action == "new"


def test_thread_command_with_params():
    cmd = ThreadCommand(
        action="fork",
        thread_id="thread_abc",
        params={"agent_type": "code-agent"},
    )
    assert cmd.params["agent_type"] == "code-agent"


def test_json_roundtrip():
    msg = UserMessage(content="Test")
    from dataclasses import asdict
    data = json.loads(json.dumps(asdict(msg)))
    assert data["content"] == "Test"


def test_run_user_shell_command_carries_turn_metadata():
    from miqi.protocol.commands import RunUserShellCommand

    cmd = RunUserShellCommand(
        command="git status --short",
        thread_id="thread-1",
        turn_id="turn-shell-1",
        cwd="/workspace",
        standalone=True,
    )

    assert cmd.type == "run_user_shell_command"
    assert cmd.command == "git status --short"
    assert cmd.thread_id == "thread-1"
    assert cmd.turn_id == "turn-shell-1"
    assert cmd.cwd == "/workspace"
    assert cmd.standalone is True
