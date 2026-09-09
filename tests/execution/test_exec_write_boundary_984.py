"""ExecTool write boundary (#984 PR1) — layer 1 plumbing tests.

Covers the exec half of the sandbox write boundary:
  * the per-call rw bind SET (workspace ∪ static shared roots ∪ gated
    ``_user_roots``; #864 card grants are deliberately excluded),
  * the four ``_execute_*`` signatures and all eight ``**splat`` call sites,
  * a missing bind source failing the command WITHOUT falling back to host
    execution,
  * ``_execute_restricted``'s explicit ``_execute_direct`` call staying valid
    without the new kwarg (plan v6 §3).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.agent.tools.shell import ExecTool
from miqi.execution.orchestrator import ToolExecutionContext, ToolOrchestrator
from miqi.execution.sandbox_policy import SandboxSelection, SandboxType
from miqi.protocol.permissions import (
    FileSystemAccessMode,
    FileSystemSandboxPolicy,
    NetworkSandboxPolicy,
)

# ── helpers ──────────────────────────────────────────────────────────────


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


def _mock_sandbox(*, is_running: bool = True) -> MagicMock:
    sb = MagicMock()
    sb.is_running = is_running
    sb.get_sandbox_env = MagicMock(return_value={})
    # Raise after recording the call so _execute_in_sandbox returns its
    # error result without needing a real stream handle.
    sb.run_command_streaming = AsyncMock(side_effect=RuntimeError("boom"))
    return sb


# ── bind set composition ─────────────────────────────────────────────────


class TestExecRwBinds:
    def test_workspace_and_shared_roots_included(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        shared = tmp_path / "extra"
        shared.mkdir()
        tool = ExecTool(working_dir=str(ws), shared_roots=[shared])
        assert tool._exec_rw_binds(None) == [str(ws), str(shared)]

    def test_user_roots_added_when_enabled(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        tool = ExecTool(working_dir=str(ws), allow_user_dirs=True)
        assert tool._exec_rw_binds([str(out)]) == [str(ws), str(out)]

    def test_user_roots_ignored_when_disabled(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        tool = ExecTool(working_dir=str(ws), allow_user_dirs=False)
        assert tool._exec_rw_binds([str(out)]) == [str(ws)]

    def test_missing_static_root_skipped(self, tmp_path: Path) -> None:
        """A stale tools.extra_roots entry must not break every command."""
        ws = tmp_path / "ws"
        ws.mkdir()
        tool = ExecTool(
            working_dir=str(ws), shared_roots=[tmp_path / "gone"],
        )
        assert tool._exec_rw_binds(None) == [str(ws)]

    def test_missing_user_root_kept_for_loud_failure(self, tmp_path: Path) -> None:
        """A granted root is never silently dropped — it fails with guidance."""
        ws = tmp_path / "ws"
        ws.mkdir()
        gone = tmp_path / "not_yet"
        tool = ExecTool(working_dir=str(ws), allow_user_dirs=True)
        assert str(gone) in tool._exec_rw_binds([str(gone)])

    def test_dedupe(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        tool = ExecTool(working_dir=str(ws), shared_roots=[ws])
        assert tool._exec_rw_binds([str(ws)]) == [str(ws)]

    def test_no_working_dir_no_shared_roots(self) -> None:
        assert ExecTool()._exec_rw_binds(None) == []

    def test_invalid_entries_ignored(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        tool = ExecTool(working_dir=str(ws))
        assert tool._exec_rw_binds([None, 123, b"x"]) == [str(ws)]


# ── signatures + splat sites ─────────────────────────────────────────────


class TestSignatures:
    @pytest.mark.parametrize(
        "method",
        [
            "_execute_in_sandbox",
            "_execute_with_sandbox_selection",
            "_execute_restricted",
            "_execute_direct",
        ],
    )
    def test_extra_rw_binds_accepted(self, method: str) -> None:
        sig = inspect.signature(getattr(ExecTool, method))
        assert "extra_rw_binds" in sig.parameters
        assert sig.parameters["extra_rw_binds"].default is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kind,expected",
        [
            (SandboxType.NONE, "_execute_direct"),
            (SandboxType.BWRAP, "_execute_in_sandbox"),
            (SandboxType.RESTRICTED, "_execute_restricted"),
            (SandboxType.LANDLOCK, None),
        ],
    )
    async def test_selection_splat_sites(self, kind, expected, monkeypatch) -> None:
        """``**common`` reaches every sub-executor (shell.py:1176/1188/1206/1225)."""
        tool = ExecTool(timeout=5, working_dir=str(Path.cwd()))
        seen: dict[str, dict] = {}

        def _capture(name):
            async def _fn(*args, **kwargs):
                seen[name] = kwargs
                return MagicMock(exit_code=0, output="", duration_ms=0, cancelled=False,
                                 timed_out=False)
            return _fn

        for name in ("_execute_direct", "_execute_in_sandbox", "_execute_restricted"):
            monkeypatch.setattr(tool, name, _capture(name))

        sandbox = _mock_sandbox()
        mgr = MagicMock()
        mgr.get_or_create = AsyncMock(return_value=sandbox)
        mgr.active_sandbox = sandbox
        tool._sandbox_manager = mgr

        await tool._execute_with_sandbox_selection(
            _selection(kind), "echo hi", str(Path.cwd()),
            session_key="k",
            extra_rw_binds=["/tmp/out"],
        )
        if expected is None:
            assert seen == {}  # LANDLOCK fails closed, no sub-executor
        else:
            assert seen[expected]["extra_rw_binds"] == ["/tmp/out"]

    @pytest.mark.asyncio
    async def test_execute_splat_sites(self, monkeypatch) -> None:
        """``**exec_kwargs`` reaches the orchestrator/legacy/host branches."""
        tool = ExecTool(timeout=5, working_dir=str(Path.cwd()))
        seen: dict[str, dict] = {}

        def _capture(name):
            async def _fn(*args, **kwargs):
                seen[name] = kwargs
                return MagicMock(exit_code=0, output="", duration_ms=0, cancelled=False,
                                 timed_out=False)
            return _fn

        for name in ("_execute_with_sandbox_selection", "_execute_in_sandbox",
                     "_execute_direct"):
            monkeypatch.setattr(tool, name, _capture(name))
        monkeypatch.setattr(tool, "_snapshot_workspace", lambda cwd: {})

        # 1) orchestrator-injected selection — workspace root is always bound
        await tool.execute("echo hi", _sandbox=_selection(SandboxType.NONE))
        assert seen["_execute_with_sandbox_selection"]["extra_rw_binds"] == [
            str(Path.cwd())
        ]

        # 2) legacy manager path with a running sandbox
        tool._sandbox_manager = MagicMock()
        tool._sandbox_manager.get_or_create = AsyncMock(return_value=_mock_sandbox())
        tool._sandbox_manager.active_sandbox = _mock_sandbox()
        await tool.execute("echo hi", _session_key="k")
        assert "extra_rw_binds" in seen["_execute_in_sandbox"]

        # 3) legacy manager path with no sandbox → direct
        tool._sandbox_manager.get_or_create = AsyncMock(return_value=None)
        await tool.execute("echo hi", _session_key="k")
        assert "extra_rw_binds" in seen["_execute_direct"]

        # 4) no manager at all → direct
        tool._sandbox_manager = None
        await tool.execute("echo hi")
        assert "extra_rw_binds" in seen["_execute_direct"]

    @pytest.mark.asyncio
    async def test_restricted_explicit_direct_call_without_bind(self, monkeypatch) -> None:
        """_execute_restricted passes no bind to _execute_direct (v6 §3)."""
        ws = Path.cwd()
        tool = ExecTool(timeout=5, working_dir=str(ws))
        captured: dict = {}

        async def _fake_direct(command, cwd, **kwargs):
            captured.update(kwargs)
            return MagicMock(exit_code=0, output="ok", duration_ms=0, cancelled=False,
                             timed_out=False)

        monkeypatch.setattr(tool, "_execute_direct", _fake_direct)
        sel = _selection(SandboxType.RESTRICTED)
        result = await tool._execute_restricted(
            "echo hi", str(ws), sandbox_selection=sel, extra_rw_binds=["/tmp/out"],
        )
        assert result.exit_code == 0
        assert "extra_rw_binds" not in captured


# ── failure must not downgrade to host execution ─────────────────────────


class TestNoHostFallback:
    @pytest.mark.asyncio
    async def test_missing_bind_fails_with_guidance(self, monkeypatch, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        missing = tmp_path / "not_yet_created"
        tool = ExecTool(timeout=5, working_dir=str(ws), allow_user_dirs=True)
        sandbox = _mock_sandbox()

        direct_called = False

        async def _fake_direct(*args, **kwargs):
            nonlocal direct_called
            direct_called = True
            return MagicMock(exit_code=0, output="host", duration_ms=0)

        monkeypatch.setattr(tool, "_execute_direct", _fake_direct)

        result = await tool._execute_in_sandbox(
            sandbox, "echo hi", str(ws),
            extra_rw_binds=[str(missing)],
        )
        assert result.exit_code != 0
        assert str(missing) in result.output
        assert "文件工具" in result.output  # guidance text
        sandbox.run_command_streaming.assert_not_awaited()
        assert direct_called is False, "must not fall back to host execution"

    @pytest.mark.asyncio
    async def test_execute_reports_missing_user_root(self, monkeypatch, tmp_path) -> None:
        """End-to-end through execute(): error + zero host fallback."""
        ws = tmp_path / "ws"
        ws.mkdir()
        missing = tmp_path / "nope"
        tool = ExecTool(timeout=5, working_dir=str(ws), allow_user_dirs=True)
        sandbox = _mock_sandbox()
        mgr = MagicMock()
        mgr.get_or_create = AsyncMock(return_value=sandbox)
        mgr.active_sandbox = sandbox
        tool._sandbox_manager = mgr

        direct_called = False

        async def _fake_direct(*args, **kwargs):
            nonlocal direct_called
            direct_called = True
            return MagicMock(exit_code=0, output="host", duration_ms=0)

        monkeypatch.setattr(tool, "_execute_direct", _fake_direct)

        out = await tool.execute(
            "echo hi",
            _sandbox=_selection(SandboxType.BWRAP),
            _user_roots=[str(missing)],
        )
        assert "命令未执行" in out
        assert direct_called is False

    @pytest.mark.asyncio
    async def test_existing_user_root_reaches_sandbox(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        tool = ExecTool(timeout=5, working_dir=str(ws), allow_user_dirs=True)
        sandbox = _mock_sandbox()
        mgr = MagicMock()
        mgr.get_or_create = AsyncMock(return_value=sandbox)
        mgr.active_sandbox = sandbox
        tool._sandbox_manager = mgr

        await tool.execute(
            "echo hi",
            _sandbox=_selection(SandboxType.BWRAP),
            _user_roots=[str(out_dir)],
        )
        binds = sandbox.run_command_streaming.await_args.kwargs["extra_rw_binds"]
        assert str(out_dir) in binds
        assert str(ws) in binds

    @pytest.mark.asyncio
    async def test_user_roots_gated_by_config(self, tmp_path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        tool = ExecTool(timeout=5, working_dir=str(ws), allow_user_dirs=False)
        sandbox = _mock_sandbox()
        mgr = MagicMock()
        mgr.get_or_create = AsyncMock(return_value=sandbox)
        mgr.active_sandbox = sandbox
        tool._sandbox_manager = mgr

        await tool.execute(
            "echo hi",
            _sandbox=_selection(SandboxType.BWRAP),
            _user_roots=[str(out_dir)],
        )
        binds = sandbox.run_command_streaming.await_args.kwargs["extra_rw_binds"]
        assert str(out_dir) not in binds


# ── #984 R2: the harness owns ``_user_roots`` ────────────────────────────


class _RecordingTool:
    """Records the kwargs ToolOrchestrator._execute_in_sandbox injects."""

    name = "write_file"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}}

    def __init__(self) -> None:
        self.last_kwargs: dict | None = None

    async def execute(self, **kwargs) -> str:
        self.last_kwargs = dict(kwargs)
        return "ok"


class _Registry:
    def __init__(self, tool: _RecordingTool) -> None:
        self._tool = tool

    def get(self, name: str) -> _RecordingTool:
        return self._tool


def _orchestrator(tool: _RecordingTool) -> ToolOrchestrator:
    return ToolOrchestrator(
        permission_engine=MagicMock(),
        sandbox_engine=MagicMock(),
        hook_runtime=MagicMock(),
        tool_registry=_Registry(tool),
        event_emitter=MagicMock(),
    )


def _ctx(arguments: dict, roots: list[str], tool_name: str = "write_file"):
    return ToolExecutionContext(
        tool_name=tool_name,
        tool_call_id="c1",
        arguments=arguments,
        turn_id="t1",
        thread_id="th1",
        agent_type="primary",
        user_mentioned_roots=roots,
    )


class TestUserRootsOwnership:
    """``_user_roots`` is injected by the harness, never by the model.

    It is in no tool schema, and object validation only walks declared keys
    (base.py:112-114), so a model-authored ``_user_roots`` would otherwise
    ride through ``ctx.arguments`` and re-open the write boundary the turn's
    sensed roots are meant to gate.
    """

    @pytest.mark.asyncio
    async def test_model_supplied_roots_dropped_when_turn_has_none(self) -> None:
        tool = _RecordingTool()
        ctx = _ctx(
            {
                "path": "C:/Users/me/Documents/report.md",
                "_user_roots": ["C:/Users/me/Documents"],
            },
            roots=[],
        )
        await _orchestrator(tool)._execute_in_sandbox(
            ctx, _selection(SandboxType.BWRAP),
        )
        assert tool.last_kwargs is not None
        assert tool.last_kwargs["_user_roots"] == []

    @pytest.mark.asyncio
    async def test_harness_roots_win_over_model_supplied(self) -> None:
        tool = _RecordingTool()
        ctx = _ctx(
            {
                "path": "C:/Users/me/Desktop/out/report.md",
                "_user_roots": ["C:/Users/me/Documents"],
            },
            roots=["C:/Users/me/Desktop/out"],
        )
        await _orchestrator(tool)._execute_in_sandbox(
            ctx, _selection(SandboxType.BWRAP),
        )
        assert tool.last_kwargs is not None
        assert tool.last_kwargs["_user_roots"] == ["C:/Users/me/Desktop/out"]

    @pytest.mark.asyncio
    async def test_model_supplied_roots_dropped_for_non_file_tools(self) -> None:
        """The strip is unconditional — no tool name keeps a model-authored root."""
        tool = _RecordingTool()
        ctx = _ctx(
            {"text": "hi", "_user_roots": ["C:/Users/me/Documents"]},
            roots=[],
            tool_name="message",
        )
        await _orchestrator(tool)._execute_in_sandbox(
            ctx, _selection(SandboxType.BWRAP),
        )
        assert tool.last_kwargs is not None
        assert "_user_roots" not in tool.last_kwargs
