"""#1005 节 1：会话目录派生全链路必须落在同一个目录名上。

会话目录名只有一个实现（``miqi.session.session_keys.session_files_dir_key``），
写侧（agent 工具落 tracked_files.json）、面板读侧（``sessions.get_tracked_files``）、
沙箱兜底搜索（``_find_in_sandbox_workspaces``）、附件落盘（``attachment_dest_dir``）
以及 ``_resolve_session_files_path`` / ``_resolve_session_snapshot_dir`` 都必须
解析出同一个 ``sessions/<safe_key>``。

用例对 issue #1005 期望表的 6 个 key 形态各写死一个字面期望目录名；三段
namespaced 键（``miqi-desktop:desktop:<ts>``）在修复前会与其余形态分叉。
"""

from __future__ import annotations

import json

import pytest

from miqi.session.session_keys import session_files_dir_key

# issue #1005 期望表：raw key → 磁盘目录名（与
# tests/session/test_session_keys.py 及
# tests/agent/tools/test_session_workspace_isolation.py::test_session_files_dir_key_derivation
# 锁定的是同一组期望值）。
SESSION_KEY_EXPECTATIONS = [
    ("miqi-desktop:desktop:1786807046853", "desktop_1786807046853"),
    ("desktop:1786807046853", "desktop_1786807046853"),
    ("cli:direct", "cli_direct"),
    ("cli:other", "cli_other"),
    ("gateway:default", "gateway_default"),
    ("thread_nomap", "thread_nomap"),
]


class _FakeSandboxManager:
    """Minimal stand-in for the sandbox manager used by the read fallback."""

    def __init__(self, workspace):
        self._workspace = str(workspace)

    def list_sandboxes(self):
        return [{"workspace": self._workspace, "distro": ""}]


class _FakeConfig:
    def __init__(self, workspace):
        self.workspace_path = workspace


class _FakeBridgeState:
    def __init__(self, workspace):
        self._workspace = workspace
        self._sandbox_manager = _FakeSandboxManager(workspace)

    def load_config(self):
        return _FakeConfig(self._workspace)


@pytest.mark.parametrize("session_key,expected_dir", SESSION_KEY_EXPECTATIONS)
def test_sandbox_fallback_searches_canonical_session_dir(
    tmp_path, monkeypatch, session_key, expected_dir,
):
    """文件只存在于 ``sessions/<canonical_key>/files`` 时，兜底必须命中它。"""
    import miqi.bridge.server as bridge_module
    from miqi.runtime.file_handlers import _find_in_sandbox_workspaces

    sandbox_ws = tmp_path / "sandbox_ws"
    target = sandbox_ws / "sessions" / expected_dir / "files" / "report.md"
    target.parent.mkdir(parents=True)
    target.write_text("payload", encoding="utf-8")

    monkeypatch.setattr(bridge_module, "_state", _FakeBridgeState(sandbox_ws))

    found = _find_in_sandbox_workspaces(
        "report.md", tmp_path / "host" / "report.md", session_key,
    )

    assert found is not None, (
        f"session_key={session_key!r}: 兜底没找到 "
        f"sessions/{expected_dir}/files/report.md"
    )
    assert found[0] == target.resolve()
    assert found[1] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("session_key,expected_dir", SESSION_KEY_EXPECTATIONS)
async def test_write_read_sandbox_attachment_share_one_session_dir(
    tmp_path, monkeypatch, session_key, expected_dir,
):
    """写侧 / 面板读侧 / 沙箱兜底 / 附件落盘 / 路径解析器 → 同一个目录名。"""
    import miqi.bridge.server as bridge_module
    from miqi.agent.tools.filesystem import _persist_tracked_file
    from miqi.bridge.loop import attachment_dest_dir
    from miqi.runtime.file_handlers import (
        _find_in_sandbox_workspaces,
        _resolve_session_files_path,
        _resolve_session_snapshot_dir,
    )
    from miqi.runtime.session_handlers import sessions_get_tracked_files_handler

    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir()
    monkeypatch.setattr(bridge_module, "_state", _FakeBridgeState(workspace))

    session_dir = workspace / "sessions" / expected_dir
    target = session_dir / "files" / "report.md"
    target.parent.mkdir(parents=True)
    target.write_text("payload", encoding="utf-8")
    rel_path = f"sessions/{expected_dir}/files/report.md"

    # ── 写侧：agent 工具把文件登记进 tracked_files.json（传【原始】session_key，
    #    归一发生在 SessionManager 层）───────────────────────────────────────
    _persist_tracked_file(workspace, target, op="write", session_key=session_key)

    tracked_path = session_dir / "tracked_files.json"
    assert tracked_path.exists(), (
        f"session_key={session_key!r}: 写侧的 tracked_files.json 没落在 "
        f"sessions/{expected_dir}/"
    )
    assert rel_path in json.loads(tracked_path.read_text(encoding="utf-8"))["files"]

    # ── 面板读侧：sessions.get_tracked_files 必须读到同一条记录 ─────────────
    response = await sessions_get_tracked_files_handler(
        "req-1", {"session_key": session_key}, "client-1", None, None,
    )
    assert rel_path in [
        f["path"] for f in response["result"]["tracked_files"]
    ], f"session_key={session_key!r}: 面板读侧与写侧不在同一个目录"

    # ── 沙箱兜底：按同一个目录名搜索 ───────────────────────────────────────
    found = _find_in_sandbox_workspaces(
        "report.md", workspace / "host" / "report.md", session_key,
    )
    assert found is not None, f"session_key={session_key!r}: 沙箱兜底没命中"
    assert found[0] == target.resolve()

    # ── 附件落盘 & 路径解析器：同一个目录名 ────────────────────────────────
    assert attachment_dest_dir(workspace, session_key) == session_dir / "files"
    assert _resolve_session_files_path("client-1", session_key) == session_dir / "files"
    assert _resolve_session_snapshot_dir("client-1", session_key) == (
        session_dir / "snapshots"
    )


def test_shared_helper_is_the_single_source_of_truth():
    """agent 工具侧的私有名必须就是公共实现本身（re-export，不是副本）。"""
    from miqi.agent.tools import filesystem

    assert filesystem._session_files_dir_key is session_files_dir_key
