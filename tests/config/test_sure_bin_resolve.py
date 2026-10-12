"""resolve_sure_bin:公开的 SURE 二进制探测(SURE_BIN → PATH → LOCALAPPDATA)。

阶段 3 从 detect_sure_mcp_server 抽出公开函数(原生 spawn 通道复用同一探测
与顺序);语义:逐段探测,单段异常视为该段未命中(不遮蔽后续),命中即停。
"""

import pytest

from miqi.config import schema


@pytest.fixture
def no_real_sure(monkeypatch):
    """隔离本机真实安装(开发机装了 SURE)。"""
    monkeypatch.setattr(schema, "_sure_bin_from_env", lambda: None)
    monkeypatch.setattr(schema, "_sure_bin_from_path", lambda: None)
    monkeypatch.setattr(schema, "_sure_bin_from_localappdata", lambda: None)


def test_resolve_order_and_stop_at_first_hit(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(schema, "_sure_bin_from_env", lambda: (calls.append("env"), None)[1])
    monkeypatch.setattr(
        schema, "_sure_bin_from_path", lambda: (calls.append("path"), r"C:\from\path.exe")[1]
    )
    monkeypatch.setattr(
        schema, "_sure_bin_from_localappdata", lambda: (calls.append("lad"), r"C:\lad.exe")[1]
    )
    assert schema.resolve_sure_bin() == r"C:\from\path.exe"
    assert calls == ["env", "path"]


def test_resolve_continues_past_exception(monkeypatch):
    """单段探测异常不遮蔽后续探测(异常按该段未命中处理)。"""
    def boom():
        raise OSError("permission denied")

    monkeypatch.setattr(schema, "_sure_bin_from_env", boom)
    monkeypatch.setattr(schema, "_sure_bin_from_path", lambda: r"C:\from\path.exe")
    assert schema.resolve_sure_bin() == r"C:\from\path.exe"


def test_resolve_none_when_all_miss(no_real_sure):
    assert schema.resolve_sure_bin() is None


def test_detect_still_uses_resolve(no_real_sure):
    assert schema.detect_sure_mcp_server() is None
