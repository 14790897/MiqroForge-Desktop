"""Tests for the process-level skill index cache (#859)."""

from pathlib import Path

from miqi.agent.skills import SkillsLoader, invalidate_skill_index


def _make_skill(parent: Path, name: str, description: str) -> None:
    skill_dir = parent / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _loader(tmp_path: Path, ws_name: str) -> tuple[SkillsLoader, Path]:
    """Build a SkillsLoader over a temp workspace with an empty builtin dir."""
    workspace = tmp_path / ws_name
    builtin = tmp_path / "builtin"
    builtin.mkdir(exist_ok=True)
    return SkillsLoader(workspace=workspace, builtin_skills_dir=builtin), workspace


def test_two_loaders_share_directory_scan(tmp_path, monkeypatch):
    """Two SkillsLoader instances over one workspace share the dir scan."""
    loader1, workspace = _loader(tmp_path, "ws")
    _make_skill(workspace / "skills", "a-skill", "Alpha")
    _make_skill(workspace / "skills", "b-skill", "Beta")
    loader2 = SkillsLoader(workspace=workspace, builtin_skills_dir=tmp_path / "builtin")

    assert {s["name"] for s in loader1.list_skills(False)} == {"a-skill", "b-skill"}

    real_iterdir = Path.iterdir
    calls = {"n": 0}

    def counting_iterdir(self, *args, **kwargs):
        calls["n"] += 1
        return real_iterdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "iterdir", counting_iterdir)
    assert {s["name"] for s in loader2.list_skills(False)} == {"a-skill", "b-skill"}
    assert calls["n"] == 0  # fully served from the shared index


def test_invalidate_resets_long_lived_loader(tmp_path):
    """invalidate_skill_index makes an existing loader see new on-disk skills."""
    loader, workspace = _loader(tmp_path, "ws")
    _make_skill(workspace / "skills", "a-skill", "Alpha")

    assert {s["name"] for s in loader.list_skills(False)} == {"a-skill"}

    _make_skill(workspace / "skills", "new-skill", "New")
    invalidate_skill_index(workspace)

    assert {s["name"] for s in loader.list_skills(False)} == {"a-skill", "new-skill"}


def test_invalidate_is_scoped_to_workspace(tmp_path):
    """invalidate for one workspace does not reset another workspace's index."""
    loader_a, ws_a = _loader(tmp_path, "ws-a")
    loader_b, ws_b = _loader(tmp_path, "ws-b")
    _make_skill(ws_a / "skills", "a-skill", "Alpha")
    _make_skill(ws_b / "skills", "b-skill", "Beta")

    assert {s["name"] for s in loader_a.list_skills(False)} == {"a-skill"}
    assert {s["name"] for s in loader_b.list_skills(False)} == {"b-skill"}

    invalidate_skill_index(ws_a)
    # ws_b's index must stay intact — loader_b still serves from cache.
    assert {s["name"] for s in loader_b.list_skills(False)} == {"b-skill"}
