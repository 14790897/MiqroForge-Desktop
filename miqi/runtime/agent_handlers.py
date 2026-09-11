"""Agent handlers for AppServer dispatch.

Phase 28.5: Migrates agent.list and agent.get from bridge legacy
handlers (which used the dead _state._agent_control pointer) to
AppServer async handlers that use the same RuntimeSession.services.agent_control
as agent.spawn and agent.kill.

This unifies the agent.* handler family under a single data source.
"""

from __future__ import annotations

from typing import Any

from miqi.runtime.app_server import AppServerError


async def agent_list_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """List all agents for the client's session.

    Uses the same AgentControl instance as agent.spawn/kill
    (via RuntimeSession.services.agent_control).
    """
    if session_id is None:
        raise AppServerError(
            "session_id is required for agent.list",
            code="INVALID_PARAMS",
        )

    runtime = await registry.get_session(client_id, session_id)
    if runtime is None:
        if registry.session_exists(session_id):
            raise AppServerError(
                "Not authorized",
                code="UNAUTHORIZED",
            )
        # Desktop calls agent.list before any session exists — return empty
        return {"result": {"agents": []}}

    ac = getattr(runtime.services, "agent_control", None)
    if ac is None:
        return {"result": {"agents": []}}

    agents = ac.list_agents()
    return {"result": {"agents": agents}}


async def agent_get_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Get detailed information about an agent.

    Uses the same AgentControl instance as agent.spawn/kill.
    """
    if session_id is None:
        raise AppServerError(
            "session_id is required for agent.get",
            code="INVALID_PARAMS",
        )

    runtime = await registry.get_session(client_id, session_id)
    if runtime is None:
        raise AppServerError(
            "Not authorized",
            code="UNAUTHORIZED",
        )

    agent_id = params.get("agent_id", "")
    if not agent_id:
        raise AppServerError("agent_id is required", code="INVALID_PARAMS")

    ac = getattr(runtime.services, "agent_control", None)
    if ac is None:
        raise AppServerError(
            "Agent control not initialized",
            code="INTERNAL",
        )

    try:
        detail = ac.get_agent_detail(agent_id)
        return {"result": {"agent": detail}}
    except KeyError:
        raise AppServerError(
            f"Unknown agent: {agent_id}",
            code="NOT_FOUND",
        )
