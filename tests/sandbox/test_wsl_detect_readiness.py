"""Real WSL integration tests for distro readiness detection (#566).

Verifies, on real WSL, that distro detection requires the full toolchain
(bwrap + python3/pip) and that auto-install installs the Python toolchain.

Regression target: a distro that has bwrap but lacks pip used to pass
the `which bwrap`-only probe, so the dependency installer never ran and
agents retried failed pip installs forever (issue #566).

These tests require a Windows runner with WSL and a bash-capable distro
(same prereq as test_bwrap_auto_install.py); they skip when WSL is
unavailable.
"""
import asyncio
import subprocess

import pytest

from miqi.sandbox.bwrap import _WSL_READY_CMD, BwrapSandbox

pytestmark = [pytest.mark.wsl, pytest.mark.sandbox]


def _real_distros() -> list[str]:
    """WSL distro names that can run bash (excluding docker-desktop)."""
    try:
        result = subprocess.run(
            ["wsl.exe", "-l", "-q"], capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return []
    distros = [
        line.strip().replace("\x00", "")
        for line in result.stdout.splitlines()
        if line.strip().replace("\x00", "")
        and "docker-desktop" not in line.lower()
    ]
    ready = []
    for distro in distros:
        try:
            check = subprocess.run(
                ["wsl.exe", "-d", distro, "--", "bash", "-c", "echo ok"],
                capture_output=True, timeout=10,
            )
        except Exception:
            continue
        if check.returncode == 0:
            ready.append(distro)
    return ready


async def _run_in_distro(distro: str, cmd: str) -> int:
    """Run a command in a WSL distro; return exit code."""
    proc = await asyncio.create_subprocess_exec(
        "wsl.exe", "-d", distro, "--", "bash", "-c", cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.wait_for(proc.communicate(), timeout=120.0)
    return proc.returncode if proc.returncode is not None else -1


async def _distro_ready(distro: str) -> bool:
    """True if the distro currently passes the full readiness probe."""
    return await _run_in_distro(distro, _WSL_READY_CMD) == 0


async def _pip_module_dir(distro: str) -> str | None:
    """Path of the pip package directory inside the distro, or None."""
    proc = await asyncio.create_subprocess_exec(
        "wsl.exe", "-d", distro, "--", "bash", "-c",
        'python3 -c "import pip; print(pip.__file__)"',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    if proc.returncode != 0:
        return None
    path = stdout.decode("utf-8", errors="replace").strip()
    if not path.endswith("/__init__.py"):
        return None
    return path[: -len("/__init__.py")]


@pytest.mark.asyncio
async def test_detect_skips_distro_with_bwrap_but_no_python():
    """#566: hiding pip in a ready distro must make detection reject it.

    Uses a real distro: temporarily moves the pip package away, verifies
    the full-toolchain probe now fails, then asserts detection no longer
    selects that distro.  The package is restored in a finally block.
    """
    distros = _real_distros()
    if not distros:
        pytest.skip("No real WSL distro available")
    # Prefer a non-sandbox distro for the experiment.
    distro = next((d for d in distros if d != "AIShadowSandbox"), distros[0])
    if not await _distro_ready(distro):
        pytest.skip(f"Distro '{distro}' is not fully ready; nothing to hide")
    pip_dir = await _pip_module_dir(distro)
    if not pip_dir:
        pytest.skip(f"Could not locate pip package in '{distro}'")

    hidden = pip_dir + ".forge-test-bak"
    hid_ok = False
    try:
        rc = await _run_in_distro(distro, f"mv {pip_dir} {hidden}")
        if rc != 0:
            # WSL user may lack write permission to dist-packages
            # (e.g. CI's non-root user) — skip instead of failing.
            pytest.skip(
                f"Cannot hide pip in '{distro}' (permissions); skipping"
            )
        hid_ok = True
        # Sanity: the distro really is missing pip now.
        assert not await _distro_ready(distro), "pip should be missing now"

        found = await BwrapSandbox._detect_wsl_distro(distro)
        assert found != distro, (
            f"detection accepted '{distro}' without pip — #566 regression"
        )
    finally:
        if hid_ok:
            rc = await _run_in_distro(distro, f"mv {hidden} {pip_dir}")
            assert rc == 0, f"CRITICAL: failed to restore pip in '{distro}'"
            assert await _distro_ready(distro), (
                "distro should be ready after restore"
            )


@pytest.mark.asyncio
async def test_detect_accepts_fully_ready_distro():
    """A distro with the full toolchain is selected and really is ready."""
    found = await BwrapSandbox._detect_wsl_distro("")
    if found is None:
        pytest.skip("No fully-ready distro (auto-install path needed)")
    assert await _distro_ready(found), (
        f"detected distro '{found}' does not actually have bwrap + python3/pip"
    )


@pytest.mark.asyncio
async def test_ensure_wsl_deps_installs_python_toolchain():
    """Auto-install leaves a distro with the full toolchain, ready or not."""
    distros = _real_distros()
    if not distros:
        pytest.skip("No real WSL distro available")
    distro = distros[0]
    ok = await BwrapSandbox._ensure_wsl_deps(distro)
    assert ok, f"_ensure_wsl_deps failed on real distro '{distro}'"
    assert await _distro_ready(distro), (
        f"distro '{distro}' not ready after _ensure_wsl_deps"
    )


@pytest.mark.asyncio
async def test_detect_falls_back_to_another_distro_when_preferred_missing_pip():
    """Preferred distro missing pip falls through to a fully ready one.

    This exercises the start() flow: _detect_wsl_distro(preferred) must
    reject a distro without pip even when it is explicitly preferred, and
    find the fully-ready distro instead.
    """
    distros = _real_distros()
    if not distros:
        pytest.skip("No real WSL distro available")
    preferred = next(
        (d for d in distros if d != "AIShadowSandbox"), distros[0]
    )
    pip_dir = await _pip_module_dir(preferred)
    if not pip_dir:
        pytest.skip(f"Could not locate pip package in '{preferred}'")
    # Only fully-ready distros are valid fallback targets; a distro that
    # is itself missing pip must not satisfy the assertion.
    ready_others = [
        d for d in distros if d != preferred and await _distro_ready(d)
    ]

    hidden = pip_dir + ".forge-test-bak"
    hid_ok = False
    try:
        rc = await _run_in_distro(preferred, f"mv {pip_dir} {hidden}")
        if rc != 0:
            pytest.skip(
                f"Cannot hide pip in '{preferred}' (permissions); skipping"
            )
        hid_ok = True
        found = await BwrapSandbox._detect_wsl_distro(preferred)
        assert found != preferred, (
            f"preferred distro '{preferred}' accepted without pip — #566 regression"
        )
        if ready_others:
            assert found in ready_others or found == "AIShadowSandbox", (
                f"expected fallback to another ready distro, got {found!r}"
            )
    finally:
        if hid_ok:
            rc = await _run_in_distro(preferred, f"mv {hidden} {pip_dir}")
            assert rc == 0, f"CRITICAL: failed to restore pip in '{preferred}'"
            assert await _distro_ready(preferred), "distro should be ready after restore"


@pytest.mark.asyncio
async def test_sandbox_start_creates_working_session_with_python():
    """Full lifecycle: sandbox.start() + python3/pip inside the sandbox.

    This is the real end-to-end guarantee of #566 — once detection and
    install pass, a sandbox session must actually run python3 and pip
    inside the isolated environment.
    """
    distros = _real_distros()
    if not distros:
        pytest.skip("No real WSL distro available")
    found = await BwrapSandbox._detect_wsl_distro("")
    if found is None:
        pytest.skip("No fully-ready distro (auto-install path needed)")
    # Give the distro a chance to pass the full probe.
    ok = await BwrapSandbox._ensure_wsl_deps(found)
    assert ok, f"_ensure_wsl_deps failed on '{found}'"

    import tempfile
    from pathlib import Path

    workspace = Path(tempfile.mkdtemp(prefix="miqi-wsl-readiness-"))
    sandbox = BwrapSandbox(
        session_key="test-wsl-readiness",
        workspace=workspace,
        share_net=True,
        wsl_distro=found,
        auto_install_deps=True,
    )
    try:
        await sandbox.start()
        assert sandbox.is_running

        rc, stdout, stderr = await sandbox.run_command(
            "python3 -V && python3 -m pip --version"
        )
        assert rc == 0, (
            f"python3/pip missing inside sandbox: rc={rc} stderr={stderr!r} "
            f"stdout={stdout!r}"
        )
        assert "Python" in stdout
        assert "pip" in stdout
    finally:
        await sandbox.stop()
        assert not sandbox.is_running

    import shutil
    shutil.rmtree(workspace, ignore_errors=True)
