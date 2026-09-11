"""Experience handlers for AppServer dispatch.

Phase 35.7: Migrates experience:list, experience:delete,
experience:toggle, and experience:search from bridge legacy handlers
to AppServer async handlers. Note: experience uses colons (:) not
dots (.) to match legacy naming convention.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from miqi.runtime.app_server import AppServerError


def _get_experience_store() -> Any:
    """Get or create the ExperienceStore singleton."""
    import miqi.bridge.server as bridge_module

    # Check for existing singleton in bridge state
    state = getattr(bridge_module, "_state", None)
    if state is not None:
        existing = getattr(bridge_module, "_experience_store", None)
        if existing is not None:
            return existing

    if state is None:
        raise AppServerError("Bridge state not available", code="INTERNAL")

    from miqi.agent.memory import MemoryStore
    from miqi.agent.memory.experience_store import ExperienceStore
    from miqi.agent.trace.store import TraceStore

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
        lessons_legacy_inject_enabled=config.agents.self_improvement.lessons_legacy_inject_enabled,
    )
    trace = TraceStore(
        workspace=config.workspace_path,
        enabled=config.agents.self_improvement.trace_enabled,
        embedding_model=config.agents.self_improvement.embedding_model,
        recover=False,
    )
    store = ExperienceStore(memory_store=memory, trace_store=trace)
    bridge_module._experience_store = store
    return store


def _cleanup_experience_store() -> None:
    """Close the cached ExperienceStore singleton and release resources.

    Safe to call when no store has been created (no-op).
    Called during bridge shutdown to ensure TraceStore SQLite
    connections are properly closed.
    """
    import miqi.bridge.server as bridge_module

    store = getattr(bridge_module, "_experience_store", None)
    if store is not None and hasattr(store, "close"):
        store.close()
    bridge_module._experience_store = None


async def experience_list_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """List experience entries with optional type/scope/session filters."""
    entry_type = params.get("type")
    scope = params.get("scope")
    session_key = params.get("session_key")
    limit = int(params.get("limit", 100))

    store = _get_experience_store()
    entries = store.list_entries(
        type=entry_type, scope=scope,
        session_key=session_key, limit=limit,
    )
    return {"result": {"entries": entries}}


async def experience_delete_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Delete an experience entry."""
    entry_type = params.get("type", "")
    entry_id = params.get("id", "")
    if not entry_type or not entry_id:
        raise AppServerError(
            "type and id are required", code="INVALID_PARAMS",
        )

    store = _get_experience_store()
    ok = store.delete_entry(entry_type, entry_id)
    return {"result": {"ok": ok}}


async def experience_toggle_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Toggle an experience entry enabled/disabled."""
    entry_type = params.get("type", "")
    entry_id = params.get("id", "")
    enabled = bool(params.get("enabled", False))

    store = _get_experience_store()
    ok = store.toggle_entry(entry_type, entry_id, enabled)
    return {"result": {"ok": ok}}


async def experience_search_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Search experience entries."""
    query = str(params.get("query", ""))
    entry_type = params.get("type")
    limit = int(params.get("limit", 10))

    store = _get_experience_store()
    try:
        entries = store.search_entries(query, type=entry_type, limit=limit)
        return {"result": {"entries": entries}}
    except Exception as exc:
        # Sanitize: log full details, return fixed message
        logger.warning("experience.search error: {}", exc)
        raise AppServerError(
            "Search failed — try a different query or check the index",
            code="INTERNAL",
        ) from exc
