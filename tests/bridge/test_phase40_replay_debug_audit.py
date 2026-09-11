"""Phase 40 replay debug audit — method registration, public helpers,
safety checks (no AgentLoop, no direct bridge imports).
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_MIQI = _ROOT / "miqi"


_PHASE40_METHODS = [
    "replay.turns",
    "replay.timeline",
    "replay.messages",
    "debug/replay/thread",
    "debug/replay/turn",
    "debug/replay/messages",
    "debug/replay/integrity",
    "debug/replay/export",
    "debug/replay/diff",
]


def _registered_methods() -> set[str]:
    pattern = re.compile(r'register_method\(\s*"([^"]+)"')
    methods: set[str] = set()
    for path in _MIQI.rglob("*.py"):
        methods.update(pattern.findall(path.read_text(encoding="utf-8")))
    return methods


def test_phase40_replay_methods_registered():
    registered = _registered_methods()
    missing = [method for method in _PHASE40_METHODS if method not in registered]
    assert not missing, f"Missing Phase 40 replay/debug methods: {missing}"


def test_replay_runtime_exposes_public_timeline_builder():
    path = _MIQI / "runtime" / "replay_runtime.py"
    text = path.read_text(encoding="utf-8")
    assert "def build_timeline_from_items" in text
    assert "def list_turn_ids_from_items" in text


def test_thread_projection_does_not_use_replay_new_private_builder():
    path = _MIQI / "runtime" / "thread_projection.py"
    text = path.read_text(encoding="utf-8")
    assert "ReplayRuntime.__new__" not in text
    assert "ReplayRuntime._build_timeline" not in text


def test_thread_projection_does_not_use_thread_projection_new_private():
    path = _MIQI / "runtime" / "thread_projection.py"
    text = path.read_text(encoding="utf-8")
    assert "ThreadProjectionRuntime.__new__" not in text


def test_phase40_runtime_modules_do_not_import_bridge_server():
    offenders: list[str] = []
    for module_name in [
        "replay_protocol.py",
        "replay_document.py",
        "replay_inspector.py",
        "replay_app_handlers.py",
    ]:
        path = _MIQI / "runtime" / module_name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "import miqi.bridge.server" in text or "from miqi.bridge.server import" in text:
            offenders.append(module_name)
    assert not offenders


def test_phase40_no_agentloop_or_process_direct_in_replay_debug_modules():
    offenders: list[str] = []
    for module_name in [
        "replay_protocol.py",
        "replay_document.py",
        "replay_inspector.py",
        "replay_app_handlers.py",
    ]:
        path = _MIQI / "runtime" / module_name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "AgentLoop(" in text or "process_direct(" in text:
            offenders.append(module_name)
    assert not offenders
