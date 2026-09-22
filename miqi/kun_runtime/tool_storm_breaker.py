"""Tool storm breaker — turn-scoped repeat-loop guard.

Aligns with KUN ``loop/tool-storm-breaker.ts``.
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_WINDOW_SIZE = 8
DEFAULT_THRESHOLD = 3
STORM_EXEMPT_TOOLS = frozenset({
    "request_user_input", "user_input", "ask_user", "ask_user_confirm_card",
})


class ToolStormBreaker:
    """Prevents repeated identical tool calls from inflating history."""

    def __init__(self, window_size: int = DEFAULT_WINDOW_SIZE, threshold: int = DEFAULT_THRESHOLD):
        self._window_size = max(1, window_size)
        self._threshold = max(2, threshold)
        self._recent: list[dict[str, Any]] = []

    def inspect(self, call_name: str, call_args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Check a tool call. Returns {'suppress': bool, 'reason': str|None}."""
        if call_name in STORM_EXEMPT_TOOLS:
            return {"suppress": False, "reason": None}

        args = call_args or {}
        args_fp = _stable_json(args)

        count = sum(
            1 for r in self._recent
            if r["name"] == call_name and r["args_fp"] == args_fp
        )

        if count >= self._threshold - 1:
            return {
                "suppress": True,
                "reason": (
                    f"{call_name} was called with identical arguments {count + 1} times "
                    "in this turn; repeat-loop guard suppressed the duplicate."
                ),
            }

        self._recent.append({"name": call_name, "args_fp": args_fp})
        while len(self._recent) > self._window_size:
            self._recent.pop(0)

        return {"suppress": False, "reason": None}

    def reset(self) -> None:
        self._recent.clear()


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
