"""Permanent-allowlist persistence tests (issue #789, 2026-09-01 review).

save_config_allowlist must write the supplied patterns as an EXACT
replacement — the previous union-with-existing merge made a clear-all
persist a no-op on disk, so cleared patterns resurrected on the next load.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from miqi.config.loader import load_config, save_config, save_config_allowlist
from miqi.config.schema import Config


@pytest.fixture
def temp_config_path(monkeypatch):
    """Point get_config_path at a temp file so tests never touch the real config."""
    import miqi.config.loader as loader_module

    path = Path(tempfile.mkdtemp()) / "config.json"
    monkeypatch.setattr(loader_module, "get_config_path", lambda: path)
    # Clear load_config's in-memory cache for the temp path.
    loader_module._cache.clear()
    return path


def _seed(path: Path, patterns: list[str]) -> None:
    config = Config()
    config.agents.permanent_approvals = patterns
    save_config(config, path)


def test_save_allowlist_replaces_disk_exactly(temp_config_path):
    """A subset save must REMOVE disk patterns not in the new set."""
    _seed(temp_config_path, ["keep", "drop-me"])
    save_config_allowlist({"keep", "new-one"})

    reloaded = load_config(temp_config_path)
    assert reloaded.agents.permanent_approvals == ["keep", "new-one"]


def test_save_allowlist_full_clear_persists_empty(temp_config_path):
    """Clearing all patterns must persist an empty list — nothing resurrects."""
    _seed(temp_config_path, ["pattern-a", "pattern-b"])
    save_config_allowlist(set())

    reloaded = load_config(temp_config_path)
    assert reloaded.agents.permanent_approvals == []
