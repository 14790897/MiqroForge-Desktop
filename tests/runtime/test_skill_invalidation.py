"""Tests for skill mutation invalidation + change notification (#859)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.config.schema import Config


def _make_config(workspace: Path) -> Config:
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    return config


@pytest.mark.asyncio
async def test_skills_create_emits_changed_and_invalidates(registry_with_state, tmp_path):
    """skills.create writes the skill, emits skills/changed, and invalidates."""
    from miqi.runtime.skill_handlers import skills_create_handler

    registry, mock_state = registry_with_state
    mock_state.load_config.return_value = _make_config(tmp_path)

    mock_app_server = MagicMock()
    mock_app_server.emit_client_event = AsyncMock()
    registry.bridge_context["app_server"] = mock_app_server

    await skills_create_handler(
        "req-1", {"name": "demo-skill", "description": "Demo"},
        "client-1", None, registry,
    )

    # Skill file written to workspace/skills.
    assert (tmp_path / "skills" / "demo-skill" / "SKILL.md").exists()

    # Change event emitted to the client.
    mock_app_server.emit_client_event.assert_awaited_once()
    args, _kwargs = mock_app_server.emit_client_event.call_args
    assert args[0] == "client-1"
    assert args[1] == "skills/changed"
    assert args[2] == {"name": "demo-skill"}


@pytest.mark.asyncio
async def test_skills_delete_emits_changed(registry_with_state, tmp_path):
    """skills.delete removes the skill and emits skills/changed."""
    from miqi.runtime.skill_handlers import skills_delete_handler

    registry, mock_state = registry_with_state
    mock_state.load_config.return_value = _make_config(tmp_path)

    # Pre-create the workspace skill to delete.
    skill_dir = tmp_path / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\n", encoding="utf-8")

    mock_app_server = MagicMock()
    mock_app_server.emit_client_event = AsyncMock()
    registry.bridge_context["app_server"] = mock_app_server

    await skills_delete_handler(
        "req-1", {"name": "demo-skill"}, "client-1", None, registry,
    )

    assert not skill_dir.exists()
    mock_app_server.emit_client_event.assert_awaited_once()
    args, _kwargs = mock_app_server.emit_client_event.call_args
    assert args[1] == "skills/changed"
    assert args[2] == {"name": "demo-skill"}
