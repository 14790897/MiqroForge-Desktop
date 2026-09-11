"""Tests for plugin skill response models."""

from __future__ import annotations

from miqi.runtime.plugin_skill_response_models import PLUGIN_SKILL_METHOD_RESULT_MODELS
from miqi.runtime.protocol_model_schema import result_schema_from_model


class TestAllMethodsExist:
    def test_all_12_methods_in_result_map(self):
        expected = {
            "plugin/list",
            "plugin/installed",
            "plugin/read",
            "plugin/skill/read",
            "plugin/install",
            "plugin/uninstall",
            "marketplace/add",
            "marketplace/remove",
            "marketplace/upgrade",
            "skills/list",
            "skills/extraRoots/set",
            "hooks/list",
        }
        assert set(PLUGIN_SKILL_METHOD_RESULT_MODELS) == expected


class TestWireFields:
    def test_plugin_uninstall_uses_plugin_id(self):
        schema = result_schema_from_model(PLUGIN_SKILL_METHOD_RESULT_MODELS["plugin/uninstall"])
        props = schema["properties"]
        assert "pluginId" in props
        assert "removed" in props

    def test_marketplace_remove_uses_marketplace_name(self):
        schema = result_schema_from_model(PLUGIN_SKILL_METHOD_RESULT_MODELS["marketplace/remove"])
        props = schema["properties"]
        assert "marketplaceName" in props

    def test_marketplace_add_uses_already_present(self):
        schema = result_schema_from_model(PLUGIN_SKILL_METHOD_RESULT_MODELS["marketplace/add"])
        props = schema["properties"]
        assert "alreadyPresent" in props

    def test_marketplace_upgrade_uses_selected_marketplaces(self):
        schema = result_schema_from_model(PLUGIN_SKILL_METHOD_RESULT_MODELS["marketplace/upgrade"])
        props = schema["properties"]
        assert "selectedMarketplaces" in props

    def test_plugin_list_uses_featured_plugin_ids(self):
        schema = result_schema_from_model(PLUGIN_SKILL_METHOD_RESULT_MODELS["plugin/list"])
        props = schema["properties"]
        assert "featuredPluginIds" in props
        assert "marketplaceLoadErrors" in props
