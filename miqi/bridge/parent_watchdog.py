"""Parent-death watchdog — hard-exit the bridge when its launcher dies (#959).

Electron spawns the bridge (directly or via ``uv run``) with a stdin pipe.
When the launcher is killed hard (E2E force-kill path, crash), the bridge
can linger as an orphan — stdin EOF may never arrive (Windows handle
inheritance) and ``_graceful_shutdown`` can block on WSL sandbox teardown.
Orphans pollute later runs: ``mcps.list`` hangs, the MCP server list never
renders (#952 实测).

The watchdog polls the launcher process chain from a daemon thread and
hard-exits (``os._exit``) once it is dead, deliberately bypassing the
hang-prone graceful shutdown path. Callers may pass ``on_death`` for fast
cleanup that must complete before the exit (state-file clear, child reap).

Watched targets:

- the direct parent (always) — covers ``python server.py`` from a shell;
- ``MIQI_PARENT_PID`` (set by the desktop BridgeManager) — required on
  Windows when the direct parent is a launcher shim (``uv run`` / venv
  redirector) that survives its own launcher and keeps waiting for the
  child. Windows keeps the original parent PID forever, so getppid
  polling cannot detect the launcher's death there.

Death detection:

- Windows: the process handle is opened ONCE and polled via
  ``GetExitCodeProcess`` — the kernel process object stays queryable
  through the pinned handle after death, so the check is exact (no
  PID-reuse race). ``OpenProcess`` failing with ERROR_INVALID_PARAMETER
  (pid did not exist) counts as dead; access-denied counts as alive
  (never false-fire).
- POSIX: getppid change for the direct parent (orphans reparent to init,
  exact) plus ``kill(pid, 0)`` → ESRCH for ``MIQI_PARENT_PID`` (small
  PID-reuse window; the direct-parent check remains exact).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

from loguru import logger

WATCHDOG_ENV = "MIQI_PARENT_PID"

_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5


class _WindowsProcessHandle:
    """Pinned handle to a target process; exact post-mortem liveness query."""

    def __init__(self, pid: int) -> None:
        import ctypes

        self._kernel32 = ctypes.windll.kernel32
        self._handle = self._kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        # OpenProcess fails with INVALID_PARAMETER when the pid does not
        # exist (parent died before the watchdog attached) — treat as dead.
        # ACCESS_DENIED means we cannot determine — treat as alive so the
        # watchdog never false-fires.
        self._unqueryable = bool(
            not self._handle and ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        )

    def is_alive(self) -> bool:
        if not self._handle:
            return self._unqueryable
        import ctypes

        exit_code = ctypes.c_ulong()
        if not self._kernel32.GetExitCodeProcess(self._handle, ctypes.byref(exit_code)):
            return self._unqueryable
        return exit_code.value == _STILL_ACTIVE


def _is_alive_posix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Cannot determine — assume alive (never false-fire).
        return True
    return True


def is_alive(pid: int) -> bool:
    """True when the process exists (Windows: queryable exit code)."""
    if os.name == "nt":
        return _WindowsProcessHandle(pid).is_alive()
    return _is_alive_posix(pid)


def _env_watch_pid() -> int | None:
    raw = os.environ.get(WATCHDOG_ENV, "").strip()
    if not raw:
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    return pid if pid > 0 else None


def start_parent_watchdog(
    on_death: Callable[[], None] | None = None,
    interval_s: float = 1.0,
) -> None:
    """Start the parent-death watchdog in a daemon thread.

    Once the launcher chain is dead the thread runs *on_death* (fast
    cleanup only — never the hang-prone graceful shutdown) and calls
    ``os._exit(0)``, bypassing atexit handlers.
    """
    direct_parent = os.getppid()
    env_pid = _env_watch_pid()
    logger.info(
        "Parent watchdog armed: direct parent={} {}={}",
        direct_parent,
        WATCHDOG_ENV,
        env_pid,
    )

    watched: list[int] = [direct_parent]
    if env_pid is not None and env_pid != direct_parent:
        watched.append(env_pid)

    windows_handles: list[_WindowsProcessHandle] | None = None
    if os.name == "nt":
        windows_handles = [_WindowsProcessHandle(pid) for pid in watched]

    def _parent_dead() -> bool:
        if windows_handles is not None:
            return any(not handle.is_alive() for handle in windows_handles)
        if os.getppid() != direct_parent:
            return True
        return env_pid is not None and not _is_alive_posix(env_pid)

    def _run() -> None:
        try:
            while not _parent_dead():
                time.sleep(interval_s)
        except Exception:
            logger.exception("Parent watchdog thread failed")
            return
        logger.warning(
            "Launcher process chain dead (direct parent={} {}={}) — hard-exiting bridge",
            direct_parent,
            WATCHDOG_ENV,
            env_pid,
        )
        try:
            if on_death is not None:
                on_death()
        except Exception:
            logger.exception("Parent watchdog on_death cleanup failed")
        os._exit(0)

    threading.Thread(target=_run, name="bridge-parent-watchdog", daemon=True).start()
