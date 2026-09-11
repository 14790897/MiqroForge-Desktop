"""Codex-style experimental feature AppServer handlers."""

from __future__ import annotations

from typing import Any

import miqi.runtime.protocol_specs as protocol_specs
from miqi.runtime.app_server import AppServer, get_bridge_context
from miqi.runtime.core_request_models import validate_core_params
from miqi.runtime.feature_runtime import FeatureRuntime


def _get_feature_runtime(registry: Any) -> FeatureRuntime:
    fr = get_bridge_context(registry, "feature_runtime", None)
    if fr is None:
        fr = FeatureRuntime()
        registry.bridge_context["feature_runtime"] = fr
    return fr


def register_feature_app_handlers(server: AppServer) -> None:

    async def _experimental_feature_list(request_id, params, client_id, session_id, registry):
        """experimentalFeature/list — paginated feature catalog.

        Params:
            cursor: str | None
            limit: int = 100
            threadId: str | None (ignored in this implementation)
        Response:
            {"data": [...], "nextCursor": str|None}
        """
        typed = validate_core_params("experimentalFeature/list", params)
        fr = _get_feature_runtime(registry)
        page = fr.list_features(cursor=typed.cursor, limit=typed.limit)
        return {"result": page}

    async def _experimental_feature_enablement_set(request_id, params, client_id, session_id, registry):
        """experimentalFeature/enablement/set — toggle feature overrides.

        Params:
            features: dict[str, bool]  or  enablement: dict[str, bool]
        Response:
            {"saved": True, "ignored": [...]}
        """
        typed = validate_core_params("experimentalFeature/enablement/set", params)
        fr = _get_feature_runtime(registry)
        ignored = fr.set_enablement(typed.features)
        return {"result": {"saved": True, "ignored": ignored}}

    server.register_method(
        "experimentalFeature/list",
        _experimental_feature_list,
        spec=protocol_specs.EXPERIMENTAL_FEATURE_LIST,
    )
    server.register_method(
        "experimentalFeature/enablement/set",
        _experimental_feature_enablement_set,
        spec=protocol_specs.EXPERIMENTAL_FEATURE_ENABLEMENT_SET,
    )
