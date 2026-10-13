"""内置默认 MCP 服务器（平台托管 slurm 网关）测试。

2026-09-05 产品确认：`miqroforge-slurm` 作为开箱即用的默认 MCP
服务器（SSE，insecure_http 默认开启——平台暂无 https）；凭据不入仓库——
登录后平台经 userinfo 下发 mcpGatewayKey，Desktop 写入 0600 token 文件，
Python 连接时自动注入 Authorization Bearer。用户显式配置 mcp_servers
（含空对象）即覆盖默认。

#1268：本机安装 SURE 时同样进入默认条目（只读探测，检测到才注入；
探测顺序与 SURE 自身启动器一致：SURE_BIN → PATH → Windows 兜底）。
"""

import pytest

from miqi.config import schema
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


# ── #1268：检测到本机 SURE 时的默认条目 ─────────────────────────────────


@pytest.fixture
def no_sure(monkeypatch):
    """把三段探测全部钉为未命中，隔离本机真实安装（开发机装了 SURE）。"""
    monkeypatch.setattr(schema, "_sure_bin_from_env", lambda: None)
    monkeypatch.setattr(schema, "_sure_bin_from_path", lambda: None)
    monkeypatch.setattr(schema, "_sure_bin_from_localappdata", lambda: None)


def test_sure_entry_fields_from_detected_binary(monkeypatch):
    monkeypatch.setattr(schema, "_sure_bin_from_env", lambda: r"C:\fake\sure.exe")
    srv = Config().tools.mcp_servers.get("sure")
    assert isinstance(srv, MCPServerConfig)
    assert srv.type == "stdio"
    assert srv.command == r"C:\fake\sure.exe"
    assert srv.args == ["mcp", "serve"]
    assert srv.tool_timeout == 600
    assert srv.progress_interval_seconds == 10
    assert "SURE" in srv.description


def test_detection_stops_at_first_hit(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(schema, "_sure_bin_from_env", lambda: (calls.append("env"), None)[1])
    monkeypatch.setattr(
        schema, "_sure_bin_from_path", lambda: (calls.append("path"), r"C:\from\path.exe")[1]
    )
    monkeypatch.setattr(
        schema,
        "_sure_bin_from_localappdata",
        lambda: (calls.append("lad"), r"C:\lad.exe")[1],
    )
    srv = schema.detect_sure_mcp_server()
    assert srv is not None and srv.command == r"C:\from\path.exe"
    assert calls == ["env", "path"]  # 命中即停，不再探测后续


def test_no_sure_no_entry(no_sure):
    assert "sure" not in Config().tools.mcp_servers


def test_detection_failure_is_swallowed(monkeypatch):
    def boom():
        raise OSError("permission denied")

    monkeypatch.setattr(schema, "_sure_bin_from_env", boom)
    monkeypatch.setattr(schema, "_sure_bin_from_path", lambda: None)
    monkeypatch.setattr(schema, "_sure_bin_from_localappdata", lambda: None)
    assert schema.detect_sure_mcp_server() is None
    # 配置加载不受探测异常影响
    assert "sure" not in Config().tools.mcp_servers


def test_explicit_config_still_overrides_detected_sure(monkeypatch):
    monkeypatch.setattr(schema, "_sure_bin_from_env", lambda: r"C:\fake\sure.exe")
    # 显式空对象：连默认（含检测到的 sure）一起去掉
    cfg = Config.model_validate({"tools": {"mcp_servers": {}}})
    assert cfg.tools.mcp_servers == {}
    # 用户显式配置 sure：以用户为准，不被探测结果覆盖
    cfg2 = Config.model_validate(
        {"tools": {"mcp_servers": {"sure": {"command": "my-sure", "args": ["mcp", "serve"]}}}}
    )
    assert cfg2.tools.mcp_servers["sure"].command == "my-sure"


def test_resolver_env_bin_requires_existing_file(tmp_path, monkeypatch):
    fake = tmp_path / "sure.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("SURE_BIN", str(fake))
    assert schema._sure_bin_from_env() == str(fake)
    # 指向不存在的文件 → 视为未命中（继续后续探测）
    monkeypatch.setenv("SURE_BIN", str(tmp_path / "missing.exe"))
    assert schema._sure_bin_from_env() is None
    monkeypatch.delenv("SURE_BIN", raising=False)
    assert schema._sure_bin_from_env() is None


def test_resolver_path_uses_which(monkeypatch):
    monkeypatch.setattr(
        schema.shutil, "which", lambda name: r"C:\found\sure.exe" if name == "sure" else None
    )
    assert schema._sure_bin_from_path() == r"C:\found\sure.exe"


def test_resolver_localappdata_windows_only(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    # 非 Windows：不兜底，即使结构存在也返回 None
    monkeypatch.setattr(schema.sys, "platform", "linux")
    b = tmp_path / "SURE" / "bin" / "sure.exe"
    b.parent.mkdir(parents=True)
    b.write_text("", encoding="utf-8")
    assert schema._sure_bin_from_localappdata() is None
    # Windows：结构存在才命中
    monkeypatch.setattr(schema.sys, "platform", "win32")
    assert schema._sure_bin_from_localappdata() == str(b)
    b.unlink()
    assert schema._sure_bin_from_localappdata() is None
