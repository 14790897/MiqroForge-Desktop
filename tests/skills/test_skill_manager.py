"""Tests for miqi.skills.skill_manager."""

import tempfile
from pathlib import Path

from miqi.skills.skill_manager import (
    SkillInjections,
    SkillScope,
    SkillsManager,
)


def _make_skill_dir(parent: Path, name: str, frontmatter: str, body: str) -> Path:
    """Create a minimal skill directory with SKILL.md."""
    skill_dir = parent / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    content = f"---\n{frontmatter}\n---\n\n{body}"
    skill_md.write_text(content, encoding="utf-8")
    return skill_dir


def test_parse_skill_file():
    with tempfile.TemporaryDirectory() as tmp:
        system_dir = Path(tmp) / "system"
        system_dir.mkdir()
        user_dir = Path(tmp) / "user"
        user_dir.mkdir()

        _make_skill_dir(
            system_dir, "test-skill",
            "name: test-skill\ndescription: A test skill\ntriggers: [test, demo]",
            "# Test Skill\n\nThis is a test skill body.",
        )

        mgr = SkillsManager(
            system_skills_dir=system_dir,
            user_skills_dir=user_dir,
        )
        outcome = mgr.load_all()
        assert len(outcome.skills) == 1
        assert outcome.skills[0].name == "test-skill"
        assert outcome.skills[0].scope == SkillScope.SYSTEM
        assert "test" in outcome.skills[0].triggers


def test_load_all_scopes():
    with tempfile.TemporaryDirectory() as tmp:
        system_dir = Path(tmp) / "system"
        system_dir.mkdir()
        user_dir = Path(tmp) / "user"
        user_dir.mkdir()
        workspace_dir = Path(tmp) / "workspace"
        workspace_dir.mkdir()

        _make_skill_dir(system_dir, "sys-skill",
                        "name: sys-skill\ndescription: System skill", "Body")
        _make_skill_dir(user_dir, "user-skill",
                        "name: user-skill\ndescription: User skill", "Body")

        mgr = SkillsManager(
            system_skills_dir=system_dir,
            user_skills_dir=user_dir,
            workspace=workspace_dir,
        )
        outcome = mgr.load_all()
        assert len(outcome.skills) == 2
        scopes = {s.scope for s in outcome.skills}
        assert SkillScope.SYSTEM in scopes
        assert SkillScope.USER in scopes


def test_disabled_skill_not_loaded():
    with tempfile.TemporaryDirectory() as tmp:
        system_dir = Path(tmp) / "system"
        system_dir.mkdir()
        user_dir = Path(tmp) / "user"
        user_dir.mkdir()

        _make_skill_dir(system_dir, "disabled-skill",
                        "name: disabled-skill\ndescription: Test\nenabled: false",
                        "Body")

        mgr = SkillsManager(
            system_skills_dir=system_dir,
            user_skills_dir=user_dir,
        )
        outcome = mgr.load_all()
        assert len(outcome.skills) == 0  # disabled


def test_build_injections():
    with tempfile.TemporaryDirectory() as tmp:
        system_dir = Path(tmp) / "system"
        system_dir.mkdir()
        user_dir = Path(tmp) / "user"
        user_dir.mkdir()

        _make_skill_dir(system_dir, "sys-skill",
                        "name: sys-skill\ndescription: System skill", "# System Body")
        _make_skill_dir(user_dir, "user-skill",
                        "name: user-skill\ndescription: User skill", "# User Body")

        mgr = SkillsManager(
            system_skills_dir=system_dir,
            user_skills_dir=user_dir,
        )
        outcome = mgr.load_all()
        injections = mgr.build_injections(outcome)

        assert isinstance(injections, SkillInjections)
        assert "sys-skill" in injections.system_skills
        assert "user-skill" in injections.user_skills
        assert injections.total_chars() > 0


def test_no_frontmatter():
    with tempfile.TemporaryDirectory() as tmp:
        system_dir = Path(tmp) / "system"
        system_dir.mkdir()
        user_dir = Path(tmp) / "user"
        user_dir.mkdir()

        skill_dir = system_dir / "no-frontmatter"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Just markdown\n\nNo frontmatter here.", encoding="utf-8")

        mgr = SkillsManager(
            system_skills_dir=system_dir,
            user_skills_dir=user_dir,
        )
        outcome = mgr.load_all()
        assert len(outcome.skills) == 1
        # Name should default to directory name
        assert outcome.skills[0].name == "no-frontmatter"


def test_empty_dirs_handled():
    with tempfile.TemporaryDirectory() as tmp:
        system_dir = Path(tmp) / "system"
        system_dir.mkdir()
        user_dir = Path(tmp) / "user"
        user_dir.mkdir()
        nonexistent = Path(tmp) / "nonexistent"

        mgr = SkillsManager(
            system_skills_dir=system_dir,
            user_skills_dir=nonexistent,  # doesn't exist
        )
        outcome = mgr.load_all()
        assert len(outcome.skills) == 0
        assert len(outcome.errors) == 0
