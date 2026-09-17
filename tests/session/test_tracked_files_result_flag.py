"""#1104: tracked_files.json 的 ``result`` 标记语义。

``declare_result_files`` 走的 ``mark_tracked_file_result`` 只打标不改 op；
声明之后任何后续文件操作（save_tracked_file / save_tracked_files_batch /
reset_tracked_file_op）都不得把标记冲掉（sticky）。
"""

from pathlib import Path

from miqi.session.manager import SessionManager


def _sm(tmp_path: Path) -> SessionManager:
    return SessionManager(tmp_path)


def test_mark_result_creates_entry_and_preserves_existing_op(tmp_path):
    sm = _sm(tmp_path)
    key = "desktop:1104flag"

    # 新条目：op=write 兜底
    assert sm.mark_tracked_file_result(key, ["run/report.md"]) == 1
    files = sm.load_tracked_files(key)
    assert files["run/report.md"]["op"] == "write"
    assert files["run/report.md"]["result"] is True
    assert files["run/report.md"]["name"] == "report.md"

    # 既有条目：op/name/lastSeen 保持不变
    sm.save_tracked_file(key, "raw/input.cif", op="read")
    before = sm.load_tracked_files(key)["raw/input.cif"]
    sm.mark_tracked_file_result(key, ["raw/input.cif"])
    after = sm.load_tracked_files(key)["raw/input.cif"]
    assert after["op"] == "read"
    assert after["lastSeen"] == before["lastSeen"]
    assert after["result"] is True

    # 空列表不写盘、返回 0
    assert sm.mark_tracked_file_result(key, []) == 0


def test_result_flag_is_sticky_across_later_ops(tmp_path):
    sm = _sm(tmp_path)
    key = "desktop:1104sticky"
    sm.mark_tracked_file_result(key, ["run/report.md"])

    # 单条 save（rank 升级 write）不得丢标记
    sm.save_tracked_file(key, "run/report.md", op="write")
    assert sm.load_tracked_files(key)["run/report.md"]["result"] is True

    # 批量 save（exec 快照回写）不得丢标记
    sm.save_tracked_files_batch(key, [("run/report.md", "write")])
    assert sm.load_tracked_files(key)["run/report.md"]["result"] is True

    # reset op 不得丢标记
    sm.reset_tracked_file_op(key, "run/report.md", op="read")
    entry = sm.load_tracked_files(key)["run/report.md"]
    assert entry["op"] == "read"
    assert entry["result"] is True

    # 反例：未声明的条目保持无标记
    sm.save_tracked_file(key, "run/intermediate.json", op="write")
    assert "result" not in sm.load_tracked_files(key)["run/intermediate.json"]
