"""Unit coverage for the WSL dependency installer's retry and cleanup paths.

Issue #1080: the ``wsl-sandbox`` job failed intermittently on unrelated
branches.  Two mechanisms were responsible, both in
``BwrapSandbox._ensure_wsl_deps``:

* the 180 s attempt timeout was fatal and un-retried — its ``except`` block
  returned ``False`` straight away, and the only ``continue`` lived in the
  non-zero-exit branch (CI job 103196368053: 180.018 s, empty stderr);
* killing the Windows-side ``wsl.exe`` left the ``apt-get`` inside the distro
  running, holding the dpkg lock.  Three minutes later the *next* install
  failed in 0.35 s with ``E: Unable to locate package python3-pip`` — a
  poisoned index, not a missing package.

These tests drive the real function against a faked
``_create_subprocess_exec``, so they need no WSL, no network and no Windows.
They are deliberately **not** marked ``wsl``/``sandbox``: the ``wsl-sandbox``
job is incident-correlated (its failures cluster on 2026-09-05 and
2026-09-11), so a green run there cannot prove anything — these can, and they
run in the portable ``test`` matrix on both operating systems.
"""

from __future__ import annotations

import asyncio
import threading
import time
import types

import pytest

from miqi.sandbox import bwrap as bwrap_mod
from miqi.sandbox.bwrap import BwrapSandbox

DISTRO = "Ubuntu"
READY_CMD = bwrap_mod._WSL_READY_CMD
SUDO_PROBE = "sudo -n true 2>/dev/null"


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch):
    """Give each test its own cooldown map and install lock.

    Both are module globals on purpose (they must survive across the four WSL
    call sites), which makes them shared state between tests; a test that
    failed while holding the lock would otherwise wedge the rest of the file.
    """
    monkeypatch.setattr(bwrap_mod, "_last_install_failure", {})
    monkeypatch.setattr(bwrap_mod, "_install_lock", threading.Lock())
    monkeypatch.setattr(bwrap_mod, "_WSL_APT_ATTEMPT_TIMEOUT", 0.05)


class _ManualClock:
    """A monotonic clock the test drives by hand.

    Needed by tests that care how much of the wall-clock budget was consumed.
    A real clock is platform-fragile here: on Windows a sub-millisecond
    ``asyncio.wait_for`` timeout truncates to 0 ms and resolves instantly, so
    a tiny budget never drains.
    """

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeProc:
    """Stand-in for what ``asyncio.create_subprocess_exec`` returns."""

    def __init__(
        self,
        rc: int = 0,
        stderr: bytes = b"",
        *,
        hang: bool = False,
        raise_timeout: bool = False,
        before_timeout=None,
    ):
        self._rc = rc
        self._stderr = stderr
        self._hang = hang
        self._raise_timeout = raise_timeout
        self._before_timeout = before_timeout
        self.returncode: int | None = None
        self.killed = False

    async def communicate(self):
        if self._raise_timeout:
            # What the real wait_for produces; raising it here lets a test pin
            # the caller's timeout branch without waiting the real 10/30 s.
            if self._before_timeout is not None:
                self._before_timeout()
            raise asyncio.TimeoutError()
        if self._hang:
            # Outlast the (patched, 0.05 s) attempt timeout so the caller's
            # asyncio.wait_for fires.
            await asyncio.sleep(30)
        self.returncode = self._rc
        return b"", self._stderr

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.returncode = -9
        return self.returncode


class _WslStub:
    """Answers every ``wsl.exe`` call ``_ensure_wsl_deps`` makes.

    ``install_results`` is consumed one entry per ``apt-get`` invocation:
    ``"hang"`` makes that attempt outlive the timeout, ``"timeout"`` makes it
    raise the timeout immediately (running ``on_install_timeout`` first, so a
    test can burn the budget), and a ``(rc, stderr)`` tuple is returned as-is.
    ``ready_after`` is how many install attempts must have happened before the
    readiness probe reports the toolchain as present — set it below
    ``len(install_results)`` to model a runner that installed everything but
    was simply too slow to say so in time.
    """

    def __init__(
        self,
        install_results,
        *,
        ready_after,
        leftovers=False,
        pgrep_raises_timeout=False,
        on_install_timeout=None,
    ):
        self.install_results = list(install_results)
        self.ready_after = ready_after
        self.leftovers = leftovers
        self.pgrep_raises_timeout = pgrep_raises_timeout
        self.on_install_timeout = on_install_timeout
        self.install_cmds: list[str] = []
        self.install_attempts = 0
        self.terminate_calls = 0
        self.pgrep_calls = 0

    async def __call__(self, *args, **_kwargs):
        assert args[0] == "wsl.exe", args
        if args[1] == "--terminate":
            self.terminate_calls += 1
            return _FakeProc(0)

        payload = args[-1]
        if payload == READY_CMD:
            return _FakeProc(0 if self.install_attempts >= self.ready_after else 1)
        if payload == SUDO_PROBE:
            return _FakeProc(1)  # no passwordless sudo -> install runs unwrapped
        if "pgrep -x apt-get" in payload:
            self.pgrep_calls += 1
            if self.pgrep_raises_timeout:
                return _FakeProc(raise_timeout=True)
            return _FakeProc(0 if self.leftovers else 1)

        assert "apt-get update" in payload, payload
        self.install_cmds.append(payload)
        result = self.install_results[self.install_attempts]
        self.install_attempts += 1
        if result == "hang":
            return _FakeProc(hang=True)
        if result == "timeout":
            return _FakeProc(raise_timeout=True, before_timeout=self.on_install_timeout)
        rc, stderr = result
        return _FakeProc(rc, stderr=stderr)


async def test_timeout_retries_and_clears_the_leftover_apt(monkeypatch):
    """A timeout must retry — and must clear the dpkg lock before it does.

    Also pins the install command itself: unstripped ``apt-get update``
    errors (no ``2>/dev/null``) are what makes the failure classifiable, and
    the Acquire/Dpkg options are what keep one slow mirror from eating the
    whole attempt.
    """
    stub = _WslStub(["hang", (0, b"")], ready_after=2, leftovers=True)
    monkeypatch.setattr(bwrap_mod, "_create_subprocess_exec", stub)

    assert await BwrapSandbox._ensure_wsl_deps(DISTRO) is True

    assert stub.install_attempts == 2, "the timed-out attempt was not retried"
    assert stub.pgrep_calls == 1, "the distro was not probed for a leftover apt"
    assert stub.terminate_calls == 1, "the poisoned distro was not restarted"

    cmd = stub.install_cmds[0]
    assert "2>/dev/null" not in cmd
    assert "-o Acquire::http::Timeout=30" in cmd
    assert "-o Acquire::Retries=2" in cmd
    assert "-o Dpkg::Lock::Timeout=60" in cmd
    # The retry repairs a possibly-interrupted dpkg before installing; the
    # first attempt must not pay for that.
    assert "dpkg --configure -a" not in cmd
    assert "dpkg --configure -a" in stub.install_cmds[1]
    assert bwrap_mod._last_install_failure == {}


async def test_timeout_does_not_disturb_a_distro_that_is_already_ready(monkeypatch):
    """A slow-but-successful install must not be turned into a failure.

    This is CI job 103196368053: the wrapper hit the 180 s cap while the
    toolchain landed inside the distro anyway.  Restarting the distro here
    would be strictly harmful — and would be the one path that can leave dpkg
    interrupted — so the probe has to run before any cleanup.
    """
    stub = _WslStub(["hang"], ready_after=1, leftovers=False)
    monkeypatch.setattr(bwrap_mod, "_create_subprocess_exec", stub)

    assert await BwrapSandbox._ensure_wsl_deps(DISTRO) is True
    assert stub.install_attempts == 1, "a needless second attempt was made"
    assert stub.terminate_calls == 0, "a ready distro was restarted anyway"
    assert bwrap_mod._last_install_failure == {}


async def test_ready_but_still_busy_distro_is_not_reported_as_success(monkeypatch):
    """A passing probe only proves the files are there.

    Killing the Windows-side wsl.exe leaves the in-distro apt running; since it
    still holds the dpkg lock, reporting success would hand the collision to
    the next installer (skills provisioning, the exec tool).  Clean up and
    retry instead.
    """
    monkeypatch.setattr(bwrap_mod, "_WSL_APT_IDLE_WAIT_S", 0.0)
    stub = _WslStub(["hang", (0, b"")], ready_after=1, leftovers=True)
    monkeypatch.setattr(bwrap_mod, "_create_subprocess_exec", stub)

    assert await BwrapSandbox._ensure_wsl_deps(DISTRO) is True
    assert stub.terminate_calls == 1, "the busy distro was never cleaned up"
    assert stub.install_attempts == 2, "it reported success without retrying"


async def test_slow_but_successful_install_is_reported_as_ready(monkeypatch):
    """Both attempts may time out while the toolchain did in fact land.

    npm-level apt on a cold runner regularly outlives the per-attempt cap;
    the readiness probe is the ground truth, so this must be True.
    """
    stub = _WslStub(["hang", "hang"], ready_after=2)
    monkeypatch.setattr(bwrap_mod, "_create_subprocess_exec", stub)

    assert await BwrapSandbox._ensure_wsl_deps(DISTRO) is True
    assert stub.install_attempts == 2
    assert bwrap_mod._last_install_failure == {}


async def test_poisoned_index_signature_is_retried(monkeypatch):
    """``Unable to locate package`` is the poisoned-index signature, not a
    missing package — CI job 103196368053 hit it 0.35 s into a retry.

    The retry is only worth anything if the poisoned state is cleared first,
    so this pins the cleanup, not just the second attempt.
    """
    stub = _WslStub(
        [(100, b"E: Unable to locate package python3-pip"), (0, b"")],
        ready_after=2,
        leftovers=True,
    )
    monkeypatch.setattr(bwrap_mod, "_create_subprocess_exec", stub)

    assert await BwrapSandbox._ensure_wsl_deps(DISTRO) is True
    assert stub.install_attempts == 2
    assert stub.pgrep_calls == 1, "the retry ran against the same poisoned distro"
    assert stub.terminate_calls == 1


async def test_non_retryable_failure_is_not_retried(monkeypatch):
    """Ordinary breakage must fail fast instead of burning a second attempt."""
    stub = _WslStub([(100, b"E: Unmet dependencies.")], ready_after=99)
    monkeypatch.setattr(bwrap_mod, "_create_subprocess_exec", stub)

    assert await BwrapSandbox._ensure_wsl_deps(DISTRO) is False
    assert stub.install_attempts == 1
    assert DISTRO in bwrap_mod._last_install_failure


async def test_a_failed_install_suppresses_the_next_call(monkeypatch):
    """The WSL suite reaches this installer four times per job; one bad
    network must not pay the full budget four times over."""
    stub = _WslStub([(100, b"E: Unmet dependencies.")], ready_after=99)
    monkeypatch.setattr(bwrap_mod, "_create_subprocess_exec", stub)

    assert await BwrapSandbox._ensure_wsl_deps(DISTRO) is False
    assert stub.install_attempts == 1

    assert await BwrapSandbox._ensure_wsl_deps(DISTRO) is False
    assert stub.install_attempts == 1, "the cooldown did not suppress the retry"


async def test_reset_restarts_the_distro_only_when_something_is_left(monkeypatch):
    """The cleanup probe must not restart a healthy distro on every timeout."""
    clean = _WslStub([], ready_after=0, leftovers=False)
    monkeypatch.setattr(bwrap_mod, "_create_subprocess_exec", clean)
    await BwrapSandbox._reset_wsl_apt_state(DISTRO)
    assert clean.pgrep_calls == 1
    assert clean.terminate_calls == 0

    dirty = _WslStub([], ready_after=0, leftovers=True)
    monkeypatch.setattr(bwrap_mod, "_create_subprocess_exec", dirty)
    await BwrapSandbox._reset_wsl_apt_state(DISTRO)
    assert dirty.terminate_calls == 1


async def test_a_reset_whose_probe_times_out_still_restarts(monkeypatch):
    """When the leftover probe cannot answer, the safe direction is to restart.

    A needless restart costs a few seconds; a missed one costs the whole job,
    so this deliberate fallback has to stay pinned.
    """
    stub = _WslStub([], ready_after=0, pgrep_raises_timeout=True)
    monkeypatch.setattr(bwrap_mod, "_create_subprocess_exec", stub)

    await BwrapSandbox._reset_wsl_apt_state(DISTRO)
    assert stub.pgrep_calls == 1
    assert stub.terminate_calls == 1


async def test_total_budget_stops_a_second_attempt(monkeypatch):
    """The per-attempt cap must not multiply into the job-level timeout.

    Four WSL call sites each burning attempts × cap is more than the job has.
    The clock is driven by hand so the first attempt provably burns the whole
    budget and the second one must not start.  A wall-clock version of this is
    platform-fragile: on Windows a sub-millisecond ``asyncio.wait_for``
    timeout truncates to 0 ms and returns instantly, so the budget never
    drains — which is exactly how this test failed on its first CI run.
    """
    clock = _ManualClock()
    monkeypatch.setattr(
        bwrap_mod, "time", types.SimpleNamespace(monotonic=clock.monotonic)
    )
    monkeypatch.setattr(bwrap_mod, "_WSL_APT_TOTAL_BUDGET", 300.0)
    stub = _WslStub(
        ["timeout", (0, b"")],
        ready_after=99,
        on_install_timeout=lambda: clock.advance(301.0),
    )
    monkeypatch.setattr(bwrap_mod, "_create_subprocess_exec", stub)

    assert await BwrapSandbox._ensure_wsl_deps(DISTRO) is False
    assert stub.install_attempts == 1, "the budget guard let a second attempt run"


async def test_cooldown_never_shadows_a_distro_that_is_ready(monkeypatch):
    """The cooldown suppresses the *install*, never the readiness report.

    A distro that recovered between the failed call and the next one must
    still come back True — otherwise the cooldown would turn a fixed distro
    into ten minutes of "no bwrap".
    """
    bwrap_mod._last_install_failure[DISTRO] = time.monotonic()
    stub = _WslStub([], ready_after=0)
    monkeypatch.setattr(bwrap_mod, "_create_subprocess_exec", stub)

    assert await BwrapSandbox._ensure_wsl_deps(DISTRO) is True
    assert stub.install_attempts == 0


def test_transient_classifier_covers_the_ci_signatures():
    transient = [
        "Temporary failure resolving 'archive.ubuntu.com'",
        "Could not get lock /var/lib/dpkg/lock-frontend",
        "Failed to fetch http://archive.ubuntu.com/...",
        "E: Unable to locate package python3-pip",
        "E: Package 'python3-venv' has no installation candidate",
        # A killed apt leaves dpkg interrupted; the retry command repairs it.
        "E: dpkg was interrupted, you must manually run 'dpkg --configure -a'",
    ]
    for message in transient:
        assert BwrapSandbox._is_transient_apt_error(message), message

    # A genuine configuration problem must not look retryable.
    assert not BwrapSandbox._is_transient_apt_error("E: Unmet dependencies.")
