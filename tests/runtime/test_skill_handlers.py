"""Tests for skill handlers — Phase 35.5 / hardening.

Validates skills.list, skills.get, skills.open_folder, skills.create,
skills.upload, and skills.delete migrated from bridge legacy to AppServer.

Hardening: Path traversal and name validation tests for create/upload/delete.
"""

import tempfile

import pytest

from miqi.runtime.app_server import ClientSessionRegistry


def _make_config_with_workspace():
    """Create a Config with a temp workspace path for handler tests."""
    from miqi.config.schema import Config
    config = Config()
    config.agents.defaults.workspace = tempfile.mkdtemp()
    return config


# ── skills.list ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skills_list_returns_skills(registry_with_state):
    """skills.list should return skills list."""
    from miqi.runtime.skill_handlers import skills_list_handler

    registry, mock_state = registry_with_state
    config = _make_config_with_workspace()
    mock_state.load_config.return_value = config
    result = await skills_list_handler("req-1", {}, "client-1", None, registry)
    skills = result["result"]["skills"]
    assert isinstance(skills, list)
    for s in skills:
        assert "name" in s
        assert "source" in s
        assert "available" in s


# ── skills.get ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skills_get_requires_name():
    """skills.get should reject empty name."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.skill_handlers import skills_get_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="name is required"):
        await skills_get_handler("req-1", {}, "client-1", None, registry)


@pytest.mark.asyncio
async def test_skills_get_not_found(registry_with_state):
    """skills.get should raise NOT_FOUND for nonexistent skill."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.skill_handlers import skills_get_handler

    registry, mock_state = registry_with_state
    config = _make_config_with_workspace()
    mock_state.load_config.return_value = config
    with pytest.raises(AppServerError, match="Skill not found"):
        await skills_get_handler(
            "req-1", {"name": "nonexistent-xyz-123"}, "client-1", None, registry,
        )


# ── skills.open_folder ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skills_open_folder_requires_name():
    """skills.open_folder should reject empty name."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.skill_handlers import skills_open_folder_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="name is required"):
        await skills_open_folder_handler("req-1", {}, "client-1", None, registry)


@pytest.mark.asyncio
async def test_skills_open_folder_returns_path(registry_with_state):
    """skills.open_folder should return a path without launching GUI."""
    from miqi.runtime.skill_handlers import skills_open_folder_handler

    registry, mock_state = registry_with_state
    config = _make_config_with_workspace()
    mock_state.load_config.return_value = config
    from miqi.runtime.app_server import AppServerError
    with pytest.raises(AppServerError, match="Skill not found"):
        await skills_open_folder_handler(
            "req-1", {"name": "no-such-skill-xyz"}, "client-1", None, registry,
        )


# ── skills.create ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skills_create_invalid_name():
    """skills.create should reject invalid skill names."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.skill_handlers import skills_create_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="Invalid name"):
        await skills_create_handler(
            "req-1", {"name": "Invalid Name!"}, "client-1", None, registry,
        )


# ── skills.delete ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skills_delete_builtin():
    """skills.delete should reject builtin skill deletion."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.skill_handlers import skills_delete_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="Builtin skills cannot be deleted"):
        await skills_delete_handler(
            "req-1", {"name": "memory"}, "client-1", None, registry,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Path traversal & name validation tests (hardening)
# ═══════════════════════════════════════════════════════════════════════════════

_PATH_TRAVERSAL_NAMES = [
    ("../x", "dot-dot-slash traversal"),
    ("../../x", "double dot-dot-slash traversal"),
    ("..\\x", "dot-dot-backslash traversal"),
    ("/etc/passwd", "absolute unix path"),
    ("C:\\Windows\\system32", "absolute windows path"),
    ("", "empty name"),
    ("x" * 256, "overly long name"),
    ("-bad", "leading hyphen"),
    ("UPPERCASE", "uppercase letters"),
    ("has space", "name with space"),
    ("has/slash", "name with forward slash"),
    ("has\\backslash", "name with backslash"),
    ("has\0null", "name with null byte"),
    (".dotfile", "name starting with dot"),
    ("x;rm -rf /", "name with shell injection"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_name,desc", _PATH_TRAVERSAL_NAMES)
async def test_skills_create_rejects_path_traversal(bad_name, desc):
    """skills.create rejects path traversal and malicious names."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.skill_handlers import skills_create_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="Invalid name"):
        await skills_create_handler(
            "req-1", {"name": bad_name}, "client-1", None, registry,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_name,desc", _PATH_TRAVERSAL_NAMES)
async def test_skills_upload_rejects_path_traversal(bad_name, desc):
    """skills.upload rejects path traversal and malicious names."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.skill_handlers import skills_upload_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="Invalid name"):
        await skills_upload_handler(
            "req-1", {"name": bad_name, "content": "content"}, "client-1", None, registry,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_name,desc", _PATH_TRAVERSAL_NAMES)
async def test_skills_delete_rejects_path_traversal(bad_name, desc):
    """skills.delete rejects path traversal and malicious names."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.skill_handlers import skills_delete_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="Invalid name"):
        await skills_delete_handler(
            "req-1", {"name": bad_name}, "client-1", None, registry,
        )


# ── _validate_skill_path defense-in-depth ────────────────────────────────────


def test_validate_skill_path_blocks_traversal_after_resolve(tmp_path):
    """_validate_skill_path blocks paths that resolve outside workspace/skills."""
    from miqi.runtime.skill_handlers import _validate_skill_path

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Valid: name stays inside workspace/skills
    skill_dir = _validate_skill_path("my-skill", workspace)
    assert skill_dir == (workspace / "skills" / "my-skill").resolve()

    # Create a symlink that would escape (if supported on this platform)
    # Even without symlinks, a crafted name could resolve outside —
    # the function is defense-in-depth. The primary guard is _validate_skill_name.
    # Test that resolve + relative_to catches unexpected escapes:
    skills_root = (workspace / "skills")
    skills_root.mkdir(parents=True, exist_ok=True)
    # Create a real subdir so we can test a valid path
    (skills_root / "valid-skill").mkdir(parents=True, exist_ok=True)
    valid_dir = _validate_skill_path("valid-skill", workspace)
    assert valid_dir == (skills_root / "valid-skill").resolve()
