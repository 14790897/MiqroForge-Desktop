"""#1005：``session_files_dir_key`` 是会话目录名的唯一实现。

锁两条性质：
1. 两段键 / 无冒号键与旧的 raw 约定（``safe_filename(key.replace(":", "_"))``）
   **逐字相同** —— 现有磁盘数据（2026-09-14 实测 341 个会话目录全是两段形态）零迁移；
2. 三段 namespaced 键剥掉 client_id 首段，且派生幂等。

``SessionManager.get_session_dir`` 必须与它同源，否则写侧与面板读侧又会分叉。
"""

from __future__ import annotations

import pytest

from miqi.session.manager import SessionManager
from miqi.session.session_keys import session_files_dir_key
from miqi.utils.helpers import safe_filename

# issue #1005 期望表：raw key → 磁盘目录名
SESSION_KEY_EXPECTATIONS = [
    ("miqi-desktop:desktop:1786807046853", "desktop_1786807046853"),
    ("desktop:1786807046853", "desktop_1786807046853"),
    ("cli:direct", "cli_direct"),
    ("cli:other", "cli_other"),
    ("gateway:default", "gateway_default"),
    ("thread_nomap", "thread_nomap"),
]

# 两段及以下的键：raw 约定与 canonical 约定必须完全一致（回归保护）
TWO_SEGMENT_KEYS = [
    "desktop:1786807046853",
    "cli:direct",
    "cli:other",
    "gateway:default",
    "thread_nomap",
]


@pytest.mark.parametrize("session_key,expected_dir", SESSION_KEY_EXPECTATIONS)
def test_expected_table(session_key, expected_dir):
    assert session_files_dir_key(session_key) == expected_dir


@pytest.mark.parametrize("session_key", TWO_SEGMENT_KEYS)
def test_two_segment_keys_keep_the_raw_convention(session_key):
    """两段键行为逐字不变：canonical == 旧 raw 公式，现有目录无需迁移。"""
    assert session_files_dir_key(session_key) == safe_filename(
        session_key.replace(":", "_")
    )


@pytest.mark.parametrize("session_key", [k for k, _ in SESSION_KEY_EXPECTATIONS])
def test_derivation_is_idempotent(session_key):
    once = session_files_dir_key(session_key)
    assert session_files_dir_key(once) == once


@pytest.mark.parametrize("session_key,expected_dir", SESSION_KEY_EXPECTATIONS)
def test_get_session_dir_matches_shared_helper(tmp_path, session_key, expected_dir):
    sm = SessionManager(tmp_path / "ws")
    assert sm.get_session_dir(session_key) == (
        tmp_path / "ws" / "sessions" / session_files_dir_key(session_key)
    )
    assert sm.get_session_dir(session_key).name == expected_dir


@pytest.mark.parametrize("session_key,expected_dir", SESSION_KEY_EXPECTATIONS)
def test_archive_marker_is_written_to_the_canonical_dir(
    tmp_path, session_key, expected_dir,
):
    """**写侧**：``SessionManager.archive`` 把 ``.archived`` 写进 canonical 目录。

    本用例不调用任何 handler —— 它只断言标记落盘位置（``get_session_dir`` 派生）。
    读侧（``sessions.list_archived`` 必须到同一个目录里找标记）由
    ``tests/bridge/test_list_archived_namespaced_key.py`` 用真实 handler + 三段 key
    覆盖（#1014 评审 B-3 缺口 1：旧名字暗示覆盖面板读侧，实际只锁了写侧）。
    """
    sm = SessionManager(tmp_path / "ws")
    sm.archive(session_key)
    marker = sm.sessions_dir / session_files_dir_key(session_key) / ".archived"
    assert marker.exists()
    assert marker.parent.name == expected_dir


# ── #1014：旧文件的 raw 名 vs 新目录的 canonical 名 ────────────────────────
#
# 三段 namespaced key 下两个名字不同：旧扁平文件 / 旧全局目录文件的名字写死于
# raw 约定（``safe_filename(key.replace(":", "_"))``），新会话目录用 canonical。
# 「找旧用 raw、放新用 canonical」是刻意不同源，本组用例防止后人「顺手统一」。

_NAMESPACED_KEY = "miqi-desktop:desktop:1786807046853"
_NAMESPACED_RAW_NAME = "miqi-desktop_desktop_1786807046853.jsonl"
_NAMESPACED_DIR = "desktop_1786807046853"


def test_migrate_flat_to_dir_finds_raw_flat_file_and_uses_canonical_dir(tmp_path):
    """旧扁平文件按 raw 名查找，迁移进 canonical 目录（#1014 C-4）。"""
    sm = SessionManager(tmp_path / "ws")
    old_flat = sm.sessions_dir / _NAMESPACED_RAW_NAME
    old_flat.write_text('{"role": "user", "content": "hi"}\n', encoding="utf-8")

    sm._migrate_flat_to_dir(_NAMESPACED_KEY)

    canonical_dir = sm.sessions_dir / session_files_dir_key(_NAMESPACED_KEY)
    assert canonical_dir.name == _NAMESPACED_DIR
    assert (canonical_dir / "conversation.jsonl").read_text(encoding="utf-8") == (
        '{"role": "user", "content": "hi"}\n'
    )
    assert not old_flat.exists()
    assert not (sm.sessions_dir / "miqi-desktop_desktop_1786807046853").exists()


def test_delete_removes_raw_flat_file_for_namespaced_key(tmp_path):
    """``delete`` 能清掉三段 key 的旧扁平文件（raw 名）并返回 True。"""
    sm = SessionManager(tmp_path / "ws")
    old_flat = sm.sessions_dir / _NAMESPACED_RAW_NAME
    old_flat.write_text('{"role": "user", "content": "hi"}\n', encoding="utf-8")

    assert sm.delete(_NAMESPACED_KEY) is True

    assert not old_flat.exists()
    assert not (sm.sessions_dir / _NAMESPACED_DIR).exists()


def test_delete_flat_fallback_looks_up_the_raw_name(tmp_path, monkeypatch):
    """``delete`` 的扁平兜底分支本身：查旧文件用 raw 名，与迁移同一约定。

    常规「只有扁平文件」的场景已被 ``_migrate_flat_to_dir`` 搬进目录，兜底分支
    只在迁移被跳过时才有机会执行；这里显式让迁移不生效以隔离该分支，锁住它的
    命名约定（canonical 化会让三段 key 的旧文件永远删不掉）。
    """
    sm = SessionManager(tmp_path / "ws")
    old_flat = sm.sessions_dir / _NAMESPACED_RAW_NAME
    old_flat.write_text('{"role": "user", "content": "hi"}\n', encoding="utf-8")
    monkeypatch.setattr(sm, "_migrate_flat_to_dir", lambda key: None)

    assert sm.delete(_NAMESPACED_KEY) is True

    assert not old_flat.exists()


def test_migrate_flat_to_dir_not_blocked_by_files_only_dir(tmp_path):
    """canonical 目录仅含 ``files/`` 时不得跳过迁移（#1014 CodeRabbit 评审）。

    附件落盘 / archive 标记会先建出目录，所以「目录存在」≠「会话已迁移」。
    判据是 ``conversation.jsonl``；把判据改回 ``not new_dir.exists()`` 时本用例
    必须变红（否则表示没锁住这个回归）。
    """
    sm = SessionManager(tmp_path / "ws")
    canonical_dir = sm.sessions_dir / _NAMESPACED_DIR
    (canonical_dir / "files").mkdir(parents=True)

    old_flat = sm.sessions_dir / _NAMESPACED_RAW_NAME
    old_flat.write_text(
        f'{{"_type": "metadata", "key": "{_NAMESPACED_KEY}",'
        ' "created_at": "2026-09-14T00:00:00", "updated_at": "2026-09-14T00:00:00",'
        ' "metadata": {}, "last_consolidated": 0}\n'
        '{"role": "user", "content": "hi", "timestamp": "2026-09-14T00:00:01"}\n',
        encoding="utf-8",
    )

    session = sm._load(_NAMESPACED_KEY)

    assert session is not None
    assert [m["content"] for m in session.messages] == ["hi"]
    assert (canonical_dir / "conversation.jsonl").exists()
    assert not old_flat.exists()


def test_migrate_flat_to_dir_keeps_existing_conversation(tmp_path):
    """对照：已含 ``conversation.jsonl`` 时跳过迁移，既有对话不被覆盖。"""
    sm = SessionManager(tmp_path / "ws")
    canonical_dir = sm.sessions_dir / _NAMESPACED_DIR
    canonical_dir.mkdir(parents=True)
    existing = canonical_dir / "conversation.jsonl"
    existing.write_text('{"role": "user", "content": "canonical"}\n', encoding="utf-8")

    old_flat = sm.sessions_dir / _NAMESPACED_RAW_NAME
    old_flat.write_text('{"role": "user", "content": "stale flat"}\n', encoding="utf-8")

    sm._migrate_flat_to_dir(_NAMESPACED_KEY)

    assert existing.read_text(encoding="utf-8") == '{"role": "user", "content": "canonical"}\n'
    assert old_flat.read_text(encoding="utf-8") == '{"role": "user", "content": "stale flat"}\n'


def test_legacy_global_path_keeps_the_raw_name(tmp_path):
    """``_get_legacy_session_path`` 保持 raw：它读的是历史写死的文件名。

    ``~/.assistant/sessions/<raw>.jsonl`` 这些文件不会因 canonical 化而改名，
    归一查找只会让它们永远找不到（#1014 C-4）。
    """
    legacy = tmp_path / "legacy-sessions"
    sm = SessionManager(tmp_path / "ws", legacy_sessions_dir=legacy)

    path = sm._get_legacy_session_path(_NAMESPACED_KEY)

    assert path == legacy / _NAMESPACED_RAW_NAME
    assert path.name != f"{session_files_dir_key(_NAMESPACED_KEY)}.jsonl"
