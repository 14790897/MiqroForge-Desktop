"""Codex-style model and provider-capability AppServer handlers."""

from __future__ import annotations

import miqi.runtime.protocol_specs as protocol_specs
from miqi.runtime.app_server import AppServer, AppServerError, get_bridge_state
from miqi.runtime.core_request_models import validate_core_params
from miqi.runtime.model_catalog import ModelCatalog


def register_model_app_handlers(server: AppServer) -> None:

    async def _model_list(request_id, params, client_id, session_id, registry):
        """model/list — deterministic model catalog.

        Params:
            includeHidden: bool = False
        Response:
            {"models": [model_dict, ...]}
        """
        typed = validate_core_params("model/list", params)
        include_hidden = typed.include_hidden

        state = get_bridge_state(registry)
        config = state.load_config()
        current_model = config.agents.defaults.model

        catalog = ModelCatalog(current_config_model=current_model)
        models = catalog.list_models(include_hidden=include_hidden)
        return {"result": {"models": [m.to_dict() for m in models]}}

    async def _model_provider_capabilities_read(request_id, params, client_id, session_id, registry):
        """modelProvider/capabilities/read — provider capabilities snapshot.

        Params:
            provider / providerName: str (optional — defaults to configured
            model's provider)
        Response:
            {"capabilities": capability_dict}
        """
        typed = validate_core_params("modelProvider/capabilities/read", params)
        provider_name = typed.provider or typed.provider_name

        state = get_bridge_state(registry)
        config = state.load_config()

        if not provider_name:
            # Default to the provider of the currently configured model
            current_model = config.agents.defaults.model
            provider_name = config.get_provider_name(current_model)

        catalog = ModelCatalog(current_config_model=config.agents.defaults.model)
        try:
            caps = catalog.get_capabilities(provider_name)
        except KeyError:
            raise AppServerError(
                f"Unknown provider: {provider_name}",
                code="NOT_FOUND",
            )

        return {"result": {"capabilities": caps.to_dict()}}

    server.register_method("model/list", _model_list, spec=protocol_specs.MODEL_LIST)
    server.register_method(
        "modelProvider/capabilities/read",
        _model_provider_capabilities_read,
        spec=protocol_specs.MODEL_PROVIDER_CAPABILITIES_READ,
    )
