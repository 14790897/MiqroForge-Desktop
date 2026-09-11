from __future__ import annotations

import pytest

from miqi.runtime.app_server import AppServer, AppServerError, ClientSessionRegistry
from miqi.runtime.config_app_handlers import register_config_app_handlers
from miqi.runtime.feature_app_handlers import register_feature_app_handlers
from miqi.runtime.model_app_handlers import register_model_app_handlers
from miqi.runtime.permission_profile_app_handlers import register_permission_profile_app_handlers


@pytest.mark.asyncio
async def test_model_list_bad_params_invalid_before_state_lookup():
    server = AppServer(ClientSessionRegistry())
    register_model_app_handlers(server)
    server.registry.bridge_context = {}

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner("req", "model/list", {"includeHidden": "false"}, "client", None)

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()


@pytest.mark.asyncio
async def test_feature_list_bad_params_invalid_before_runtime_lookup():
    server = AppServer(ClientSessionRegistry())
    register_feature_app_handlers(server)
    server.registry.bridge_context = {}

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner("req", "experimentalFeature/list", {"limit": "10"}, "client", None)

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()


@pytest.mark.asyncio
async def test_permission_profile_bad_params_invalid_before_workspace_lookup():
    server = AppServer(ClientSessionRegistry())
    register_permission_profile_app_handlers(server)
    server.registry.bridge_context = {}

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner("req", "permissionProfile/list", {"limit": "10"}, "client", None)

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()


@pytest.mark.asyncio
async def test_model_provider_capabilities_bad_params_invalid_before_state_lookup():
    server = AppServer(ClientSessionRegistry())
    register_model_app_handlers(server)
    server.registry.bridge_context = {}

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner(
            "req", "modelProvider/capabilities/read", {"providerName": 123}, "client", None,
        )

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()


@pytest.mark.asyncio
async def test_config_batch_write_bad_params_invalid_before_state_lookup():
    server = AppServer(ClientSessionRegistry())
    register_config_app_handlers(server)
    server.registry.bridge_context = {}

    with pytest.raises(AppServerError) as exc:
        await server._dispatch_inner("req", "config/batchWrite", {"edits": []}, "client", None)

    assert exc.value.code == "INVALID_PARAMS"
    await server.stop()
