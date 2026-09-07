"""Capability resolution — computes the effective tool and plugin
capabilities for an agent before each turn.

Resolves agent-type-specific tool definitions, active plugin MCP
servers, and skill sets into a single RuntimeCapabilities snapshot
that is attached to TurnContext.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeCapabilities:
    """Resolved capabilities for a single agent turn."""

    tool_definitions: list[dict[str, Any]]
    skills: list[dict[str, Any]] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)


# User-configured MCP servers (config.tools.mcp_servers) register their tools
# into the ToolRegistry at session start with an `mcp_<server>_<tool>` name
# (or a `use_<server>` gateway when lazy).  The agent allowlist only covers
# built-in tools, so these prefixes bypass the allowlist filter — otherwise
# every user-configured MCP tool would be invisible to the model.
_MCP_TOOL_PREFIXES = ("mcp_", "use_")


class CapabilityResolver:
    """Resolves capabilities for a given agent type.

    Filters tool definitions by the agent's allowlist and collects
    active plugin MCP servers and skills from PluginManager.
    """

    def __init__(self, *, tool_registry: Any, plugin_manager: Any | None = None):
        self._tools = tool_registry
        self._plugins = plugin_manager

    def resolve(self, *, agent_metadata: Any) -> RuntimeCapabilities:
        """Compute the effective capabilities for an agent."""
        allowed = set(agent_metadata.available_tools)
        tool_definitions = []
        for spec in self._tools.get_definitions():
            name = spec.get("function", {}).get("name")
            if name in allowed or (
                isinstance(name, str) and name.startswith(_MCP_TOOL_PREFIXES)
            ):
                tool_definitions.append(spec)

        skills: list[dict[str, Any]] = []
        mcp_servers: list[dict[str, Any]] = []
        plugins: list[str] = []

        if self._plugins is not None:
            plugins = [
                p.manifest.name
                for p in self._plugins.list_plugins()
                if p.status == "active"
            ]
            mcp_servers = self._plugins.get_mcp_servers()

        return RuntimeCapabilities(
            tool_definitions=tool_definitions,
            skills=skills,
            mcp_servers=mcp_servers,
            plugins=plugins,
        )
