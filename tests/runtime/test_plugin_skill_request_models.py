"""Tests for plugin skill request models."""

from __future__ import annotations

import pytest

from miqi.runtime.app_server import AppServerError
from miqi.runtime.plugin_skill_request_models import (
    PLUGIN_SKILL_METHOD_PARAM_MODELS,
    validate_plugin_skill_params,
)


class TestAllMethodsExist:
    def test_all_12_methods_in_param_map(self):
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
        assert set(PLUGIN_SKILL_METHOD_PARAM_MODELS) == expected


class TestPluginRead:
    def test_accepts_camel_case_plugin_name(self):
        typed = validate_plugin_skill_params("plugin/read", {"pluginName": "my-plugin"})
        assert typed.plugin_name == "my-plugin"

    def test_accepts_plugin_name(self):
        typed = validate_plugin_skill_params("plugin/read", {"plugin_name": "my-plugin"})
        assert typed.plugin_name == "my-plugin"

    def test_accepts_name(self):
        typed = validate_plugin_skill_params("plugin/read", {"name": "my-plugin"})
        assert typed.plugin_name == "my-plugin"

    def test_rejects_empty_plugin_name(self):
        with pytest.raises(AppServerError) as exc:
            validate_plugin_skill_params("plugin/read", {"pluginName": ""})
        assert exc.value.code == "INVALID_PARAMS"

    def test_rejects_missing_plugin_name(self):
        with pytest.raises(AppServerError) as exc:
            validate_plugin_skill_params("plugin/read", {})
        assert exc.value.code == "INVALID_PARAMS"

    def test_accepts_camel_case_marketplace_name(self):
        typed = validate_plugin_skill_params("plugin/read", {"pluginName": "p", "marketplaceName": "gh"})
        assert typed.marketplace_name == "gh"

    def test_accepts_marketplace_name(self):
        typed = validate_plugin_skill_params("plugin/read", {"pluginName": "p", "marketplace_name": "gh"})
        assert typed.marketplace_name == "gh"


class TestPluginSkillRead:
    def test_rejects_missing_skill_name(self):
        with pytest.raises(AppServerError) as exc:
            validate_plugin_skill_params("plugin/skill/read", {"pluginName": "p"})
        assert exc.value.code == "INVALID_PARAMS"

    def test_accepts_required_fields(self):
        typed = validate_plugin_skill_params("plugin/skill/read", {"pluginName": "p", "skillName": "s"})
        assert typed.plugin_name == "p"
        assert typed.skill_name == "s"


class TestPluginInstall:
    def test_rejects_missing_source(self):
        with pytest.raises(AppServerError) as exc:
            validate_plugin_skill_params("plugin/install", {"pluginName": "p"})
        assert exc.value.code == "INVALID_PARAMS"

    def test_accepts_required_fields(self):
        typed = validate_plugin_skill_params("plugin/install", {"pluginName": "p", "source": "s"})
        assert typed.plugin_name == "p"
        assert typed.source == "s"


class TestMarketplaceAdd:
    def test_rejects_empty_name(self):
        with pytest.raises(AppServerError) as exc:
            validate_plugin_skill_params("marketplace/add", {"name": "", "source": "s"})
        assert exc.value.code == "INVALID_PARAMS"


class TestSkillsList:
    def test_normalizes_cwd_string_to_cwds(self):
        typed = validate_plugin_skill_params("skills/list", {"cwd": "/some/path"})
        assert typed.cwds == ["/some/path"]

    def test_cwds_passed_directly(self):
        typed = validate_plugin_skill_params("skills/list", {"cwds": ["/a", "/b"]})
        assert typed.cwds == ["/a", "/b"]


class TestSkillsExtraRootsSet:
    def test_rejects_string_roots(self):
        with pytest.raises(AppServerError) as exc:
            validate_plugin_skill_params("skills/extraRoots/set", {"roots": "C:/x"})
        assert exc.value.code == "INVALID_PARAMS"


class TestHooksList:
    def test_rejects_string_cwds(self):
        with pytest.raises(AppServerError) as exc:
            validate_plugin_skill_params("hooks/list", {"cwds": "C:/x"})
        assert exc.value.code == "INVALID_PARAMS"
