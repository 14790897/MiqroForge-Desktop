"""会话级写授权共享 store（issue #1013）。

`#864` 审批卡产生的**会话级**授权原先只落在文件工具实例上
（``WriteFileTool._granted`` & co），``ExecTool`` 无引用 → 同一会话的
``exec`` 写「卡片已授权目录」仍然 fail-closed 失败。

本文件覆盖 store 本身与**写入口**一侧（发布）：三个写工具（write_file /
edit_file / apply_patch）在 ``bypass`` 与 ``always_dir`` 两处把授权发布到
进程级 store；``once``（允许本次）绝不发布。exec 一侧（orchestrator 注入 →
rw bind / 护栏消费）见 ``tests/execution/test_exec_write_grants_1013.py``。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from miqi.agent.tools.apply_patch import ApplyPatchTool
from miqi.agent.tools.filesystem import EditFileTool, WriteFileTool
from miqi.agent.tools.write_grants import (
    SessionWriteGrants,
    get_write_grants,
    norm_session_key,
    reset_write_grants,
)


@pytest.fixture(autouse=True)
def _clean_process_store():
    """进程级 store 是全局的——每个用例前后清空，避免跨用例串味。"""
    reset_write_grants()
    yield
    reset_write_grants()


def _resolver(choice: str):
    async def r(payload: dict):
        return {"status": "submitted", "answers": {"choice_id": choice}}

    return r


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


# ── store 本体 ───────────────────────────────────────────────────────────


class TestSessionWriteGrants:
    def test_add_then_get(self):
        store = SessionWriteGrants()
        store.add("s1", r"C:\Users\me\Desktop\out")
        assert store.get("s1") == frozenset({r"C:\Users\me\Desktop\out"})

    def test_unknown_session_is_empty_not_none(self):
        """fail-closed：未知会话（以及空 store）返回空集，不是 None。"""
        store = SessionWriteGrants()
        assert store.get("nope") == frozenset()
        assert store.get(None) == frozenset()
        assert store.get("") == frozenset()

    def test_none_and_empty_key_share_a_bucket(self):
        """键空间与文件工具的 ``_session_granted`` 一致：None 与 "" 同桶。

        （``_session_granted`` 用 ``self._granted.setdefault(key or "", ...)``，
        store 用同一个 ``norm_session_key``，两侧不可能分叉。）
        """
        store = SessionWriteGrants()
        store.add(None, "/tmp/out")
        assert store.get("") == frozenset({"/tmp/out"})
        assert norm_session_key(None) == norm_session_key("")

    def test_sessions_are_isolated(self):
        store = SessionWriteGrants()
        store.add("a", "/tmp/a")
        store.add("b", "/tmp/b")
        assert store.get("a") == frozenset({"/tmp/a"})
        assert store.get("b") == frozenset({"/tmp/b"})

    def test_dedupe_is_case_insensitive_keeps_original_spelling(self):
        """大小写变体只留一条，且保留首次写入的原始拼写（exec 要用它做 bind 源）。"""
        store = SessionWriteGrants()
        store.add("s", r"C:\Users\Me\Desktop")
        store.add("s", r"c:\users\me\desktop")
        assert len(store.get("s")) == 1

    def test_empty_and_invalid_input_ignored(self):
        """空/非法输入静默忽略——store 只做追加，绝不因脏输入放大授权。"""
        store = SessionWriteGrants()
        store.add("s", "")
        store.add("s", None)
        store.add("s", 42)  # os.fspath rejects non-path-likes
        assert store.get("s") == frozenset()

    def test_clear(self):
        store = SessionWriteGrants()
        store.add("s", "/tmp/x")
        store.clear()
        assert store.get("s") == frozenset()


# ── 写入口发布（always_dir / bypass / once）──────────────────────────────


class TestFileToolPublishing:
    async def test_always_dir_publishes_to_store(self, tmp_path: Path):
        """「本目录不再询问」→ 同会话 store 里有该目录。"""
        ws = _ws(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        tool = WriteFileTool(
            workspace=ws, allowed_dir=ws, shared_roots=[],
            write_resolver=_resolver("always_dir"),
        )
        result = await tool.execute(
            path=str(outside / "x.txt"), content="hi", _session_key="sess-A",
        )
        assert result.startswith("Successfully wrote")
        assert get_write_grants().get("sess-A") == frozenset({str(outside.resolve())})

    async def test_once_never_publishes(self, tmp_path: Path):
        """「允许本次」是调用级的——绝不进 store（否则一次点击放大成会话级 exec 授权）。"""
        ws = _ws(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        tool = WriteFileTool(
            workspace=ws, allowed_dir=ws, shared_roots=[],
            write_resolver=_resolver("once"),
        )
        result = await tool.execute(
            path=str(outside / "x.txt"), content="hi", _session_key="sess-A",
        )
        assert result.startswith("Successfully wrote")
        assert get_write_grants().get("sess-A") == frozenset()
        assert get_write_grants().session_keys() == ()

    async def test_bypass_publishes_to_store(self, tmp_path: Path):
        """bypass（approvals.bypass*_write_approval）产生的会话授权同给 exec（本次口径）。"""
        ws = _ws(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        tool = WriteFileTool(
            workspace=ws, allowed_dir=ws, shared_roots=[],
            write_resolver=None, bypass_approval=True,
        )
        result = await tool.execute(
            path=str(outside / "x.txt"), content="hi", _session_key="sess-A",
        )
        assert result.startswith("Successfully wrote")
        assert get_write_grants().get("sess-A") == frozenset({str(outside.resolve())})

    async def test_grant_does_not_leak_across_sessions(self, tmp_path: Path):
        """会话 A 的授权不出现在会话 B（同一工具实例服务所有会话）。"""
        ws = _ws(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        tool = WriteFileTool(
            workspace=ws, allowed_dir=ws, shared_roots=[],
            write_resolver=_resolver("always_dir"),
        )
        await tool.execute(
            path=str(outside / "x.txt"), content="hi", _session_key="sess-A",
        )
        assert get_write_grants().get("sess-B") == frozenset()
        assert get_write_grants().get("sess-A") == frozenset({str(outside.resolve())})

    async def test_unrestricted_native_path_publishes_nothing(self, tmp_path: Path):
        """无边界路径（allowed_dir=None 且无 WSL 沙箱）不弹卡也不发布——与今天逐字节一致。"""
        ws = _ws(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        tool = WriteFileTool(workspace=ws, shared_roots=[])  # allowed_dir=None
        result = await tool.execute(
            path=str(outside / "x.txt"), content="hi", _session_key="sess-A",
        )
        assert result.startswith("Successfully wrote")
        assert get_write_grants().get("sess-A") == frozenset()

    async def test_deny_publishes_nothing(self, tmp_path: Path):
        ws = _ws(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        tool = WriteFileTool(
            workspace=ws, allowed_dir=ws, shared_roots=[],
            write_resolver=_resolver("deny"),
        )
        result = await tool.execute(
            path=str(outside / "x.txt"), content="hi", _session_key="sess-A",
        )
        assert result.startswith("Error: 权限被拒绝")
        assert get_write_grants().get("sess-A") == frozenset()

    async def test_edit_file_publishes_to_store(self, tmp_path: Path):
        """全部三个写工具都经 _resolve_write_shared_roots——edit_file 同样发布。"""
        ws = _ws(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "x.txt"
        target.write_text("old", encoding="utf-8")
        tool = EditFileTool(
            workspace=ws, allowed_dir=ws, shared_roots=[],
            write_resolver=_resolver("always_dir"),
        )
        result = await tool.execute(
            path=str(target), old_text="old", new_text="new", _session_key="sess-A",
        )
        assert result.startswith("Successfully")
        assert get_write_grants().get("sess-A") == frozenset({str(outside.resolve())})

    async def test_apply_patch_publishes_to_store(self, tmp_path: Path):
        ws = _ws(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "x.txt"
        target.write_text("line1\nline2\nline3\n", encoding="utf-8")
        patch = (
            f"--- a/{target}\n+++ b/{target}\n"
            "@@ -1,3 +1,3 @@\n line1\n-line2\n+line2-changed\n line3\n"
        )
        tool = ApplyPatchTool(
            workspace=ws, allowed_dir=ws, shared_roots=[],
            write_resolver=_resolver("always_dir"),
        )
        result = await tool.execute(patch=patch, _session_key="sess-A")
        assert result.startswith("Applied patch to")
        assert get_write_grants().get("sess-A") == frozenset({str(outside.resolve())})

    async def test_injected_store_instance_is_used(self, tmp_path: Path):
        """工厂/测试可注入自己的 store——注入后不再写进程级单例。"""
        ws = _ws(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        own = SessionWriteGrants()
        tool = WriteFileTool(
            workspace=ws, allowed_dir=ws, shared_roots=[],
            write_resolver=_resolver("always_dir"), write_grants=own,
        )
        await tool.execute(
            path=str(outside / "x.txt"), content="hi", _session_key="sess-A",
        )
        assert own.get("sess-A") == frozenset({str(outside.resolve())})
        assert get_write_grants().get("sess-A") == frozenset()

    async def test_no_session_key_publishes_to_empty_bucket(self, tmp_path: Path):
        """无会话键（headless）落到 "" 桶——两侧同一 ``norm_session_key``，
        与文件工具 ``_session_granted(None)`` 的分桶完全一致。"""
        ws = _ws(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        tool = WriteFileTool(
            workspace=ws, allowed_dir=ws, shared_roots=[],
            write_resolver=_resolver("always_dir"),
        )
        await tool.execute(path=str(outside / "x.txt"), content="hi")
        assert get_write_grants().get("") == frozenset({str(outside.resolve())})
        assert get_write_grants().get(None) == get_write_grants().get("")
