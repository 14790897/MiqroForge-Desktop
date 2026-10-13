"""#1036：桥的协议通道必须对子进程不可见。

桥用 stdin 收发 JSON 协议。Windows（与 POSIX）会把父进程的标准输入交给**每一个
没有显式重定向它的子进程** —— 于是桥在沙箱里跑命令时，那条命令链
（``wsl.exe … ``）拿的是同一根管道，命令存活期间就能把协议行读走：请求永远到
不了桥的读线程，Desktop 一路等到 720s 超时。

这里把「修复前 / 修复后」的对照一起测了，一条命令即可复现：

    pytest tests/bridge/test_bridge_stdin_isolation.py -v -s

输出里 A_* 是修复前的处境（子进程**当场把协议行读走**），B_* 是修复做法
（子进程读不到任何协议字节，而桥自己仍按行读得到）。

整套动作跑在一个**子进程**里：它会改写自己的 fd 0，不能碰 pytest 进程的。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PROTOCOL_LINE = '{"id":"req-1","method":"config.get"}'

SCRIPT = """
import os, subprocess, sys
sys.path.insert(0, {repo!r})
from miqi.bridge.server import _detach_protocol_stdin

CHILD_CODE = "import sys; print(repr(sys.stdin.readline()))"


def spawn_child():
    \"\"\"起一个**故意不给 stdin=** 的子进程 —— 与仓库里大多数 spawn 的写法一致。\"\"\"
    return subprocess.run(
        [sys.executable, '-c', CHILD_CODE],
        capture_output=True, text=True, timeout=60,
    ).stdout.strip()


# ── A) 修复前的处境：fd 0 就是协议管道
r1, w1 = os.pipe()
os.write(w1, b'{line}\\n')
os.dup2(r1, 0)
print('A_CHILD=' + spawn_child())

# ── B) 修复做法：协议挪到私有描述符，fd 0 → 空设备
r2, w2 = os.pipe()
os.write(w2, b'{line}\\n')
os.dup2(r2, 0)
protocol = _detach_protocol_stdin()
if protocol is None:
    print('DETACH=failed')
    raise SystemExit(0)
print('B_CHILD=' + spawn_child())
print('B_PARENT=' + repr(protocol.readline().strip()))
"""


def _run_experiment() -> dict[str, str]:
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(SCRIPT).format(repo=str(REPO_ROOT), line=PROTOCOL_LINE)],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(REPO_ROOT),
    )
    out = proc.stdout
    assert "DETACH=failed" not in out, out + proc.stderr
    observed = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        observed[key] = value
    return observed


def test_detach_protocol_stdin_hides_protocol_from_children() -> None:
    """对照：不隔离时子进程把协议行读走；隔离后它读不到，桥自己还读得到。"""
    o = _run_experiment()
    child_after = o.get("B_CHILD", "")
    print(
        "\n"
        "  ── 修复前（fd 0 就是协议管道，子进程默认继承）────────────────\n"
        f"     子进程读到 : {o.get('A_CHILD')}\n"
        "     → 协议行被它读走，桥的读线程永远收不到这条请求\n"
        "  ── 修复后（协议挪到私有 fd，fd 0 换成空设备）────────────────\n"
        f"     子进程读到 : {child_after}\n"
        f"     桥自己读到 : {o.get('B_PARENT')}\n"
        "     → 子进程碰不到协议，请求只归桥的读线程\n"
    )
    # 修复前的处境：不给 stdin= 的子进程**当场读到**协议行（这就是 #1036 的机制）
    assert PROTOCOL_LINE in o.get("A_CHILD", ""), o
    # 修复后：子进程只能读到空设备
    assert o.get("B_CHILD") == "''", o
    # 桥自己照样能读到协议行
    assert o.get("B_PARENT") == repr(PROTOCOL_LINE), o


def test_detach_protocol_stdin_returns_none_for_a_bad_fd() -> None:
    """环境不允许隔离时返回 None（调用方据此打一行警告），不抛、不阻塞启动。"""
    from miqi.bridge.server import _detach_protocol_stdin

    assert _detach_protocol_stdin(999_123) is None
