from __future__ import annotations

import pytest

from miqi.runtime.app_server import AppServerError
from miqi.runtime.core_request_models import (
    CORE_METHOD_PARAM_MODELS,
    ConfigBatchWriteParams,
    ExperimentalFeatureEnablementSetParams,
    InitializeParams,
    PermissionProfileListParams,
    validate_core_params,
)


def test_core_method_map_contains_plan69_methods():
    assert set(CORE_METHOD_PARAM_MODELS) == {
        "initialize",
        "initialized",
        "status",
        "python.check",
        "config/read",
        "config/batchWrite",
        "config.get",
        "config.update",
        "model/list",
        "modelProvider/capabilities/read",
        "experimentalFeature/list",
        "experimentalFeature/enablement/set",
        "permissionProfile/list",
    }


def test_initialize_accepts_expected_wire_shape():
    parsed = validate_core_params(
        "initialize",
        {
            "clientInfo": {"name": "desktop", "title": "MiQi", "version": "1.0"},
            "clientId": "client-desktop",
            "capabilities": {
                "experimentalApi": True,
                "optOutNotificationMethods": ["fs/changed"],
            },
        },
    )

    assert isinstance(parsed, InitializeParams)
    assert parsed.client_info.name == "desktop"
    assert parsed.client_id == "client-desktop"
    assert parsed.capabilities is not None
    assert parsed.capabilities.experimental_api is True


def test_initialize_rejects_non_string_title():
    with pytest.raises(AppServerError) as exc:
        validate_core_params(
            "initialize",
            {"clientInfo": {"name": "desktop", "title": 123}},
        )

    assert exc.value.code == "INVALID_PARAMS"


def test_model_list_rejects_string_bool():
    with pytest.raises(AppServerError) as exc:
        validate_core_params("model/list", {"includeHidden": "false"})

    assert exc.value.code == "INVALID_PARAMS"


def test_config_batch_write_requires_non_empty_edits():
    with pytest.raises(AppServerError) as exc:
        validate_core_params("config/batchWrite", {"edits": []})

    assert exc.value.code == "INVALID_PARAMS"


def test_config_batch_write_accepts_delete_edit():
    parsed = validate_core_params(
        "config/batchWrite",
        {"edits": [{"op": "delete", "path": "desktop.example"}]},
    )

    assert isinstance(parsed, ConfigBatchWriteParams)
    assert parsed.edits[0].op == "delete"


def test_config_update_requires_config_object():
    with pytest.raises(AppServerError) as exc:
        validate_core_params("config.update", {"config": "bad"})

    assert exc.value.code == "INVALID_PARAMS"


def test_provider_capabilities_accepts_provider_aliases():
    parsed = validate_core_params(
        "modelProvider/capabilities/read",
        {"providerName": "openai"},
    )

    assert parsed.provider_name == "openai"


def test_experimental_feature_list_rejects_string_limit():
    with pytest.raises(AppServerError) as exc:
        validate_core_params("experimentalFeature/list", {"limit": "100"})

    assert exc.value.code == "INVALID_PARAMS"


def test_experimental_feature_enablement_requires_bool_values():
    with pytest.raises(AppServerError) as exc:
        validate_core_params(
            "experimentalFeature/enablement/set",
            {"features": {"shell": "true"}},
        )

    assert exc.value.code == "INVALID_PARAMS"


def test_experimental_feature_enablement_accepts_features():
    parsed = validate_core_params(
        "experimentalFeature/enablement/set",
        {"features": {"shell": True}},
    )

    assert isinstance(parsed, ExperimentalFeatureEnablementSetParams)
    assert parsed.features == {"shell": True}


def test_experimental_feature_enablement_accepts_enablement_alias():
    parsed = validate_core_params(
        "experimentalFeature/enablement/set",
        {"enablement": {"shell": False}},
    )

    assert parsed.features == {"shell": False}


def test_permission_profile_list_rejects_string_limit():
    with pytest.raises(AppServerError) as exc:
        validate_core_params("permissionProfile/list", {"limit": "10"})

    assert exc.value.code == "INVALID_PARAMS"


def test_permission_profile_list_accepts_cwd_cursor_limit():
    parsed = validate_core_params(
        "permissionProfile/list",
        {"cwd": "C:/workspace/project", "cursor": "abc", "limit": 25},
    )

    assert isinstance(parsed, PermissionProfileListParams)
    assert parsed.cwd == "C:/workspace/project"
    assert parsed.cursor == "abc"
    assert parsed.limit == 25
