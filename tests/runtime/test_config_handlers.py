"""Tests for config handlers — Phase 28.3 / Phase 38.5.

Validates that config.get returns redacted config, config.update
saves and propagates to active sessions, and error paths are safe.

Phase 38.5: Updated to use registry.bridge_context["state"] DI
instead of the deprecated import miqi.bridge.server pattern.
"""

from unittest.mock import MagicMock

import pytest


def _setup_registry(fake_config, tmp_path):
    """Set up a ClientSessionRegistry with bridge_state in bridge_context."""
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()
    state = MagicMock()
    state.load_config.return_value = fake_config
    state.config = fake_config
    registry.bridge_context = {
        "state": state,
    }
    return registry


# ── config.get ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_get_returns_redacted_config(fake_config, fake_provider, tmp_path):
    """config.get returns config dict with secrets redacted."""
    from miqi.runtime.config_handlers import config_get_handler

    registry = _setup_registry(fake_config, tmp_path)

    result = await config_get_handler("req-1", {}, "client-1", None, registry)
    assert "result" in result
    assert isinstance(result["result"], dict)


# ── config.update ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_update_saves_and_propagates(fake_config, fake_provider, tmp_path):
    """config.update saves config and propagates to active sessions."""
    from miqi.runtime.config_handlers import config_get_handler, config_update_handler

    registry = _setup_registry(fake_config, tmp_path)

    # Get the current config
    await config_get_handler("req-1", {}, "client-1", None, registry)

    # Create a session so propagation can be verified
    session = await registry.create_session(
        client_id="client-1",
        session_key="test-session",
        config=fake_config,
        provider=fake_provider,
        workspace=tmp_path,
    )

    # Update a safe field (agents.defaults.name) to test propagation
    result = await config_update_handler(
        "req-1",
        {"config": {"agents": {"defaults": {"name": "phase-28-test"}}}},
        "client-1", None, registry,
    )
    assert result["result"]["saved"] is True

    # Verify propagation: session's config_snapshot should have been updated
    session_state = getattr(session.services, "session_state", None)
    if session_state is not None:
        assert session_state.config_snapshot is not None


@pytest.mark.asyncio
async def test_config_update_rejects_provider_credentials(fake_config, fake_provider, tmp_path):
    """后端收口（#835）：config.update 拒绝写入 providers 凭据字段。"""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler(
            "req-1",
            {"config": {"providers": {"deepseek": {"api_key": "sk-custom"}}}},
            "client-1", None, registry,
        )
    assert exc_info.value.code == "NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_config_update_rejects_empty_provider_credential(fake_config, fake_provider, tmp_path):
    """后端收口（#835）：config.update 连空值/null 凭据也拒绝。"""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler(
            "req-1",
            {"config": {"providers": {"deepseek": {"api_key": ""}}}},
            "client-1", None, registry,
        )
    assert exc_info.value.code == "NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_config_update_rejects_empty_config(fake_config, fake_provider, tmp_path):
    """config.update rejects empty config param."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler("req-1", {"config": {}}, "client-1", None, registry)
    assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_config_update_rejects_missing_config_param(fake_config, fake_provider, tmp_path):
    """config.update rejects request without config param."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler("req-1", {}, "client-1", None, registry)
    assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_config_update_rejects_invalid_config(fake_config, fake_provider, tmp_path):
    """config.update rejects invalid config with INVALID_PARAMS code."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler(
            "req-1",
            {"config": {"agents": {"defaults": {"model": None}}}},
            "client-1", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_config_update_rejects_unresolvable_model(fake_config, fake_provider, tmp_path):
    """收口（#929）：config.update 拒绝运行时无法解析的模型值（如 custom/*）。"""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler(
            "req-1",
            {"config": {"agents": {"defaults": {"model": "custom/default"}}}},
            "client-1", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_config_update_unrelated_field_ignores_legacy_model(fake_config, fake_provider, tmp_path):
    """#929 review：模型门控只作用于「本次改写模型」的更新 —— 历史遗留的
    不可用模型不得阻塞改名/改温度等无关保存。"""
    from unittest import mock

    from miqi.runtime.config_handlers import config_update_handler

    fake_config.agents.defaults.model = "custom/default"  # 遗留模型
    registry = _setup_registry(fake_config, tmp_path)

    with mock.patch("miqi.config.loader.save_config"):
        result = await config_update_handler(
            "req-1",
            {"config": {"agents": {"defaults": {"name": "renamed"}}}},
            "client-1", None, registry,
        )
    assert result["result"]["saved"] is True


@pytest.mark.asyncio
async def test_config_update_rejects_empty_model(fake_config, fake_provider, tmp_path):
    """#929 review：空模型不再能绕过门控落盘 —— 之前空值跳过校验但
    Config 也接受空串，运行时会把空模型名发给兜底 provider。"""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler(
            "req-1",
            {"config": {"agents": {"defaults": {"model": ""}}}},
            "client-1", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_config_update_agents_null_is_clean_invalid_params(fake_config, fake_provider, tmp_path):
    """#929 review：{"agents": null} 之前让门控抛 AttributeError → INTERNAL，
    现在应回到干净的 INVALID_PARAMS（校验先于模型门控执行）。"""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler(
            "req-1",
            {"config": {"agents": None}},
            "client-1", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_config_update_rejects_custom_model_even_with_gateway(fake_config, fake_provider, tmp_path):
    """#933 review：已配置网关也不得复活 custom/* 模型。"""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    fake_config.providers.openrouter.api_key = "sk-or-1234567890"
    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler(
            "req-1",
            {"config": {"agents": {"defaults": {"model": "custom/default"}}}},
            "client-1", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"


# ── 平台 AI 网关路由与保存门控（#922 收尾） ─────────────────────────────


def _write_qraft_gateway_token(config, *, status: str = "active") -> None:
    """在 config 的 workspace 下写入带 aiGateway 块的 token 文件。"""
    import json
    from pathlib import Path

    token_file = Path(config.agents.defaults.workspace) / ".qraft" / "token.json"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(json.dumps({
        "accessToken": "tok-123",
        "aiGateway": {
            "encryptedApiKey": "sk-test-gateway-secret",
            "status": status,
            "configVersion": 1,
        },
    }), encoding="utf-8")


@pytest.mark.asyncio
async def test_config_update_accepts_gateway_model_with_active_qraft_creds(
    fake_config, fake_provider, tmp_path, monkeypatch,
):
    """平台网关凭据 active 时，网关实测模型可保存 —— 门控须与
    factory.make_provider 的网关路由一致（#922）；登录用户无任何本地
    provider 凭据时，这是唯一可用的模型。"""
    from unittest import mock

    from miqi.runtime.config_handlers import config_update_handler

    monkeypatch.delenv("QRAFT_GATEWAY_BASE", raising=False)
    _write_qraft_gateway_token(fake_config)
    registry = _setup_registry(fake_config, tmp_path)

    with mock.patch("miqi.config.loader.save_config"):
        result = await config_update_handler(
            "req-1",
            {"config": {"agents": {"defaults": {"model": "deepseek/deepseek-v4-flash"}}}},
            "client-1", None, registry,
        )
    assert result["result"]["saved"] is True


@pytest.mark.asyncio
async def test_config_update_rejects_gateway_model_without_qraft_creds(
    fake_config, fake_provider, tmp_path, monkeypatch,
):
    """无网关凭据时，网关模型与其他模型一样必须由本地 provider 凭据背书。"""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    monkeypatch.delenv("QRAFT_GATEWAY_BASE", raising=False)
    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler(
            "req-1",
            {"config": {"agents": {"defaults": {"model": "deepseek/deepseek-v4-flash"}}}},
            "client-1", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_config_update_rejects_non_gateway_model_even_with_qraft_creds(
    fake_config, fake_provider, tmp_path, monkeypatch,
):
    """网关凭据 active 只放行网关实测模型 —— 其余 deepseek 模型运行时仍走
    直连（factory.make_provider），不能借网关凭据保存。"""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    monkeypatch.delenv("QRAFT_GATEWAY_BASE", raising=False)
    _write_qraft_gateway_token(fake_config)
    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler(
            "req-1",
            {"config": {"agents": {"defaults": {"model": "deepseek/deepseek-v4-pro"}}}},
            "client-1", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_config_update_accepts_model_of_configured_provider(fake_config, fake_provider, tmp_path):
    """#929 review：模型归属的 provider 持有凭据（或经网关路由）时放行。"""
    from unittest import mock

    from miqi.runtime.config_handlers import config_update_handler

    fake_config.providers.deepseek.api_key = "sk-ds-1234567890"
    registry = _setup_registry(fake_config, tmp_path)

    with mock.patch("miqi.config.loader.save_config"):
        result = await config_update_handler(
            "req-1",
            {"config": {"agents": {"defaults": {"model": "deepseek/deepseek-v4-flash"}}}},
            "client-1", None, registry,
        )
    assert result["result"]["saved"] is True
    assert registry.bridge_context["state"].config.agents.defaults.model == "deepseek/deepseek-v4-flash"


# ── 比较并设置 expect_model（#991 review）────────────────────────────────


@pytest.mark.asyncio
async def test_config_update_expect_model_match_saves(fake_config, fake_provider, tmp_path):
    """期望值与磁盘当前模型一致时正常保存。"""
    from unittest import mock

    from miqi.runtime.config_handlers import config_update_handler

    fake_config.providers.deepseek.api_key = "sk-ds-1234567890"
    fake_config.agents.defaults.model = ""
    registry = _setup_registry(fake_config, tmp_path)

    with mock.patch("miqi.config.loader.save_config") as save:
        result = await config_update_handler(
            "req-1",
            {
                "config": {"agents": {"defaults": {"model": "deepseek/deepseek-v4-flash"}}},
                "expect_model": "",
            },
            "client-1", None, registry,
        )
    assert result["result"]["saved"] is True
    save.assert_called_once()


@pytest.mark.asyncio
async def test_config_update_expect_model_mismatch_skips(fake_config, fake_provider, tmp_path):
    """快照读取后用户已选了别的模型 → 跳过写入，保留更新的选择。"""
    from unittest import mock

    from miqi.runtime.config_handlers import config_update_handler

    fake_config.providers.deepseek.api_key = "sk-ds-1234567890"
    fake_config.agents.defaults.model = "deepseek/deepseek-v4-pro"  # 间隙中被用户改写
    registry = _setup_registry(fake_config, tmp_path)

    with mock.patch("miqi.config.loader.save_config") as save:
        result = await config_update_handler(
            "req-1",
            {
                "config": {"agents": {"defaults": {"model": "deepseek/deepseek-v4-flash"}}},
                "expect_model": "",
            },
            "client-1", None, registry,
        )
    assert result["result"] == {"saved": False, "skipped": "expect_model_mismatch"}
    save.assert_not_called()


@pytest.mark.asyncio
async def test_config_update_expect_model_ignored_for_unrelated_fields(
    fake_config, fake_provider, tmp_path,
):
    """不改写默认模型的更新不受期望值影响（只改名等字段时照常保存）。"""
    from unittest import mock

    from miqi.runtime.config_handlers import config_update_handler

    fake_config.agents.defaults.model = "deepseek/deepseek-v4-pro"  # 与期望不一致
    registry = _setup_registry(fake_config, tmp_path)

    with mock.patch("miqi.config.loader.save_config") as save:
        result = await config_update_handler(
            "req-1",
            {
                "config": {"agents": {"defaults": {"name": "renamed"}}},
                "expect_model": "",
            },
            "client-1", None, registry,
        )
    assert result["result"]["saved"] is True
    save.assert_called_once()
