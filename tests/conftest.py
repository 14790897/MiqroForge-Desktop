"""Shared fixtures for all test packages."""

import os
import shutil
import tempfile
import warnings
from pathlib import Path

import pytest

from miqi.config.schema import Config

_AUTO_BASETEMP_ATTR = "_miqi_auto_basetemp"


def _repo_root() -> Path:
    """Return the repository root that contains this conftest.py file."""
    return Path(__file__).resolve().parent.parent


def _auto_basetemp_name() -> str:
    """Return a per-process / per-xdist-worker basetemp directory name."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    suffix = worker if worker else f"pid{os.getpid()}"
    return f".pytest-basetemp-{suffix}"


def _is_safe_to_clean(path: Path, repo_root: Path) -> bool:
    """Verify ``path`` is an automatic basetemp directory inside the repo."""
    try:
        resolved = path.resolve()
        root = repo_root.resolve()
    except Exception:
        return False
    if not resolved.exists():
        return False
    if resolved == root:
        return False
    if not resolved.is_relative_to(root):
        return False
    if not resolved.name.startswith(".pytest-basetemp-"):
        return False
    return True


def pytest_configure(config):
    """Bootstrap a writable, repository-local pytest base temp directory.

    On some Windows/CI environments the system temp directory is read-only
    or contains an unowned ``pytest-of-*`` directory left by another process.
    This hook sets a repository-local base temp *only* when the caller has
    not explicitly provided ``--basetemp``.  The chosen directory is recorded
    on the config object so ``pytest_unconfigure`` can safely remove it.

    A per-process (or per-xdist-worker) suffix avoids conflicts between
    consecutive local runs, including cases where a previous subprocess test
    is still releasing handles when the next pytest session starts.
    """
    if getattr(config.option, "basetemp", None) is not None:
        # Respect the caller's explicit --basetemp; do not auto-manage it.
        return

    basetemp = _repo_root() / _auto_basetemp_name()
    basetemp.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(basetemp)
    setattr(config, _AUTO_BASETEMP_ATTR, basetemp)


def pytest_unconfigure(config):
    """Clean up the automatic base temp directory created by this conftest.

    User-provided ``--basetemp`` directories are never touched.  Before
    deleting, the path must pass safety checks: it must be inside the
    repository root, its name must start with ``.pytest-basetemp-``, and it
    must not be the repository root itself.  Cleanup failures are reported
    as warnings so they do not mask test results.
    """
    basetemp = getattr(config, _AUTO_BASETEMP_ATTR, None)
    if basetemp is None:
        return

    repo_root = _repo_root()
    if not _is_safe_to_clean(basetemp, repo_root):
        warnings.warn(
            f"Refusing to clean automatic pytest base temp {basetemp}: safety check failed",
            RuntimeWarning,
            stacklevel=2,
        )
        return

    try:
        shutil.rmtree(basetemp)
    except Exception as exc:  # pragma: no cover
        warnings.warn(
            f"Failed to clean automatic pytest base temp {basetemp}: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )


class _FakeResponse:
    """Minimal fake LLM response with all attributes AgentLoop accesses."""
    def __init__(self, content="done", tool_calls=None, finish_reason="stop"):
        self.content = content
        self.tool_calls = tool_calls or []
        self._has_tool_calls = bool(tool_calls)
        self.reasoning_content = None
        self.usage: dict[str, int] = {}
        self.finish_reason = finish_reason

    @property
    def has_tool_calls(self):
        return self._has_tool_calls


@pytest.fixture
def fake_config():
    """Minimal Config with all fields needed by AgentLoop constructor."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        config = Config()
        config.agents.defaults.workspace = str(tmp)
        yield config


@pytest.fixture
def fake_provider():
    """Fake LLM provider that returns canned responses."""
    class FakeProvider:
        def __init__(self):
            self.chat_calls: list[dict] = []

        async def chat(self, **kwargs):
            self.chat_calls.append(kwargs)
            return _FakeResponse(content="done", tool_calls=[])

        async def stream_chat(self, **kwargs):
            """Phase 20: streaming fallback wrapping chat()."""
            from miqi.providers.base import LLMStreamEvent
            response = await self.chat(**kwargs)
            yield LLMStreamEvent(kind="completed", response=response)

    return FakeProvider()


@pytest.fixture(autouse=True)
def isolated_process_environment(monkeypatch, tmp_path, request):
    """Isolate every test from the real user home and system temp.

    Sets MIQI_HOME, HOME, USERPROFILE, TEMP, TMP, TMPDIR, and
    tempfile.tempdir below ``tmp_path`` so that no MiQi-owned write
    or third-party temp file can accidentally land in the real user
    profile.

    Tests decorated with ``@pytest.mark.self_managed_env`` opt out
    of automatic isolation — they MUST set all six environment
    variables themselves before any path access.
    """
    import tempfile

    if request.node.get_closest_marker("self_managed_env") is not None:
        return

    iso_dir = tmp_path / ".pytest-isolation"
    iso_dir.mkdir()
    home = iso_dir / "home"
    miqi_home = iso_dir / ".miqi"
    temp_dir = iso_dir / "tmp"
    home.mkdir()
    temp_dir.mkdir()

    monkeypatch.setenv("MIQI_HOME", str(miqi_home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("TEMP", str(temp_dir))
    monkeypatch.setenv("TMP", str(temp_dir))
    monkeypatch.setenv("TMPDIR", str(temp_dir))
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))


# ── Platform capability detection ─────────────────────────────────────────────


def _has_subprocess() -> bool:
    """Check whether we can spawn a normal local subprocess."""
    import shutil
    import sys

    # On all platforms, look for a basic shell
    if sys.platform == "win32":
        return shutil.which("cmd.exe") is not None
    return shutil.which("sh") is not None


def _has_bwrap() -> bool:
    """Check whether bubblewrap is available."""
    import shutil
    import subprocess

    if shutil.which("bwrap") is None:
        return False
    try:
        subprocess.run(["bwrap", "--version"], capture_output=True, timeout=5, check=True)
        return True
    except Exception:
        return False


def _has_wsl() -> bool:
    """Check whether WSL has a usable distribution."""
    import shutil
    import subprocess
    import sys

    if sys.platform != "win32":
        return False
    if shutil.which("wsl.exe") is None:
        return False
    try:
        result = subprocess.run(
            ["wsl.exe", "--status"], capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


@pytest.fixture
def require_subprocess():
    """Skip the test if a normal subprocess cannot be launched."""
    if not _has_subprocess():
        pytest.skip("subprocess executable is not available")


def _has_wsl_bwrap() -> bool:
    """Check whether bwrap is available inside any WSL distribution."""
    import shutil
    import subprocess
    import sys

    if sys.platform != "win32":
        return False
    if shutil.which("wsl.exe") is None:
        return False
    try:
        result = subprocess.run(
            ["wsl.exe", "-l", "-q"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return False
        distros = [
            line.strip().replace("\x00", "")
            for line in result.stdout.splitlines()
            if line.strip().replace("\x00", "")
            and "docker-desktop" not in line.lower()
        ]
        for distro in distros:
            check = subprocess.run(
                ["wsl.exe", "-d", distro, "--", "bash", "-c", "which bwrap"],
                capture_output=True, timeout=10,
            )
            if check.returncode == 0:
                return True
        return False
    except Exception:
        return False


@pytest.fixture
def require_bwrap():
    """Skip the test if bubblewrap is not available (native or via WSL)."""
    import sys

    if sys.platform == "win32":
        if not _has_wsl_bwrap():
            pytest.skip("bwrap is not available on Windows (requires WSL with bwrap installed)")
        return  # WSL bwrap found — allow test to run
    if not _has_bwrap():
        pytest.skip("bwrap executable is not available")


@pytest.fixture
def require_wsl():
    """Skip the test if WSL is not available."""
    if not _has_wsl():
        pytest.skip("wsl.exe is not available or has no usable distribution")
