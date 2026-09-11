"""Tests for RuntimeSession MCP connection (Phase MCP integration).

Verifies that a session connects configured ``tools.mcp_servers`` at
``start()``, registers the server's tools into the session registry, and
tears the connection down at ``stop()``.  Uses scripts/mock_mcp_server.py
(stdio FastMCP server) for a real MCP subprocess round-trip.
"""

from pathlib import Path

import pytest

from miqi.config.schema import MCPServerConfig

SERVER_SCRIPT = str(
    Path(__file__).resolve().parents[2] / "scripts" / "mock_mcp_server.py"
)
ECHO_MARKER = "MCP_ECHO_RESULT_7f3a9c"
WRAPPED_TOOL = "mcp_e2emcp_e2e_echo"


@pytest.mark.asyncio
async def test_session_connects_mcp_server_and_registers_tools(
    fake_config, fake_provider, tmp_path
):
    import sys

    from miqi.runtime.session import RuntimeSession

    fake_config.tools.mcp_servers["e2emcp"] = MCPServerConfig(
        command=sys.executable,
        args=[SERVER_SCRIPT],
        tool_timeout=30,
    )

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-mcp-connect",
        workspace=tmp_path,
    )
    await runtime.start()
    try:
        registry = runtime.services.tool_registry
        assert registry.has(WRAPPED_TOOL), (
            "MCP tool should be registered: "
            f"{registry.tool_names}"
        )

        # Real subprocess round-trip: execute the wrapper, which drives the
        # MCP client → stdio server → back.
        tool = registry.get(WRAPPED_TOOL)
        result = await tool.execute(text="pytest-probe")
        assert ECHO_MARKER in result, result
        assert "pytest-probe" in result, result
    finally:
        await runtime.stop()

    # stop() releases the connection tasks (terminates the stdio subprocess)
    assert runtime._mcp_keep_alive is None
    assert runtime._mcp_tasks == []


@pytest.mark.asyncio
async def test_session_skips_mcp_when_no_servers_configured(
    fake_config, fake_provider, tmp_path
):
    from miqi.runtime.session import RuntimeSession

    fake_config.tools.mcp_servers = {}

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-mcp-none",
        workspace=tmp_path,
    )
    await runtime.start()
    try:
        assert runtime._mcp_keep_alive is None
        assert runtime._mcp_connected is True  # guard set even with no servers
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_session_start_survives_broken_mcp_server(
    fake_config, fake_provider, tmp_path
):
    """A failing MCP server must never block session startup."""
    from miqi.runtime.session import RuntimeSession

    fake_config.tools.mcp_servers["broken"] = MCPServerConfig(
        command=str(tmp_path / "no-such-binary"),
        args=[],
        tool_timeout=10,
    )

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-mcp-broken",
        workspace=tmp_path,
    )
    # Must not raise despite the connection failure
    await runtime.start()
    try:
        registry = runtime.services.tool_registry
        assert not any(
            name.startswith("mcp_broken_") for name in registry.tool_names
        )
    finally:
        await runtime.stop()
