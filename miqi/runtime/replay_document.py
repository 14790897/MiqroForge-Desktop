"""Deterministic replay document helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from miqi.runtime.ledger_runtime import LedgerItem
from miqi.runtime.replay_protocol import ReplayDiffView

DOCUMENT_VERSION = 1


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    digest = hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def ledger_item_to_dict(item: LedgerItem) -> dict[str, Any]:
    return {
        "itemId": item.item_id,
        "sessionId": item.session_id,
        "threadId": item.thread_id,
        "turnId": item.turn_id,
        "seq": item.seq,
        "itemType": item.item_type,
        "role": item.role,
        "content": item.content,
        "payload": dict(item.payload),
        "createdAt": item.created_at,
    }


def timeline_to_dict(timeline: Any) -> dict[str, Any]:
    data = asdict(timeline)
    return data


def canonical_replay_payload(
    *,
    thread_id: str,
    session_id: str,
    source: str,
    turns: list[dict[str, Any]],
    provider_messages: list[dict[str, Any]],
    integrity: dict[str, Any],
    raw_ledger_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "version": DOCUMENT_VERSION,
        "threadId": thread_id,
        "sessionId": session_id,
        "source": source,
        "turns": list(turns),
        "providerMessages": list(provider_messages),
        "integrity": dict(integrity),
        "rawLedgerItems": list(raw_ledger_items or []),
    }


def with_document_hash(payload: dict[str, Any]) -> dict[str, Any]:
    without_hash = dict(payload)
    without_hash.pop("documentHash", None)
    payload = dict(payload)
    payload["documentHash"] = stable_hash(without_hash)
    return payload


def diff_replay_documents(left: dict[str, Any], right: dict[str, Any]) -> ReplayDiffView:
    left_hash = left.get("documentHash") or stable_hash(left)
    right_hash = right.get("documentHash") or stable_hash(right)
    differences: list[dict[str, Any]] = []

    for path in ["threadId", "sessionId", "turns", "providerMessages", "integrity"]:
        if left.get(path) != right.get(path):
            differences.append({
                "path": path,
                "left": left.get(path),
                "right": right.get(path),
            })

    return ReplayDiffView(
        same_hash=left_hash == right_hash and not differences,
        left_hash=left_hash,
        right_hash=right_hash,
        differences=differences,
    )
