"""Provider handler tests — #602 list 标注 + #835 后端收口.

#602 的「写自定义 key 自动切模型」用例已删除:后端收口(#835)移除了
providers.update 的自定义凭据写入路径,该行为不再存在。替换为收口回归测试,
断言自配凭据被拒绝、model-only 覆盖仍可用。
"""

from __future__ import annotations

import json

import pytest

from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
from miqi.runtime.provider_handlers import (
    providers_deactivate_handler,
    providers_list_handler,
    providers_test_handler,
    providers_update_handler,
)


def _make_registry(model: str, **provider_keys) -> ClientSessionRegistry:
    """Registry with a real Config whose default model is `model`.

    provider_keys: provider_name -> api_key to configure.
    """
    from unittest.mock import MagicMock

    from miqi.config.schema import Config

    cfg = Config()
    cfg.agents.defaults.model = model
    for name, key in provider_keys.items():
        setattr(getattr(cfg.providers, name), "api_key", key)

    state = MagicMock()
    state.load_config.return_value = cfg
    state.config = cfg

    registry = ClientSessionRegistry()
    registry.bridge_context["state"] = state
    return registry


# ── providers.list（保留）─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_providers_list_does_not_mislabel_global_model_as_deepseek_model():
    """providers.list must not report the claude default as deepseek's configured_model."""
    registry = _make_registry("anthropic/claude-opus-4-5", deepseek="sk-ds-1234567890")

    result = await providers_list_handler("r1", {}, "client-1", None, registry)
    providers = {p["name"]: p for p in result["result"]["providers"]}

    deepseek = providers["deepseek"]
    assert deepseek["configured"] is True
    assert deepseek["configured_model"] is None
    assert result["result"]["active_model"] == "anthropic/claude-opus-4-5"
    assert result["result"]["active_provider"] == "anthropic"


@pytest.mark.asyncio
async def test_providers_list_reports_model_when_it_belongs_to_provider():
    """When the default model genuinely matches the provider, configured_model is set."""
    registry = _make_registry("deepseek-v4-flash", deepseek="sk-ds-1234567890")

    result = await providers_list_handler("r1", {}, "client-1", None, registry)
    providers = {p["name"]: p for p in result["result"]["providers"]}

    deepseek = providers["deepseek"]
    assert deepseek["configured_model"] == "deepseek-v4-flash"


# ── active_model_resolvable（#1010 后续）：发送门禁的判定依据 ──────────────


@pytest.mark.asyncio
async def test_providers_list_active_model_unresolvable_without_credentials(
    tmp_path, monkeypatch
):
    """无任何凭据（未登录/无本地 key、无网关）时默认模型不可发起会话。"""
    monkeypatch.delenv("QRAFT_GATEWAY_BASE", raising=False)
    registry = _make_registry("deepseek/deepseek-v4-flash")
    cfg = registry.bridge_context["state"].load_config()
    cfg.agents.defaults.workspace = str(tmp_path)

    result = await providers_list_handler("r1", {}, "client-1", None, registry)

    assert result["result"]["active_model_resolvable"] is False


@pytest.mark.asyncio
async def test_providers_list_active_model_resolvable_via_ai_gateway(tmp_path, monkeypatch):
    """登录后网关凭据 active 且默认模型为网关模型时，没有任何本地 provider
    凭据也可发起会话 —— 前端发送门禁只看 configured 会把这条路径拦成
    「尚未配置模型服务」（用户登录后仍被要求配置模型）。"""
    monkeypatch.delenv("QRAFT_GATEWAY_BASE", raising=False)
    registry = _make_registry("deepseek/deepseek-v4-flash")
    cfg = registry.bridge_context["state"].load_config()
    cfg.agents.defaults.workspace = str(tmp_path)

    qraft_dir = tmp_path / ".qraft"
    qraft_dir.mkdir()
    (qraft_dir / "token.json").write_text(
        json.dumps({
            "aiGateway": {
                "status": "active",
                "encryptedApiKey": "enc-key",
                "configVersion": 1,
            }
        }),
        encoding="utf-8",
    )

    result = await providers_list_handler("r1", {}, "client-1", None, registry)

    assert all(p["configured"] is False for p in result["result"]["providers"])
    assert result["result"]["active_model_resolvable"] is True


@pytest.mark.asyncio
async def test_providers_list_gateway_creds_do_not_resolve_other_models(tmp_path, monkeypatch):
    """网关只路由实测网关模型：默认模型是别的模型时仍不可发起会话
    （make_provider 会落回直连并因无凭据报 NO_API_KEY）。"""
    monkeypatch.delenv("QRAFT_GATEWAY_BASE", raising=False)
    registry = _make_registry("openai/gpt-4o")
    cfg = registry.bridge_context["state"].load_config()
    cfg.agents.defaults.workspace = str(tmp_path)

    qraft_dir = tmp_path / ".qraft"
    qraft_dir.mkdir()
    (qraft_dir / "token.json").write_text(
        json.dumps({
            "aiGateway": {
                "status": "active",
                "encryptedApiKey": "enc-key",
                "configVersion": 1,
            }
        }),
        encoding="utf-8",
    )

    result = await providers_list_handler("r1", {}, "client-1", None, registry)

    assert result["result"]["active_model_resolvable"] is False


# ── 后端收口（#835）：providers.update 拒绝自配凭据 ─────────────────────────


@pytest.mark.asyncio
async def test_providers_update_rejects_custom_api_key():
    """后端收口：providers.update 拒绝非空 api_key。"""
    registry = _make_registry("deepseek/deepseek-v4-flash")

    with pytest.raises(AppServerError) as exc:
        await providers_update_handler(
            "r1",
            {"provider_name": "deepseek", "api_key": "sk-ds-custom"},
            "client-1", None, registry,
        )
    assert exc.value.code == "NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_providers_update_rejects_custom_api_base():
    """后端收口：providers.update 拒绝非空 api_base。"""
    registry = _make_registry("deepseek/deepseek-v4-flash")

    with pytest.raises(AppServerError) as exc:
        await providers_update_handler(
            "r1",
            {"provider_name": "deepseek", "api_base": "https://evil.example.com/v1"},
            "client-1", None, registry,
        )
    assert exc.value.code == "NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_providers_update_model_only_still_works():
    """后端收口：providers.update 仍支持 model 覆盖（内置激活流程用）。"""
    from unittest import mock

    # 收口后模型必须解析到「有凭据可用」的 provider（#929 review）——
    # deepseek 已激活（持有 key）时其模型才可通过。
    registry = _make_registry("deepseek/deepseek-v4-flash", deepseek="sk-ds-1234567890")
    state = registry.bridge_context["state"]

    with mock.patch("miqi.config.loader.save_config"):
        result = await providers_update_handler(
            "r1",
            {"provider_name": "deepseek", "model": "deepseek/deepseek-v4-flash"},
            "client-1", None, registry,
        )

    assert result["result"]["saved"] is True
    assert state.config.agents.defaults.model == "deepseek/deepseek-v4-flash"


@pytest.mark.asyncio
async def test_providers_update_rejects_model_of_unconfigured_provider():
    """#929：注册表成员资格不够 —— 无凭据 provider 的模型会被兜底错发到
    第一个已配置 provider 的 API，必须拒绝。"""
    from unittest import mock

    registry = _make_registry("deepseek/deepseek-v4-flash", deepseek="sk-ds-1234567890")

    with mock.patch("miqi.config.loader.save_config"):
        with pytest.raises(AppServerError) as exc:
            await providers_update_handler(
                "r1",
                {"provider_name": "deepseek", "model": "openai/gpt-4o"},
                "client-1", None, registry,
            )
    assert exc.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_providers_update_accepts_gateway_routed_model():
    """#929：已配置的网关在运行时兜底路由任意模型 —— 网关可用的模型
    应被接受，否则 UI 过滤与后端判定不一致。"""
    from unittest import mock

    registry = _make_registry("deepseek/deepseek-v4-flash", openrouter="sk-or-1234567890")
    state = registry.bridge_context["state"]

    with mock.patch("miqi.config.loader.save_config"):
        result = await providers_update_handler(
            "r1",
            {"provider_name": "openrouter", "model": "anthropic/claude-opus-4-5"},
            "client-1", None, registry,
        )

    assert result["result"]["saved"] is True
    assert state.config.agents.defaults.model == "anthropic/claude-opus-4-5"


@pytest.mark.asyncio
async def test_providers_update_rejects_custom_model_even_with_gateway():
    """#933 review：已配置网关也不得复活 custom/* 模型 —— custom provider
    已从运行时移除，选择后新会话会在 make_provider 报错。"""
    from unittest import mock

    registry = _make_registry("deepseek/deepseek-v4-flash", openrouter="sk-or-1234567890")

    with mock.patch("miqi.config.loader.save_config"):
        with pytest.raises(AppServerError) as exc:
            await providers_update_handler(
                "r1",
                {"provider_name": "openrouter", "model": "custom/default"},
                "client-1", None, registry,
            )
    assert exc.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_providers_deactivate_resets_to_other_configured_provider():
    """#933 review：取消激活后默认模型应切到其他可用 provider 的模型，
    而不是无条件重置为无凭据的出厂默认。"""
    from unittest import mock

    registry = _make_registry(
        "deepseek/deepseek-v4-flash",
        deepseek="sk-ds-1234567890",
        openai="sk-proj-1234567890abcdef",
    )
    state = registry.bridge_context["state"]
    config = state.load_config()
    config.desktop = {"providerActivation": {"deepseek": {"builtin": True}}}

    with mock.patch("miqi.config.loader.save_config"):
        await providers_deactivate_handler(
            "r1",
            {"provider_name": "deepseek"},
            "client-1", None, registry,
        )

    assert config.agents.defaults.model == "openai/gpt-4.1"


@pytest.mark.asyncio
async def test_providers_deactivate_clears_builtin_activation():
    """后端收口（#835）：providers.deactivate 清空 api_key、遗留端点覆盖
    与 activation 标记；默认模型归属该 provider 时重置为可用模型或清空
    （#929 / #933 review）。"""
    from unittest import mock

    registry = _make_registry("deepseek/deepseek-v4-flash", deepseek="sk-ds-1234567890")
    state = registry.bridge_context["state"]
    config = state.load_config()
    # 模拟已激活标记 + 历史遗留的自定义端点
    config.desktop = {"providerActivation": {"deepseek": {"builtin": True}}}
    config.providers.deepseek.api_base = "https://proxy.example.com/v1"

    with mock.patch("miqi.config.loader.save_config"):
        result = await providers_deactivate_handler(
            "r1",
            {"provider_name": "deepseek"},
            "client-1", None, registry,
        )

    assert result["result"]["deactivated"] is True
    assert config.providers.deepseek.api_key == ""
    assert config.providers.deepseek.api_base is None
    assert config.desktop.get("providerActivation", {}).get("deepseek") is None
    # 默认模型属于 deepseek，且没有其他可用 provider → 清空为「未选择」
    # 状态（#933 review：不得回退到无凭据的出厂默认）
    assert config.agents.defaults.model == ""


@pytest.mark.asyncio
async def test_providers_activate_clears_legacy_endpoint_overrides():
    """#929：激活内置密钥时清除历史遗留的 api_base / extra_headers ——
    企业共享密钥只能走官方端点。"""
    from unittest import mock

    from miqi.runtime.provider_handlers import providers_activate_handler

    registry = _make_registry("deepseek/deepseek-v4-flash")
    config = registry.bridge_context["state"].load_config()
    config.providers.deepseek.api_base = "https://proxy.example.com/v1"
    config.providers.deepseek.extra_headers = {"X-Evil": "1"}

    with mock.patch("miqi.config.loader.save_config"):
        result = await providers_activate_handler(
            "r1",
            {"provider_name": "deepseek", "activation_code": "weiguanjiyuan5g"},
            "client-1", None, registry,
        )

    assert result["result"]["activated"] is True
    assert config.providers.deepseek.api_key  # 已写入内置密钥
    assert config.providers.deepseek.api_base is None
    assert config.providers.deepseek.extra_headers is None


def test_config_is_builtin_activated_tolerates_legacy_shapes():
    """#929：activation store 的历史/异常形态不能崩（None、字符串、旧 bool
    格式），统一经 Config.is_builtin_activated 解码。"""
    from miqi.config.schema import Config

    cfg = Config()
    assert cfg.is_builtin_activated("deepseek") is False

    cfg.desktop = {"providerActivation": {"deepseek": None}}
    assert cfg.is_builtin_activated("deepseek") is False  # 不崩，视为未激活

    cfg.desktop = {"providerActivation": {"deepseek": True}}  # 旧格式
    assert cfg.is_builtin_activated("deepseek") is True

    cfg.desktop = {"providerActivation": {"deepseek": {"builtin": True}}}
    assert cfg.is_builtin_activated("deepseek") is True

    cfg.desktop = {"providerActivation": {"deepseek": {"builtin": "false"}}}
    assert cfg.is_builtin_activated("deepseek") is False  # 字符串 "false" 不再当真

    cfg.desktop = {"providerActivation": "not-a-dict"}
    assert cfg.is_builtin_activated("deepseek") is False


@pytest.mark.asyncio
async def test_providers_update_rejects_custom_provider_name():
    """#929：providers.update 按运行时注册表校验 provider_name。

    ProvidersConfig 存储 schema 仍保留 custom 字段，但工厂已移除 custom
    provider —— 用 schema 校验会放行一个运行时无法解析的默认模型。
    """
    from unittest import mock

    registry = _make_registry("deepseek/deepseek-v4-flash")

    with mock.patch("miqi.config.loader.save_config"):
        with pytest.raises(AppServerError) as exc:
            await providers_update_handler(
                "r1",
                {"provider_name": "custom", "model": "custom/default"},
                "client-1", None, registry,
            )
    assert exc.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_providers_deactivate_rejects_non_builtin_provider():
    """只有内置 provider 支持取消激活。"""
    registry = _make_registry("anthropic/claude-opus-4-5", anthropic="sk-an-1234567890")

    with pytest.raises(AppServerError) as exc:
        await providers_deactivate_handler(
            "r1",
            {"provider_name": "anthropic"},
            "client-1", None, registry,
        )
    assert exc.value.code == "NOT_SUPPORTED"


# ── 内置密钥端点收口（#929）：test 不走历史遗留 api_base ─────────────────────


def _builtin_activated_registry(base: str | None) -> ClientSessionRegistry:
    registry = _make_registry("deepseek/deepseek-v4-flash", deepseek="builtin-secret")
    cfg = registry.bridge_context["state"].load_config()
    cfg.providers.deepseek.api_base = base
    cfg.desktop = {"providerActivation": {"deepseek": {"builtin": True}}}
    return registry


@pytest.mark.asyncio
async def test_providers_test_ignores_legacy_api_base_when_builtin_activated():
    """内置激活时测试必须走官方端点，忽略历史遗留的自定义 api_base。"""
    from unittest import mock

    registry = _builtin_activated_registry("https://evil.example.com/v1")
    response = mock.MagicMock()
    response.finish_reason = "stop"
    response.error_kind = None

    with mock.patch("miqi.config.loader.save_config"), mock.patch(
        "miqi.providers.openai_provider.OpenAIProvider"
    ) as provider_cls:
        provider_cls.return_value.chat = mock.AsyncMock(return_value=response)
        result = await providers_test_handler(
            "r1",
            {"provider_name": "deepseek"},
            "client-1", None, registry,
        )

    assert result["result"]["ok"] is True
    kwargs = provider_cls.call_args.kwargs
    # provider 构造回落 spec.default_api_base（官方端点）
    assert kwargs["api_base"] is None


@pytest.mark.asyncio
async def test_providers_test_keeps_saved_base_without_builtin_activation():
    """未内置激活时保留历史行为：使用配置里保存的 api_base。"""
    from unittest import mock

    registry = _make_registry("deepseek/deepseek-v4-flash", deepseek="sk-legacy-1234567890")
    cfg = registry.bridge_context["state"].load_config()
    cfg.providers.deepseek.api_base = "https://legacy.example.com/v1"
    response = mock.MagicMock()
    response.finish_reason = "stop"
    response.error_kind = None

    with mock.patch("miqi.config.loader.save_config"), mock.patch(
        "miqi.providers.openai_provider.OpenAIProvider"
    ) as provider_cls:
        provider_cls.return_value.chat = mock.AsyncMock(return_value=response)
        result = await providers_test_handler(
            "r1",
            {"provider_name": "deepseek"},
            "client-1", None, registry,
        )

    assert result["result"]["ok"] is True
    kwargs = provider_cls.call_args.kwargs
    assert kwargs["api_base"] == "https://legacy.example.com/v1"


@pytest.mark.asyncio
async def test_providers_update_rejects_unresolvable_model():
    """#929：providers.update 拒绝运行时无法解析的模型值。"""
    from unittest import mock

    registry = _make_registry("deepseek/deepseek-v4-flash")

    with mock.patch("miqi.config.loader.save_config"):
        with pytest.raises(AppServerError) as exc:
            await providers_update_handler(
                "r1",
                {"provider_name": "deepseek", "model": "custom/default"},
                "client-1", None, registry,
            )
    assert exc.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_providers_deactivate_rejects_without_activation_marker():
    """#929：没有内置激活标记时拒绝取消激活，避免误清历史遗留的自配 key。"""
    registry = _make_registry("deepseek/deepseek-v4-flash", deepseek="sk-legacy-1234567890")

    with pytest.raises(AppServerError) as exc:
        await providers_deactivate_handler(
            "r1",
            {"provider_name": "deepseek"},
            "client-1", None, registry,
        )
    assert exc.value.code == "NOT_ACTIVATED"
