"""Phase 36 audit tests — Codex-style thread API.

Validates that all 9 Codex-style thread methods are registered on
AppServer and that the 5 legacy dot-style thread methods still work.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_MIQI = _ROOT / "miqi"

_PHASE36_THREAD_METHODS = [
    "thread/start",
    "thread/resume",
    "thread/fork",
    "thread/read",
    "thread/turns/list",
    "thread/turns/items/list",
    "thread/name/set",
    "thread/rollback",
    "thread/loaded/list",
]


def _registered_methods() -> set[str]:
    methods: set[str] = set()
    pattern = re.compile(r'register_method\(\s*"([^"]+)"')
    for py_file in _MIQI.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        methods.update(pattern.findall(text))
    return methods


def test_phase36_codex_thread_methods_registered():
    """All 9 Codex-style thread methods must be registered."""
    registered = _registered_methods()
    missing = [m for m in _PHASE36_THREAD_METHODS if m not in registered]
    assert not missing, f"Missing Phase 36 thread methods: {missing}"


def test_phase36_legacy_dot_thread_methods_still_registered():
    """Legacy dot-style thread methods must still be registered."""
    registered = _registered_methods()
    for method in [
        "thread.create",
        "thread.list",
        "thread.rename",
        "thread.archive",
        "thread.delete",
    ]:
        assert method in registered, f"Legacy method {method} not registered"
