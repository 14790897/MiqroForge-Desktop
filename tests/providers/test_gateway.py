"""Platform AI gateway — creds 解析 + factory 路由(issue #922)。

覆盖:token 文件缺失/无 aiGateway/非 active/空密钥 → 无凭据回直连;
aiGateway active + encryptedApiKey → make_provider 把 deepseek-v4-flash 路由到
AnthropicProvider(网关),其余 deepseek 模型与非 active 场景仍走 OpenAI 直连。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from miqi.providers.anthropic_provider import AnthropicProvider
from miqi.providers.factory import make_provider
from miqi.providers.gateway import (
    GATEWAY_PREFIX,
    gateway_origin,
    read_gateway_creds,
)
from miqi.providers.openai_provider import OpenAIProvider

SK = "sk-test-gateway-secret"


@pytest.fixture(autouse=True)
def _clean_gateway_env(monkeypatch: pytest.MonkeyPatch):
    """网关地址/前缀由环境变量注入,测试须与宿主环境隔离。"""
    monkeypatch.delenv("QRAFT_GATEWAY_BASE", raising=False)
    monkeypatch.delenv("QRAFT_GATEWAY_PREFIX", raising=False)


def _write_token(tmp_path: Path, *, gateway: dict[str, Any] | None) -> Path:
    token_file = tmp_path / ".qraft" / "token.json"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "accessToken": "tok-123",
        "expiresAt": 4102444800000,
        "baseUrl": "https://test.forge.miqroera.com/api",
    }
    if gateway is not None:
        payload["aiGateway"] = gateway
    token_file.write_text(json.dumps(payload), encoding="utf-8")
    return token_file


def _active_gateway(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "encryptedApiKey": SK,
        "status": "active",
        "configVersion": 1,
    }
    data.update(overrides)
    return data


class _FakeConfig:
    """make_provider 只 duck-type 用到的 Config 成员。"""

    def __init__(self, workspace: Path, *, model: str, api_key: str = "builtin-key"):
        self.workspace_path = workspace
        self.agents = SimpleNamespace(defaults=SimpleNamespace(model=model))
        self._api_key = api_key

    def get_provider_name(self, model: str) -> str:
        return model.split("/", 1)[0]

    def get_provider(self, _model: str):
        return SimpleNamespace(api_key=self._api_key, extra_headers=None)

    def get_api_base(self, _model: str) -> str | None:
        return None

    def is_builtin_activated(self, provider_name: str) -> bool:
        # #933 收口后 factory 直连路径校验内置激活态；测试桩按已激活处理
        return provider_name == "deepseek"


# ---------------------------------------------------------------------------
# read_gateway_creds
# ---------------------------------------------------------------------------


class TestReadGatewayCreds:
    def test_missing_token_file_returns_none(self, tmp_path: Path):
        assert read_gateway_creds(tmp_path / ".qraft" / "token.json") is None

    def test_broken_or_no_ai_gateway_returns_none(self, tmp_path: Path):
        broken = _write_token(tmp_path, gateway=None)
        assert read_gateway_creds(broken) is None
        broken.write_text("{not json", encoding="utf-8")
        assert read_gateway_creds(broken) is None

    @pytest.mark.parametrize(
        "status",
        ["provisioning", "failed", "disabled", "unknown"],
    )
    def test_non_active_status_returns_none(self, tmp_path: Path, status: str):
        token = _write_token(tmp_path, gateway=_active_gateway(status=status))
        assert read_gateway_creds(token) is None

    def test_empty_key_returns_none(self, tmp_path: Path):
        token = _write_token(tmp_path, gateway=_active_gateway(encryptedApiKey=""))
        assert read_gateway_creds(token) is None

    def test_active_returns_creds(self, tmp_path: Path):
        token = _write_token(tmp_path, gateway=_active_gateway(configVersion=3))
        creds = read_gateway_creds(token)
        assert creds == {"encryptedApiKey": SK, "status": "active", "configVersion": 3}


# ---------------------------------------------------------------------------
# gateway_origin（QRAFT_GATEWAY_BASE 校验）
# ---------------------------------------------------------------------------


class TestGatewayOrigin:
    def test_unset_returns_test_default(self):
        # 未配置时回退 test env 实测 IP（平台尚无 https 网关域名，#923）
        assert gateway_origin() == "http://118.25.115.164"

    def test_https_accepted_with_trailing_slash_normalized(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("QRAFT_GATEWAY_BASE", "https://gateway.example.com/v1/")
        assert gateway_origin() == "https://gateway.example.com/v1"

    @pytest.mark.parametrize("raw", ["http://gateway.example.com", "ftp://g.example.com"])
    def test_non_https_rejected(self, monkeypatch: pytest.MonkeyPatch, raw: str):
        monkeypatch.setenv("QRAFT_GATEWAY_BASE", raw)
        assert gateway_origin() is None


# ---------------------------------------------------------------------------
# make_provider 网关路由
# ---------------------------------------------------------------------------


def _token_for(tmp_path: Path, gateway: dict[str, Any] | None) -> Path:
    return _write_token(tmp_path, gateway=gateway)


class TestMakeProviderGatewayRoute:
    def test_active_creds_routes_deepseek_v4_flash_to_anthropic(self, tmp_path: Path):
        _token_for(tmp_path, _active_gateway())
        config = _FakeConfig(tmp_path, model="deepseek/deepseek-v4-flash")

        provider = make_provider(config)

        assert isinstance(provider, AnthropicProvider)
        assert provider.api_key == SK
        assert provider.api_base == f"{gateway_origin()}{GATEWAY_PREFIX}"
        assert provider._resolve_model("deepseek/deepseek-v4-flash") == "deepseek-v4-flash"

    def test_https_gateway_base_accepted_with_trailing_slash_normalized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("QRAFT_GATEWAY_BASE", "https://gateway.example.com/")
        _token_for(tmp_path, _active_gateway())
        config = _FakeConfig(tmp_path, model="deepseek/deepseek-v4-flash")

        provider = make_provider(config)

        assert isinstance(provider, AnthropicProvider)
        assert provider.api_base == f"https://gateway.example.com{GATEWAY_PREFIX}"

    def test_non_https_gateway_base_rejected_back_to_direct(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # CWE-319：明文通道禁止承载密钥 —— 显式配置 http 地址时拒绝接受，
        # 回退直连而不是把 encryptedApiKey 发到明文网关。
        monkeypatch.setenv("QRAFT_GATEWAY_BASE", "http://gateway.example.com")
        _token_for(tmp_path, _active_gateway())
        config = _FakeConfig(tmp_path, model="deepseek/deepseek-v4-flash")

        provider = make_provider(config)

        assert isinstance(provider, OpenAIProvider)
        assert provider.api_key == "builtin-key"

    def test_gateway_route_bypasses_missing_builtin_key(self, tmp_path: Path):
        # 登录用户即使未激活内置密钥也走网关(守卫之前已路由)
        _token_for(tmp_path, _active_gateway())
        config = _FakeConfig(tmp_path, model="deepseek/deepseek-v4-flash", api_key="")

        provider = make_provider(config)

        assert isinstance(provider, AnthropicProvider)
        assert provider.api_key == SK

    def test_no_creds_keeps_openai_direct(self, tmp_path: Path):
        _token_for(tmp_path, None)
        config = _FakeConfig(tmp_path, model="deepseek/deepseek-v4-flash")

        provider = make_provider(config)

        assert isinstance(provider, OpenAIProvider)
        assert provider.api_key == "builtin-key"

    def test_non_active_keeps_openai_direct(self, tmp_path: Path):
        _token_for(tmp_path, _active_gateway(status="provisioning"))
        config = _FakeConfig(tmp_path, model="deepseek/deepseek-v4-flash")

        provider = make_provider(config)

        assert isinstance(provider, OpenAIProvider)

    def test_gateway_only_for_verified_model(self, tmp_path: Path):
        # 网关只实测支持 v4-flash;deepseek-chat 等仍走直连,不回退也不硬塞网关
        _token_for(tmp_path, _active_gateway())
        config = _FakeConfig(tmp_path, model="deepseek/deepseek-chat")

        provider = make_provider(config)

        assert isinstance(provider, OpenAIProvider)
