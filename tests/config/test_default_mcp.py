"""内置默认 MCP 服务器（平台托管 slurm 网关）测试。

2026-09-05 产品确认：`miqroforge-slurm` 作为开箱即用的默认 MCP
服务器（SSE，insecure_http 默认开启——平台暂无 https）；凭据不入仓库——
登录后平台经 userinfo 下发 mcpGatewayKey，Desktop 写入 0600 token 文件，
Python 连接时自动注入 Authorization Bearer。用户显式配置 mcp_servers
（含空对象）即覆盖默认。
"""

from miqi.config.schema import Config, MCPServerConfig


def test_default_config_includes_hosted_slurm_gateway():
    srv = Config().tools.mcp_servers.get("miqroforge-slurm")
    assert isinstance(srv, MCPServerConfig)
    assert srv.type == "sse"
    assert srv.url == "http://124.220.57.194:9000/sse"
    # 平台暂无 https：内置网关默认 opt-in 明文 http（共享 token 明文传输
    # 的已知权衡），登录后自动连接 + 注入凭据；用户可改回 false 关闭
    assert srv.insecure_http is True
    # 凭据不入仓库：默认条目不含 headers，运行时从 token 文件注入
    assert srv.headers == {}
    assert srv.tool_timeout == 90
    assert "SLURM" in srv.description
    # 键名含 "slurm"：进入计费范围（#936 RUNNING 扣 10 分）
    assert "slurm" in "miqroforge-slurm"


def test_explicit_mcp_servers_overrides_default():
    # 用户删除默认服务器：显式空配置即覆盖（不会反复复活）
    cfg = Config.model_validate({"tools": {"mcp_servers": {}}})
    assert cfg.tools.mcp_servers == {}

    # 用户自己的服务器列表同样覆盖默认
    cfg2 = Config.model_validate(
        {"tools": {"mcp_servers": {"my-server": {"command": "npx", "args": ["x"]}}}}
    )
    assert set(cfg2.tools.mcp_servers.keys()) == {"my-server"}


def test_default_servers_are_isolated_per_instance():
    # default_factory 每次新建实例：修改一个实例不影响另一个
    a = Config().tools.mcp_servers
    b = Config().tools.mcp_servers
    assert a is not b
    a.pop("miqroforge-slurm")
    assert "miqroforge-slurm" in b
