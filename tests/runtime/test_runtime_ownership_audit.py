"""Production audit gate: AgentLoop/process_direct must not appear in
runtime or any production frontend (Phase 22)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# Directories that constitute the production runtime + frontend surface.
# Each must be free of AgentLoop() construction and process_direct() calls.
PRODUCTION_DIRS = [
    ROOT / "miqi" / "runtime",
    ROOT / "miqi" / "cli",
    ROOT / "miqi" / "bridge",
    ROOT / "miqi" / "tui",
    ROOT / "miqi" / "channels",
    ROOT / "miqi" / "cron",
]


def _non_comment_lines(text: str) -> list[str]:
    """Return lines that are not pure comments (but may be inside docstrings)."""
    return [line for line in text.split("\n") if not line.strip().startswith("#")]


def test_production_frontends_do_not_construct_agent_loop():
    """No production frontend or runtime may construct AgentLoop()."""
    offenders = []
    for base in PRODUCTION_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            for line in _non_comment_lines(_read(path)):
                if "AgentLoop(" in line:
                    offenders.append(f"{path.relative_to(ROOT)}:{line.strip()[:80]}")
    assert offenders == [], (
        f"AgentLoop() construction found in production code: {offenders}"
    )


def test_production_frontends_do_not_call_process_direct():
    """No production frontend or runtime may call process_direct()."""
    offenders = []
    for base in PRODUCTION_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            for line in _non_comment_lines(_read(path)):
                if "process_direct(" in line:
                    offenders.append(f"{path.relative_to(ROOT)}:{line.strip()[:80]}")
    assert offenders == [], (
        f"process_direct() call found in production code: {offenders}"
    )


def test_runtime_services_do_not_import_agent_loop():
    """RuntimeServices must not import AgentLoop."""
    text = _read(ROOT / "miqi" / "runtime" / "services.py")
    assert "from miqi.agent.loop import AgentLoop" not in text
    assert "AgentLoop(" not in text


def test_runtime_services_has_model_settings_not_agent_loop():
    """RuntimeServices must expose model_settings and have no agent_loop field."""
    from miqi.runtime.services import RuntimeModelSettings

    ms = RuntimeModelSettings(
        model="test",
        temperature=0.1,
        max_tokens=4096,
        max_tool_result_chars=16000,
        context_limit_chars=600000,
    )
    assert ms.model == "test"
    assert ms.temperature == 0.1
    assert ms.max_tokens == 4096
    # RuntimeModelSettings is a frozen dataclass — mutation must raise FrozenInstanceError
    from dataclasses import FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        ms.model = "other"  # type: ignore[misc]
