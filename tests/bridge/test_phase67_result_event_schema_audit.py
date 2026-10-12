from __future__ import annotations

import pytest


class _CaptureSend:
    def __init__(self):
        self.messages: list[dict] = []

    def send(self, data: dict) -> None:
        self.messages.append(data)


def _dispatch_legacy(_req_id: str, _method: str, _params: dict) -> None:
    pass


@pytest.mark.asyncio
async def test_result_and_event_schemas_match_response_models():
    from miqi.bridge.loop import BridgeRuntimeLoop
    from miqi.runtime.filesystem_response_models import (
        FILESYSTEM_EVENT_MODELS,
        FILESYSTEM_METHOD_RESULT_MODELS,
    )
    from miqi.runtime.process_response_models import (
        PROCESS_EVENT_MODELS,
        PROCESS_METHOD_RESULT_MODELS,
    )
    from miqi.runtime.protocol_model_schema import result_schema_from_model
    from miqi.runtime.sure_protocol_models import (
        SURE_EVENT_MODELS,
        SURE_METHOD_RESULT_MODELS,
    )

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()

    try:
        catalog = loop.app_server.protocol_catalog()
        by_method = {item["method"]: item for item in catalog["methods"]}

        failures: list[str] = []
        for method, model in {
            **PROCESS_METHOD_RESULT_MODELS,
            **FILESYSTEM_METHOD_RESULT_MODELS,
            **SURE_METHOD_RESULT_MODELS,
        }.items():
            expected = result_schema_from_model(model)
            actual = by_method[method]["resultSchema"]
            if actual != expected:
                failures.append(f"{method}: resultSchema mismatch")

        expected_events = {
            **PROCESS_EVENT_MODELS,
            **FILESYSTEM_EVENT_MODELS,
            **SURE_EVENT_MODELS,
        }
        for method, item in by_method.items():
            event_schemas = item.get("eventSchemas", {})
            for event_name, schema in event_schemas.items():
                expected_model = expected_events.get(event_name)
                if expected_model is None:
                    failures.append(f"{method}: unexpected event schema {event_name}")
                    continue
                expected_schema = result_schema_from_model(expected_model)
                if schema != expected_schema:
                    failures.append(f"{method}: eventSchema mismatch for {event_name}")

        assert failures == [], "\n".join(failures)
    finally:
        await loop.app_server.stop()
