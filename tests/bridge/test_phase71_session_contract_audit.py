from __future__ import annotations

import pytest

from miqi.bridge.loop import BridgeRuntimeLoop
from miqi.runtime.protocol_model_schema import params_schema_from_model, result_schema_from_model
from miqi.runtime.session_request_models import SESSION_METHOD_PARAM_MODELS
from miqi.runtime.session_response_models import SESSION_METHOD_RESULT_MODELS


class _CaptureSend:
    def __init__(self):
        self.messages: list[dict] = []

    def send(self, data: dict) -> None:
        self.messages.append(data)


def _dispatch_legacy(_req_id: str, _method: str, _params: dict) -> None:
    pass


@pytest.mark.asyncio
async def test_plan71_session_methods_are_non_legacy_and_model_derived():
    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()
    try:
        catalog = loop.app_server.protocol_catalog()
        by_method = {item["method"]: item for item in catalog["methods"]}

        for method, model in SESSION_METHOD_PARAM_MODELS.items():
            entry = by_method[method]
            assert entry["stability"] != "legacy", method
            assert entry["paramsSchema"] == params_schema_from_model(model), method
            assert entry["resultSchema"] == result_schema_from_model(
                SESSION_METHOD_RESULT_MODELS[method],
            ), method
    finally:
        await loop.app_server.stop()


@pytest.mark.asyncio
async def test_plan71_session_contract_counts():
    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()
    try:
        catalog = loop.app_server.protocol_catalog()
        typed = [item for item in catalog["methods"] if item["stability"] != "legacy"]
        legacy = [item for item in catalog["methods"] if item["stability"] == "legacy"]

        assert len(typed) >= 65
        assert len(legacy) <= 89
    finally:
        await loop.app_server.stop()
