"""ExecTool write boundary (#984 PR1) — layer 1 plumbing tests.

Covers the exec half of the sandbox write boundary:
  * the per-call rw bind SET (workspace ∪ static shared roots ∪ gated
    ``_user_roots``; #864 card grants are deliberately excluded),
  * the four ``_execute_*`` signatures and all eight ``**splat`` call sites,
  * a missing bind source failing the command WITHOUT falling back to host
    execution,
  * ``_execute_restricted``'s explicit ``_execute_direct`` call staying valid
    without the new kwarg (plan v6 §3),
  * the per-call ``working_dir`` argument NEVER becoming a bind source (R3
    contract hardening).
"""

from __future__ import annotations

import inspect
import os
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


# ── #1007 review: a UNC static root is not a bind source ─────────────────


class TestUncStaticRoot:
    """``\\\\wsl$\\…`` exists on the host but cannot be bound.

    ``_add(strict=False)`` keeps a static root whose ``os.path.exists()``
    succeeds — for a UNC entry that is true, and the resulting hard
    ``--bind`` then died inside ``_host_path_to_sandbox`` (which raises on
    UNC), surfacing as the generic 「沙箱执行失败」 for EVERY command.  The
    root extractors never produce UNC, so such entries are dropped instead.
    """

    _UNC = r"\\wsl$\Ubuntu\home\out"

    def _tool_with_unc_shared_root(self, ws: Path, monkeypatch) -> ExecTool:
        """Build the tool with ``os.path.exists`` reporting the UNC as reachable."""
        real_exists = os.path.exists

        def _exists(p: object) -> bool:
            if str(p).replace("\\", "/").startswith("//"):
                return True  # a WSL share IS reachable from the Windows host
            return real_exists(p)

        monkeypatch.setattr(os.path, "exists", _exists)
        return ExecTool(
            timeout=5, working_dir=str(ws), shared_roots=[self._UNC],
        )

    def test_unc_static_root_not_bound(self, tmp_path: Path, monkeypatch) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        tool = self._tool_with_unc_shared_root(ws, monkeypatch)
        binds = tool._exec_rw_binds(None)
        assert str(ws) in binds  # the reachable roots are untouched
        assert not any(
            str(b).replace("\\", "/").startswith(("//", "\\\\")) for b in binds
        )

    def test_unc_not_reported_as_missing_source(self) -> None:
        """It is not a bind source at all — not a 'missing' one either.

        The pre-flight report turned it into a hard refusal for every
        command (and a missing bind never falls back to the host).
        """
        assert ExecTool._missing_bind_sources([self._UNC]) == []


# ── #1007 review: the BWRAP→host fallback keeps the per-call grant ───────


class TestHostFallbackRoots:
    """``_execute_with_sandbox_selection`` must hand the per-call roots to
    the host-fallback guard.

    When BWRAP is selected but no sandbox is live, execution falls back to
    the host and the guard is re-run with HOST path semantics.  That re-check
    was called without ``user_roots``, so ``_guard_write_roots`` saw ``None``
    and refused a write into the very directory the user had just authorized
    (the legacy no-selection fallback already passed them).
    """

    def _tool(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        tool = ExecTool(timeout=5, working_dir=str(ws), allow_user_dirs=True)
        mgr = MagicMock()
        mgr.get_or_create = AsyncMock(return_value=None)  # sandbox never starts
        mgr.active_sandbox = None
        tool._sandbox_manager = mgr
        return tool, out

    @pytest.mark.asyncio
    async def test_fallback_guard_receives_user_roots(
        self, monkeypatch, tmp_path,
    ) -> None:
        tool, out = self._tool(tmp_path)
        seen: dict = {}

        def _fake_guard(command: str, cwd: str, user_roots=None):
            seen["user_roots"] = user_roots
            return None

        async def _fake_direct(command, cwd, **kwargs):
            return MagicMock(exit_code=0, output="host", duration_ms=0,
                             cancelled=False, timed_out=False)

        monkeypatch.setattr(tool, "_guard_host_fallback", _fake_guard)
        monkeypatch.setattr(tool, "_execute_direct", _fake_direct)
        monkeypatch.setattr(tool, "_snapshot_workspace", lambda cwd: {})

        await tool.execute(
            "echo hi",
            _sandbox=_selection(SandboxType.BWRAP),
            _user_roots=[str(out)],
        )
        assert seen["user_roots"] == [str(out)]

    @pytest.mark.asyncio
    async def test_authorized_write_not_refused_on_fallback(
        self, monkeypatch, tmp_path,
    ) -> None:
        """Behavioural half: the real guard must let the granted write run."""
        tool, out = self._tool(tmp_path)
        target = (out / "result.txt").as_posix()
        ran: dict = {}

        async def _fake_direct(command, cwd, **kwargs):
            ran["yes"] = True
            return MagicMock(exit_code=0, output="ok", duration_ms=0,
                             cancelled=False, timed_out=False)

        monkeypatch.setattr(tool, "_execute_direct", _fake_direct)
        monkeypatch.setattr(tool, "_snapshot_workspace", lambda cwd: {})

        result = await tool.execute(
            f'echo written > "{target}"',
            _sandbox=_selection(SandboxType.BWRAP),
            _user_roots=[str(out)],
        )
        assert ran.get("yes") is True, f"guard refused the granted write: {result}"

    @pytest.mark.asyncio
    async def test_ungranted_write_still_refused_on_fallback(
        self, monkeypatch, tmp_path,
    ) -> None:
        """The opposite direction: no grant → the host re-check still fires.

        ``/home/miqi/...`` is a legitimate in-sandbox path (the pre-flight
        guard, running with SANDBOX semantics, allows it) that is a real
        out-of-scope host path once the sandbox is gone — exactly what the
        re-check exists for.  Emptying ``user_roots`` must not turn the
        fallback guard off.
        """
        tool, _out = self._tool(tmp_path)
        ran: dict = {}

        async def _fake_direct(command, cwd, **kwargs):
            ran["yes"] = True
            return MagicMock(exit_code=0, output="ok", duration_ms=0,
                             cancelled=False, timed_out=False)

        monkeypatch.setattr(tool, "_execute_direct", _fake_direct)
        monkeypatch.setattr(tool, "_snapshot_workspace", lambda cwd: {})

        result = await tool.execute(
            'echo written > "/home/miqi/out/x.txt"',
            _sandbox=_selection(SandboxType.BWRAP),
        )
        assert ran.get("yes") is None, "ungranted host write must not run"
        assert "拦截" in result


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


# ── #984 R3: the per-call ``working_dir`` is not a bind source ────────────


class TestWorkingDirNotABindSource:
    """``execute(working_dir=...)`` is model-controlled and must stay cwd-only.

    It selects the process cwd (and the workspace-diff root); the rw bind set
    is fixed at construction (``self.working_dir`` + ``self._shared_roots``)
    plus the harness-injected ``_user_roots``.  If the per-call value leaked
    into ``_exec_rw_binds``, a model could name ANY existing host directory as
    ``working_dir`` and have it re-opened writable inside the sandbox with no
    grant — the exact boundary #984 exists to enforce.  The contract is stated
    in ``ExecTool._exec_rw_binds``'s docstring; this test locks it.
    """

    @pytest.mark.asyncio
    async def test_per_call_working_dir_is_not_a_bind_source(
        self, monkeypatch, tmp_path,
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        shared = tmp_path / "static_shared"
        shared.mkdir()
        # An EXISTING directory outside the workspace — the shape a
        # model-supplied ``working_dir`` takes when it is honoured as a bind
        # source (a missing one would be skipped by the static-root check).
        secret = tmp_path / "outside_secret"
        secret.mkdir()
        (secret / "credentials.txt").write_text("x", encoding="utf-8")

        tool = ExecTool(timeout=5, working_dir=str(ws), shared_roots=[shared])
        # Never walk the model-named cwd on disk during this test.
        monkeypatch.setattr(tool, "_snapshot_workspace", lambda cwd: {})

        before = tool._exec_rw_binds([])
        assert before == [str(ws), str(shared)]

        sandbox = _mock_sandbox()
        mgr = MagicMock()
        mgr.get_or_create = AsyncMock(return_value=sandbox)
        mgr.active_sandbox = sandbox
        tool._sandbox_manager = mgr

        # Exactly the way the model reaches the tool: ``working_dir`` is a
        # declared schema parameter, ``_sandbox``/``_session_key`` are
        # harness-injected.
        await tool.execute(
            command="echo t",
            working_dir=str(secret),
            _sandbox=_selection(SandboxType.BWRAP),
            _session_key="k",
        )

        # (1) what the sandbox was actually asked to re-open writable
        binds = sandbox.run_command_streaming.await_args.kwargs["extra_rw_binds"]
        assert binds == before
        assert str(secret) not in binds
        # (2) the instance's bind set is unchanged after the call
        after = tool._exec_rw_binds([])
        assert after == before
        assert str(secret) not in after

    @pytest.mark.asyncio
    async def test_per_call_working_dir_still_sets_cwd(
        self, monkeypatch, tmp_path,
    ) -> None:
        """The hardening must not break the parameter it constrains."""
        ws = tmp_path / "ws"
        ws.mkdir()
        other = tmp_path / "other_cwd"
        other.mkdir()

        tool = ExecTool(timeout=5, working_dir=str(ws))
        seen: dict = {}

        async def _fake_direct(command, cwd, **kwargs):
            seen["cwd"] = cwd
            return MagicMock(exit_code=0, output="ok", duration_ms=0,
                             cancelled=False, timed_out=False)

        monkeypatch.setattr(tool, "_execute_direct", _fake_direct)
        monkeypatch.setattr(tool, "_snapshot_workspace", lambda cwd: {})

        await tool.execute("echo t", working_dir=str(other))
        assert seen["cwd"] == str(other)


# ── #1007 review: the cross-session guard reaches the sandbox ────────────


class TestCrossSessionGuardWiring:
    """``ExecTool`` must hand bwrap the workspace root + session files dir.

    The mount ordering itself is asserted in
    ``tests/sandbox/test_sandbox_write_boundary_984.py``; this locks the
    shell.py half — both kwargs default to ``None`` in bwrap (old args), so a
    dropped call site would silently leave ``<ws>/sessions/**`` writable.
    """

    @staticmethod
    def _tool(tmp_path: Path):
        ws = tmp_path / "ws"
        files = ws / "sessions" / "desktop_1" / "files"
        files.mkdir(parents=True)
        tool = ExecTool(
            timeout=5, working_dir=str(files), shared_roots=[ws],
            workspace_root=str(ws), session_files_dir=str(files),
        )
        sandbox = _mock_sandbox()
        mgr = MagicMock()
        mgr.get_or_create = AsyncMock(return_value=sandbox)
        mgr.active_sandbox = sandbox
        tool._sandbox_manager = mgr
        return tool, sandbox, ws, files

    def test_defaults_disable_the_guard(self) -> None:
        tool = ExecTool(timeout=5, working_dir=str(Path.cwd()))
        assert tool._workspace_root is None
        assert tool._session_files_dir is None

    @pytest.mark.asyncio
    async def test_execute_forwards_guard_paths(self, tmp_path) -> None:
        tool, sandbox, ws, files = self._tool(tmp_path)
        await tool.execute("echo hi", _sandbox=_selection(SandboxType.BWRAP))
        kwargs = sandbox.run_command_streaming.await_args.kwargs
        assert kwargs["workspace_root"] == str(ws)
        assert kwargs["session_files_dir"] == str(files)
        # The workspace root is still a bind source (the guard narrows it,
        # it does not replace it) — and so is the session's own files dir.
        assert str(ws) in kwargs["extra_rw_binds"]
        assert str(files) in kwargs["extra_rw_binds"]
