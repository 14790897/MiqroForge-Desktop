"""Data models for git-like task traces."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from loguru import logger

_HASH_FALLBACK_WARNED = False


@dataclass
class TaskStep:
    tool_name: str
    args_summary: str
    result_summary: str
    timestamp: float


@dataclass
class TaskTrace:
    trace_hash: str
    parent_hash: str | None
    session_id: str
    task_name: str
    goal: str
    tool_calls: list[TaskStep]
    outcome: str
    outcome_notes: str
    embedding: bytes | None
    created_at: float
    ended_at: float | None
    metadata: dict[str, Any] = field(default_factory=dict)
    similarity_score: float = 0.0


def compute_trace_hash(goal: str, tool_names: list[str]) -> str:
    """Compute the content-addressed trace hash."""
    global _HASH_FALLBACK_WARNED

    payload = (goal.strip() + "|" + ",".join(tool_names)).encode("utf-8")
    try:
        from blake3 import blake3

        return blake3(payload).hexdigest()
    except ImportError:
        import hashlib

        if not _HASH_FALLBACK_WARNED:
            logger.warning("blake3 unavailable; falling back to sha256 for trace hashes")
            _HASH_FALLBACK_WARNED = True
        return hashlib.sha256(payload).hexdigest()


def serialize_tool_calls(steps: list[TaskStep]) -> str:
    """Serialize task steps as compact JSON."""
    return json.dumps([asdict(step) for step in steps], ensure_ascii=False, separators=(",", ":"))


def deserialize_tool_calls(s: str) -> list[TaskStep]:
    """Deserialize task steps from JSON."""
    if not s:
        return []
    try:
        raw_steps = json.loads(s)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(raw_steps, list):
        return []

    steps: list[TaskStep] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        steps.append(
            TaskStep(
                tool_name=str(item.get("tool_name", "")),
                args_summary=str(item.get("args_summary", "")),
                result_summary=str(item.get("result_summary", "")),
                timestamp=float(item.get("timestamp", 0.0) or 0.0),
            )
        )
    return steps
