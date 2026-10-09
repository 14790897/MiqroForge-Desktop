"""#1203 回归：stdout 写绝不能阻塞事件循环。

故障形状：桥往 stdout 写，管道缓冲满了就阻塞；读端是 Electron 主进程，
它最忙的时候（长回合）最可能不排空。以前这个阻塞发生在**事件循环线程**上，
一次写卡住就冻住所有在飞请求——chat.abort 也发不出去，用户看到的是
「停止按钮和只读请求集体超时」。

这里守两件事：
  1. 底层写入受阻时，_send 仍然立刻返回（它只入队）；
  2. 数据不会因此丢失或乱序——writer 线程恢复后照样按序完整写出。
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from miqi.bridge import server


class BlockingSink:
    """模拟「管道满了」：write 一直阻塞到 release 被置位。"""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()
        self.chunks: list[bytes] = []

    def write(self, chunk: bytes) -> int:
        self.entered.set()
        self.release.wait(timeout=10.0)
        self.chunks.append(chunk)
        return len(chunk)

    def flush(self) -> None:
        pass


class RecordingSink:
    """不阻塞，只记录，用来验证顺序与完整性。"""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, chunk: bytes) -> int:
        self.chunks.append(chunk)
        return len(chunk)

    def flush(self) -> None:
        pass


class DiscardingSink:
    """垃圾桶：吞掉不属于本用例的遗留输出。"""

    def write(self, chunk: bytes) -> int:
        return len(chunk)

    def flush(self) -> None:
        pass


def _drain(timeout: float = 5.0) -> None:
    """等 writer 线程把队列排空。"""
    deadline = time.monotonic() + timeout
    while server._stdout_queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not server._stdout_queue.unfinished_tasks, "writer 线程没能排空队列"


@pytest.fixture(autouse=True)
def _isolate_stdout(monkeypatch):
    """writer 是模块级单例，必须让它在用例之间写进垃圾桶、并把遗留排空。"""
    monkeypatch.setattr(server, "_stdout_buffer", DiscardingSink())
    server._ensure_stdout_writer()
    _drain()
    yield
    _drain()


def test_send_returns_immediately_while_the_pipe_is_full(monkeypatch):
    """关键断言：底层写入卡住时，_send 不许跟着卡，且不会写重。

    最后那条"进 200 条、出 200 条"是防重复的：_send 只入队一次，writer 也只写
    一次——不存在"循环线程先同步写一遍、再进队列写第二遍"这种情况。
    """
    sink = BlockingSink()
    monkeypatch.setattr(server, "_stdout_buffer", sink)

    calls = 200
    started = time.monotonic()
    for i in range(calls):
        server._send({"id": str(i), "result": {}})
    elapsed = time.monotonic() - started

    # 以前这里会一直挂到 sink 放行（10s）；现在只入队，应当近乎瞬时。
    assert elapsed < 1.0, f"_send 被底层写入阻塞了：{elapsed:.2f}s"

    # 证明确实有一条约会被卡住的写先到了 sink —— 不是没写好。
    assert sink.entered.wait(timeout=2.0), "writer 线程没把数据交到 sink"

    sink.release.set()
    _drain()

    # 放行后：进多少出多少，一条不多一条不少。
    assert len(sink.chunks) == calls, f"写出 {len(sink.chunks)} 条，应为 {calls} 条"


def test_lines_survive_backpressure_in_order(monkeypatch):
    """先卡住、再放行：所有行都要到齐，且顺序不乱。"""
    sink = BlockingSink()
    monkeypatch.setattr(server, "_stdout_buffer", sink)

    count = 50
    for i in range(count):
        server._send({"id": str(i), "result": {"n": i}})
    assert sink.entered.wait(timeout=2.0)

    sink.release.set()
    _drain()

    ids = [json.loads(c.decode("utf-8"))["id"] for c in sink.chunks]
    assert ids == [str(i) for i in range(count)]


def test_stop_writer_flushes_the_backlog(monkeypatch):
    """退出前必须把队列里的东西写完，不能带着未发出的响应就结束。"""
    sink = RecordingSink()
    monkeypatch.setattr(server, "_stdout_buffer", sink)

    for i in range(10):
        server._send({"id": f"last-{i}", "result": {}})

    server._stop_stdout_writer()

    assert server._stdout_queue.unfinished_tasks == 0
    ids = [json.loads(c.decode("utf-8"))["id"] for c in sink.chunks]
    assert ids == [f"last-{i}" for i in range(10)]


def test_backlog_warning_fires_when_the_parent_stops_reading(monkeypatch):
    """父进程完全不读时要留下证据 —— 这正是 #1203 事后查不出来的东西。"""
    sink = BlockingSink()
    monkeypatch.setattr(server, "_stdout_buffer", sink)
    monkeypatch.setattr(server, "_STDOUT_BACKLOG_WARN", 10)
    monkeypatch.setattr(server, "_stdout_backlog_warned_at", 0.0)

    warnings: list[str] = []
    monkeypatch.setattr(
        server, "_log", lambda msg, level="INFO": warnings.append(f"{level}:{msg}")
    )

    for i in range(40):  # 远超阈值；第一条会被 writer 线程取走并卡住
        server._send({"id": str(i), "result": {}})
    assert sink.entered.wait(timeout=2.0)
    # 让 writer 线程观察到积压并报警
    deadline = time.monotonic() + 3.0
    while not warnings and time.monotonic() < deadline:
        time.sleep(0.01)

    sink.release.set()
    _drain()

    assert any("stdout backlog" in w for w in warnings), f"没有积压告警：{warnings}"
