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


def test_session_lock_is_shared_by_alias_keys(tmp_path):
    """``desktop:983`` 与 ``desktop_983`` 派生同一目录 → 必须同一把锁。

    两条写链的 key 形态不同：``_persist_tracked_file`` 传派生名
    （``desktop_983``），``file_handlers``（files.accept/revert/write）传客户端
    原始 key（``desktop:983``）。按原始字符串取锁 = 同一文件两把锁。
    """
    sm = SessionManager(tmp_path)

    assert sm.get_session_dir("desktop:983") == sm.get_session_dir("desktop_983")
    assert sm._get_session_lock("desktop:983") is sm._get_session_lock("desktop_983")


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


def test_concurrent_raw_and_derived_key_writes_keep_both(tmp_path, monkeypatch):
    """别名 key 并发写同一文件：两条都要保留。

    复刻生产的两条写链：工具写端 ``_persist_tracked_file`` 传派生 key
    （``desktop_983alias``），面板写端 ``file_handlers`` 传客户端原始 key
    （``desktop:983alias``）——两者落同一个 tracked_files.json。
    """
    _slow_read(monkeypatch)
    sm_tool = SessionManager(tmp_path)
    sm_panel = SessionManager(tmp_path)

    errors = _run_two_threads(
        lambda: sm_tool.save_tracked_file("desktop_983alias", "tool.md", op="write"),
        lambda: sm_panel.save_tracked_file("desktop:983alias", "panel.md", op="write"),
    )

    assert errors == [], errors
    files = SessionManager(tmp_path).load_tracked_files("desktop:983alias")
    assert set(files) == {"tool.md", "panel.md"}, files


def test_clear_tracked_files_waits_for_key_lock(tmp_path):
    """clear 必须在 key 锁内：否则整文件删除会与在途的读-改-写交错。"""
    key = "desktop:983clear"
    sm = SessionManager(tmp_path)
    sm.save_tracked_file(key, "a.md", op="write")
    store = sm.get_session_dir(key) / "tracked_files.json"
    assert store.exists()

    done = threading.Event()
    thread = threading.Thread(
        target=lambda: (sm.clear_tracked_files(key), done.set()),
        name="tracked-clearer",
    )
    with sm._get_session_lock(key):
        thread.start()
        time.sleep(0.1)
        assert not done.is_set(), "clear 未等待 key 锁"
        assert store.exists(), "clear 在持锁期间删除了文件"
    thread.join(timeout=10)

    assert done.is_set(), "clear 未退出"
    assert not store.exists()
