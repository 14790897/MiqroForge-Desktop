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


def test_provisioned_dep_is_not_reported_missing(tmp_path):
    """A dep recorded as provisioned is no longer reported missing."""
    from miqi.skills.provision import record_provision

    loader, workspace = _loader(tmp_path)
    _make_skill(
        workspace / "skills",
        "provisioned-skill",
        "Provisioned",
        requirements=f"{_MISSING_DIST}\n",
    )
    assert loader._check_requirements("provisioned-skill") is False

    record_provision("provisioned-skill", [_MISSING_DIST], has_venv=True)
    assert loader._missing_python_deps("provisioned-skill") == []
    assert loader._check_requirements("provisioned-skill") is True


def test_provisioned_version_specifier_is_not_reported_missing(tmp_path):
    """A provisioned version specifier (pydantic>=999) is no longer missing."""
    from miqi.skills.provision import record_provision

    loader, workspace = _loader(tmp_path)
    _make_skill(
        workspace / "skills",
        "provisioned-pydantic",
        "Provisioned pydantic",
        requirements="pydantic>=999\n",
    )
    assert loader._check_requirements("provisioned-pydantic") is False

    record_provision("provisioned-pydantic", ["pydantic>=999"], has_venv=True)
    assert loader._missing_python_deps("provisioned-pydantic") == []
    assert loader._check_requirements("provisioned-pydantic") is True


def _fake_sandbox_manager():
    """A minimal stand-in that ``sandbox_is_active`` treats as an active sandbox."""
    import types

    return types.SimpleNamespace(enabled=True, _initialized=True)


def _sandbox_loader(tmp_path, sandbox_manager, ws_name="ws"):
    """Build a SkillsLoader bound to a (possibly fake) sandbox manager."""
    workspace = tmp_path / ws_name
    builtin = tmp_path / "builtin"
    builtin.mkdir(exist_ok=True)
    return (
        SkillsLoader(
            workspace=workspace,
            builtin_skills_dir=builtin,
            sandbox_manager=sandbox_manager,
        ),
        workspace,
    )


def test_sandbox_active_ignores_host_installed_package(tmp_path):
    """With a sandbox active, a host-installed package still counts as missing."""
    loader, workspace = _sandbox_loader(tmp_path, _fake_sandbox_manager())
    # pydantic is installed on the host, but the sandbox may not have it —
    # the host check must be bypassed in sandbox mode.
    _make_skill(
        workspace / "skills",
        "sandbox-needs-pydantic",
        "Needs pydantic",
        requirements="pydantic\n",
    )
    assert loader._check_requirements("sandbox-needs-pydantic") is False
    assert (
        "Python: pydantic"
        in loader._get_missing_requirements("sandbox-needs-pydantic")
    )


def test_sandbox_active_provisioned_dep_is_available(tmp_path, monkeypatch):
    """A provisioned dep satisfies the requirement even with a sandbox active."""
    from miqi.skills import provision as prov
    from miqi.skills.provision import record_provision

    # Isolate the registry so the test never touches the real data dir.
    monkeypatch.setattr(
        prov, "_registry_path", lambda: tmp_path / "skill-provisioning.json"
    )

    loader, workspace = _sandbox_loader(tmp_path, _fake_sandbox_manager())
    _make_skill(
        workspace / "skills",
        "sandbox-provisioned-pydantic",
        "Provisioned under sandbox",
        requirements="pydantic\n",
    )
    assert loader._check_requirements("sandbox-provisioned-pydantic") is False

    record_provision("sandbox-provisioned-pydantic", ["pydantic"], has_venv=True)
    assert loader._missing_python_deps("sandbox-provisioned-pydantic") == []
    assert loader._check_requirements("sandbox-provisioned-pydantic") is True


def test_sandbox_disabled_sentinel_keeps_host_check(tmp_path):
    """The "disabled" sentinel is not an active sandbox; host check still applies."""
    loader, workspace = _sandbox_loader(tmp_path, "disabled")
    _make_skill(
        workspace / "skills",
        "sandbox-disabled-pydantic",
        "Needs pydantic",
        requirements="pydantic\n",
    )
    assert loader._check_requirements("sandbox-disabled-pydantic") is True


def test_sandbox_active_skips_windows_markers(tmp_path):
    """A win32-only marker is inactive in the Linux sandbox (not reported missing)."""
    loader, workspace = _sandbox_loader(tmp_path, _fake_sandbox_manager())
    _make_skill(
        workspace / "skills",
        "sandbox-win-marker",
        "Windows-only dep",
        requirements=f'{_MISSING_DIST}; sys_platform == "win32"\n',
    )
    # The sandbox runs Linux, so a win32-only requirement is inactive there.
    assert loader._missing_python_deps("sandbox-win-marker") == []
    assert loader._check_requirements("sandbox-win-marker") is True


def test_sandbox_active_evaluates_linux_markers(tmp_path):
    """A linux-only marker is active in the sandbox (reported missing on a Windows host)."""
    loader, workspace = _sandbox_loader(tmp_path, _fake_sandbox_manager())
    _make_skill(
        workspace / "skills",
        "sandbox-linux-marker",
        "Linux-only dep",
        requirements=f'{_MISSING_DIST}; sys_platform == "linux"\n',
    )
    missing = loader._missing_python_deps("sandbox-linux-marker")
    assert len(missing) == 1 and _MISSING_DIST in missing[0]
    assert loader._check_requirements("sandbox-linux-marker") is False
