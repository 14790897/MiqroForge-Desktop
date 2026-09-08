"""Parent-death watchdog tests (#959).

The watchdog lives in spawned helper processes because its exit path is
``os._exit`` — running it in-process would kill pytest. All tests spawn
short-lived dummy processes and watch children that arm the watchdog.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from miqi.bridge.parent_watchdog import WATCHDOG_ENV, is_alive

pytestmark = pytest.mark.subprocess

POLL_INTERVAL = 0.2
WAIT_TIMEOUT = 15.0
WATCHDOG_CHILD = (
    "import time\n"
    "from miqi.bridge.parent_watchdog import start_parent_watchdog\n"
    f"start_parent_watchdog(interval_s={POLL_INTERVAL})\n"
    "print('watchdog-ready', flush=True)\n"
    "time.sleep(120)\n"
)


def _spawn(code: str, env: dict[str, str] | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1", **(env or {})},
    )


def _base_python() -> str:
    """绕过 uv venv 的 redirector shim（Windows）。

    shim 会在拉起真实 python 后存活等待，杀死 launcher 时 shim 仍在 ——
    直接父进程（shim）不死，回退路径的看门狗永远不触发。用
    sys._base_executable 直接 spawn 真实 python，使「launcher = 直接父进程」成立。
    """
    return getattr(sys, "_base_executable", None) or sys.executable


def _wait_dead(proc: subprocess.Popen, timeout_s: float = WAIT_TIMEOUT) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.1)
    return False


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def test_watchdog_exits_when_env_parent_dies():
    """MIQI_PARENT_PID 指向的进程死亡 → 看门狗子进程硬退出（跨平台）。"""
    dummy = _spawn("import time; time.sleep(120)")
    try:
        assert dummy.poll() is None
        child = _spawn(WATCHDOG_CHILD, env={WATCHDOG_ENV: str(dummy.pid)})
        try:
            assert child.stdout.readline().strip() == "watchdog-ready"
            assert is_alive(dummy.pid)
            dummy.kill()
            dummy.wait(timeout=10)
            assert _wait_dead(child), (
                f"watchdog child (pid {child.pid}) should exit after the "
                f"watched pid {dummy.pid} died"
            )
        finally:
            _kill(child)
    finally:
        _kill(dummy)


def test_watchdog_falls_back_to_direct_parent():
    """无 MIQI_PARENT_PID 时监视直接父进程：launcher 自杀后子进程应退出。

    Windows 上子进程必须用 base python 直接 spawn（绕过 venv redirector
    shim —— shim 存活会掩盖 launcher 死亡，见 _base_python）。
    """
    base_py = _base_python()
    launcher_code = (
        "import os, subprocess, time\n"
        # base python 直跑没有 venv 的 site-packages —— 把 launcher 的
        # sys.path 透传给子进程（loguru 等依赖在 venv 里）
        "venv_path = os.pathsep.join(p for p in __import__('sys').path if p)\n"
        f"child = subprocess.Popen([{base_py!r}, '-c', {WATCHDOG_CHILD!r}],"
        " stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,"
        " env={**os.environ, 'PYTHONPATH': venv_path})\n"
        "line = child.stdout.readline()\n"
        "while line and line.strip() != 'watchdog-ready':\n"  # skip watchdog log line
        "    line = child.stdout.readline()\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    launcher = _spawn(launcher_code)
    try:
        child_pid = int(launcher.stdout.readline().strip())
        assert is_alive(child_pid)
        launcher.kill()
        launcher.wait(timeout=10)
        deadline = time.time() + WAIT_TIMEOUT
        while time.time() < deadline and is_alive(child_pid):
            time.sleep(0.1)
        assert not is_alive(child_pid), (
            f"watchdog child (pid {child_pid}) should exit after its direct "
            "parent (launcher) died"
        )
    finally:
        _kill(launcher)


def test_watchdog_does_not_fire_while_parents_alive():
    """被监视进程存活时看门狗不误触发。"""
    dummy = _spawn("import time; time.sleep(120)")
    try:
        child = _spawn(WATCHDOG_CHILD, env={WATCHDOG_ENV: str(dummy.pid)})
        try:
            assert child.stdout.readline().strip() == "watchdog-ready"
            # 数个轮询周期后子进程仍应存活（测试进程与 dummy 都活着）
            time.sleep(POLL_INTERVAL * 5)
            assert child.poll() is None, "watchdog fired while watched pids are alive"
        finally:
            _kill(child)
    finally:
        _kill(dummy)


def test_is_alive_reports_death():
    """is_alive：存活 True → 进程死亡（回收）后 False。"""
    proc = _spawn("import time; time.sleep(120)")
    try:
        assert is_alive(proc.pid)
        proc.kill()
        proc.wait(timeout=10)
        deadline = time.time() + WAIT_TIMEOUT
        while time.time() < deadline and is_alive(proc.pid):
            time.sleep(0.1)
        assert not is_alive(proc.pid)
    finally:
        _kill(proc)
