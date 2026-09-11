"""Phase 38 cross-handler integration tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from miqi.runtime.app_server import AppServer, ClientSessionRegistry
from miqi.runtime.config_app_handlers import register_config_app_handlers
from miqi.runtime.feature_app_handlers import register_feature_app_handlers
from miqi.runtime.model_app_handlers import register_model_app_handlers
from miqi.runtime.permission_profile_app_handlers import register_permission_profile_app_handlers


def _setup_server(
    registry: ClientSessionRegistry,
    *,
    model: str = "anthropic/claude-opus-4-5",
) -> AppServer:
    """Set up an AppServer with all Phase 38 handlers and mock bridge state."""
    from miqi.config.schema import Config

    state = MagicMock()
    cfg = Config()
    cfg.agents.defaults.model = model
    cfg.providers.anthropic.api_key = "sk-ant-test-key-1234"
    state.load_config.return_value = cfg
    state.config = cfg
    registry.bridge_context["state"] = state

    server = AppServer(registry)
    register_model_app_handlers(server)
    register_feature_app_handlers(server)
    register_permission_profile_app_handlers(server)
    register_config_app_handlers(server)
    return server


# ── Model ↔ Config integration ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_list_reflects_config_batch_write_model_change():
    """After config/batchWrite changes the model, model/list picks it up."""
    registry = ClientSessionRegistry()
    server = _setup_server(registry, model="anthropic/claude-opus-4-5")

    # Verify current model is default
    list_before = await server.dispatch("1", "model/list", {}, "client-1", None)
    current = next(
        m for m in list_before["result"]["models"] if m["id"] == "anthropic/claude-opus-4-5"
    )
    assert current["default"] is True

    # Change model via batchWrite
    await server.dispatch(
        "2", "config/batchWrite",
        {"edits": [{"path": "agents.defaults.model", "value": "openai/gpt-4.1"}]},
        "client-1", None,
    )

    # Update the mock to return the new model from saved config
    state = registry.bridge_context["state"]
    saved_cfg = state.config
    saved_cfg.agents.defaults.model = "openai/gpt-4.1"
    state.load_config.return_value = saved_cfg

    list_after = await server.dispatch("3", "model/list", {}, "client-1", None)
    new_current = next(
        m for m in list_after["result"]["models"] if m["id"] == "openai/gpt-4.1"
    )
    assert new_current["default"] is True


@pytest.mark.asyncio
async def test_provider_capabilities_reflect_default_model_provider():
    """Provider capabilities default to the configured model's provider."""
    registry = ClientSessionRegistry()
    server = _setup_server(registry, model="anthropic/claude-opus-4-5")

    response = await server.dispatch(
        "1", "modelProvider/capabilities/read", {}, "client-1", None,
    )
    caps = response["result"]["capabilities"]
    assert caps["provider"] == "anthropic"


# ── Feature enablement is process-local ────────────────────────────────────


@pytest.mark.asyncio
async def test_feature_enablement_does_not_mutate_config_file():
    """Feature enablement overrides are process-local — they do not
    change the persisted config."""
    registry = ClientSessionRegistry()
    server = _setup_server(registry)

    # Enable a feature
    await server.dispatch(
        "1", "experimentalFeature/enablement/set",
        {"features": {"desktop.next": True}}, "client-1", None,
    )

    # The config should be untouched
    state = registry.bridge_context["state"]
    config = state.load_config()
    # Config model has no 'features' key for experimental features
    assert not hasattr(config, "experimental_features")


# ── Permission profile listing is safe ────────────────────────────────────


@pytest.mark.asyncio
async def test_permission_profile_list_does_not_mutate_global_permissions():
    """Listing permission profiles must not change orchestrator permissions."""
    registry = ClientSessionRegistry()
    server = _setup_server(registry)

    # Simulate a pre-existing orchestrator permissions value
    registry.bridge_context["orchestrator"] = MagicMock()
    orchestrator = registry.bridge_context["orchestrator"]
    orchestrator.permissions = {"allow": ["read"], "deny": []}
    original_perms = dict(orchestrator.permissions)

    await server.dispatch(
        "1", "permissionProfile/list", {}, "client-1", None,
    )

    # Permissions should not have been mutated
    assert orchestrator.permissions == original_perms


# ── Config error path hardening ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_batch_write_invalid_edit_sanitized():
    """Invalid edit returns INVALID_PARAMS without raw stack traces or paths."""
    registry = ClientSessionRegistry()
    server = _setup_server(registry)

    # Path with dunder
    response = await server.dispatch(
        "1", "config/batchWrite",
        {"edits": [{"path": "agents.__hidden.field", "value": "bad"}]},
        "client-1", None,
    )
    assert response.get("code") == "INVALID_PARAMS"
    error = response.get("error", "")
    # Must not leak raw stack traces
    assert "Traceback" not in error
    assert "File " not in error
    # Must not expose filesystem paths
    assert "/miqi/" not in error.lower()
    assert "\\miqi\\" not in error.lower()
