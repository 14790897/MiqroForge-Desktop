"""Tests for RuntimeSession MCP connection (Phase MCP integration).

Verifies that a session connects configured ``tools.mcp_servers`` at
``start()``, registers the server's tools into the session registry, and
tears the connection down at ``stop()``.  Uses scripts/mock_mcp_server.py
(stdio FastMCP server) for a real MCP subprocess round-trip.
"""

from pathlib import Path

import pytest

from miqi.config.schema import MCPServerConfig
from miqi.skills.plugin_manager import PluginManager

SERVER_SCRIPT = str(
    Path(__file__).resolve().parents[2] / "scripts" / "mock_mcp_server.py"
)
ECHO_MARKER = "MCP_ECHO_RESULT_7f3a9c"
WRAPPED_TOOL = "mcp_e2emcp_e2e_echo"


@pytest.fixture(autouse=True)
def _isolate_plugin_discovery(monkeypatch):
    """#1267:把插件维度钉死为空。

    本文件的用例钉的是「config 配置的 MCP 语义」;插件发现会引入内置 sure
    插件(开发机可解析、CI 不可解析)——不隔离则同一用例的行为随机器而异。
    插件接入本身由 test_connects_plugin_declared_mcp_servers 覆盖。
    """

    async def _no_discover(self):
        return []

    monkeypatch.setattr(PluginManager, "discover", _no_discover)


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
async def test_connects_plugin_declared_mcp_servers(fake_config, fake_provider, tmp_path):
    """#1267:config 未配置任何服务器时,插件声明的服务器(命令可解析)也会
    接入并注册工具——插件声明的 MCP 服务器此前到不了运行时(死端)。"""
    import sys

    from miqi.runtime.session import RuntimeSession

    fake_config.tools.mcp_servers = {}

    class _FakePluginManager:
        """伪装插件管理器:声明一个用当前解释器+现成 mock 服务端脚本的服务器。"""

        async def discover(self):
            return []

        def get_mcp_servers(self):
            return [{
                "name": "e2eplug",
                "type": "stdio",
                "command": sys.executable,
                "args": [SERVER_SCRIPT],
                "tool_timeout": 30,
            }]

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-mcp-plugin",
        workspace=tmp_path,
    )
    runtime.services.plugin_manager = _FakePluginManager()
    await runtime.start()
    try:
        registry = runtime.services.tool_registry
        assert registry.has("mcp_e2eplug_e2e_echo"), (
            "插件声明的服务器应接入并注册工具: " f"{registry.tool_names}"
        )
    finally:
        await runtime.stop()

    # stop() 释放连接任务(stdio 子进程随之终止)
    assert runtime._mcp_keep_alive is None
    assert runtime._mcp_tasks == []


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
