"""#1036：桥的协议通道必须对子进程不可见。

桥用 stdin 收发 JSON 协议。Windows（与 POSIX）会把父进程的标准输入交给**每一个
没有显式重定向它的子进程** —— 于是桥在沙箱里跑命令时，那条命令链
（``wsl.exe … ``）拿的是同一根管道，命令存活期间就能把协议行读走：请求永远到
不了桥的读线程，Desktop 一路等到 720s 超时。

这里测两件事：

1. ``_detach_protocol_stdin()`` 之后，**新起的、没给 ``stdin=`` 的子进程**读标准
   输入只能读到 EOF（空设备）—— 协议行它再也拿不到；
2. 桥自己仍能从私有描述符上按行读到协议。

整套动作跑在一个**子进程**里：它会改写自己的 fd 0，不能碰 pytest 进程的。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SCRIPT = """
import os, subprocess, sys
sys.path.insert(0, {repo!r})
from miqi.bridge.server import _detach_protocol_stdin

r, w = os.pipe()
os.write(w, b'{{"id":"req-1","method":"config.get"}}\\n')
os.dup2(r, 0)          # 模拟「桥的 fd 0 就是协议管道」

protocol = _detach_protocol_stdin()
if protocol is None:
    print('DETACH=failed')
    raise SystemExit(0)

# 新起的子进程：故意不给 stdin=，与仓库里大多数 spawn 的写法一致
child = subprocess.run(
    [sys.executable, '-c', "import sys; print(repr(sys.stdin.readline()))"],
    capture_output=True, text=True, timeout=60,
)
print('CHILD=' + child.stdout.strip())
print('PARENT=' + repr(protocol.readline().strip()))
"""


def test_detach_protocol_stdin_hides_protocol_from_children() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(SCRIPT).format(repo=str(REPO_ROOT))],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(REPO_ROOT),
    )
    out = proc.stdout
    assert "DETACH=failed" not in out, proc.stdout + proc.stderr
    # 子进程读到的是空设备（EOF），不是协议行
    assert "CHILD=''" in out, out + proc.stderr
    assert "req-1" not in out.split("PARENT=")[0], out
    # 桥自己照样能读到协议行
    assert "PARENT='{\"id\":\"req-1\",\"method\":\"config.get\"}'" in out, out + proc.stderr


def test_detach_protocol_stdin_returns_none_for_a_bad_fd() -> None:
    """环境不允许隔离时返回 None（调用方据此打一行警告），不抛、不阻塞启动。"""
    from miqi.bridge.server import _detach_protocol_stdin

    assert _detach_protocol_stdin(999_123) is None
