"""Durable audit: legacy AgentLoop module and public API are removed.

Phase 48 contract — these tests MUST remain red until the AgentLoop
module, public export, and compatibility identifiers are fully deleted.
"""

from __future__ import annotations

import ast
from pathlib import Path

import miqi.agent

ROOT = Path(__file__).resolve().parents[2]
MIQI = ROOT / "miqi"


def _python_files() -> list[Path]:
    return sorted(MIQI.rglob("*.py"))


def test_legacy_agent_loop_module_is_deleted() -> None:
    assert not (MIQI / "agent" / "loop.py").exists()


def test_agent_loop_is_not_public_api() -> None:
    assert not hasattr(miqi.agent, "AgentLoop")
    assert "AgentLoop" not in getattr(miqi.agent, "__all__", [])


def test_production_code_has_no_legacy_agent_loop_import_or_construction() -> None:
    # Exclude kun_runtime — it uses the *new* AgentLoop from
    # miqi.kun_runtime.loop, not the legacy miqi.agent.loop one.
    excluded_prefixes = ("miqi/kun_runtime/", "miqi\\kun_runtime\\")
    violations: list[str] = []
    for path in _python_files():
        rel = str(path.relative_to(ROOT))
        if any(rel.startswith(p) for p in excluded_prefixes):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "miqi.agent.loop":
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: import")
            if isinstance(node, ast.Import):
                if any(alias.name == "miqi.agent.loop" for alias in node.names):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: import")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "AgentLoop":
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: call")
    assert violations == []


def test_compatibility_identifiers_are_absent_from_production_code() -> None:
    violations: list[str] = []
    forbidden = (
        "RuntimeAgentLoopCompat",
        "services.agent_loop",
        "configure_agent_orchestrator",
    )
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                violations.append(f"{path.relative_to(ROOT)}: {token}")
    assert violations == []
