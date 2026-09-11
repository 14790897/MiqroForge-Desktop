"""Versioned thread export/import documents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from miqi.runtime.ledger_runtime import LedgerItem
from miqi.runtime.thread_runtime import RuntimeThread

EXPORT_VERSION = 1


@dataclass(frozen=True)
class ThreadExportDocument:
    version: int
    thread: dict[str, Any]
    ledgerItems: list[dict[str, Any]] = field(default_factory=list)  # noqa: N815
    providerMessages: list[dict[str, Any]] = field(default_factory=list)  # noqa: N815

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "thread": self.thread,
            "ledgerItems": list(self.ledgerItems),
            "providerMessages": list(self.providerMessages),
        }


def build_export_document(
    *,
    thread: RuntimeThread,
    ledger_items: list[LedgerItem],
    provider_messages: list[dict[str, Any]] | None = None,
) -> ThreadExportDocument:
    return ThreadExportDocument(
        version=EXPORT_VERSION,
        thread=asdict(thread),
        ledgerItems=[_ledger_to_dict(item) for item in ledger_items],
        providerMessages=list(provider_messages or []),
    )


def _ledger_to_dict(item: LedgerItem) -> dict[str, Any]:
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


# ── Import validation ─────────────────────────────────────────────────────


class ThreadImportError(ValueError):
    """Raised for invalid thread import documents."""


def validate_import_document(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ThreadImportError("Import document must be an object")
    if raw.get("version") != EXPORT_VERSION:
        raise ThreadImportError("Unsupported thread export version")
    thread = raw.get("thread")
    if not isinstance(thread, dict):
        raise ThreadImportError("Import document missing thread")
    if not thread.get("thread_id"):
        raise ThreadImportError("Import document missing thread_id")
    ledger_items = raw.get("ledgerItems", [])
    if not isinstance(ledger_items, list):
        raise ThreadImportError("ledgerItems must be a list")
    return raw
