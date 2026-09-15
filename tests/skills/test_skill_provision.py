"""Tests for SkillProvisioner (Layer 2: persistent skill dependency provisioning)."""

from unittest.mock import AsyncMock

from miqi.skills.provision import APT_NAME_MAP, VENV_ROOT, SkillProvisioner, venv_python


class _FakeLoader:
    """Stand-in for SkillsLoader exposing only _missing_python_deps."""

    def __init__(self, missing):
        self._missing = list(missing)

    def _missing_python_deps(self, name):
        return list(self._missing)


class _FakeSandbox:
    """Stand-in for BwrapSandbox exposing supports_system_installs + run_in_distro_root."""

    def __init__(self, supports_system_installs=True):
        self._supports = supports_system_installs
        self.run_in_distro_root = AsyncMock(return_value=(0, "", ""))

    @property
    def supports_system_installs(self):
        return self._supports


class _FakeSandboxManager:
    """Stand-in for SandboxManager exposing active_sandbox + allow_system_installs."""

    def __init__(self, sandbox=None, allow_system_installs=False):
        self.active_sandbox = sandbox
        self.allow_system_installs = allow_system_installs


def _provisioner(missing, *, allow=True, supports=True):
    """Build a SkillProvisioner over fakes, returning (provisioner, sandbox)."""
    sandbox = _FakeSandbox(supports_system_installs=supports)
    manager = _FakeSandboxManager(sandbox=sandbox, allow_system_installs=allow)
    loader = _FakeLoader(missing)
    return SkillProvisioner(loader, manager), sandbox


def test_apt_name_map_has_common_packages():
    """Common heavy libs map to their Debian python3-* package names."""
    assert APT_NAME_MAP["matplotlib"] == "python3-matplotlib"
    assert APT_NAME_MAP["numpy"] == "python3-numpy"
    assert APT_NAME_MAP["pymupdf"] == "python3-fitz"


def test_plan_routes_mapped_to_apt_and_rest_to_venv():
    """Mapped packages go to apt; unmapped ones go to the per-skill venv."""
    provisioner, _ = _provisioner(["matplotlib", "numpy", "some-unique-pkg"])
    plan = provisioner.plan("skill-a")
    assert plan["apt"] == ["python3-matplotlib", "python3-numpy"]
    assert plan["venv"] == ["some-unique-pkg"]


def test_plan_routes_all_to_venv_when_system_installs_disabled():
    """With system installs off, everything goes through the venv path."""
    provisioner, _ = _provisioner(["matplotlib"], allow=False)
    plan = provisioner.plan("skill-a")
    assert plan["apt"] == []
    assert plan["venv"] == ["matplotlib"]


def test_plan_preserves_version_specifier_in_venv():
    """Unmapped requirements keep their specifier for pip (e.g. pydantic>=999)."""
    provisioner, _ = _provisioner(["pydantic>=999"])
    plan = provisioner.plan("skill-a")
    assert plan["apt"] == []
    assert plan["venv"] == ["pydantic>=999"]


async def test_provision_runs_apt_and_venv():
    """provision issues apt-get for mapped deps and venv+pip for the rest."""
    provisioner, sandbox = _provisioner(["matplotlib", "some-unique-pkg"])
    result = await provisioner.provision("skill-a")

    calls = [c.args[0] for c in sandbox.run_in_distro_root.call_args_list]
    assert any("apt-get install -y python3-matplotlib" in c for c in calls)
    assert any(f"python3 -m venv '{VENV_ROOT}/skill-a'" in c for c in calls)
    assert any("pip install some-unique-pkg" in c for c in calls)

    assert result["ok"] is True
    assert result["installed_apt"] == ["python3-matplotlib"]
    assert result["installed_venv"] == ["some-unique-pkg"]
    assert result["venv_python"] == venv_python("skill-a")


async def test_provision_skips_venv_when_none_needed():
    """When every missing dep is apt-mapped, no venv is created."""
    provisioner, sandbox = _provisioner(["matplotlib"])
    result = await provisioner.provision("skill-a")
    assert result["installed_venv"] == []
    assert result["venv_python"] is None
    calls = [c.args[0] for c in sandbox.run_in_distro_root.call_args_list]
    assert len(calls) == 1  # only the apt-get call


async def test_provision_reports_apt_failure():
    """A failed apt-get is surfaced as ok=False with an error message."""
    provisioner, sandbox = _provisioner(["matplotlib"])
    sandbox.run_in_distro_root.return_value = (1, "", "apt error")
    result = await provisioner.provision("skill-a")
    assert result["ok"] is False
    assert result["installed_apt"] == []
    assert result["errors"]
