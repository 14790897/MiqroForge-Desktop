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
    """无网关凭据时，网关模型与其他模型一样必须由本地 provider 凭据背书。

    #1258 起错误码改为可重试的 GATEWAY_CREDS_UNAVAILABLE：凭据缺失是暂态
    （握手文件还没落盘/写失败），报成「模型不支持」会误导用户且无法重试。
    """
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
    assert exc_info.value.code == "GATEWAY_CREDS_UNAVAILABLE"
    assert exc_info.value.recoverable is True


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


# ── #1258：网关模型「凭据暂缺」与「模型真不支持」必须分开 ────────────────
#
# 真实用户两次遇到：登录后把默认模型写成网关模型，后端回
# `Unsupported model: deepseek/deepseek-v4-flash (INVALID_PARAMS)`。
# 那不是模型选错了 —— 是后端读的握手文件 <workspace>/.qraft/token.json 不可用，
# 而渲染进程判定「网关可用」用的是内存登录态，两者之间没有一致性校验。
# 把这种暂态报成「模型不存在」，用户既看不懂也没法重试。


@pytest.mark.asyncio
async def test_config_update_reports_gateway_creds_missing_as_retryable(
    fake_config, fake_provider, tmp_path, monkeypatch,
):
    """网关模型 + 本机凭据不可用 → 可重试的 GATEWAY_CREDS_UNAVAILABLE。"""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    monkeypatch.delenv("QRAFT_GATEWAY_BASE", raising=False)
    # 不写 token 文件：握手文件缺失（写失败/尚未落盘/被读到半个文件都归这一类）
    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler(
            "req-1",
            {"config": {"agents": {"defaults": {"model": "deepseek/deepseek-v4-flash"}}}},
            "client-1", None, registry,
        )

    err = exc_info.value
    assert err.code == "GATEWAY_CREDS_UNAVAILABLE", err
    assert err.recoverable is True, err
    # 不能再说「模型不支持」——模型是对的，缺的是凭据
    assert "Unsupported model" not in err.message, err
    assert "deepseek/deepseek-v4-flash" not in err.message, err


@pytest.mark.asyncio
async def test_config_update_keeps_unsupported_model_for_non_gateway_model(
    fake_config, fake_provider, tmp_path, monkeypatch,
):
    """非网关模型（或网关凭据正常但模型不属于任何可用 provider）保持旧语义。

    收紧校验不允许：只有「网关模型 + 凭据暂缺」这一种暂态能被判成可重试。
    """
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    monkeypatch.delenv("QRAFT_GATEWAY_BASE", raising=False)
    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler(
            "req-1",
            {"config": {"agents": {"defaults": {"model": "openai/gpt-4o"}}}},
            "client-1", None, registry,
        )

    err = exc_info.value
    assert err.code == "INVALID_PARAMS", err
    assert "Unsupported model: openai/gpt-4o" in err.message, err


@pytest.mark.parametrize(
    ("status", "expect_retryable"),
    [("active", False), ("provisioning", True)],
)
@pytest.mark.asyncio
async def test_config_update_requires_active_gateway_creds(
    fake_config, fake_provider, tmp_path, monkeypatch, status, expect_retryable,
):
    """非 active 的网关凭据同样算「本机不可用」（可重试）；active 时正常保存。"""
    from unittest import mock

    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    monkeypatch.delenv("QRAFT_GATEWAY_BASE", raising=False)
    _write_qraft_gateway_token(fake_config, status=status)
    registry = _setup_registry(fake_config, tmp_path)

    if not expect_retryable:
        with mock.patch("miqi.config.loader.save_config"):
            result = await config_update_handler(
                "req-1",
                {"config": {"agents": {"defaults": {"model": "deepseek/deepseek-v4-flash"}}}},
                "client-1", None, registry,
            )
        assert result["result"]["saved"] is True
        return

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler(
            "req-1",
            {"config": {"agents": {"defaults": {"model": "deepseek/deepseek-v4-flash"}}}},
            "client-1", None, registry,
        )
    assert exc_info.value.code == "GATEWAY_CREDS_UNAVAILABLE", exc_info.value


@pytest.mark.asyncio
async def test_config_update_reports_gateway_origin_misconfig_as_not_retryable(
    fake_config, fake_provider, tmp_path, monkeypatch,
):
    """凭据齐备但 QRAFT_GATEWAY_BASE 非法（非 https）：环境问题，重试无用，
    必须与「凭据暂缺」区分开，不能都报成可重试。"""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.config_handlers import config_update_handler

    monkeypatch.setenv("QRAFT_GATEWAY_BASE", "http://gateway.example.com")
    _write_qraft_gateway_token(fake_config)
    registry = _setup_registry(fake_config, tmp_path)

    with pytest.raises(AppServerError) as exc_info:
        await config_update_handler(
            "req-1",
            {"config": {"agents": {"defaults": {"model": "deepseek/deepseek-v4-flash"}}}},
            "client-1", None, registry,
        )

    err = exc_info.value
    assert err.code == "GATEWAY_ORIGIN_INVALID", err
    assert err.recoverable is False, err


@pytest.mark.asyncio
async def test_config_batch_write_reports_gateway_creds_missing_as_retryable(
    fake_config, fake_provider, tmp_path, monkeypatch,
):
    """config.batchWrite 与 config.update 共用同一门控语义（#1258）。"""
    from miqi.runtime.app_server import AppServer
    from miqi.runtime.config_app_handlers import register_config_app_handlers

    monkeypatch.delenv("QRAFT_GATEWAY_BASE", raising=False)
    registry = _setup_registry(fake_config, tmp_path)
    server = AppServer(registry)
    register_config_app_handlers(server)

    envelope = await server.dispatch(
        "req-bw",
        "config/batchWrite",
        {"edits": [
            {"op": "set", "path": "agents.defaults.model",
             "value": "deepseek/deepseek-v4-flash"},
        ]},
        "client-1",
    )

    assert envelope.get("code") == "GATEWAY_CREDS_UNAVAILABLE", envelope


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
