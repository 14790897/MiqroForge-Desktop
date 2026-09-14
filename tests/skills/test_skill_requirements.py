"""Tests for reading a skill's requirements.txt and gating on Python deps."""

from pathlib import Path

from miqi.agent.skills import SkillsLoader, _parse_requirements_text

# A distribution name guaranteed not to be installed in any test env.
_MISSING_DIST = "miqi-nonexistent-pkg-xyz"


def _make_skill(parent, name, description, requirements=None):
    skill_dir = parent / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    if requirements is not None:
        (skill_dir / "requirements.txt").write_text(requirements, encoding="utf-8")


def _loader(tmp_path, ws_name="ws"):
    """Build a SkillsLoader over a temp workspace with an empty builtin dir."""
    workspace = tmp_path / ws_name
    builtin = tmp_path / "builtin"
    builtin.mkdir(exist_ok=True)
    return SkillsLoader(workspace=workspace, builtin_skills_dir=builtin), workspace


def test_parse_requirements_text_strips_noise():
    text = (
        "# a comment\n"
        "matplotlib>=3.7\n"
        "numpy==1.26.4\n"
        "pymupdf>=1.23\n"
        "requests[socks]>=2.31\n"
        "somepkg; python_version >= '3.8'\n"
        "-r other-requirements.txt\n"
        "-e git+https://example.com/pkg.git\n"
        "git+https://example.com/repo.git#egg=pkg\n"
        "https://example.com/pkg.whl\n"
        "\n"
        "--index-url https://example.com/simple\n"
    )
    assert _parse_requirements_text(text) == [
        "matplotlib",
        "numpy",
        "pymupdf",
        "requests",
        "somepkg",
    ]


def test_read_requirements_returns_empty_without_file(tmp_path):
    loader, workspace = _loader(tmp_path)
    _make_skill(workspace / "skills", "no-reqs", "No requirements")
    assert loader._read_requirements("no-reqs") == []
    assert loader._check_requirements("no-reqs") is True


def test_missing_python_dep_marks_skill_unavailable(tmp_path):
    loader, workspace = _loader(tmp_path)
    _make_skill(
        workspace / "skills",
        "needs-missing",
        "Needs a missing dep",
        requirements=f"{_MISSING_DIST}>=1.0\n",
    )
    assert loader._read_requirements("needs-missing") == [_MISSING_DIST]
    assert loader._check_requirements("needs-missing") is False
    assert (
        f"Python: {_MISSING_DIST}"
        in loader._get_missing_requirements("needs-missing")
    )


def test_installed_package_not_reported_missing(tmp_path):
    loader, workspace = _loader(tmp_path)
    # pydantic is a hard runtime dependency, so it is always installed.
    _make_skill(
        workspace / "skills",
        "needs-pydantic",
        "Needs pydantic",
        requirements="pydantic\n",
    )
    assert loader._check_requirements("needs-pydantic") is True
    assert loader._get_missing_requirements("needs-pydantic") == ""


def test_build_skills_summary_includes_requirements(tmp_path):
    loader, workspace = _loader(tmp_path)
    _make_skill(
        workspace / "skills",
        "needs-missing",
        "Needs a missing dep",
        requirements=f"{_MISSING_DIST}\n",
    )
    summary = loader.build_skills_summary()
    assert "<requirements>" in summary
    assert _MISSING_DIST in summary
    assert '<skill available="false">' in summary
    assert f"Python: {_MISSING_DIST}" in summary


def test_list_skills_filters_unavailable_python_deps(tmp_path):
    loader, workspace = _loader(tmp_path)
    _make_skill(workspace / "skills", "good", "No deps")
    _make_skill(
        workspace / "skills",
        "bad",
        "Missing dep",
        requirements=f"{_MISSING_DIST}\n",
    )
    names = {s["name"] for s in loader.list_skills(filter_unavailable=True)}
    assert names == {"good"}
