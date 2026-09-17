"""Tests for SkillProvisioner (Layer 2: persistent skill dependency provisioning)."""

from unittest.mock import AsyncMock

from packaging.requirements import Requirement

from miqi.skills.provision import (
    APT_NAME_MAP,
    VENV_ROOT,
    SkillProvisioner,
    get_provisioned,
    has_provisioned_venv,
    record_provision,
    venv_python,
)


class _FakeLoader:
    """Stand-in for SkillsLoader exposing only _read_requirements."""

    def __init__(self, reqs):
        self._reqs = [Requirement(r) for r in reqs]

    def _read_requirements(self, name):
        return list(self._reqs)


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
    assert any(
        f"python3 -m venv --system-site-packages {VENV_ROOT}/skill-a" in c for c in calls
    )
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


def test_plan_routes_specifier_mapped_name_to_venv():
    """A version-pinned dep that maps to apt must still go to venv (honour the specifier)."""
    provisioner, _ = _provisioner(["numpy==1.26"])
    plan = provisioner.plan("skill-a")
    assert plan["apt"] == []
    assert plan["venv"] == ["numpy==1.26"]


async def test_provision_fails_without_active_sandbox():
    """No active sandbox → provisioning reports failure, not fake success."""
    loader = _FakeLoader(["matplotlib"])
    manager = _FakeSandboxManager(sandbox=None, allow_system_installs=True)
    provisioner = SkillProvisioner(loader, manager)
    result = await provisioner.provision("skill-a")
    assert result["ok"] is False
    assert result["errors"]


async def test_provision_rejects_invalid_name():
    """A skill name with shell metacharacters is rejected outright."""
    provisioner, _ = _provisioner(["matplotlib"])
    result = await provisioner.provision("bad'name;rm -rf /")
    assert result["ok"] is False
    assert result["errors"]


def test_plan_tracks_apt_source():
    """plan() records which original requirements were routed to apt."""
    provisioner, _ = _provisioner(["matplotlib", "some-unique-pkg"])
    plan = provisioner.plan("skill-a")
    assert plan["apt_src"] == ["matplotlib"]


async def test_provision_records_provisioned_deps():
    """A successful provision flips the host-side registry for availability."""
    provisioner, _ = _provisioner(["matplotlib", "some-unique-pkg"])
    result = await provisioner.provision("skill-a")
    assert result["ok"] is True
    assert result["provisioned"] == ["matplotlib", "some-unique-pkg"]
    assert get_provisioned("skill-a") == ["matplotlib", "some-unique-pkg"]
    assert has_provisioned_venv("skill-a") is True


async def test_provision_apt_only_records_no_venv():
    """apt-only provisioning records deps but does not create a venv."""
    provisioner, _ = _provisioner(["matplotlib"])
    result = await provisioner.provision("skill-a")
    assert result["ok"] is True
    assert get_provisioned("skill-a") == ["matplotlib"]
    assert has_provisioned_venv("skill-a") is False


async def test_provision_failed_apt_records_nothing():
    """A failed install records no deps, so availability is unaffected."""
    provisioner, sandbox = _provisioner(["matplotlib"])
    sandbox.run_in_distro_root.return_value = (1, "", "apt error")
    result = await provisioner.provision("skill-a")
    assert result["ok"] is False
    assert result["provisioned"] == []
    assert get_provisioned("skill-a") == []


def test_record_provision_merges_and_persists():
    """record_provision merges into existing deps and preserves has_venv."""
    record_provision("skill-a", ["numpy"], has_venv=False)
    record_provision("skill-a", ["pydantic>=999"], has_venv=True)
    assert get_provisioned("skill-a") == ["numpy", "pydantic>=999"]
    assert has_provisioned_venv("skill-a") is True


def test_record_provision_overwrites_same_package():
    """A later provision of the same package replaces the older specifier."""
    record_provision("skill-a", ["pydantic>=999"], has_venv=True)
    record_provision("skill-a", ["pydantic>=2.5"], has_venv=True)
    assert get_provisioned("skill-a") == ["pydantic>=2.5"]
    assert has_provisioned_venv("skill-a") is True
