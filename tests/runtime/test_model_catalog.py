"""Tests for miqi.runtime.model_catalog."""

from __future__ import annotations

from miqi.runtime.model_catalog import ModelCatalog


def test_model_catalog_includes_current_config_model():
    """The currently configured model appears in the list and is marked default."""
    catalog = ModelCatalog(current_config_model="anthropic/claude-opus-4-5")
    models = catalog.list_models()

    current = next((m for m in models if m.id == "anthropic/claude-opus-4-5"), None)
    assert current is not None, "current configured model must appear"
    assert current.default is True


def test_model_catalog_filters_hidden_models():
    """include_hidden=False excludes hidden rows."""
    catalog = ModelCatalog(current_config_model="anthropic/claude-opus-4-5")

    visible = catalog.list_models(include_hidden=False)
    all_models = catalog.list_models(include_hidden=True)

    visible_ids = {m.id for m in visible}
    all_ids = {m.id for m in all_models}
    hidden_ids = all_ids - visible_ids

    # At least one hidden model should be filtered out
    assert len(hidden_ids) > 0, "expected at least one hidden model"
    for hid in hidden_ids:
        hidden_model = next(m for m in all_models if m.id == hid)
        assert hidden_model.hidden is True


def test_model_catalog_preserves_reasoning_effort_order():
    """Supported reasoning efforts are preserved in declaration order."""
    catalog = ModelCatalog(current_config_model="anthropic/claude-opus-4-5")
    models = catalog.list_models()

    opus = next(m for m in models if m.id == "anthropic/claude-opus-4-5")
    assert opus.supported_reasoning_efforts == ["low", "medium", "high"]


def test_provider_capabilities_project_registry_flags():
    """Provider capabilities reflect the ProviderSpec flags from the registry."""
    catalog = ModelCatalog(current_config_model="anthropic/claude-opus-4-5")

    caps = catalog.get_capabilities("anthropic")
    assert caps.provider == "anthropic"
    assert caps.provider_type == "anthropic"
    assert caps.is_gateway is False
    assert caps.is_local is False
    assert caps.supports_streaming is True
    assert caps.supports_tools is True
    # Anthropic supports prompt caching
    assert caps.supports_prompt_caching is True


def test_provider_capabilities_do_not_include_api_key():
    """Capabilities must not expose any API key, token, or secret."""
    catalog = ModelCatalog(current_config_model="anthropic/claude-opus-4-5")

    for provider in ["anthropic", "openai", "deepseek"]:
        caps = catalog.get_capabilities(provider)
        d = caps.to_dict()
        for key in d:
            assert "key" not in key.lower() or "api" not in key.lower(), (
                f"Capabilities for {provider} should not contain api_key"
            )
        for val in d.values():
            if isinstance(val, str):
                assert "sk-" not in val.lower(), (
                    f"Capabilities for {provider} should not contain raw key"
                )


def test_model_catalog_unknown_model_appears_visible():
    """A configured model not in the built-in catalog still appears as visible+default."""
    catalog = ModelCatalog(current_config_model="custom/some-experimental-model")
    models = catalog.list_models(include_hidden=False)

    current = next((m for m in models if m.id == "custom/some-experimental-model"), None)
    assert current is not None
    assert current.default is True
    assert current.hidden is False


def test_model_catalog_covers_common_preset_models():
    """Issue #788: 常用模型预设必须覆盖核心常用模型（provider/model 格式）。"""
    catalog = ModelCatalog(current_config_model="")
    ids = {m.id for m in catalog.list_models()}
    for expected in [
        "deepseek/deepseek-v4-flash",
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "anthropic/claude-sonnet-4-5",
        "anthropic/claude-opus-4-5",
        "gemini/gemini-2.5-pro",
        "dashscope/qwen-max",
        "dashscope/qwen-plus",
        "dashscope/qwen-turbo",
        "moonshot/kimi-k2.5",
        "zhipu/glm-4",
        "minimax/minimax-m1",
    ]:
        assert expected in ids, f"missing common preset model: {expected}"

