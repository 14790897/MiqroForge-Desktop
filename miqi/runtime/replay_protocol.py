"""Serializable replay/debug response views."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ReplaySource = Literal["live", "stored"]
IntegritySeverity = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class ReplayIntegrityCheck:
    name: str
    ok: bool
    severity: IntegritySeverity
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "severity": self.severity,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ReplayIntegrityReport:
    thread_id: str
    session_id: str
    ok: bool
    checks: list[ReplayIntegrityCheck] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "threadId": self.thread_id,
            "sessionId": self.session_id,
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True)
class ReplayDocumentView:
    version: int
    thread_id: str
    session_id: str
    source: ReplaySource
    document_hash: str
    turns: list[dict[str, Any]]
    provider_messages: list[dict[str, Any]]
    integrity: dict[str, Any]
    raw_ledger_items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "threadId": self.thread_id,
            "sessionId": self.session_id,
            "source": self.source,
            "documentHash": self.document_hash,
            "turns": list(self.turns),
            "providerMessages": list(self.provider_messages),
            "integrity": dict(self.integrity),
            "rawLedgerItems": list(self.raw_ledger_items),
        }


@dataclass(frozen=True)
class ReplayDiffView:
    same_hash: bool
    left_hash: str
    right_hash: str
    differences: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sameHash": self.same_hash,
            "leftHash": self.left_hash,
            "rightHash": self.right_hash,
            "differences": list(self.differences),
        }
