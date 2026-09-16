"""Tests for reading a skill's requirements.txt and gating on Python deps."""

from miqi.agent.skills import SkillsLoader, _parse_requirements_text

# A distribution name guaranteed not to be installed in any test env.
_MISSING_DIST = "miqi-nonexistent-pkg-xyz"


def _make_skill(parent, name, description, requirements=None):
    """Write a minimal SKILL.md (and optional requirements.txt) under parent."""
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
    """Comments, includes, URL lines and options are dropped; names survive."""
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
    assert [r.name for r in _parse_requirements_text(text)] == [
        "matplotlib",
        "numpy",
        "pymupdf",
        "requests",
        "somepkg",
    ]


def test_read_requirements_returns_empty_without_file(tmp_path):
    """A skill without requirements.txt has no declared Python deps."""
    loader, workspace = _loader(tmp_path)
    _make_skill(workspace / "skills", "no-reqs", "No requirements")
    assert loader._read_requirements("no-reqs") == []
    assert loader._check_requirements("no-reqs") is True


def test_missing_python_dep_marks_skill_unavailable(tmp_path):
    """A declared-but-uninstalled package makes the skill unavailable."""
    loader, workspace = _loader(tmp_path)
    _make_skill(
        workspace / "skills",
        "needs-missing",
        "Needs a missing dep",
        requirements=f"{_MISSING_DIST}>=1.0\n",
    )
    assert [r.name for r in loader._read_requirements("needs-missing")] == [
        _MISSING_DIST
    ]
    assert loader._check_requirements("needs-missing") is False
    assert (
        f"Python: {_MISSING_DIST}"
        in loader._get_missing_requirements("needs-missing")
    )


def test_installed_package_not_reported_missing(tmp_path):
    """An installed distribution satisfies a bare requirement."""
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


def test_installed_compatible_version_satisfies(tmp_path):
    """An installed version matching the specifier is not reported missing."""
    loader, workspace = _loader(tmp_path)
    _make_skill(
        workspace / "skills",
        "needs-pydantic-v2",
        "Needs pydantic v2",
        requirements="pydantic>=2.0\n",
    )
    assert loader._check_requirements("needs-pydantic-v2") is True


def test_installed_but_incompatible_version_reported(tmp_path):
    """An installed but incompatible version is reported as missing."""
    loader, workspace = _loader(tmp_path)
    _make_skill(
        workspace / "skills",
        "needs-future-pydantic",
        "Needs a future pydantic",
        requirements="pydantic>=999\n",
    )
    assert loader._check_requirements("needs-future-pydantic") is False
    assert "pydantic" in loader._get_missing_requirements("needs-future-pydantic")


def test_inactive_environment_marker_is_skipped(tmp_path):
    """A requirement with an inactive marker is not checked."""
    loader, workspace = _loader(tmp_path)
    _make_skill(
        workspace / "skills",
        "marker-off",
        "Marker off",
        requirements=f'{_MISSING_DIST}; python_version < "3.0"\n',
    )
    assert loader._missing_python_deps("marker-off") == []
    assert loader._check_requirements("marker-off") is True


def test_missing_named_direct_url_requirement_marks_skill_unavailable(tmp_path):
    """An uninstalled named direct-URL requirement makes the skill unavailable."""
    loader, workspace = _loader(tmp_path)
    _make_skill(
        workspace / "skills",
        "direct-url",
        "Direct URL",
        requirements=f"{_MISSING_DIST} @ https://example.com/pkg.whl\n",
    )
    assert loader._check_requirements("direct-url") is False
    assert _MISSING_DIST in loader._get_missing_requirements("direct-url")


def test_build_skills_summary_includes_requirements(tmp_path):
    """The summary exposes <requirements> and marks unavailable skills."""
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
    """Unavailable skills (missing Python deps) are filtered from listings."""
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


def test_requirements_cache_invalidated_on_index_change(tmp_path):
    """Editing requirements.txt is reflected after invalidate_skill_index."""
    loader, workspace = _loader(tmp_path)
    _make_skill(
        workspace / "skills", "evolving", "Evolving", requirements="pydantic\n"
    )
    assert loader._check_requirements("evolving") is True

    # Rewrite requirements.txt to require a missing dist, then invalidate.
    (workspace / "skills" / "evolving" / "requirements.txt").write_text(
        f"{_MISSING_DIST}\n", encoding="utf-8"
    )
    from miqi.agent.skills import invalidate_skill_index

    invalidate_skill_index(workspace)
    assert loader._check_requirements("evolving") is False
