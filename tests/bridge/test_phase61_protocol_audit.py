from __future__ import annotations

import pytest

# ── Corrected required fields per-handler audit ───────────────────────────
# Maps method → expected required params (as exported in paramsSchema.required)

CORRECTED_REQUIRED: dict[str, list[str]] = {
    "command/exec": ["command"],
    "command/exec/write": ["processId"],
    "command/exec/terminate": ["processId"],
    "process/spawn": ["command", "processHandle", "cwd"],
    "process/writeStdin": ["processHandle"],
    "process/kill": ["processHandle"],
    "fs/writeFile": ["path", "dataBase64"],
    "fuzzyFileSearch": ["query", "roots"],
    "fuzzyFileSearch/sessionStart": ["sessionId", "roots"],
}

UNSUPPORTED_METHODS = {
    "command/exec/resize",
    "process/resizePty",
}

PLAN61_TYPED_METHODS = {
    "initialize",
    "turn/start",
    "turn/interrupt",
    "turn/steer",
    "thread/compact/start",
    "thread/inject_items",
    "thread/shellCommand",
    "command/exec",
    "command/exec/write",
    "command/exec/resize",
    "command/exec/terminate",
    "process/spawn",
    "process/writeStdin",
    "process/resizePty",
    "process/kill",
    "fs/readFile",
    "fs/writeFile",
    "fs/createDirectory",
    "fs/getMetadata",
    "fs/readDirectory",
    "fs/remove",
    "fs/copy",
    "fs/watch",
    "fs/unwatch",
    "fuzzyFileSearch",
    "fuzzyFileSearch/sessionStart",
    "fuzzyFileSearch/sessionUpdate",
    "fuzzyFileSearch/sessionStop",
    "replay.turns",
    "replay.timeline",
    "replay.messages",
}


class _CaptureSend:
    def __init__(self):
        self.messages: list[dict] = []

    def send(self, data: dict) -> None:
        self.messages.append(data)


def _dispatch_legacy(_req_id: str, _method: str, _params: dict) -> None:
    pass


@pytest.mark.asyncio
async def test_phase61_typed_methods_have_explicit_non_legacy_specs():
    from miqi.bridge.loop import BridgeRuntimeLoop

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()
    catalog = loop.app_server.protocol_catalog()
    by_method = {item["method"]: item for item in catalog["methods"]}

    missing = sorted(PLAN61_TYPED_METHODS - set(by_method))
    assert missing == []

    legacy = sorted(
        method
        for method in PLAN61_TYPED_METHODS
        if by_method[method]["stability"] == "legacy"
    )
    assert legacy == []

    await loop.app_server.stop()


@pytest.mark.asyncio
async def test_phase61_protocol_catalog_has_no_duplicate_methods():
    from miqi.bridge.loop import BridgeRuntimeLoop

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()
    catalog = loop.app_server.protocol_catalog()
    names = [item["method"] for item in catalog["methods"]]

    assert len(names) == len(set(names))

    await loop.app_server.stop()


@pytest.mark.asyncio
async def test_phase61_protocol_catalog_registered_on_bridge_loop():
    from miqi.bridge.loop import BridgeRuntimeLoop

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()

    assert "protocol/catalog" in loop.app_server._methods

    await loop.app_server.stop()


# ── Corrected required fields audit ──────────────────────────────────────


@pytest.mark.asyncio
async def test_corrected_required_fields_match_handler_params():
    """Verify that corrected required fields are exported correctly."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()
    catalog = loop.app_server.protocol_catalog()
    by_method = {item["method"]: item for item in catalog["methods"]}

    failures: list[str] = []
    for method, expected in sorted(CORRECTED_REQUIRED.items()):
        entry = by_method.get(method)
        if entry is None:
            failures.append(f"{method}: not found in catalog")
            continue
        actual = entry.get("paramsSchema", {}).get("required", [])
        if sorted(actual) != sorted(expected):
            failures.append(
                f"{method}: expected required={sorted(expected)}, "
                f"got required={sorted(actual)}"
            )

    assert failures == [], "\n".join(failures)

    await loop.app_server.stop()


@pytest.mark.asyncio
async def test_unsupported_methods_have_stability_and_description():
    """Verify unsupported methods have stability/scope but no required fields."""
    from miqi.bridge.loop import BridgeRuntimeLoop

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()
    catalog = loop.app_server.protocol_catalog()
    by_method = {item["method"]: item for item in catalog["methods"]}

    for method in sorted(UNSUPPORTED_METHODS):
        entry = by_method.get(method)
        assert entry is not None, f"{method} not in catalog"
        assert entry["stability"] in ("experimental", "deprecated"), (
            f"{method}: expected experimental/deprecated stability, "
            f"got {entry['stability']}"
        )
        assert entry["scope"] in ("process", "filesystem", "debug", "session"), (
            f"{method}: expected valid scope, got {entry['scope']}"
        )
        assert entry.get("description"), (
            f"{method}: expected a description explaining unsupported status"
        )
        assert "unsupported" in (entry.get("description") or "").lower(), (
            f"{method}: description should mention unsupported: {entry.get('description')}"
        )
        # No required fields declared
        required = entry.get("paramsSchema", {}).get("required", [])
        assert required == [], (
            f"{method}: unsupported methods should have no required fields, got {required}"
        )

    await loop.app_server.stop()
