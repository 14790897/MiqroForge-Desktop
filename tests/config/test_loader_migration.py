"""Tests for config loader migrations — custom/* model reset (#929 / #933)."""

from __future__ import annotations

import json

from miqi.config.loader import _migrate_config, load_config


def test_migrate_resets_custom_model_to_configured_provider_model():
    """遗留 custom/* 默认模型应重置为已配置 provider 的测试模型，
    而不是无条件写无凭据的出厂默认（#933 review）。"""
    data = {
        "agents": {"defaults": {"model": "custom/my-model"}},
        "providers": {"deepseek": {"api_key": "sk-ds-1234567890"}},
    }
    migrated = _migrate_config(data)
    assert migrated["agents"]["defaults"]["model"] == "deepseek/deepseek-v4-flash"


def test_migrate_resets_custom_model_to_gateway_model():
    """已配置网关时重置为网关可路由的完整模型 id（网关在注册表最前）。"""
    data = {
        "agents": {"defaults": {"model": "custom/default"}},
        "providers": {"openrouter": {"api_key": "sk-or-1234567890"}},
    }
    migrated = _migrate_config(data)
    assert migrated["agents"]["defaults"]["model"] == "anthropic/claude-opus-4-5"


def test_migrate_clears_model_without_credentials():
    """没有任何可用凭据时清空为「未选择」状态（UI 显示未设置），
    而不是写一个无凭据、无法使用的出厂默认（#933 review）。"""
    data = {"agents": {"defaults": {"model": "custom/x"}}}
    migrated = _migrate_config(data)
    assert migrated["agents"]["defaults"]["model"] == ""


def test_migrate_ignores_camelcase_provider_keys():
    """配置 JSON 可能是 camelCase 键，迁移需兼容（#933 review）。"""
    data = {
        "agents": {"defaults": {"model": "custom/x"}},
        "providers": {"deepseek": {"apiKey": "sk-ds-1234567890"}},
    }
    migrated = _migrate_config(data)
    assert migrated["agents"]["defaults"]["model"] == "deepseek/deepseek-v4-flash"


def test_migrate_leaves_other_models_untouched():
    data = {"agents": {"defaults": {"model": "deepseek/deepseek-v4-flash"}}}
    migrated = _migrate_config(data)
    assert migrated["agents"]["defaults"]["model"] == "deepseek/deepseek-v4-flash"


def test_legacy_billing_key_does_not_break_config_load(tmp_path):
    """#960：30 分计费闸门移除后，旧版写入的 billing 键仍必须可加载。

    Config 继承 BaseSettings（extra 默认 forbid），把 billing 字段整个删掉
    会让旧 config.json 校验失败，load_config 静默回退默认空配置——providers
    全部丢失，表现为「尚未配置模型服务」。billing 字段须以透传 dict 保留。
    """
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "billing": {"enabled": True, "costPerTask": 30, "source": "desktop-agent-task"},
                "providers": {"deepseek": {"apiKey": "sk-ds-1234567890"}},
                "agents": {"defaults": {"model": "deepseek/deepseek-v4-flash"}},
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    # 关键断言：不是静默回退的默认空配置——providers 必须原样存活
    assert cfg.providers.deepseek.api_key == "sk-ds-1234567890"
    assert cfg.agents.defaults.model == "deepseek/deepseek-v4-flash"
