"""Bubblewrap (bwrap) sandbox — creates per-session isolated environments.

Supports two execution modes:
1. **Native Linux** — runs bwrap directly
2. **Windows + WSL** — detects WSL availability and runs bwrap inside WSL

Each conversation gets its own mount namespace with:
- A writable overlay (tmpfs) for /tmp, /home/miqi/workspace
- Read-only bind mounts for /usr, /lib, /bin, etc.
- A per-session home directory with its own copy of the workspace
- Network shared with host by default (unshare-net only when share_net=False)
- PID namespace isolation (unshare-pid)

Usage:
    sandbox = BwrapSandbox(session_key="feishu:oc_123", workspace="/path/to/workspace")
    await sandbox.start()

    # Batch (legacy) — returns everything at once:
    exit_code, stdout, stderr = await sandbox.run_command("ls -la")

    # Streaming (Phase 33.2) — incremental stdout/stderr, cancel support:
    handle = await sandbox.run_command_streaming("long-running-cmd")
    while True:
        chunk = await handle.stdout.read(4096)
        if not chunk:
            break
        print(chunk.decode())
    await handle.wait()
    await handle.cleanup()

    await sandbox.stop()
"""

from __future__ import annotations

# pylint: disable=no-member,import-error
# Linux-specific APIs (os.killpg, signal.SIGKILL, os.getpgid) and
# loguru are only available on the target platform / in the WSL venv.
import asyncio
import os
import platform
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from miqi.runtime.workspace_logging import append_workspace_log


class BwrapSandboxError(Exception):
    """Error raised when bwrap operations fail."""


_auto_install_cache: dict[str, bool] = {}
"""Cache auto-install results per distro to avoid repeated apt-get calls."""

_install_lock = threading.Lock()

#: Tail-bounded accumulation for :meth:`BwrapSandbox._run_linux_command`.
#: A texlive-scale distro install can emit tens of MB of dpkg progress
#: text; the agent never needs more than the tail (the failure message is
#: at the end).  Keep only the trailing chunk so memory and the agent's
#: context stay bounded (CodeRabbit review #820).
_MAX_COMMAND_OUTPUT_CHARS = 1_000_000

#: Max time to wait for the distro-wide install lock before giving up,
#: consistent with :meth:`_ensure_wsl_deps`' bounded wait (180 s).  Two
#: parallel installs normally finish within it; beyond that something is
#: stuck, and the agent gets a clear error instead of a silent hang that
#: would otherwise last until the outer install timeout (CodeRabbit #820).
_INSTALL_LOCK_WAIT_TIMEOUT = 180.0

#: WSL distro readiness probe: bwrap + python3/pip toolchain.
#:
#: Used by _detect_wsl_distro / _find_any_wsl_distro and by
#: _ensure_wsl_deps.  A distro that has bwrap but lacks pip would pass
#: the old `which bwrap`-only probe and never reach the dependency
#: installer, leaving the agent to retry failed pip installs forever
#: (issue #566).  Requiring the full toolchain up front makes such a
#: distro fall through to auto-install instead.  python3-venv and unzip
#: are part of the installed package set, so they are probed too.
#: bwrap is probed at the exact path start() uses (/usr/bin/bwrap on
#: Debian/Ubuntu), not via `which`, so readiness implies the execution
#: path the sandbox will actually invoke exists.
_WSL_READY_CMD = (
    "test -x /usr/bin/bwrap && "
    "python3 -V >/dev/null 2>&1 && "
    "python3 -m pip --version >/dev/null 2>&1 && "
    "python3 -m venv --help >/dev/null 2>&1 && "
    "unzip -v >/dev/null 2>&1"
)
"""Serialize _ensure_wsl_deps to prevent concurrent apt-get.

When sandbox init is deferred to background (after the bridge ready
signal), a file_tool request may also trigger _ensure_wsl_deps via the
lazy check in SandboxManager.get_or_create().  Without a lock two
apt-get processes race on the dpkg lock and one fails.  This lock
makes the second caller wait for the first install, then re-check with
a quick readiness probe that succeeds immediately.
"""

#: Idempotent check that the distro's WSL default user is root. Prints
#: ``ROOT_OK`` when /etc/wsl.conf already declares ``default=root``, or
#: ``ROOT_FIXED`` after correcting it (so the caller knows to terminate
#: the distro to apply the change). Run as root (``-u root``) so it has
#: write access to /etc/wsl.conf regardless of the current default user.
_WSL_ENSURE_ROOT_CMD = (
    "if [ \"$(id -un)\" != \"root\" ]; then echo NOT_ROOT; exit 1; fi; "
    "if grep -qF 'default=root' /etc/wsl.conf 2>/dev/null; then "
    "echo ROOT_OK; "
    "else "
    "if grep -qF 'default=' /etc/wsl.conf 2>/dev/null; then "
    "sed -i 's/^default=.*/default=root/' /etc/wsl.conf; "
    "elif grep -qF '[user]' /etc/wsl.conf 2>/dev/null; then "
    "sed -i '/^\\[user\\]/a default=root' /etc/wsl.conf; "
    "else "
    "printf '\\n[user]\\ndefault=root\\n' >> /etc/wsl.conf; "
    "fi; "
    "echo ROOT_FIXED; "
    "fi"
)


class BwrapCommandHandle:
    """Handle to a running command inside the bwrap sandbox.

    Provides incremental access to stdout/stderr via :class:`asyncio.StreamReader`
    and lifecycle control (wait, kill, cleanup).

    Created by :meth:`BwrapSandbox.run_command_streaming`.  The caller MUST call
    :meth:`cleanup` after the process exits (or after calling :meth:`kill`) to
    release temporary resources (e.g. the WSL script file).

    Usage::

        handle = await sandbox.run_command_streaming("ls -la")
        # ... read handle.stdout, handle.stderr incrementally ...
        exit_code = await handle.wait()
        await handle.cleanup()
    """

    __slots__ = ("_process", "_pgid", "_use_wsl", "_script_path", "_sandbox_ref")

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        pgid: int | None = None,
        use_wsl: bool = False,
    ):
        self._process = process
        self._pgid: int | None = pgid
        self._use_wsl: bool = use_wsl
        self._script_path: str | None = None
        self._sandbox_ref: BwrapSandbox | None = None

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        """stdout stream for incremental reading (4096-byte chunks)."""
        return self._process.stdout

    @property
    def stderr(self) -> asyncio.StreamReader | None:
        """stderr stream for incremental reading (4096-byte chunks)."""
        return self._process.stderr

    @property
    def returncode(self) -> int | None:
        """Process return code (None if still running)."""
        return self._process.returncode

    async def wait(self) -> int:
        """Wait for the command to exit.  Returns the exit code."""
        await self._process.wait()
        return self._process.returncode if self._process.returncode is not None else -1

    async def kill(self) -> None:
        """Kill the running command.

        No-op when the process has already exited and been reaped: killing
        the stale process group is both pointless and dangerous — its pgid
        (the group-leader pid) may have been recycled for an unrelated
        process group, and `killpg` would signal innocent processes (#472).

        On native Linux, tries SIGTERM then SIGKILL against the process group
        (bwrap creates a PID namespace but the outer bwrap process itself is
        in the process group created with ``start_new_session=True``).

        On WSL, terminates the wsl.exe wrapper — the inner bwrap processes
        should be cleaned up via ``--die-with-parent`` when the wrapper bash
        receives SIGHUP.

        After calling this, call :meth:`cleanup` to release temporary resources.
        """
        if self._process.returncode is not None:
            return
        if self._pgid is not None:
            # Native Linux — kill the process group (bwrap + children)
            try:
                os.killpg(self._pgid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
        else:
            try:
                self._process.terminate()
            except ProcessLookupError:
                return

        try:
            await asyncio.wait_for(self._process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            # Force kill
            try:
                if self._pgid is not None:
                    try:
                        os.killpg(self._pgid, signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
                else:
                    self._process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass

    async def cleanup(self) -> None:
        """Release temporary resources (script file, etc.).

        Must be called after the process exits — either naturally, via
        :meth:`kill`, or after a timeout.
        """
        if self._script_path is not None and self._sandbox_ref is not None:
            try:
                await self._sandbox_ref._run_linux_command(
                    f"rm -f '{self._script_path}'", timeout=5.0,
                )
            except Exception:
                pass
            self._script_path = None



async def _create_subprocess_exec(*args, **kwargs):
    """Wrapper around asyncio.create_subprocess_exec that suppresses Windows console windows."""
    kwargs.update(_subprocess_kwargs())
    return await asyncio.create_subprocess_exec(*args, **kwargs)


def _shell_quote(s: str) -> str:
    """Shell-escape a string using single quotes.

    Replaces every single quote with `'\''` and wraps the whole thing
    in single quotes.  This is the standard POSIX idiom for embedding
    arbitrary text in a shell command.
    """
    return "'" + s.replace("'", "'\\''") + "'"


def _is_windows() -> bool:
    """Check if running on Windows."""
    return platform.system() == "Windows"


def _subprocess_kwargs():
    """Return kwargs for asyncio.create_subprocess_exec to hide console windows.

    On Windows, ``asyncio.create_subprocess_exec`` creates a console window
    by default for every subprocess.  Passing ``creationflags`` with
    ``CREATE_NO_WINDOW`` suppresses this, preventing the brief black console
    flash (Issue #301).

    Returns:
        dict with ``creationflags`` on Windows; empty dict on other platforms.
    """
    if not _is_windows():
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


class BwrapSandbox:
    """Manages a single bwrap sandbox for one conversation session.

    Automatically detects Windows + WSL and routes all bwrap commands
    through ``wsl.exe -d <distro> -- bash -c "..."`` when needed.
    """

    def __init__(
        self,
        session_key: str,
        workspace: Path | str,
        sandbox_base_dir: Path | str | None = None,
        share_net: bool = True,
        extra_ro_binds: list[str] | None = None,
        extra_rw_binds: list[str] | None = None,
        hostname: str = "miqi-sandbox",
        uid: int = 1000,
        gid: int = 1000,
        wsl_distro: str = "",
        wsl_base_dir: str = "/tmp/miqi-sandboxes",
        sandbox_distro_name: str = "",
        auto_install_deps: bool = True,
    ):
        self.session_key = session_key
        self.workspace = Path(workspace).resolve()
        self.share_net = share_net
        self.extra_ro_binds = extra_ro_binds or []
        self.extra_rw_binds = extra_rw_binds or []
        self.hostname = hostname
        self.uid = uid
        self.gid = gid
        self.wsl_distro = wsl_distro
        self.wsl_base_dir = wsl_base_dir
        self.sandbox_distro_name = sandbox_distro_name
        self.auto_install_deps = auto_install_deps

        # Per-session directories (always Linux-style paths inside WSL or native)
        safe_key = session_key.replace(":", "_").replace("/", "_").replace("\\", "_").replace("'", "_")
        if sandbox_base_dir:
            self._base_dir = Path(sandbox_base_dir) / safe_key
        else:
            self._base_dir = Path(tempfile.gettempdir()) / "miqi-sandboxes" / safe_key

        # When running on Windows, we MUST use Linux paths inside WSL
        if _is_windows():
            self._linux_base_dir = f"{self.wsl_base_dir}/{safe_key}"
        else:
            self._linux_base_dir = str(self._base_dir)

        self.sandbox_home: str = f"{self._linux_base_dir}/home/miqi"
        self.sandbox_workspace: str = f"{self._linux_base_dir}/home/miqi/workspace"
        self.sandbox_rootfs: str = f"{self._linux_base_dir}/rootfs"

        self._process: asyncio.subprocess.Process | None = None
        self._running = False
        self._bwrap_path: str | None = None
        self._use_wsl: bool = False
        self._detected_distro: str = ""
        self._linux_workspace: str | None = None
        self._log_workspace = Path(workspace).expanduser().resolve()

    # ── WSL detection & command execution ────────────────────────────────

    async def _run_host_command(
        self,
        *args: str,
        timeout: float = 30.0,
    ) -> tuple[int, str, str]:
        """Run a command on the host OS (Windows or Linux).

        On Windows, wraps with ``wsl.exe -d <distro> --`` if needed.
        Returns (exit_code, stdout, stderr).
        """
        if self._use_wsl:
            full_args = self._wsl_prefix() + list(args)
        else:
            full_args = list(args)

        try:
            process = await _create_subprocess_exec(
                *full_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                return (-1, "", f"Command timed out after {timeout}s")

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            return (process.returncode if process.returncode is not None else -1, stdout, stderr)
        except Exception as exc:
            return (-1, "", f"Failed to run command: {exc}")

    async def _run_linux_command(
        self,
        cmd: str,
        timeout: float = 30.0,
        as_root: bool = False,
        on_output: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ) -> tuple[int, str, str]:
        """Run a shell command inside the Linux environment.

        On Windows, runs via ``wsl.exe -d <distro> -- bash -c "..."``.
        On Linux, runs via ``bash -c "..."``.

        ``as_root=True`` (Windows/WSL only) adds ``-u root`` to the wsl.exe
        invocation so the command runs as the distro's root user — used for
        system package installs that persist in the distro and become
        visible inside every bwrap sandbox via its ro-bind of the distro's
        system directories (#759).  Native Linux does not have a rootful
        distro layer; callers must check :attr:`supports_system_installs`
        first.

        ``on_output``, when given, is awaited with ``(text, stream_name)``
        for every read chunk (``stream_name`` is "stdout" or "stderr").
        The install path uses it to emit periodic progress so a long
        texlive-scale install keeps the chat turn alive (CodeRabbit #820);
        ``None`` keeps the previous fully-buffered behaviour.  The
        accumulated output is tail-bounded to
        :data:`_MAX_COMMAND_OUTPUT_CHARS` regardless.
        """
        if self._use_wsl:
            wsl_args = self._wsl_prefix()
            if as_root:
                wsl_args = ["wsl.exe", "-d", self._detected_distro, "-u", "root", "--"] \
                    if self._detected_distro else \
                    ["wsl.exe", "-u", "root", "--"]
            full_args = wsl_args + ["bash", "-c", cmd]
        else:
            full_args = ["bash", "-c", cmd]

        async def _drain(stream: Any, name: str, sink: deque[str]) -> None:
            """Read *stream* incrementally, keep only the trailing output."""
            if stream is None:
                return
            total = 0
            try:
                while True:
                    chunk = await stream.read(4096)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    if on_output is not None:
                        await on_output(text, name)
                    sink.append(text)
                    total += len(text)
                    while total > _MAX_COMMAND_OUTPUT_CHARS and len(sink) > 1:
                        total -= len(sink.popleft())
            except Exception:
                pass

        out_chunks: deque[str] = deque()
        err_chunks: deque[str] = deque()
        try:
            process = await _create_subprocess_exec(
                *full_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        _drain(process.stdout, "stdout", out_chunks),
                        _drain(process.stderr, "stderr", err_chunks),
                    ),
                    timeout=timeout,
                )
                # Reap the process: the drains finish at pipe EOF, which
                # can precede the process actually exiting — returncode
                # would still be None.  Short wait_for: the exit code is
                # available almost immediately after EOF.
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
            except asyncio.TimeoutError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                # Keep the tail captured so far: a long-running command
                # that overruns its budget still leaves the agent the last
                # diagnostic lines instead of an empty timeout message
                # (CodeRabbit #820).
                return (
                    -1,
                    "".join(out_chunks),
                    f"{''.join(err_chunks)}\nCommand timed out after {timeout}s",
                )

            stdout = "".join(out_chunks)
            stderr = "".join(err_chunks)
            return (process.returncode if process.returncode is not None else -1, stdout, stderr)
        except Exception as exc:
            return (-1, "", f"Failed to run command: {exc}")

    @property
    def supports_system_installs(self) -> bool:
        """True when package installs can be routed to a rootful WSL distro.

        The bwrap sandbox itself runs unprivileged (uid 1000) against
        read-only system dirs, so ``apt-get`` etc. can never work inside it
        (#759).  On Windows the WSL distro the sandbox ro-binds its system
        dirs from *is* a persistent root-capable layer: installing there
        once makes the toolchain visible in every sandbox session.  Native
        Linux has no such layer (the host must not receive root installs
        from the sandboxed agent), so the capability is WSL-only.
        """
        return bool(self._use_wsl and self._detected_distro)

    @property
    def distro_name(self) -> str:
        """The WSL distro backing this sandbox (empty when not WSL)."""
        return self._detected_distro or ""

    async def run_in_distro_root(
        self,
        command: str,
        timeout: float = 1200.0,
        on_output: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ) -> tuple[int, str, str]:
        """Run a command as root in the WSL distro, OUTSIDE the bwrap sandbox.

        Used to install system toolchains (LaTeX, compilers, ...) that the
        unprivileged read-only sandbox cannot install itself.  Because the
        sandbox ro-binds the distro's system directories (/usr, /lib, /etc,
        ...), anything installed here is immediately and persistently
        available to every sandbox command — no sandbox restart needed.

        Only available on Windows + WSL (:attr:`supports_system_installs`);
        native Linux returns an error result since there is no rootful
        distro layer and the host must never receive root installs from the
        sandboxed agent.

        Concurrent distro-side installs are serialized on
        :data:`_install_lock` (the same lock the auto-install path uses):
        two parallel ``apt-get`` runs race on dpkg's lock and one fails
        with "Could not get lock /var/lib/dpkg/lock" (review #759 N3).  The
        wait polls the non-blocking acquire on the event loop — same
        strategy as :meth:`_ensure_wsl_deps` — so a queued install neither
        blocks the loop nor parks a default-executor thread for the whole
        wait (CodeRabbit review #820).

        ``on_output`` is forwarded to :meth:`_run_linux_command` — the
        install path streams chunk callbacks through it so a long install
        can emit periodic progress and keep the chat turn alive.

        Returns:
            (exit_code, stdout, stderr) — output is tail-bounded to
            :data:`_MAX_COMMAND_OUTPUT_CHARS`.
        """
        if not self.supports_system_installs:
            return (
                -1,
                "",
                "System package installs require Windows + WSL (the sandbox's "
                "WSL distro). Not supported on native Linux.",
            )

        # Non-interactive frontend so apt/dnf never hang on a TTY prompt.
        full_cmd = f"export DEBIAN_FRONTEND=noninteractive; {command}"

        # threading.Lock (distro-wide, shared with the auto-install path) —
        # polled without blocking the event loop and without parking a
        # default-executor thread for the whole wait (which is shared with
        # workspace snapshots and approval checks).  The wait is bounded by
        # real elapsed time, like _ensure_wsl_deps' — a stuck holder
        # (crashed apt-get) surfaces as a clear error instead of a silent
        # hang until the outer install timeout — and emits progress so the
        # agent knows the install is queued, not stalled (CodeRabbit #820).
        deadline = time.monotonic() + _INSTALL_LOCK_WAIT_TIMEOUT
        while not _install_lock.acquire(blocking=False):
            if time.monotonic() >= deadline:
                return (
                    -1,
                    "",
                    "Timed out waiting for the distro install lock "
                    "(another install is still running)",
                )
            await asyncio.sleep(1.0)
            if on_output is not None:
                await on_output(
                    "[system install] 等待其他安装完成（distro 锁）……\n",
                    "stdout",
                )
        try:
            return await self._run_linux_command(
                full_cmd, timeout=timeout, as_root=True, on_output=on_output,
            )
        finally:
            _install_lock.release()

    async def _write_wsl_file_via_stdin(
        self,
        linux_path: str,
        content: str,
        timeout: float = 15.0,
    ) -> tuple[int, str, str]:
        """Write content to a file inside WSL by piping through stdin.

        This avoids the Windows CreateProcess command-line length limit
        by passing file content through a pipe rather than as a command
        argument.  The command line stays short:
            wsl.exe -d distro -- bash -c 'cat > /path/to/file'
        while the actual content flows through stdin.
        """
        # Use a short command; data goes through stdin pipe
        write_cmd = f"mkdir -p \"$(dirname '{linux_path}')\" && cat > '{linux_path}'"
        if self._use_wsl:
            full_args = self._wsl_prefix() + ["bash", "-c", write_cmd]
        else:
            full_args = ["bash", "-c", write_cmd]

        try:
            process = await _create_subprocess_exec(
                *full_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(input=content.encode("utf-8")),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                return (-1, "", f"Command timed out after {timeout}s")

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            return (process.returncode if process.returncode is not None else -1, stdout, stderr)
        except Exception as exc:
            return (-1, "", f"Failed to write file: {exc}")

    def _wsl_prefix(self) -> list[str]:
        """Build the wsl.exe prefix for command execution."""
        distro = self._detected_distro or self.wsl_distro
        if distro:
            return ["wsl.exe", "-d", distro, "--"]
        return ["wsl.exe", "--"]

    @staticmethod
    async def _detect_wsl_distro(preferred: str = "") -> str | None:
        """Detect available WSL distribution with bwrap + python3/pip.

        Returns the distro name if found, None if WSL or the toolchain
        (bwrap + python3/pip) is not available.
        """
        if not _is_windows():
            return None

        # Try preferred distro first
        if preferred:
            try:
                proc = await _create_subprocess_exec(
                    "wsl.exe", "-d", preferred, "--", "bash", "-c",
                    _WSL_READY_CMD,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=15.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    try:
                        await asyncio.wait_for(proc.communicate(), timeout=5.0)
                    except (asyncio.TimeoutError, ProcessLookupError):
                        pass
                    # Already timed out — fall through to the outer except
                    # clause which swallows this distro probe and continues.
                if proc.returncode == 0:
                    return preferred
            except (asyncio.TimeoutError, OSError):
                pass

        # List all distros and find one with bwrap
        try:
            proc = await _create_subprocess_exec(
                "wsl.exe", "-l", "-q",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_data, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=30.0,
                )
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=5.0)
                except (asyncio.TimeoutError, ProcessLookupError):
                    pass
                # Fall through — the outer try/except below handles the
                # case where the process timed out and we can't read stdout.
                raise
            if proc.returncode != 0:
                return None

            output = stdout_data.decode("utf-16-le", errors="replace") if stdout_data else ""
            # WSL -l -q output has null bytes and newlines; clean up
            distros = [
                line.strip().replace("\x00", "")
                for line in output.splitlines()
                if line.strip().replace("\x00", "")
            ]

            for distro in distros:
                if not distro:
                    continue
                try:
                    check = await _create_subprocess_exec(
                        "wsl.exe", "-d", distro, "--", "bash", "-c",
                        _WSL_READY_CMD,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        await asyncio.wait_for(check.communicate(), timeout=15.0)
                    except asyncio.TimeoutError:
                        check.kill()
                        try:
                            await asyncio.wait_for(check.communicate(), timeout=5.0)
                        except (asyncio.TimeoutError, ProcessLookupError):
                            pass
                        # Fall through — outer except swallows this distro
                        # probe and continues to the next one.
                    if check.returncode == 0:
                        return distro
                except Exception:
                    continue
        except Exception:
            pass

        return None

    @staticmethod
    async def _find_any_wsl_distro(preferred: str = "") -> str | None:
        """Find any available WSL distribution (with or without bwrap).

        Returns the distro name if found, None if no WSL available.
        Skips non-standard distros (docker-desktop*, etc.) by checking
        that bash is available.
        """
        if not _is_windows():
            return None

        async def _distro_has_bash(distro: str) -> bool:
            """Check if a distro has bash (indicating a real Linux distro)."""
            proc = None
            try:
                proc = await _create_subprocess_exec(
                    "wsl.exe", "-d", distro, "--", "bash", "-c", "echo ok",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=30.0)
                return proc.returncode == 0
            except (asyncio.TimeoutError, OSError):
                if proc is not None:
                    proc.kill()
                return False

        # Try preferred distro first
        if preferred:
            if await _distro_has_bash(preferred):
                return preferred

        # List all distros and find the first one with bash
        try:
            proc = await _create_subprocess_exec(
                "wsl.exe", "-l", "-q",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_data, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            if proc.returncode != 0:
                return None

            output = stdout_data.decode("utf-16-le", errors="replace") if stdout_data else ""
            distros = [
                line.strip().replace("\x00", "")
                for line in output.splitlines()
                if line.strip().replace("\x00", "")
                and "docker-desktop" not in line.lower()
            ]
            for distro in distros:
                if await _distro_has_bash(distro):
                    return distro
            return None
        except (asyncio.TimeoutError, OSError, ValueError):
            return None
    @staticmethod
    async def _ensure_root_default_user(distro: str) -> bool:
        """Ensure the distro's WSL default user is root (idempotent).

        miqi's sandbox relies on the distro running as root so apt-get
        and bwrap never hit a sudo password prompt. The distro's
        /etc/wsl.conf may have been edited externally (or the distro
        created outside miqi) to a non-root default user, which breaks
        that assumption and makes every sandbox invocation stall on a
        password. This corrects it and terminates the distro so the
        change takes effect.

        Returns True if a change was made (default user flipped to
        root), False if it was already root or could not be verified.
        """
        try:
            proc = await _create_subprocess_exec(
                "wsl.exe", "-d", distro, "-u", "root", "--",
                "bash", "-c", _WSL_ENSURE_ROOT_CMD,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_data, _ = await asyncio.wait_for(
                proc.communicate(), timeout=15.0,
            )
        except (asyncio.TimeoutError, OSError):
            return False

        output = stdout_data.decode("utf-8", errors="replace") if stdout_data else ""
        if proc.returncode != 0:
            logger.warning(
                "Failed to ensure root default user for '{}': {}",
                distro, output.strip()[:200],
            )
            return False

        if "ROOT_FIXED" not in output:
            return False  # ROOT_OK (already root) or no action needed

        # Terminate so wsl.conf takes effect on the next launch
        try:
            term = await _create_subprocess_exec(
                "wsl.exe", "--terminate", distro,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(term.communicate(), timeout=10.0)
        except (asyncio.TimeoutError, OSError):
            pass
        logger.info(
            "Sandbox distro '{}' default user set to root", distro,
        )
        return True

    @staticmethod
    async def _ensure_sandbox_distro(target_name: str = "AIShadowSandbox") -> bool:
        """Create a dedicated sandbox WSL distro if it does not exist.

        Exports the first available non-docker WSL distro to a temporary
        tar file, then imports it as a new distro with the given name.
        This gives the sandbox a root-user distro that can install
        packages without sudo password prompts.

        Returns True if the distro already exists or was created.
        """
        # Check if already exists
        try:
            check = await _create_subprocess_exec(
                "wsl.exe", "-d", target_name, "--", "bash", "-c",
                "echo ok",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(check.communicate(), timeout=10.0)
            if check.returncode == 0:
                logger.info(
                    "Sandbox distro '{}' already exists", target_name,
                )
                # The distro may exist but with a non-root default user
                # (e.g. /etc/wsl.conf edited externally), which would
                # make apt-get/bwrap stall on a sudo password prompt.
                # Enforce root even on the already-exists path.
                await BwrapSandbox._ensure_root_default_user(target_name)
                return True
        except (asyncio.TimeoutError, OSError):
            pass

        # Find a source distro to export
        source = await BwrapSandbox._find_any_wsl_distro(preferred="")
        if source is None:
            logger.warning("No WSL distro available to create sandbox from")
            return False

        logger.info(
            "Creating sandbox distro '{}' from '{}' (this may take "
            "2-5 minutes)...", target_name, source,
        )

        tar_path = None
        try:
            fd, tar_path = tempfile.mkstemp(
                suffix=".tar", prefix="miqi-sandbox-",
            )
            os.close(fd)

            # Export source distro
            _t0 = time.monotonic()
            export_proc = await _create_subprocess_exec(
                "wsl.exe", "--export", source, tar_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, export_stderr = await asyncio.wait_for(
                export_proc.communicate(), timeout=300.0,
            )
            logger.info("  wsl --export completed in {:.0f}s", time.monotonic() - _t0)
            if export_proc.returncode != 0:
                err = (
                    export_stderr.decode("utf-8", errors="replace")[:200]
                    if export_stderr else "unknown error"
                )
                logger.warning(
                    "Failed to export distro '{}': {}", source, err,
                )
                return False

            # Import as WSL2 sandbox distro in LOCALAPPDATA
            install_dir = str(
                Path(
                    os.environ.get("LOCALAPPDATA", "")
                    or os.environ.get("APPDATA", "")
                    or Path.home() / "AppData" / "Local"
                )
                / "MiQi Sandbox"
            )

            import_proc = await _create_subprocess_exec(
                "wsl.exe", "--import", target_name,
                install_dir, tar_path,
                "--version", "2",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _import_t0 = time.monotonic()
            _, import_stderr = await asyncio.wait_for(
                import_proc.communicate(), timeout=120.0,
            )
            logger.info("  wsl --import completed in {:.0f}s", time.monotonic() - _import_t0)
            if import_proc.returncode != 0:
                err = (
                    import_stderr.decode("utf-8", errors="replace")[:200]
                    if import_stderr else "unknown error"
                )
                logger.warning(
                    "Failed to import sandbox distro '{}': {}",
                    target_name, err,
                )
                return False

            # Set default user to root so apt-get never needs a password.
            # Reuse the idempotent helper so the just-imported distro's
            # wsl.conf is normalized the same way as the already-exists
            # path above (keeps any extra [boot]/[network] sections intact).
            await BwrapSandbox._ensure_root_default_user(target_name)

            logger.info(
                "Sandbox distro '{}' created (installed at {})",
                target_name, install_dir,
            )
            return True

        except (asyncio.TimeoutError, OSError) as exc:
            logger.warning(
                "Failed to create sandbox distro '{}': {}",
                target_name, exc,
            )
            return False
        finally:
            if tar_path and os.path.exists(tar_path):
                try:
                    os.remove(tar_path)
                except OSError:
                    pass

    @staticmethod
    def _is_transient_apt_error(msg: str) -> bool:
        """apt 网络瞬断类错误（重试一次可恢复）；非瞬断错误立即失败。"""
        low = msg.lower()
        return any(
            key in low
            for key in (
                "temporary failure resolving",
                "could not resolve",
                "connection timed out",
                "network is unreachable",
                "connection refused",
            )
        )

    @staticmethod
    async def _ensure_wsl_deps(distro: str) -> bool:
        """Install required packages in a WSL distro and verify bwrap + Python.
        Installs: bubblewrap, coreutils, rsync, python3, python3-pip,
        python3-venv, unzip.

        Returns True if bwrap and python3/pip are available after
        installation, False otherwise.
        """
        # Quick check: skip if bwrap and python3/pip already installed
        try:
            check = await _create_subprocess_exec(
                "wsl.exe", "-d", distro, "--", "bash", "-c", _WSL_READY_CMD,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(check.communicate(), timeout=30.0)
            except asyncio.TimeoutError:
                try:
                    check.kill()
                    await asyncio.wait_for(check.wait(), timeout=5.0)
                except (asyncio.TimeoutError, ProcessLookupError, OSError):
                    pass
                raise
            if check.returncode == 0:
                logger.info(
                    "bwrap and python3/pip already installed in WSL distro '{}'",
                    distro,
                )
                return True
        except (asyncio.TimeoutError, OSError):
            pass

        # Serialize installation: if another thread is already running
        # apt-get (e.g. sandbox manager background init), poll-wait for
        # the toolchain to become available instead of launching a second
        # apt-get. This avoids concurrent apt-get processes racing on
        # dpkg lock.
        if not _install_lock.acquire(blocking=False):
            logger.info(
                "Concurrent apt-get detected in WSL distro '{}' — waiting...",
                distro,
            )
            # Bounded by real elapsed time (180 s), not loop iterations:
            # each iteration also awaits a subprocess with its own timeout.
            deadline = time.monotonic() + 180.0
            while time.monotonic() < deadline:
                await asyncio.sleep(1)
                recheck = None
                try:
                    recheck = await _create_subprocess_exec(
                        "wsl.exe", "-d", distro, "--", "bash", "-c",
                        _WSL_READY_CMD,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        await asyncio.wait_for(
                            recheck.communicate(), timeout=5.0,
                        )
                    except asyncio.TimeoutError:
                        # Probe timed out — terminate and reap the WSL
                        # process so it cannot leak past this loop.
                        try:
                            recheck.kill()
                            await asyncio.wait_for(
                                recheck.wait(), timeout=5.0,
                            )
                        except (asyncio.TimeoutError, ProcessLookupError, OSError):
                            pass
                        recheck = None
                        continue
                    if recheck.returncode == 0:
                        logger.info(
                            "bwrap/python3/pip installed by concurrent "
                            "thread in WSL distro '{}' after ~{:.0f}s",
                            distro, time.monotonic() - deadline + 180.0,
                        )
                        return True
                except (asyncio.TimeoutError, OSError):
                    pass
                finally:
                    if recheck is not None and recheck.returncode is None:
                        try:
                            recheck.kill()
                        except (ProcessLookupError, OSError):
                            pass
            logger.warning(
                "Timed out waiting for concurrent apt-get in WSL distro '{}'",
                distro,
            )
            return False

        try:
            _t0 = time.monotonic()
            logger.info(
                "Auto-installing sandbox dependencies in WSL distro '{}'...",
                distro,
            )

            # Determine whether passwordless sudo is available.
            # Default WSL distros have a non-root user + sudo with
            # password — running "sudo apt-get ..." non-interactively
            # would hang waiting for the password prompt until the
            # 180 s timeout.  Check with sudo -n (non-interactive)
            # first and fall back to plain apt-get when sudo needs
            # a password.
            use_sudo = False
            try:
                check_nopass = await _create_subprocess_exec(
                    "wsl.exe", "-d", distro, "--", "bash", "-c",
                    "sudo -n true 2>/dev/null",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    await asyncio.wait_for(
                        check_nopass.communicate(), timeout=10.0,
                    )
                except asyncio.TimeoutError:
                    try:
                        check_nopass.kill()
                        await asyncio.wait_for(check_nopass.wait(), timeout=5.0)
                    except (asyncio.TimeoutError, ProcessLookupError, OSError):
                        pass
                    raise
                use_sudo = (check_nopass.returncode == 0)
            except (asyncio.TimeoutError, OSError):
                pass

            if use_sudo:
                logger.info("Using passwordless sudo for install in '{}'", distro)
            else:
                logger.info(
                    "sudo needs password in '{}' — trying without sudo",
                    distro,
                )

            # Build install command
            install_cmd = (
                "export DEBIAN_FRONTEND=noninteractive; "
                "apt-get update -qq 2>/dev/null; "
                "apt-get install -y -qq bubblewrap coreutils rsync "
                "python3 python3-pip python3-venv unzip"
            )
            if use_sudo:
                install_cmd = f"sudo bash -c '{install_cmd}'"

            # WSL/runner 网络偶发瞬断（Temporary failure resolving）会让
            # 安装失败——瞬断类错误重试一次，非瞬断错误立即失败。
            for _attempt in range(2):
                try:
                    proc = await _create_subprocess_exec(
                        "wsl.exe", "-d", distro, "--", "bash", "-c",
                        install_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        _stdout, stderr = await asyncio.wait_for(
                            proc.communicate(), timeout=180.0,
                        )
                    except asyncio.TimeoutError:
                        # Kill the wsl.exe wrapper before releasing the lock —
                        # an orphaned apt-get would keep holding the dpkg lock
                        # and deadlock the next installer.
                        try:
                            proc.kill()
                            await asyncio.wait_for(proc.wait(), timeout=5.0)
                        except (asyncio.TimeoutError, ProcessLookupError, OSError):
                            pass
                        raise
                    stderr = stderr.decode("utf-8", errors="replace") if stderr else ""

                    if proc.returncode != 0:
                        logger.info(
                            "  apt-get install completed in {:.0f}s (failed, attempt {})",
                            time.monotonic() - _t0, _attempt + 1,
                        )
                        err_msg = stderr[:300] or "unknown error"
                        if not use_sudo and (
                            "permission denied" in err_msg.lower()
                            or "are you root" in err_msg.lower()
                        ):
                            err_msg += (
                                " (sudo is required but needs a password. "
                                "Configure passwordless sudo in the WSL distro "
                                "or run: wsl -d {0} -- sudo apt-get install "
                                "bubblewrap python3 python3-pip)".format(distro)
                            )
                        if _attempt == 0 and BwrapSandbox._is_transient_apt_error(err_msg):
                            logger.warning(
                                "apt install failed with transient network error, retrying once: {}",
                                err_msg,
                            )
                            continue
                        logger.warning(
                            "Failed to install dependencies in WSL distro "
                            "'{}': {}", distro, err_msg,
                        )
                        return False
                    break
                except (asyncio.TimeoutError, OSError) as exc:
                    logger.warning(
                        "Failed to run apt install in WSL distro '{}': {}",
                        distro, exc,
                    )
                    return False
        finally:
            _install_lock.release()

        # Verify bwrap + python3/pip are now available
        try:
            verify = await _create_subprocess_exec(
                "wsl.exe", "-d", distro, "--", "bash", "-c", _WSL_READY_CMD,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(verify.communicate(), timeout=30.0)
            if verify.returncode == 0:
                logger.info(
                    "Successfully installed sandbox dependencies in WSL distro "
                    "'{}' (total {:.0f}s)", distro, time.monotonic() - _t0,
                )
                return True
        except (asyncio.TimeoutError, OSError):
            pass

        logger.warning(
            "Dependencies installed but bwrap or python3/pip still missing "
            "in WSL distro '{}'",
            distro,
        )
        return False

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Prepare the sandbox filesystem and verify bwrap availability."""
        # Detect execution environment
        if _is_windows():
            self._use_wsl = True
            distro = await self._detect_wsl_distro(self.wsl_distro)
            if not distro and self.auto_install_deps:
                # No distro with bwrap found — try installing deps in any WSL distro
                install_distro = await self._find_any_wsl_distro(self.wsl_distro)
                if install_distro:
                    if await self._ensure_wsl_deps(install_distro):
                        # Retry detection after install
                        distro = await self._detect_wsl_distro(install_distro)
            if not distro:
                raise BwrapSandboxError(
                    "No WSL distribution with bwrap and python3/pip found. "
                    "Install bubblewrap, python3, python3-pip in WSL: "
                    "apt install bubblewrap python3 python3-pip"
                )
            self._detected_distro = distro
            self._bwrap_path = "/usr/bin/bwrap"  # Always available if WSL detection passed
            logger.info(
                "Sandbox will run via WSL distro '{}' for session {}",
                distro, self.session_key,
            )
            append_workspace_log(
                self._log_workspace,
                f"Sandbox start via WSL distro={distro} session={self.session_key}",
                level="INFO",
                source="sandbox",
            )
        else:
            self._use_wsl = False
            self._bwrap_path = await self._find_bwrap_native()
            if not self._bwrap_path:
                raise BwrapSandboxError(
                    "bwrap not found. Install it: apt install bubblewrap"
                )

        # Create per-session directory structure inside Linux/WSL
        rc, out, err = await self._run_linux_command(
            f"mkdir -p '{self._linux_base_dir}' '{self.sandbox_home}' '{self.sandbox_workspace}'"
        )
        if rc != 0:
            raise BwrapSandboxError(
                f"Failed to create sandbox directories: {err}"
            )

        # Verify directories actually exist
        rc, _, err = await self._run_linux_command(
            f"test -d '{self.sandbox_home}' && test -d '{self.sandbox_workspace}'"
        )
        if rc != 0:
            raise BwrapSandboxError(
                f"Sandbox directories not found after creation: {err}"
            )

        # Copy workspace into sandbox if it exists
        # For WSL, the workspace path needs to be accessible from inside WSL
        linux_workspace = await self._resolve_workspace_path()
        if linux_workspace:
            # Check if workspace is directly accessible (e.g. /mnt/c/...)
            # In that case, we can skip rsync and just bind-mount it
            rc, _, _ = await self._run_linux_command(f"test -d '{linux_workspace}'")
            if rc == 0:
                logger.debug(
                    "Workspace accessible at {} — will use per-sandbox workspace instead of shared bind-mount",
                    linux_workspace,
                )

        # If the session uses a CUSTOM workspace (the user picked a project
        # directory in the workspace picker), bind-mount it into the sandbox
        # so exec and the file tools operate on the SAME directory.  The
        # default workspace keeps the per-session private sandbox dir
        # (Issue #221 isolation).
        from miqi.agent.tools.filesystem import _is_default_workspace
        if not _is_default_workspace(self.workspace):
            # A custom workspace MUST be bind-mounted so exec and the file
            # tools operate on the same directory.  If it can't be resolved
            # to an accessible Linux path, fail startup instead of silently
            # falling back to the (empty) private sandbox dir — that would
            # make exec and file tools modify different directories.
            if not linux_workspace:
                raise BwrapSandboxError(
                    f"Custom workspace is not accessible from the sandbox: {self.workspace}"
                )
            # Defensive: the resolved path must actually exist on the Linux
            # side (test -d) before binding — a stale/non-empty-but-missing
            # path would make bwrap fail with an obscure error.
            rc_exists, _, _ = await self._run_linux_command(f"test -d '{linux_workspace}'")
            if rc_exists != 0:
                raise BwrapSandboxError(
                    f"Custom workspace does not exist on the Linux side: {linux_workspace}"
                )
            self._linux_workspace = linux_workspace
            logger.info(
                "Sandbox will bind-mount custom workspace {} → /home/miqi/workspace",
                linux_workspace,
            )
        else:
            # Always use per-sandbox workspace — no shared host workspace bind mount
            self._linux_workspace = None

        self._running = True
        logger.info(
            "Sandbox prepared for session {}: {}",
            self.session_key,
            self.sandbox_workspace,
        )
        append_workspace_log(
            self._log_workspace,
            f"Sandbox prepared for session={self.session_key} workspace={self.sandbox_workspace}",
            level="INFO",
            source="sandbox",
        )

    async def stop(self) -> None:
        """Stop any running bwrap process and clean up sandbox directories."""
        self._running = False

        # Kill any streaming commands still running, then release their temp
        # resources.  Previously this only called cleanup() (script-file
        # removal), leaving the wsl.exe → bash → bwrap process chain alive
        # when a long-running command was in flight — the real source of
        # orphan WSL processes after stop() (#472).
        for handle in getattr(self, '_streaming_handles', []):
            try:
                await handle.kill()
            except Exception:
                pass
            try:
                await handle.cleanup()
            except Exception:
                pass
        self._streaming_handles = []

        # Clean up sandbox filesystem inside Linux/WSL — retry + verify the
        # directory is actually gone so failures surface instead of silently
        # leaking disk (#472).
        if await BwrapSandbox._rm_rf_retry(self._linux_base_dir, self._detected_distro):
            logger.info("Sandbox cleaned up: {}", self._linux_base_dir)
            append_workspace_log(
                self._log_workspace,
                f"Sandbox cleaned up session={self.session_key} path={self._linux_base_dir}",
                level="INFO",
                source="sandbox",
            )
        else:
            logger.warning("Failed to clean sandbox {}", self._linux_base_dir)
            append_workspace_log(
                self._log_workspace,
                f"Sandbox cleanup failed session={self.session_key} path={self._linux_base_dir}",
                level="WARNING",
                source="sandbox",
            )

    async def run_command(
        self,
        command: str,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> tuple[int, str, str]:
        """Run a command inside the bwrap sandbox.

        Returns:
            (exit_code, stdout, stderr)
        """
        if not self._running or not self._bwrap_path:
            raise BwrapSandboxError("Sandbox not started")

        # ── Defensive: verify sandbox directories still exist ──────────
        # In WSL, tmpfs /tmp directories can vanish between calls (e.g.
        # when multiple sandboxes are created/destroyed in CI).  Recreate
        # if the source bind-mounts are missing so bwrap doesn't fail with
        # "Can't find source path".
        rc, _, _ = await self._run_linux_command(
            f"test -d '{self.sandbox_home}' && test -d '{self.sandbox_workspace}'"
        )
        if rc != 0:
            logger.warning(
                "Sandbox directories missing for {} — recreating ({}, {})",
                self.session_key, self.sandbox_home, self.sandbox_workspace,
            )
            rc2, _, err2 = await self._run_linux_command(
                f"mkdir -p '{self._linux_base_dir}' '{self.sandbox_home}' '{self.sandbox_workspace}'"
            )
            if rc2 != 0:
                raise BwrapSandboxError(
                    f"Sandbox directories vanished and could not be recreated: {err2}"
                )
            logger.info("Sandbox directories recreated for {}", self.session_key)

        bwrap_args = self._build_bwrap_args(command, env=env, cwd=cwd)

        exit_code = -1
        stdout = ""
        stderr = ""

        try:
            if self._use_wsl:
                # Windows CreateProcess has a ~32767 char command-line limit.
                # bwrap with all its --ro-bind-try / --setenv flags can easily
                # exceed that.  Write the full command into a temp shell script
                # inside WSL and execute the script instead — the wsl.exe
                # command line stays short (just "bash /tmp/…").
                exit_code, stdout, stderr = await self._run_bwrap_via_script(bwrap_args, timeout)
            else:
                # Run bwrap natively — no command-line length issue on Linux
                full_args = bwrap_args

                process = await _create_subprocess_exec(
                    *full_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        process.communicate(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass
                    exit_code, stdout, stderr = (-1, "", f"Command timed out after {timeout}s")
                else:
                    stdout = stdout_bytes.decode("utf-8", errors="replace")
                    stderr = stderr_bytes.decode("utf-8", errors="replace")
                    exit_code = process.returncode if process.returncode is not None else -1

        except Exception as exc:
            exit_code, stdout, stderr = (-1, "", f"Failed to run bwrap: {exc}")

        # Capture command output to workspace logs for debugging
        self._log_command_result(command, exit_code, stdout, stderr)
        return exit_code, stdout, stderr

    def _log_command_result(
        self, command: str, exit_code: int, stdout: str, stderr: str
    ) -> None:
        """Log the result of a sandbox command to the workspace log file.

        Output is truncated to keep log entries manageable while still
        capturing enough context for debugging.
        """
        cmd_summary = command[:200] + "…" if len(command) > 200 else command
        level = "ERROR" if exit_code != 0 else "INFO"

        append_workspace_log(
            self._log_workspace,
            f"cmd [{self.session_key}] exit={exit_code}: {cmd_summary}",
            level=level,
            source="sandbox",
            session_key=self.session_key,
        )

        stdout_trimmed = stdout.rstrip()
        stderr_trimmed = stderr.rstrip()

        if stdout_trimmed:
            if len(stdout_trimmed) > 5000:
                stdout_trimmed = stdout_trimmed[:5000] + f"\n…[truncated {len(stdout)}B total]"
            append_workspace_log(
                self._log_workspace,
                f"[{self.session_key}] stdout:\n{stdout_trimmed}",
                level="DEBUG",
                source="sandbox",
                session_key=self.session_key,
            )

        if stderr_trimmed:
            if len(stderr_trimmed) > 5000:
                stderr_trimmed = stderr_trimmed[:5000] + f"\n…[truncated {len(stderr)}B total]"
            stderr_level = "WARNING" if exit_code != 0 else "DEBUG"
            append_workspace_log(
                self._log_workspace,
                f"[{self.session_key}] stderr:\n{stderr_trimmed}",
                level=stderr_level,
                source="sandbox",
                session_key=self.session_key,
            )

    async def run_command_streaming(
        self,
        command: str,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> BwrapCommandHandle:
        """Run a command inside the bwrap sandbox with streaming I/O.

        Unlike :meth:`run_command` which buffers all output and returns it
        at once, this method returns a :class:`BwrapCommandHandle` that
        provides incremental stdout/stderr access via
        :class:`asyncio.StreamReader`.  The **caller** is responsible for:

        * reading from ``handle.stdout`` / ``handle.stderr``,
        * calling ``await handle.wait()`` to await the exit code, and
        * calling ``await handle.cleanup()`` to release temporary resources.

        The caller also owns timeout and cancellation — use
        :meth:`BwrapCommandHandle.kill` to stop a running command.

        Returns:
            BwrapCommandHandle with .stdout, .stderr, .wait(), .kill(),
            and .cleanup().

        Raises:
            BwrapSandboxError: if the sandbox is not started.
        """
        if not self._running or not self._bwrap_path:
            raise BwrapSandboxError("Sandbox not started")

        bwrap_args = self._build_bwrap_args(command, env=env, cwd=cwd)

        if not hasattr(self, '_streaming_handles'):
            self._streaming_handles: list[BwrapCommandHandle] = []

        if self._use_wsl:
            handle = await self._run_bwrap_streaming_via_script(bwrap_args)
        else:
            handle = await self._run_bwrap_streaming_native(bwrap_args)

        self._streaming_handles.append(handle)
        # Handles are cleaned up when the sandbox stops (see stop()).
        # No need to wrap cleanup — __slots__ prevents monkey-patching.
        return handle

    async def _run_bwrap_streaming_native(
        self, bwrap_args: list[str],
    ) -> BwrapCommandHandle:
        """Launch bwrap natively with streaming stdout/stderr.

        Uses ``start_new_session=True`` so that :meth:`BwrapCommandHandle.kill`
        can target the entire process group (bwrap + children).
        """
        process = await _create_subprocess_exec(
            *bwrap_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            pgid = os.getpgid(process.pid)
        except (ProcessLookupError, OSError):
            pgid = None

        return BwrapCommandHandle(
            process, pgid=pgid, use_wsl=False,
        )

    async def _run_bwrap_streaming_via_script(
        self, bwrap_args: list[str],
    ) -> BwrapCommandHandle:
        """Launch bwrap via WSL script with streaming stdout/stderr.

        Writes a temp shell script inside WSL (via stdin pipe to avoid the
        32 767-char command-line limit), then executes it with
        ``wsl.exe -d distro -- bash script``.

        The script path is stored on the handle so :meth:`BwrapCommandHandle.cleanup`
        can remove it after the process exits.
        """
        script_id = uuid.uuid4().hex[:12]
        script_path = f"{self._linux_base_dir}/_bwrap_{script_id}.sh"

        escaped_args = " ".join(
            _shell_quote(a) for a in bwrap_args
        )
        script_content = (
            f"#!/bin/bash\n"
            f"# Diagnostic: log whether sandbox dirs needed recreation\n"
            f"for d in '{self.sandbox_home}' '{self.sandbox_workspace}'; do\n"
            f"  if test -d \"$d\"; then\n"
            f"    echo \"[sandbox] dir OK: $d\" >&2\n"
            f"  else\n"
            f"    echo \"[sandbox] dir MISSING — recreating: $d\" >&2\n"
            f"    mkdir -p \"$d\" || {{ echo \"[sandbox] FATAL: cannot create $d\" >&2; exit 1; }}\n"
            f"  fi\n"
            f"done\n"
            f"{escaped_args}\n"
        )

        write_rc, _, write_err = await self._write_wsl_file_via_stdin(
            script_path, script_content,
        )
        if write_rc != 0:
            raise BwrapSandboxError(
                f"Failed to write bwrap streaming script: {write_err}"
            )

        await self._run_linux_command(f"chmod +x '{script_path}'")

        full_args = self._wsl_prefix() + ["bash", script_path]

        process = await _create_subprocess_exec(
            *full_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        handle = BwrapCommandHandle(
            process, pgid=None, use_wsl=True,
        )
        handle._script_path = script_path
        handle._sandbox_ref = self
        return handle

    # ── WSL script-based execution ─────────────────────────────────────

    async def _run_bwrap_via_script(
        self,
        bwrap_args: list[str],
        timeout: float,
    ) -> tuple[int, str, str]:
        """Write bwrap args into a temp shell script inside WSL and execute it.

        This avoids the Windows CreateProcess command-line length limit
        (~32 767 chars) by keeping the wsl.exe invocation short and putting
        the potentially long bwrap command into a file inside the WSL
        filesystem.

        The script content is piped through stdin to avoid the same
        command-line length limit when writing the file.
        """
        # Build a unique script path inside WSL
        script_id = uuid.uuid4().hex[:12]
        script_path = f"{self._linux_base_dir}/_bwrap_{script_id}.sh"

        # Shell-escape each argument for the script
        escaped_args = " ".join(
            _shell_quote(a) for a in bwrap_args
        )
        script_content = (
            f"#!/bin/bash\n"
            f"# Diagnostic: log whether sandbox dirs needed recreation\n"
            f"for d in '{self.sandbox_home}' '{self.sandbox_workspace}'; do\n"
            f"  if test -d \"$d\"; then\n"
            f"    echo \"[sandbox] dir OK: $d\" >&2\n"
            f"  else\n"
            f"    echo \"[sandbox] dir MISSING — recreating: $d\" >&2\n"
            f"    mkdir -p \"$d\" || {{ echo \"[sandbox] FATAL: cannot create $d\" >&2; exit 1; }}\n"
            f"  fi\n"
            f"done\n"
            f"{escaped_args}\n"
        )

        # Write script into WSL via stdin pipe (avoids cmd-line length limit)
        write_rc, _, write_err = await self._write_wsl_file_via_stdin(
            script_path, script_content,
        )
        if write_rc != 0:
            return (-1, "", f"Failed to write bwrap script: {write_err}")

        # Make it executable
        await self._run_linux_command(f"chmod +x '{script_path}'")

        try:
            # Execute the script via wsl.exe — short command line
            full_args = self._wsl_prefix() + ["bash", script_path]

            process = await _create_subprocess_exec(
                *full_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                return (-1, "", f"Command timed out after {timeout}s")

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            return (process.returncode if process.returncode is not None else -1, stdout, stderr)

        finally:
            # Clean up the script file
            await self._run_linux_command(f"rm -f '{script_path}'", timeout=5.0)

    # ── Bwrap command builder ──────────────────────────────────────────

    # System directories that should be bind-mounted read-only from the host.
    # We bind individual directories instead of `--ro-bind / /` because the
    # latter makes the *entire* root read-only, preventing bwrap from creating
    # mount-point directories (like /home/miqi) for subsequent --bind mounts.
    _RO_BIND_DIRS: list[str] = [
        "/usr", "/bin", "/lib", "/lib64", "/lib32",
        "/etc", "/sbin", "/var", "/opt", "/snap",
    ]

    def _build_bwrap_args(
        self,
        command: str,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> list[str]:
        """Build the full bwrap argument list.

        Returns a list of arguments suitable for ``subprocess.exec`` — no
        shell quoting needed because each argument is passed separately.

        On WSL, this list is directly appended after ``wsl.exe -d distro --``.

        The sandbox layout:
        /usr, /bin, /lib, etc — read-only bind mounts from host
        /tmp                  — tmpfs (writable, per-session)
        /home/miqi            — per-session home (writable bind)
        /home/miqi/workspace  — per-session workspace (writable bind)
        /dev                  — new devtmpfs (minimal)
        /proc                 — new procfs
        """
        args = [self._bwrap_path]

        # ── Namespace isolation ─────────────────────────────────────
        args.append("--unshare-pid")
        if not self.share_net:
            args.append("--unshare-net")
        args.append("--unshare-ipc")
        args.append("--unshare-uts")

        # ── Hostname ────────────────────────────────────────────────
        args.extend(["--hostname", self.hostname])

        # ── UID/GID (requires --unshare-user) ──────────────────────
        # Hard --unshare-user, NOT --unshare-user-try: the -try variant
        # silently skips user-namespace isolation when the kernel refuses
        # (Docker containers, restricted kernels) while PID/net/ipc/uts stay
        # hard — an inconsistent, silent security downgrade (#81).  With the
        # hard flag bwrap fails loudly (stderr surfaces the error), so the
        # user knows the sandbox is not fully isolated.
        args.append("--unshare-user")
        args.extend(["--uid", str(self.uid)])
        args.extend(["--gid", str(self.gid)])

        # ── Proc & Dev ──────────────────────────────────────────────
        args.extend(["--proc", "/proc"])
        args.extend(["--dev", "/dev"])

        # ── Read-only host bind mounts ──────────────────────────────
        for d in self._RO_BIND_DIRS:
            args.extend(["--ro-bind-try", d, d])

        # ── /mnt bind mount (needed when running via WSL) ──────────
        # Windows files are accessible via /mnt/c, /mnt/d, etc. in WSL.
        # We need to bind-mount /mnt so the sandbox can access the
        # workspace files that live on the Windows filesystem.
        if self._use_wsl:
            args.extend(["--bind-try", "/mnt", "/mnt"])

        # ── Writable overlays ───────────────────────────────────────
        args.extend(["--tmpfs", "/tmp"])
        args.extend(["--bind", self.sandbox_home, "/home/miqi"])

        # ── Workspace mount ────────────────────────────────────────
        # Default workspace: use the per-sandbox workspace directory for
        # full session isolation (Issue #221).  Custom workspace: bind-mount
        # the user's project directory so exec and file tools see the same
        # files (consistency), while the session-key still isolates the
        # sandbox from other sessions.
        if self._linux_workspace:
            args.extend(["--bind", self._linux_workspace, "/home/miqi/workspace"])
        else:
            args.extend(["--bind", self.sandbox_workspace, "/home/miqi/workspace"])

        # ── /etc/resolv.conf ─────────────────────────────────────────
        # /etc is already ro-bind-mounted from host (share_net=True),
        # which includes the host's resolv.conf. No need to create
        # a separate copy that would fail on read-only /etc.


        # ── Extra bind mounts ───────────────────────────────────────
        for src in self.extra_ro_binds:
            args.extend(["--ro-bind", src, src])
        for src in self.extra_rw_binds:
            args.extend(["--bind", src, src])

        # ── Die with parent ─────────────────────────────────────────
        args.append("--die-with-parent")

        # ── New session ─────────────────────────────────────────────
        args.append("--new-session")

        # ── Environment variables (via --setenv for proper isolation) ──
        sandbox_env = self.get_sandbox_env()
        if env:
            sandbox_env.update(env)
        for k, v in sandbox_env.items():
            args.extend(["--setenv", k, v])

        # ── Command to execute ──────────────────────────────────────
        work_dir = cwd or "/home/miqi/workspace"
        args.extend(["/bin/bash", "-c", f"cd '{work_dir}' && {command}"])

        return args

    # ── Workspace sync ─────────────────────────────────────────────────

    async def _resolve_workspace_path(self) -> str | None:
        """Resolve the workspace path to a Linux-accessible path.

        On Windows, converts Windows paths to WSL paths (e.g.
        C:\\Users\\... → /mnt/c/Users/...) or uses the WSL-native path
        if the workspace is inside WSL's filesystem.
        """
        ws = str(self.workspace)

        if not self._use_wsl:
            # Native Linux — just check it exists
            rc, _, _ = await self._run_linux_command(f"test -d '{ws}'")
            return ws if rc == 0 else None

        # Windows + WSL — check if the workspace is accessible from WSL
        # First, try the path as-is (it might already be a WSL path)
        rc, out, _ = await self._run_linux_command(f"wslpath -u '{ws}' 2>/dev/null")
        if rc == 0 and out.strip():
            linux_path = out.strip()
            # Verify it exists
            rc2, _, _ = await self._run_linux_command(f"test -d '{linux_path}'")
            if rc2 == 0:
                return linux_path

        # Fallback: try common WSL path conversions
        # C:\path → /mnt/c/path
        if len(ws) >= 2 and ws[1] == ":":
            drive = ws[0].lower()
            rest = ws[2:].replace("\\", "/")
            linux_path = f"/mnt/{drive}{rest}"
            rc, _, _ = await self._run_linux_command(f"test -d '{linux_path}'")
            if rc == 0:
                return linux_path

        # Check if workspace is inside WSL filesystem already
        rc, _, _ = await self._run_linux_command(f"test -d '{ws}'")
        if rc == 0:
            return ws

        logger.warning(
            "Workspace path '{}' not accessible from WSL, sandbox will have empty workspace",
            ws,
        )
        return None

    async def _sync_workspace(self, linux_workspace: str) -> None:
        """Copy workspace files into the sandbox's workspace directory.

        Uses rsync if available for efficiency; falls back to cp -r.
        All operations happen inside Linux/WSL.
        """
        try:
            # Try rsync first
            rc, _, _ = await self._run_linux_command(
                f"rsync -a --delete '{linux_workspace}/' '{self.sandbox_workspace}/'",
                timeout=120.0,
            )
            if rc == 0:
                logger.debug("Workspace synced via rsync for {}", self.session_key)
                return
        except Exception:
            pass

        # Fallback: cp -r
        try:
            rc, _, err = await self._run_linux_command(
                f"rm -rf '{self.sandbox_workspace}'/* && "
                f"cp -r '{linux_workspace}/.' '{self.sandbox_workspace}/'",
                timeout=120.0,
            )
            if rc == 0:
                logger.debug("Workspace synced via cp for {}", self.session_key)
            else:
                logger.warning("Failed to sync workspace for {}: {}", self.session_key, err)
        except Exception as exc:
            logger.warning("Failed to sync workspace for {}: {}", self.session_key, exc)

    # ── Utility ────────────────────────────────────────────────────────

    @staticmethod
    async def _find_bwrap_native() -> str | None:
        """Find the bwrap binary on native Linux."""
        for candidate in ("bwrap", "/usr/bin/bwrap", "/usr/local/bin/bwrap"):
            try:
                proc = await _create_subprocess_exec(
                    "which", candidate,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.wait()
                if proc.returncode == 0:
                    return candidate
            except FileNotFoundError:
                continue
        return None

    @staticmethod
    async def is_available(wsl_distro: str = "", auto_install_deps: bool = True) -> bool:
        """Check if bwrap is available (natively or via WSL).

        When ``auto_install_deps`` is True and running on Windows, if no
        WSL distro has bwrap installed, this method will:
        1. Auto-create a dedicated sandbox distro (AIShadowSandbox)
           by exporting the first available distro
        2. Install bubblewrap, coreutils, rsync, python3, python3-pip,
           python3-venv, unzip into it
        """
        if _is_windows():
            distro = await BwrapSandbox._detect_wsl_distro(wsl_distro)
            if distro is not None:
                return True
            # No distro with bwrap — try auto-setup
            if auto_install_deps:
                # Ensure a dedicated sandbox distro exists
                target = wsl_distro or "AIShadowSandbox"
                if await BwrapSandbox._ensure_sandbox_distro(target):
                    install_distro = target
                else:
                    install_distro = await BwrapSandbox._find_any_wsl_distro(
                        wsl_distro,
                    )

                if install_distro:
                    cached = _auto_install_cache.get(install_distro)
                    if cached is False:
                        return False  # already tried and failed
                    if await BwrapSandbox._ensure_wsl_deps(install_distro):
                        distro = await BwrapSandbox._detect_wsl_distro(
                            install_distro,
                        )
                        result = distro is not None
                    else:
                        result = False
                    _auto_install_cache[install_distro] = result
                    return result
            return False
        else:
            return await BwrapSandbox._find_bwrap_native() is not None

    @staticmethod
    async def _communicate_or_kill(
        proc: asyncio.subprocess.Process, timeout: float = 15.0
    ) -> bytes:
        """``communicate()`` with a timeout; on timeout kill + await the process.

        A timed-out ``rm``/``test`` subprocess would otherwise keep running as
        an orphaned WSL wrapper — exactly what this PR is meant to eliminate.
        """
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return stderr
        except asyncio.TimeoutError:
            logger.warning("Sandbox subprocess timed out — killing it")
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            raise

    @staticmethod
    async def _rm_rf_retry(linux_dir: str, wsl_distro: str = "") -> bool:
        """Remove a directory tree with retries and post-delete verification.

        ``rm -rf`` can fail transiently in WSL (file locks, slow tmpfs, the
        WSL server momentarily restarting); retry with exponential backoff
        (0.5s/1s/2s, 3 attempts) and confirm the path is actually gone before
        reporting success.  Paths are passed as argv (never through a shell),
        so there is no quoting-injection surface (#472).
        """
        if _is_windows():
            distro = wsl_distro
            if not distro:
                distro = await BwrapSandbox._detect_wsl_distro() or ""
            if not distro:
                logger.warning("No WSL distro available for cleanup of {}", linux_dir)
                return False
            prefix = ["wsl.exe", "-d", distro, "--"]
        else:
            prefix = []

        for attempt in range(3):
            try:
                proc = await _create_subprocess_exec(
                    *prefix, "rm", "-rf", linux_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stderr_bytes = await BwrapSandbox._communicate_or_kill(proc)
                if proc.returncode != 0:
                    logger.warning(
                        "rm -rf {} failed (attempt {}/3): {}",
                        linux_dir, attempt + 1,
                        stderr_bytes.decode("utf-8", errors="replace").strip(),
                    )
                else:
                    # Verify the path is actually gone — test -e returns 0 if it
                    # still exists, so success means "removed".
                    check = await _create_subprocess_exec(
                        *prefix, "test", "-e", linux_dir,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await BwrapSandbox._communicate_or_kill(check)
                    if check.returncode != 0:
                        return True
                    logger.warning(
                        "rm -rf {} reported success but path still exists (attempt {}/3)",
                        linux_dir, attempt + 1,
                    )
            except asyncio.TimeoutError:
                logger.warning(
                    "rm -rf {} timed out (attempt {}/3)", linux_dir, attempt + 1
                )
            except Exception as exc:
                logger.warning(
                    "rm -rf {} failed (attempt {}/3): {}", linux_dir, attempt + 1, exc
                )
            if attempt < 2:
                await asyncio.sleep(0.5 * (2 ** attempt))
        logger.warning("Failed to remove {} after 3 attempts", linux_dir)
        return False

    @staticmethod
    async def cleanup_dir(
        linux_dir: str, wsl_distro: str = "", expected_root: str = ""
    ) -> bool:
        """Remove a sandbox directory from the Linux/WSL filesystem.

        This is used by SandboxManager to clean up stale sandboxes from
        previous bridge runs, without needing a full BwrapSandbox instance.

        Args:
            linux_dir: Absolute path inside Linux/WSL to remove.
            wsl_distro: WSL distribution name (auto-detect if empty).
            expected_root: The configured sandbox root (native ``sandbox_base_dir``
                or WSL ``wsl_base_dir``).  When provided, ``linux_dir`` must be
                at or below this root (boundary-safe); when empty, the legacy
                fixed-prefix guard is applied instead.

        Returns:
            True if the directory is gone, False if cleanup failed.
        """
        if not linux_dir or not linux_dir.startswith("/"):
            logger.warning("Refusing to cleanup non-absolute path: {}", linux_dir)
            return False

        if expected_root:
            root = expected_root.rstrip("/")
            if not (linux_dir == root or linux_dir.startswith(root + "/")):
                logger.warning(
                    "Refusing to cleanup path outside expected root {}: {}",
                    root, linux_dir,
                )
                return False
        else:
            # Legacy guard — only allow paths under known sandbox prefixes
            allowed_prefixes = ("/tmp/miqi-sandboxes/", "/tmp/miqi-sandbox")
            if not any(linux_dir.startswith(p) for p in allowed_prefixes):
                logger.warning(
                    "Refusing to cleanup path outside allowed prefixes: {}", linux_dir
                )
                return False

        ok = await BwrapSandbox._rm_rf_retry(linux_dir, wsl_distro)
        if ok:
            logger.debug("Cleaned up directory: {}", linux_dir)
        else:
            logger.warning("Failed to cleanup {} after retries", linux_dir)
        return ok

    @property
    def is_running(self) -> bool:
        """True if the sandbox has been started and not stopped."""
        return self._running

    @property
    def workspace_path(self) -> str:
        """The sandbox workspace path visible to tools (Linux-style)."""
        return self.sandbox_workspace

    @property
    def home_path(self) -> str:
        """The sandbox home directory path (Linux-style)."""
        return self.sandbox_home

    def get_sandbox_env(self) -> dict[str, str]:
        """Get environment variables to use inside the sandbox."""
        return {
            "HOME": "/home/miqi",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
            "TERM": "xterm-256color",
            "MIQI_SANDBOX": "1",
            "MIQI_SESSION_KEY": self.session_key,
        }
