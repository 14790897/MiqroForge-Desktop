"""Minimal stdio MCP server used by the desktop E2E spec (mcp.spec.ts).

Exposes a single tool, ``e2e_echo``, which returns a distinctive marker
string.  The E2E mock LLM (scripts/mock_mcp.py) calls this tool through
MiQroForge's MCP client and only reports success when the marker comes
back — proving the full chain:

    registry → orchestrator → MCP client (stdio subprocess) → server

The server is spawned with the same interpreter as the bridge (repo venv),
so the ``mcp`` SDK import always resolves.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

MCP = FastMCP("e2e-mock-mcp")

# Distinctive marker the E2E mock LLM asserts on.  Do not reuse elsewhere.
ECHO_MARKER = "MCP_ECHO_RESULT_7f3a9c"


@MCP.tool()
def e2e_echo(text: str) -> str:
    """Echo the given text prefixed with an E2E marker."""
    return f"{ECHO_MARKER}:{text}"


if __name__ == "__main__":
    MCP.run(transport="stdio")
