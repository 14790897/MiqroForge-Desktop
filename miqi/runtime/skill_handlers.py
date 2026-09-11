"""Skill handlers for AppServer dispatch.

Phase 35.5: Migrates skills.list, skills.get, skills.open_folder,
skills.create, skills.upload, and skills.delete from bridge legacy
handlers to AppServer async handlers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from miqi.runtime.app_server import AppServerError, get_bridge_context, get_bridge_state

_SKILL_NAME_RE = re.compile(r'^[a-z][a-z0-9-]*$')


_SKILL_NAME_MAX_LENGTH = 64


def _validate_skill_name(name: str) -> str:
    """Validate and normalize a skill name for create/upload/delete.

    Returns the stripped name if valid, raises AppServerError otherwise.
    This guards against path traversal (../, absolute paths) and illegal
    characters in skill names.
    """
    name = name.strip()
    if not name or not _SKILL_NAME_RE.match(name) or len(name) > _SKILL_NAME_MAX_LENGTH:
        raise AppServerError(
            "Invalid name — use lowercase letters, digits, hyphens",
            code="INVALID_PARAMS",
        )
    return name


def _validate_skill_path(name: str, workspace_path: Any) -> Path:
    """Resolve a skill directory path and enforce it stays within workspace/skills.

    Args:
        name: Validated skill name (must pass _validate_skill_name first).
        workspace_path: The workspace root path.

    Returns:
        Resolved Path to the skill directory.

    Raises:
        AppServerError: If the resolved path escapes workspace/skills.
    """
    skills_root = (workspace_path / "skills").resolve()
    skill_dir = (skills_root / name).resolve()
    try:
        skill_dir.relative_to(skills_root)
    except ValueError:
        raise AppServerError(
            "Skill path escapes workspace", code="INVALID_PARAMS",
        )
    return skill_dir


def _get_skills_loader(registry: Any) -> Any:
    """Get a SkillsLoader for the current workspace via registry DI."""
    state = get_bridge_state(registry)
    config = state.load_config()
    from miqi.agent.skills import SkillsLoader
    return SkillsLoader(workspace=config.workspace_path)


async def _invalidate_and_notify(client_id: str, registry: Any, name: str) -> None:
    """Invalidate the shared skill index and notify clients after a mutation.

    Aligns with ``_extra_roots_set`` (skills_app_handlers.py), which emits the
    same ``skills/changed`` event. create/upload/delete previously neither
    invalidated the process-level index (#859) nor notified the frontend.
    """
    from miqi.agent.skills import invalidate_skill_index

    state = get_bridge_state(registry)
    config = state.load_config()
    invalidate_skill_index(config.workspace_path)

    app_server = get_bridge_context(registry, "app_server")
    if app_server is not None:
        await app_server.emit_client_event(
            client_id,
            "skills/changed",
            {"name": name},
        )


async def skills_list_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """List all skills with availability info and missing requirements."""
    loader = _get_skills_loader(registry)
    all_skills = loader.list_skills(filter_unavailable=False)
    result = []
    for s in all_skills:
        meta = loader._get_skill_meta(s["name"])
        desc = loader._get_skill_description(s["name"])
        available = loader._check_requirements(meta)
        missing = loader._get_missing_requirements(meta) if not available else None
        result.append({
            "name": s["name"],
            "source": s["source"],
            "path": s["path"],
            "description": desc,
            "available": available,
            "missingRequirements": missing,
        })
    result.sort(key=lambda x: (0 if x["available"] else 1, x["name"]))
    return {"result": {"skills": result}}


async def skills_get_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Get detailed skill info with content and metadata."""
    name = params.get("name", "").strip()
    if not name:
        raise AppServerError("name is required", code="INVALID_PARAMS")

    loader = _get_skills_loader(registry)
    content = loader.load_skill(name)
    if content is None:
        raise AppServerError(f"Skill not found: {name}", code="NOT_FOUND")

    skill_info = None
    for s in loader.list_skills(filter_unavailable=False):
        if s["name"] == name:
            skill_info = s
            break

    meta = loader._get_skill_meta(name)
    available = loader._check_requirements(meta)
    missing = loader._get_missing_requirements(meta) if not available else None
    metadata = loader.get_skill_metadata(name)

    return {"result": {
        "name": name,
        "source": skill_info["source"] if skill_info else "unknown",
        "path": skill_info["path"] if skill_info else "",
        "description": loader._get_skill_description(name),
        "available": available,
        "missingRequirements": missing,
        "content": content,
        "metadata": metadata,
    }}


async def skills_open_folder_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Return the skill's folder path for the Desktop to open.

    This handler does NOT launch a GUI app — it returns a structured
    result with the folder path that the Desktop can act on.
    """
    name = params.get("name", "").strip()
    if not name:
        raise AppServerError("name is required", code="INVALID_PARAMS")

    loader = _get_skills_loader(registry)
    skill_path = loader.get_skill_path(name)
    if skill_path is None:
        raise AppServerError(f"Skill not found: {name}", code="NOT_FOUND")

    folder = str(skill_path.parent if skill_path.is_file() else skill_path)
    return {"result": {"opened": True, "path": folder}}


async def skills_create_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Create a blank workspace skill."""
    name = _validate_skill_name(str(params.get("name", "")))
    description = str(params.get("description", "")).strip()

    state = get_bridge_state(registry)
    config = state.load_config()

    skill_dir = _validate_skill_path(name, config.workspace_path)
    if skill_dir.exists():
        raise AppServerError(
            f"Skill '{name}' already exists", code="INVALID_PARAMS",
        )

    skill_dir.mkdir(parents=True)
    template = (
        f"---\n"
        f"name: {name}\n"
        f"description: {description or 'A new skill'}\n"
        f'version: "1.0"\n'
        f"---\n\n"
        f"# {name}\n\n{description or 'A new skill'}\n"
    )
    (skill_dir / "SKILL.md").write_text(template, encoding="utf-8")
    await _invalidate_and_notify(client_id, registry, name)
    return {"result": {"ok": True, "path": str(skill_dir)}}


async def skills_upload_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Save uploaded YAML content as a new workspace skill."""
    name = _validate_skill_name(str(params.get("name", "")))
    content = str(params.get("content", "")).strip()
    if not content:
        raise AppServerError(
            "content is required", code="INVALID_PARAMS",
        )

    state = get_bridge_state(registry)
    config = state.load_config()

    skill_dir = _validate_skill_path(name, config.workspace_path)
    if skill_dir.exists():
        raise AppServerError(
            f"Skill '{name}' already exists", code="INVALID_PARAMS",
        )

    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    await _invalidate_and_notify(client_id, registry, name)
    return {"result": {"ok": True}}


async def skills_delete_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Delete a workspace skill. Builtin skills cannot be deleted."""
    import shutil as _shutil

    name = _validate_skill_name(str(params.get("name", "")))

    # Check builtin first (before path validation since builtins live elsewhere)
    builtin_dir = Path(__file__).parent.parent / "skills"
    if (builtin_dir / name).exists():
        raise AppServerError(
            "Builtin skills cannot be deleted", code="INVALID_PARAMS",
        )

    state = get_bridge_state(registry)
    config = state.load_config()

    skill_dir = _validate_skill_path(name, config.workspace_path)
    if not skill_dir.exists():
        raise AppServerError(
            f"Skill '{name}' not found in workspace", code="NOT_FOUND",
        )

    _shutil.rmtree(skill_dir)
    await _invalidate_and_notify(client_id, registry, name)
    return {"result": {"ok": True}}
