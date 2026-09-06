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
