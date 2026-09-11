"""#1003 finding ③：tracked_files.json 的读-改-写必须跨 SessionManager 实例串行。

``_persist_tracked_file``（``miqi/agent/tools/filesystem.py``）与 AppServer
handler 各自新建 ``SessionManager``，所以「实例级锁」锁不住同一 key 的并发写：
两个实例读到同一份旧快照，后写者覆盖先写者 → 丢条目。

修复把 ``_session_locks`` / ``_session_locks_guard`` 提升为模块级（按 key 共享），
并在 4 个 tracked 读-改-写方法（save_tracked_file / save_tracked_files_batch /
reset_tracked_file_op / remove_tracked_file）内全程持锁。

CodeRabbit 复审补充：``SessionManager.delete`` 也纳入同一把 key 锁 —— 否则
落盘删除会与在途写回交错，把已删会话目录连同 ``tracked_files.json`` 重建（复活）。

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


def _gate_tracked_read(monkeypatch, gate: dict) -> None:
    """把写端卡在「已读旧快照、尚未 mkdir/写回」的窗口内，直到 ``release`` 置位。

    与 ``_slow_read`` 的固定 sleep 不同，这里用事件闸门定序：写端进入窗口后主线程
    才启动删除线程，「删除线程是否被 key 锁挡住」完全由锁决定，不靠调度运气。
    """
    orig = SessionManager.load_tracked_files

    def gated(self, key, **kwargs):
        files = orig(self, key, **kwargs)
        gate["entered"].set()
        gate["release"].wait(timeout=30)
        return files

    monkeypatch.setattr(SessionManager, "load_tracked_files", gated)


def _delete_race_round(tmp_path, key, gate: dict, round_no: int) -> None:
    """一轮「在途 save_tracked_file vs delete」竞态，断言删除后目录未被重建。"""
    sm_writer = SessionManager(tmp_path)
    sm_deleter = SessionManager(tmp_path)
    session_dir = sm_writer.get_session_dir(key)
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "conversation.jsonl").write_text("", encoding="utf-8")

    gate["entered"].clear()
    gate["release"].clear()
    errors: list[BaseException] = []
    delete_results: list[bool] = []
    delete_done = threading.Event()

    def writer():
        try:
            sm_writer.save_tracked_file(key, f"ghost-{round_no}.md", op="write")
        except BaseException as exc:  # noqa: BLE001 — 线程异常要带回主线程
            errors.append(exc)

    def deleter():
        try:
            delete_results.append(sm_deleter.delete(key))
        except BaseException as exc:  # noqa: BLE001 — 线程异常要带回主线程
            errors.append(exc)
        finally:
            delete_done.set()

    t_writer = threading.Thread(target=writer, name=f"tracked-writer-{round_no}")
    t_deleter = threading.Thread(target=deleter, name=f"session-deleter-{round_no}")
    t_writer.start()
    assert gate["entered"].wait(timeout=30), f"[round {round_no}] 写线程未进入读-改-写窗口"
    t_deleter.start()

    # 写线程仍持 key 锁：删除线程必须被挡住，不得在写回之前完成删除
    deleted_while_writing = delete_done.wait(timeout=0.5)
    gate["release"].set()
    t_writer.join(timeout=30)
    t_deleter.join(timeout=30)

    assert not t_writer.is_alive(), f"[round {round_no}] 写线程未退出（疑似死锁）"
    assert not t_deleter.is_alive(), f"[round {round_no}] 删除线程未退出（疑似死锁）"
    assert errors == [], errors
    assert not deleted_while_writing, (
        f"[round {round_no}] delete 未等待 key 锁：写端仍持锁时就完成了删除"
    )
    assert delete_results == [True], delete_results
    assert not session_dir.exists(), (
        f"[round {round_no}] 已删除的会话目录被在途写端重建（会话复活）"
    )
    assert not (session_dir / "tracked_files.json").exists(), (
        f"[round {round_no}] tracked_files.json 在删除后被重建"
    )


def test_delete_does_not_resurrect_session_during_in_flight_tracked_write(tmp_path, monkeypatch):
    """delete 必须与 tracked 读-改-写共用同一把 key 锁（#1003 CodeRabbit 复审）。

    写端在 ``save_tracked_file`` 的「已读旧快照 → mkdir/写回」窗口内被闸门卡住，
    删除端此刻并发 ``delete``：

    - 无锁实现：删除立刻 ``rmtree``，写端随后 ``path.parent.mkdir(...)`` 把会话
      目录连同 ``tracked_files.json`` 重建 —— 已删会话复活；
    - 持锁实现：删除端被 key 锁挡住，等写端写完后才删，目录保持消失。

    连跑 3 轮，避免单轮调度偶然性。
    """
    key = "desktop:983delete"
    gate: dict = {"entered": threading.Event(), "release": threading.Event()}
    _gate_tracked_read(monkeypatch, gate)

    for round_no in range(3):
        _delete_race_round(tmp_path, key, gate, round_no)


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
