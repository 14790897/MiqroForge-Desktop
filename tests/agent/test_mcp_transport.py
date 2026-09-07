"""MCP 连接传输选择测试（stdio / streamable HTTP / SSE，2026-09-05 平台托管网关接入）。"""

from types import SimpleNamespace

from miqi.agent.tools.mcp import _transport_for


def _cfg(**kw):
    defaults = dict(type="", command="", url="", headers={})
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_explicit_sse_type_wins_over_command():
    assert _transport_for(_cfg(type="sse", url="http://h:9000/sse", command="npx")) == "sse"


def test_sse_without_url_falls_through():
    # type=sse 但没有 url：按字段推断（command 优先）
    assert _transport_for(_cfg(type="sse", command="npx")) == "stdio"


def test_command_implies_stdio():
    assert _transport_for(_cfg(command="npx", args=["-y", "x"])) == "stdio"


def test_url_implies_http_by_default():
    assert _transport_for(_cfg(url="http://127.0.0.1:9000/mcp")) == "http"


def test_explicit_http_type():
    assert _transport_for(_cfg(type="http", url="http://h/mcp")) == "http"


def test_empty_config_returns_empty():
    assert _transport_for(_cfg()) == ""


class TestGatewayTokenInjection:
    def test_reads_key_from_token_file(self, tmp_path):
        from miqi.agent.tools.mcp import _gateway_key_from_token_file

        f = tmp_path / "token.json"
        f.write_text('{"accessToken": "a", "mcpGatewayKey": "k-123"}', encoding="utf-8")
        assert _gateway_key_from_token_file(f) == "k-123"

    def test_missing_file_or_field_returns_none(self, tmp_path):
        from miqi.agent.tools.mcp import _gateway_key_from_token_file

        assert _gateway_key_from_token_file(tmp_path / "nope.json") is None
        f = tmp_path / "token.json"
        f.write_text('{"accessToken": "a"}', encoding="utf-8")
        assert _gateway_key_from_token_file(f) is None
        f.write_text("not json", encoding="utf-8")
        assert _gateway_key_from_token_file(f) is None

    def test_default_gateway_name_matches_schema(self):
        from miqi.agent.tools.mcp import _DEFAULT_GATEWAY_NAME
        from miqi.config.schema import DEFAULT_MCP_SERVERS

        assert _DEFAULT_GATEWAY_NAME in DEFAULT_MCP_SERVERS


class TestInjectionGuards:
    def test_https_detection(self):
        from miqi.agent.tools.mcp import _is_https_url

        assert _is_https_url("https://mcp.example.com/sse") is True
        assert _is_https_url("http://127.0.0.1:9000/sse") is False
        assert _is_https_url("") is False

    def test_trusted_gateway_url_match(self):
        from miqi.agent.tools.mcp import _url_matches_trusted_gateway
        from miqi.config.schema import DEFAULT_MCP_SERVERS

        builtin = DEFAULT_MCP_SERVERS["miqroforge-slurm"]["url"]
        assert _url_matches_trusted_gateway(builtin) is True
        assert _url_matches_trusted_gateway("http://evil.example.com/sse") is False
        assert _url_matches_trusted_gateway("") is False
