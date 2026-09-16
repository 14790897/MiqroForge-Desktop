"""Persistent provisioning of a skill's Python dependencies.

Layer 2 of skill-dependency support (#1084). Layer 1 (``SkillsLoader``)
detects missing deps; this module installs them persistently so the skill
actually runs inside the sandbox. Hybrid strategy:

- deps with an apt mapping (on WSL with system installs enabled) are installed
  via ``apt-get install python3-<pkg>`` through ``BwrapSandbox.run_in_distro_root``
  — this persists in the WSL distro and is auto-visible via the sandbox's
  read-only bind of the distro's ``/usr``.
- everything else is installed into a per-skill venv at
  ``/opt/miqi/venvs/<skill>`` (``/opt`` is ro-bound into the sandbox, so the
  venv is visible); scripts must then be run with that venv's python.

Both paths currently go through ``run_in_distro_root``, which is Windows+WSL
only; native-Linux persistence is a follow-up.

A successful provision is recorded in a small host-side registry under the
runtime data dir, so the Windows host — which cannot introspect the WSL venv
via ``importlib.metadata`` — can mark the skill available afterwards.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# pip distribution name (PEP 503 normalised) → Debian/Ubuntu apt package name.
APT_NAME_MAP: dict[str, str] = {
    "matplotlib": "python3-matplotlib",
    "numpy": "python3-numpy",
    "scipy": "python3-scipy",
    "pandas": "python3-pandas",
    "scikit-learn": "python3-sklearn",
    "pillow": "python3-pil",
    "reportlab": "python3-reportlab",
    "svglib": "python3-svglib",
    "pymupdf": "python3-fitz",
    "markdown": "python3-markdown",
}

VENV_ROOT = "/opt/miqi/venvs"

# Skill names are lowercase letters/digits/hyphens (same shape as the
# create/upload validation); anything else is rejected to avoid shell injection.
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

#: Filename of the host-side provisioning registry under the runtime data dir.
_PROVISIONED_FILE = "skill-provisioning.json"


def _registry_path() -> Path:
    """Path to the host-side skill-provisioning registry file."""
    from miqi.utils.helpers import get_data_path

    return get_data_path() / _PROVISIONED_FILE


def _load_registry() -> dict[str, dict[str, Any]]:
    """Read the provisioning registry; empty on a missing/corrupt file."""
    try:
        data = json.loads(_registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_registry(registry: dict[str, Any]) -> None:
    """Write the provisioning registry to disk (may raise OSError)."""
    _registry_path().write_text(
        json.dumps(registry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_provisioned(name: str) -> list[str]:
    """Return the original requirement strings provisioned for *name*."""
    entry = _load_registry().get(name, {})
    deps = entry.get("deps", []) if isinstance(entry, dict) else []
    return list(deps) if isinstance(deps, list) else []


def has_provisioned_venv(name: str) -> bool:
    """Return True when *name* has a provisioned per-skill venv."""
    entry = _load_registry().get(name, {})
    return bool(isinstance(entry, dict) and entry.get("has_venv", False))


def record_provision(name: str, deps: list[str], has_venv: bool) -> None:
    """Merge a successful provision into the registry (best-effort).

    The registry is the host-side source of truth for a skill's provisioned
    dependencies, since the Windows host cannot inspect the WSL venv.  A write
    failure is non-fatal — the install already succeeded — and only means the
    skill keeps showing as unavailable until the registry can be written.
    """
    registry = _load_registry()
    entry = registry.get(name, {}) or {}
    existing = entry.get("deps", []) if isinstance(entry, dict) else []
    entry["deps"] = sorted(set(existing if isinstance(existing, list) else []) | set(deps))
    entry["has_venv"] = bool(entry.get("has_venv", False) or has_venv)
    registry[name] = entry
    try:
        _save_registry(registry)
    except OSError as exc:
        _log.warning("skill provisioning registry write failed: %s", exc)


def _dist_name(dist: str) -> str:
    """Strip a version specifier from a dist name (``pydantic>=999`` → ``pydantic``)."""
    return re.split(r"[<>=!~]", dist, 1)[0].strip()


def venv_python(skill_name: str) -> str:
    """Path to a skill venv's python interpreter inside the sandbox."""
    return f"{VENV_ROOT}/{skill_name}/bin/python"


class SkillProvisioner:
    """Install a skill's missing Python deps persistently (apt + per-skill venv)."""

    def __init__(self, loader: Any, sandbox_manager: Any):
        self._loader = loader
        self._sandbox_manager = sandbox_manager

    def _active_sandbox(self) -> Any:
        return getattr(self._sandbox_manager, "active_sandbox", None)

    def _system_installs_available(self) -> bool:
        """True when apt provisioning can be routed to a rootful WSL distro."""
        sandbox = self._active_sandbox()
        if sandbox is None:
            return False
        if not getattr(sandbox, "supports_system_installs", False):
            return False
        return bool(getattr(self._sandbox_manager, "allow_system_installs", False))

    def plan(self, name: str) -> dict[str, list[str]]:
        """Split a skill's missing deps into apt-installable vs venv-required.

        ``apt`` holds apt package names — only plain, unversioned deps are
        eligible (a specifier like ``numpy==1.26`` must go through venv/pip so
        the version is honoured). ``venv`` holds the original requirement
        strings for pip, and ``apt_src`` the original requirement strings
        routed to apt (so the caller can record exactly which deps were
        provisioned).
        """
        missing = self._loader._missing_python_deps(name)
        apt: list[str] = []
        apt_src: list[str] = []
        venv: list[str] = []
        if self._system_installs_available():
            for dist in missing:
                base = _dist_name(dist)
                if dist == base and base in APT_NAME_MAP:
                    apt.append(APT_NAME_MAP[base])
                    apt_src.append(dist)
                else:
                    venv.append(dist)
        else:
            venv = list(missing)
        return {"apt": apt, "apt_src": apt_src, "venv": venv}

    @staticmethod
    def _fail(name: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "skill": name,
            "installed_apt": [],
            "installed_venv": [],
            "venv_python": None,
            "provisioned": [],
            "errors": [message],
        }

    async def provision(self, name: str) -> dict[str, Any]:
        """Install a skill's missing deps persistently; return a summary dict."""
        if not _NAME_RE.match(name):
            return self._fail(name, "非法技能名称（仅小写字母/数字/连字符）")

        plan = self.plan(name)
        sandbox = self._active_sandbox()
        if (plan["apt"] or plan["venv"]) and sandbox is None:
            return self._fail(name, "没有可用的活动沙箱，无法供给依赖")

        installed_apt: list[str] = []
        installed_venv: list[str] = []
        errors: list[str] = []
        vpy = venv_python(name)

        if plan["apt"] and sandbox is not None:
            pkgs = " ".join(shlex.quote(p) for p in plan["apt"])
            rc, _out, err = await sandbox.run_in_distro_root(
                f"apt-get install -y {pkgs}", timeout=1200.0,
            )
            if rc == 0:
                installed_apt = plan["apt"]
            else:
                errors.append(f"apt install 失败: {err.strip()[-300:]}")

        if plan["venv"] and sandbox is not None:
            vpy_q = shlex.quote(vpy)
            vdir_q = shlex.quote(f"{VENV_ROOT}/{name}")
            reqs = " ".join(shlex.quote(r) for r in plan["venv"])
            cmd = (
                f"mkdir -p {shlex.quote(VENV_ROOT)} && "
                f"(test -x {vpy_q} || python3 -m venv {vdir_q}) && "
                f"{vpy_q} -m pip install {reqs}"
            )
            rc, _out, err = await sandbox.run_in_distro_root(cmd, timeout=1200.0)
            if rc == 0:
                installed_venv = plan["venv"]
            else:
                errors.append(f"venv install 失败: {err.strip()[-300:]}")

        # Original requirement strings actually installed (only the routes that
        # succeeded) — recorded so the host can flip the skill to available.
        provisioned: list[str] = []
        if installed_apt:
            provisioned.extend(plan["apt_src"])
        if installed_venv:
            provisioned.extend(installed_venv)
        if provisioned:
            record_provision(name, provisioned, has_venv=bool(installed_venv))

        return {
            "ok": not errors,
            "skill": name,
            "installed_apt": installed_apt,
            "installed_venv": installed_venv,
            "venv_python": vpy if installed_venv else None,
            "provisioned": provisioned,
            "errors": errors,
        }
