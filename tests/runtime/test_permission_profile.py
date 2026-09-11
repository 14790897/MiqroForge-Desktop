"""Tests for PermissionProfile (Phase 13.5)."""

from pathlib import Path


def test_permission_profile_defaults():
    from miqi.runtime.permission_profile import PermissionProfile

    profile = PermissionProfile(workspace=Path("/tmp/ws"))
    assert profile.filesystem_mode == "workspace-write"
    assert profile.network == "restricted"
    assert profile.allow_exec is True
    assert profile.permanent_allowlist == set()


def test_permission_profile_custom_values():
    from miqi.runtime.permission_profile import PermissionProfile

    profile = PermissionProfile(
        workspace=Path("/tmp/ws"),
        filesystem_mode="workspace-readonly",
        network="none",
        allow_exec=False,
        permanent_allowlist={"git", "python"},
    )
    assert profile.filesystem_mode == "workspace-readonly"
    assert profile.network == "none"
    assert profile.allow_exec is False
    assert "git" in profile.permanent_allowlist


def test_permission_profile_attaches_to_turn_context():
    from miqi.runtime.agent_registry import AgentMetadata
    from miqi.runtime.permission_profile import PermissionProfile
    from miqi.runtime.turn_context import TurnContext

    profile = PermissionProfile(
        workspace=Path("/tmp/ws"),
        network="restricted",
    )

    metadata = AgentMetadata(
        name="test-agent",
        display_name="Test Agent",
        description="Test",
        system_prompt="You are a test agent.",
        available_tools=["read_file"],
    )

    turn = TurnContext(
        turn_id="turn-1",
        agent_metadata=metadata,
        thread_id="thread-1",
        workspace=Path("/tmp/ws"),
        model="test-model",
        provider=None,
        permission_profile=profile,
    )

    assert turn.permission_profile is profile
    assert turn.permission_profile.workspace == Path("/tmp/ws")
    assert turn.permission_profile.network == "restricted"
