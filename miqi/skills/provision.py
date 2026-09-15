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
"""

from __future__ import annotations

import re
import shlex
from typing import Any

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
        strings for pip.
        """
        missing = self._loader._missing_python_deps(name)
        apt: list[str] = []
        venv: list[str] = []
        if self._system_installs_available():
            for dist in missing:
                base = _dist_name(dist)
                if dist == base and base in APT_NAME_MAP:
                    apt.append(APT_NAME_MAP[base])
                else:
                    venv.append(dist)
        else:
            venv = list(missing)
        return {"apt": apt, "venv": venv}

    @staticmethod
    def _fail(name: str, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "skill": name,
            "installed_apt": [],
            "installed_venv": [],
            "venv_python": None,
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

        return {
            "ok": not errors,
            "skill": name,
            "installed_apt": installed_apt,
            "installed_venv": installed_venv,
            "venv_python": vpy if installed_venv else None,
            "errors": errors,
        }
