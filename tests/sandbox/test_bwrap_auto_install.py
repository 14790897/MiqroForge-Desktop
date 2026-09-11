"""Real WSL sandbox integration tests.

Verifies the full auto-install + sandbox lifecycle on actual WSL.
These tests require a Windows runner with WSL enabled and a real distro.

CI prereqs on GitHub Actions windows-latest:
    wsl --install -d Ubuntu --no-launch
"""

import tempfile
from pathlib import Path

import pytest

from miqi.sandbox.bwrap import BwrapSandbox

pytestmark = [pytest.mark.wsl, pytest.mark.sandbox]


def _has_real_distro() -> str | None:
    """Check if a real WSL distro (with bash) is available. Returns distro name or None."""
    import platform
    import subprocess

    if platform.system() != "Windows":
        return None
    try:
        result = subprocess.run(
            ["wsl.exe", "-l", "-q"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [
            line.strip().replace("\x00", "")
            for line in result.stdout.splitlines()
            if line.strip().replace("\x00", "")
            and "docker-desktop" not in line.lower()
        ]
        for distro in lines:
            check = subprocess.run(
                ["wsl.exe", "-d", distro, "--", "bash", "-c", "echo ok"],
                capture_output=True, timeout=10,
            )
            if check.returncode == 0:
                return distro
        return None
    except Exception:
        return None


@pytest.mark.asyncio
async def test_wsl_sandbox_auto_install():
    """Install bwrap in a bare WSL distro, then create sandbox and run commands.

    Exercises the complete path:
    1. Find WSL distro
    2. _ensure_wsl_deps: auto-install bubblewrap (idempotent)
    3. is_available() confirms bwrap is usable
    4. Create sandbox, run commands, verify, destroy
    """
    distro = _has_real_distro()
    if not distro:
        pytest.skip("No real WSL distro available (need bash-capable distro)")

    # Step 1: Ensure bwrap is installed (idempotent — skips if already present)
    success = await BwrapSandbox._ensure_wsl_deps(distro)
    assert success, (
        f"Failed to install bwrap in WSL distro '{distro}'. "
        "Check internet connectivity and apt-get."
    )

    # Step 2: Verify is_available()
    available = await BwrapSandbox.is_available(
        wsl_distro=distro,
        auto_install_deps=True,
    )
    assert available, f"is_available() should return True after install in '{distro}'"

    # Step 3: Create sandbox, run commands, destroy
    workspace = Path(tempfile.mkdtemp(prefix="miqi-wsl-test-"))
    sandbox = BwrapSandbox(
        session_key="test-wsl-auto-install",
        workspace=workspace,
        share_net=True,
        wsl_distro=distro,
        auto_install_deps=False,  # already installed
    )

    try:
        await sandbox.start()
        assert sandbox.is_running

        # Basic command execution
        rc, stdout, stderr = await sandbox.run_command("echo hello-wsl")
        assert rc == 0, f"echo failed: rc={rc} stderr={stderr!r} stdout={stdout!r}"
        assert "hello-wsl" in stdout

        # Shell pipeline
        rc, stdout, stderr = await sandbox.run_command(
            "echo hello | tr '[:lower:]' '[:upper:]'"
        )
        assert rc == 0, f"pipeline failed: rc={rc} stderr={stderr!r}"
        assert "HELLO" in stdout

        # File creation inside sandbox (single command: write + read)
        rc, stdout, stderr = await sandbox.run_command(
            "echo isolated > /tmp/miqi-test.txt && cat /tmp/miqi-test.txt"
        )
        assert rc == 0, f"write+read failed: rc={rc} stderr={stderr!r}"
        assert "isolated" in stdout

        # Python/pip toolchain available inside sandbox (issue #566)
        rc, stdout, stderr = await sandbox.run_command(
            "python3 -V && python3 -m pip --version"
        )
        assert rc == 0, (
            f"python3/pip missing inside sandbox: rc={rc} "
            f"stderr={stderr!r} stdout={stdout!r}"
        )
        assert "Python" in stdout
        assert "pip" in stdout

    finally:
        await sandbox.stop()
        assert not sandbox.is_running

    import shutil
    shutil.rmtree(workspace, ignore_errors=True)


@pytest.mark.asyncio
async def test_wsl_sandbox_env_isolation():
    """Verify sandbox has clean environment with MIQI flags set."""
    distro = _has_real_distro()
    if not distro:
        pytest.skip("No real WSL distro available")

    available = await BwrapSandbox.is_available(wsl_distro=distro)
    if not available:
        pytest.skip("bwrap not available in WSL distro")

    workspace = Path(tempfile.mkdtemp(prefix="miqi-wsl-env-"))
    sandbox = BwrapSandbox(
        session_key="test-wsl-env-iso",
        workspace=workspace,
        share_net=True,
        wsl_distro=distro,
    )

    try:
        await sandbox.start()

        # MIQI_SANDBOX flag should be set inside sandbox
        rc, stdout, _ = await sandbox.run_command("echo $MIQI_SANDBOX")
        assert rc == 0
        assert "1" in stdout, f"MIQI_SANDBOX not set: {stdout!r}"

        # MIQI_SESSION_KEY should match
        rc, stdout, _ = await sandbox.run_command("echo $MIQI_SESSION_KEY")
        assert rc == 0
        assert "test-wsl-env-iso" in stdout

    finally:
        await sandbox.stop()

    import shutil
    shutil.rmtree(workspace, ignore_errors=True)
