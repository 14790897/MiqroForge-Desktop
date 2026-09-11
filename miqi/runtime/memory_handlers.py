"""Memory handlers for AppServer dispatch.

Phase 35.7: Migrates memory.list, memory.get, memory.update,
memory.delete, memory.lessons, and memory.lesson.unlearn from bridge
legacy handlers to AppServer async handlers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from miqi.runtime.app_server import AppServerError


def _get_memory_dir() -> Path:
    """Return the workspace memory directory."""
    import miqi.bridge.server as bridge_module

    state = getattr(bridge_module, "_state", None)
    if state is None:
        raise AppServerError("Bridge state not available", code="INTERNAL")
    config = state.load_config()
    return config.workspace_path / "memory"


def _validate_memory_path(file_path: str) -> Path:
    """Validate and resolve a memory file path, preventing directory traversal."""
    memory_dir = _get_memory_dir()
    resolved = (memory_dir / file_path).resolve()
    try:
        resolved.relative_to(memory_dir.resolve())
    except ValueError:
        raise AppServerError(
            f"Path escapes memory directory: {file_path}", code="INVALID_PARAMS",
        )
    return resolved


# ── memory.list ──────────────────────────────────────────────────────────────


async def memory_list_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """List memory files in workspace memory directory."""
    memory_dir = _get_memory_dir()
    files: list[dict[str, Any]] = []

    if memory_dir.exists():
        for f in sorted(memory_dir.glob("*.md")):
            files.append({
                "path": f.name,
                "scope": "workspace" if f.name != "MEMORY.md" else "agent",
                "size": f.stat().st_size,
                "updatedAtMs": int(f.stat().st_mtime * 1000),
            })
        mem_file = memory_dir / "MEMORY.md"
        if mem_file.exists() and "MEMORY.md" not in {f["path"] for f in files}:
            files.insert(0, {
                "path": "MEMORY.md",
                "scope": "agent",
                "size": mem_file.stat().st_size,
                "updatedAtMs": int(mem_file.stat().st_mtime * 1000),
            })

    return {"result": {"files": files}}


# ── memory.get ───────────────────────────────────────────────────────────────


async def memory_get_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Get memory file content."""
    file_path = params.get("path", "").strip()
    if not file_path:
        raise AppServerError("path is required", code="INVALID_PARAMS")

    resolved = _validate_memory_path(file_path)

    if not resolved.exists():
        raise AppServerError(
            f"File not found: {file_path}", code="NOT_FOUND",
        )

    content = resolved.read_text(encoding="utf-8")
    return {"result": {
        "path": file_path,
        "content": content,
        "size": len(content),
    }}


# ── memory.update ────────────────────────────────────────────────────────────


async def memory_update_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Update memory file content."""
    file_path = params.get("path", "").strip()
    content = params.get("content", "")
    if not file_path:
        raise AppServerError("path is required", code="INVALID_PARAMS")

    resolved = _validate_memory_path(file_path)

    if resolved.suffix not in (".md",):
        raise AppServerError(
            "Only .md files can be edited", code="INVALID_PARAMS",
        )

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return {"result": {"saved": True, "path": file_path}}


# ── memory.delete ────────────────────────────────────────────────────────────


async def memory_delete_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Delete a memory file."""
    file_path = params.get("path", "").strip()
    if not file_path:
        raise AppServerError("path is required", code="INVALID_PARAMS")

    resolved = _validate_memory_path(file_path)

    if not resolved.exists():
        raise AppServerError(
            f"File not found: {file_path}", code="NOT_FOUND",
        )

    if resolved.suffix not in (".md",):
        raise AppServerError(
            "Only .md files can be deleted", code="INVALID_PARAMS",
        )

    resolved.unlink()
    return {"result": {"deleted": True, "path": file_path}}


# ── memory.lessons ───────────────────────────────────────────────────────────


async def memory_lessons_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """List learned lessons from MemoryStore."""
    import miqi.bridge.server as bridge_module
    from miqi.agent.memory import MemoryStore

    state = getattr(bridge_module, "_state", None)
    if state is None:
        raise AppServerError("Bridge state not available", code="INTERNAL")
    config = state.load_config()
    memory = MemoryStore(
        workspace=config.workspace_path,
        self_improvement_enabled=config.agents.self_improvement.enabled,
        max_lessons=config.agents.self_improvement.max_lessons,
        min_lesson_confidence=config.agents.self_improvement.min_lesson_confidence,
        max_lessons_in_prompt=config.agents.self_improvement.max_lessons_in_prompt,
        lesson_stale_days=config.agents.self_improvement.lesson_stale_days,
        lesson_archive_days=config.agents.self_improvement.lesson_archive_days,
        feedback_max_message_chars=config.agents.self_improvement.feedback_max_message_chars,
        feedback_require_prefix=config.agents.self_improvement.feedback_require_prefix,
        promotion_enabled=config.agents.self_improvement.promotion_enabled,
        promotion_min_users=config.agents.self_improvement.promotion_min_users,
        promotion_triggers=config.agents.self_improvement.promotion_triggers,
    )
    lessons = memory.list_lessons(scope="all", limit=100, include_disabled=True)
    result = []
    for lesson in lessons:
        result.append({
            "id": str(lesson.get("id", "")),
            "trigger": str(lesson.get("trigger", "")),
            "badAction": str(lesson.get("bad_action", "")),
            "betterAction": str(lesson.get("better_action", "")),
            "scope": str(lesson.get("scope", "session")),
            "sessionKey": lesson.get("session_key"),
            "confidence": lesson.get("confidence", 0),
            "effectiveConfidence": lesson.get("effective_confidence", 0),
            "hits": lesson.get("hits", 0),
            "state": str(lesson.get("state", "active")),
            "enabled": lesson.get("enabled", True),
            "source": str(lesson.get("source", "")),
            "createdAt": str(lesson.get("created_at", "")),
            "updatedAt": str(lesson.get("updated_at", "")),
        })
    return {"result": {"lessons": result}}


# ── memory.lesson.unlearn ────────────────────────────────────────────────────


async def memory_lesson_unlearn_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Unlearn a lesson by ID."""
    from miqi.agent.memory import MemoryStore

    lesson_id = str(params.get("lesson_id", ""))
    if not lesson_id:
        raise AppServerError("lesson_id is required", code="INVALID_PARAMS")

    import miqi.bridge.server as bridge_module

    state = getattr(bridge_module, "_state", None)
    if state is None:
        raise AppServerError("Bridge state not available", code="INTERNAL")
    config = state.load_config()
    memory = MemoryStore(
        workspace=config.workspace_path,
        self_improvement_enabled=config.agents.self_improvement.enabled,
        max_lessons=config.agents.self_improvement.max_lessons,
        min_lesson_confidence=config.agents.self_improvement.min_lesson_confidence,
        max_lessons_in_prompt=config.agents.self_improvement.max_lessons_in_prompt,
        lesson_stale_days=config.agents.self_improvement.lesson_stale_days,
        lesson_archive_days=config.agents.self_improvement.lesson_archive_days,
        feedback_max_message_chars=config.agents.self_improvement.feedback_max_message_chars,
        feedback_require_prefix=config.agents.self_improvement.feedback_require_prefix,
        promotion_enabled=config.agents.self_improvement.promotion_enabled,
        promotion_min_users=config.agents.self_improvement.promotion_min_users,
        promotion_triggers=config.agents.self_improvement.promotion_triggers,
    )
    success = memory._lesson_store.unlearn_by_id(lesson_id)
    if success:
        memory.flush()
    return {"result": {"unlearned": [lesson_id] if success else []}}
