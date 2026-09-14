"""exec 侧的会话级写授权接线（issue #1013）。

闭环：文件工具审批卡（``本目录不再询问`` / bypass）→ 进程级 store
（``miqi.agent.tools.write_grants``）→ ``ToolOrchestrator._execute_in_sandbox``
的 ``_user_roots`` 注入 → ``ExecTool._exec_rw_binds``（沙箱 rw bind）与
``ExecTool._guard_write_roots``（静态护栏 ``extra_write_roots``）。

口径（用户 2026-09-14 拍板）：
  * ``always_dir`` / ``bypass`` 的会话授权同给 exec；
  * ``once``（允许本次）绝不进 store、不给 exec；
  * fail-closed 只增不减——没有任何授权时注入值与 #1013 之前逐字节一致。

写入口一侧（发布）见 ``tests/agent/tools/test_write_grants.py``；#984 的
bind/护栏构造契约保持不变，由 ``tests/execution/test_exec_write_boundary_984.py``
继续锁定。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miqi.agent.tools.filesystem import WriteFileTool
from miqi.agent.tools.shell import ExecTool
from miqi.agent.tools.write_grants import reset_write_grants
from miqi.execution.orchestrator import ToolExecutionContext, ToolOrchestrator
from miqi.execution.sandbox_policy import SandboxSelection, SandboxType
from miqi.protocol.permissions import (
    FileSystemAccessMode,
    FileSystemSandboxPolicy,
    NetworkSandboxPolicy,
)


@pytest.fixture(autouse=True)
def _clean_process_store():
    reset_write_grants()
    yield
    reset_write_grants()


def _resolver(choice: str):
    async def r(payload: dict):
        return {"status": "submitted", "answers": {"choice_id": choice}}

    return r


def _selection(kind: SandboxType) -> SandboxSelection:
    return SandboxSelection(
        sandbox_type=kind,
        filesystem_policy=FileSystemSandboxPolicy(
            default_mode=FileSystemAccessMode.READ,
        ),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        env_passthrough=[],
        timeout_ms=30_000,
        reason=f"test {kind.value}",
    )


class _RecordingTool:
    """Records the kwargs ToolOrchestrator._execute_in_sandbox injects."""

    parameters = {"type": "object", "properties": {"command": {"type": "string"}}}

    def __init__(self, name: str = "exec") -> None:
        self.name = name
        self.last_kwargs: dict | None = None

    async def execute(self, **kwargs) -> str:
        self.last_kwargs = dict(kwargs)
        return "ok"


class _Registry:
    def __init__(self, tool: _RecordingTool) -> None:
        self._tool = tool

    def get(self, name: str) -> _RecordingTool:
        return self._tool


async def _injected_roots(
    session_id: str,
    *,
    user_roots: list[str] | None = None,
    tool_name: str = "exec",
) -> list[str]:
    """跑一遍真实的注入路径，返回 exec 实际收到的 ``_user_roots``。"""
    tool = _RecordingTool(tool_name)
    orchestrator = ToolOrchestrator(
        permission_engine=MagicMock(),
        sandbox_engine=MagicMock(),
        hook_runtime=MagicMock(),
        tool_registry=_Registry(tool),
        event_emitter=MagicMock(),
    )
    ctx = ToolExecutionContext(
        tool_name=tool_name,
        tool_call_id="c1",
        arguments={"command": "touch x"},
        turn_id="t1",
        thread_id="th1",
        agent_type="primary",
        session_id=session_id,
        user_mentioned_roots=list(user_roots or []),
    )
    await orchestrator._execute_in_sandbox(ctx, _selection(SandboxType.BWRAP))
    assert tool.last_kwargs is not None
    return tool.last_kwargs["_user_roots"]


async def _grant_via_file_tool(
    tmp_path: Path, *, choice: str | None, bypass: bool = False, session: str = "sess-A",
) -> Path:
    """让 write_file 对工作区外目录走一次审批卡，返回被授权的目录。"""
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    tool = WriteFileTool(
        workspace=ws,
        allowed_dir=ws,
        shared_roots=[],
        write_resolver=_resolver(choice) if choice else None,
        bypass_approval=bypass,
    )
    result = await tool.execute(
        path=str(outside / "x.txt"), content="hi", _session_key=session,
    )
    assert result.startswith("Successfully wrote"), result
    return outside


def _exec_tool(ws: Path) -> ExecTool:
    """真实 ExecTool：验证注入值确实被 rw bind 与静态护栏消费。"""
    return ExecTool(working_dir=str(ws), shared_roots=[ws], allow_user_dirs=True)


# ── ① always_dir / ④ bypass：会话授权同给 exec ───────────────────────────


class TestSessionGrantReachesExec:
    async def test_always_dir_grant_reaches_exec(self, tmp_path: Path):
        """① 卡片「本目录不再询问」后，同会话 exec 的注入值含该目录，
        且 rw bind 与护栏都把它算作可写根。"""
        outside = await _grant_via_file_tool(tmp_path, choice="always_dir")
        granted = str(outside.resolve())

        roots = await _injected_roots("sess-A")
        assert granted in roots

        ws = tmp_path / "ws"
        exec_tool = _exec_tool(ws)
        assert granted in exec_tool._exec_rw_binds(roots)
        assert granted in exec_tool._guard_write_roots(roots)

    async def test_bypass_grant_reaches_exec(self, tmp_path: Path):
        """④ bypass（approvals.bypass*_write_approval）产生的会话授权同给 exec。"""
        outside = await _grant_via_file_tool(tmp_path, choice=None, bypass=True)
        granted = str(outside.resolve())

        roots = await _injected_roots("sess-A")
        assert granted in roots
        assert granted in _exec_tool(tmp_path / "ws")._exec_rw_binds(roots)

    async def test_write_file_calls_also_see_the_grant(self, tmp_path: Path):
        """注入对 exec 与文件工具一视同仁：write_file 的 _user_roots 同样带上授权。"""
        outside = await _grant_via_file_tool(tmp_path, choice="always_dir")
        roots = await _injected_roots("sess-A", tool_name="write_file")
        assert str(outside.resolve()) in roots


# ── ② once 不泄漏 ────────────────────────────────────────────────────────


class TestOnceNeverReachesExec:
    async def test_once_grant_absent_from_exec(self, tmp_path: Path):
        """②「允许本次」是调用级的：同会话 exec 的注入值里绝不能出现该目录。"""
        outside = await _grant_via_file_tool(tmp_path, choice="once")
        granted = str(outside.resolve())

        roots = await _injected_roots("sess-A")
        assert granted not in roots
        assert roots == []
        assert granted not in _exec_tool(tmp_path / "ws")._exec_rw_binds(roots)
        assert granted not in _exec_tool(tmp_path / "ws")._guard_write_roots(roots)


# ── ③ 跨会话隔离 ─────────────────────────────────────────────────────────


class TestCrossSessionIsolation:
    async def test_other_session_does_not_inherit(self, tmp_path: Path):
        """③ 会话 A 的授权不出现在会话 B 的 exec 注入值里。"""
        outside = await _grant_via_file_tool(tmp_path, choice="always_dir", session="sess-A")
        granted = str(outside.resolve())

        assert granted in await _injected_roots("sess-A")
        assert granted not in await _injected_roots("sess-B")

    async def test_grant_for_other_session_only(self, tmp_path: Path):
        """反向：B 有权时 A 仍为空（两边对称，不是单向串味）。"""
        await _grant_via_file_tool(tmp_path, choice="always_dir", session="sess-B")
        assert await _injected_roots("sess-A") == []


# ── fail-closed：无授权时与 #1013 之前逐字节一致 ─────────────────────────


class TestFailClosedUnchanged:
    async def test_no_grant_injection_unchanged(self):
        """没授权时注入值 = 本回合用户点名目录，一个字符都不多。"""
        roots = await _injected_roots(
            "sess-A", user_roots=["C:/Users/me/Desktop/test_result"],
        )
        assert roots == ["C:/Users/me/Desktop/test_result"]

    async def test_user_roots_come_first_then_sorted_grants(self, tmp_path: Path):
        """顺序确定：用户点名目录保持原序在前，store 授权排序在后。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        b = tmp_path / "b_out"
        a = tmp_path / "a_out"
        b.mkdir()
        a.mkdir()
        for d in (b, a):  # 故意按 b、a 顺序授权
            tool = WriteFileTool(
                workspace=ws, allowed_dir=ws, shared_roots=[],
                write_resolver=_resolver("always_dir"),
            )
            await tool.execute(
                path=str(d / "x.txt"), content="hi", _session_key="sess-A",
            )

        roots = await _injected_roots("sess-A", user_roots=["C:/users/me/dir"])
        assert roots[0] == "C:/users/me/dir"
        assert roots[1:] == sorted([str(a.resolve()), str(b.resolve())])

    async def test_model_supplied_roots_still_dropped(self, tmp_path: Path):
        """安全前提不变：模型塞进 arguments 的 _user_roots 仍被丢弃，
        即使 store 里确有本会话授权。"""
        outside = await _grant_via_file_tool(tmp_path, choice="always_dir")
        granted = str(outside.resolve())

        tool = _RecordingTool("exec")
        orchestrator = ToolOrchestrator(
            permission_engine=MagicMock(),
            sandbox_engine=MagicMock(),
            hook_runtime=MagicMock(),
            tool_registry=_Registry(tool),
            event_emitter=MagicMock(),
        )
        ctx = ToolExecutionContext(
            tool_name="exec",
            tool_call_id="c1",
            arguments={"command": "x", "_user_roots": ["C:/Users/me/Documents"]},
            turn_id="t1",
            thread_id="th1",
            agent_type="primary",
            session_id="sess-A",
        )
        await orchestrator._execute_in_sandbox(ctx, _selection(SandboxType.BWRAP))
        assert tool.last_kwargs is not None
        roots = tool.last_kwargs["_user_roots"]
        assert "C:/Users/me/Documents" not in roots
        assert granted in roots

    async def test_injection_not_mutated_by_exec_consumers(self, tmp_path: Path):
        """护栏/绑定消费注入值不会改动 store 内容（只读语义）。"""
        outside = await _grant_via_file_tool(tmp_path, choice="always_dir")
        granted = str(outside.resolve())
        roots = await _injected_roots("sess-A")
        exec_tool = _exec_tool(tmp_path / "ws")
        exec_tool._exec_rw_binds(roots)
        exec_tool._guard_write_roots(roots)
        assert await _injected_roots("sess-A") == roots
        assert granted in roots


# ── ExecTool 构造契约不变 ────────────────────────────────────────────────


def test_exec_tool_signature_unchanged():
    """ExecTool 不新增任何 grant/授权参数——授权只走 harness 注入通道。"""
    import inspect

    params = set(inspect.signature(ExecTool.__init__).parameters)
    assert not {p for p in params if "grant" in p.lower()}
    assert "_user_roots" not in params  # 只在 execute() 里以 kwargs 形式消费
    assert {"shared_roots", "allow_user_dirs", "working_dir"} <= params
