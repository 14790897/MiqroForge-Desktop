"""#1005 节 1：``files.read`` 沙箱兜底的会话目录派生必须与 canonical 派生一致。

``_find_in_sandbox_workspaces`` 自己拼 ``sessions/<safe_key>/files`` 兜底搜索路径。
它原先用 ``split(":", 1)[-1]`` 派生目录名，对两段渠道键（``desktop:<ts>`` /
``cli:*`` / ``gateway:*``）少剥一层，搜到一个没有任何写入方的目录 → 文件明明
在沙箱工作区里也报「文件不存在」。

用例对 issue #1005 期望表的 6 个 key 形态各写死一个字面期望目录名；修复前
4 个形态（两段键）会红。
"""

from __future__ import annotations

import pytest

# issue #1005 期望表：raw key → 磁盘目录名（与
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


class _FakeBridgeState:
    def __init__(self, workspace):
        self._sandbox_manager = _FakeSandboxManager(workspace)


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
