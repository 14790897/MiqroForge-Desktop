"""Build deterministic App Server protocol compatibility snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from miqi.bridge.loop import BridgeRuntimeLoop
from miqi.runtime.export_app_protocol_ts import render_typescript_contract

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = ROOT / "tests" / "fixtures" / "protocol" / "app_protocol_snapshot.v1.json"
SNAPSHOT_SCHEMA_VERSION = 1


class _CaptureSend:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def send(self, data: dict[str, Any]) -> None:
        self.messages.append(data)


def _dispatch_legacy(_req_id: str, _method: str, _params: dict[str, Any]) -> None:
    return None


def canonical_json(data: Any) -> str:
    """Return stable JSON text for snapshot comparison and file output."""

    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _method_counts(catalog: dict[str, Any]) -> dict[str, int]:
    methods = catalog["methods"]
    typed = [item for item in methods if item["stability"] != "legacy"]
    legacy = [item for item in methods if item["stability"] == "legacy"]
    return {
        "total": len(methods),
        "typed": len(typed),
        "legacy": len(legacy),
    }


async def build_protocol_snapshot_async() -> dict[str, Any]:
    """Build the protocol snapshot from the real App Server registration path."""

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()
    try:
        catalog = loop.app_server.protocol_catalog()
    finally:
        await loop.app_server.stop()

    generated_ts = render_typescript_contract()
    return {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "catalogVersion": catalog["version"],
        "methodCounts": _method_counts(catalog),
        "catalog": catalog,
        "generatedTypes": {
            "source": "miqi.runtime.export_app_protocol_ts.render_typescript_contract",
            "outputPath": "apps/desktop/src/shared/app-protocol.ts",
            "sha256": hashlib.sha256(generated_ts.encode("utf-8")).hexdigest(),
        },
    }


def build_protocol_snapshot() -> dict[str, Any]:
    return asyncio.run(build_protocol_snapshot_async())


def write_protocol_snapshot(path: Path = DEFAULT_SNAPSHOT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(build_protocol_snapshot()), encoding="utf-8")


def main() -> None:
    write_protocol_snapshot()


if __name__ == "__main__":
    main()
