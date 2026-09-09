"""#1003 finding ③：tracked_files.json 的读-改-写必须跨 SessionManager 实例串行。

``_persist_tracked_file``（``miqi/agent/tools/filesystem.py``）与 AppServer
handler 各自新建 ``SessionManager``，所以「实例级锁」锁不住同一 key 的并发写：
两个实例读到同一份旧快照，后写者覆盖先写者 → 丢条目。

修复把 ``_session_locks`` / ``_session_locks_guard`` 提升为模块级（按 key 共享），
并在 4 个 tracked 读-改-写方法（save_tracked_file / save_tracked_files_batch /
reset_tracked_file_op / remove_tracked_file）内全程持锁。

边界：本 PR 只做到**同进程跨实例**；跨进程（多个 bridge 进程、外部进程直接改
文件）协调仍缺失，见 PR #1003 说明。
"""

import threading
import time

from miqi.session.manager import SessionManager


def _slow_read(monkeypatch, delay: float = 0.02) -> None:
    """把「读旧快照 → 写回」的窗口撑开，使竞态稳定复现（不靠调度运气）。

    读完之后固定等待 *delay*：无锁实现下两个线程都会拿到同一份旧快照，
    后写者必然覆盖先写者；持锁实现下第二次读发生在第一次写之后，两条都在。
    """
    orig = SessionManager.load_tracked_files

    def slow(self, key, **kwargs):
        files = orig(self, key, **kwargs)
        time.sleep(delay)
        return files

    monkeypatch.setattr(SessionManager, "load_tracked_files", slow)


def _run_two_threads(fn_a, fn_b) -> list[BaseException]:
    """并发跑两个闭包，返回线程内未处理的异常（主线程断言用）。"""
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def wrap(fn):
        try:
            barrier.wait(timeout=10)
            fn()
        except BaseException as exc:  # noqa: BLE001 — 线程异常要带回主线程
            errors.append(exc)

    threads = [
        threading.Thread(target=wrap, args=(fn_a,), name="tracked-writer-a"),
        threading.Thread(target=wrap, args=(fn_b,), name="tracked-writer-b"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "线程未退出（疑似死锁）"
    return errors


def test_session_lock_is_shared_across_instances(tmp_path):
    """锁本身：同一 key 在不同实例上拿到同一把锁。"""
    sm_a = SessionManager(tmp_path)
    sm_b = SessionManager(tmp_path)

    assert sm_a._get_session_lock("desktop:983") is sm_b._get_session_lock("desktop:983")
    # 不同 key 仍是不同的锁
    assert sm_a._get_session_lock("desktop:983") is not sm_a._get_session_lock("desktop:984")


def test_two_instances_concurrent_single_writes_keep_both(tmp_path, monkeypatch):
    """两个不同实例并发写不同条目 → 两条都必须保留。"""
    _slow_read(monkeypatch)
    key = "desktop:983concurrent"
    sm_a = SessionManager(tmp_path)
    sm_b = SessionManager(tmp_path)

    errors = _run_two_threads(
        lambda: sm_a.save_tracked_file(key, "a.md", op="write"),
        lambda: sm_b.save_tracked_file(key, "b.md", op="write"),
    )

    assert errors == [], errors
    files = SessionManager(tmp_path).load_tracked_files(key)
    assert set(files) == {"a.md", "b.md"}, files


def test_two_instances_concurrent_batch_and_single_keep_both(tmp_path, monkeypatch):
    """批量写与单条写共用同一把 key 锁 → 三条都必须保留。"""
    _slow_read(monkeypatch)
    key = "desktop:983batch"
    sm_a = SessionManager(tmp_path)
    sm_b = SessionManager(tmp_path)

    errors = _run_two_threads(
        lambda: sm_a.save_tracked_files_batch(
            key, [("batch1.md", "write"), ("batch2.md", "write")],
        ),
        lambda: sm_b.save_tracked_file(key, "single.md", op="write"),
    )

    assert errors == [], errors
    files = SessionManager(tmp_path).load_tracked_files(key)
    assert set(files) == {"batch1.md", "batch2.md", "single.md"}, files
