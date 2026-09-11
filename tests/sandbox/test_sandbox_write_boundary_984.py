"""Sandbox write boundary (#984 PR1) — layer 1 + layer 2 unit tests.

Layer 1: per-call ``extra_rw_binds`` on ``run_command`` /
``run_command_streaming`` → ``_build_bwrap_args``, hard ``--bind`` only.
Layer 2: ``/mnt`` is ``--ro-bind-try`` (read-only) since #984, with the
per-call rw binds applied AFTER it so authorized subtrees stay writable.

These are pure argument-construction tests — no bwrap/WSL needed.  The
kernel-level behaviour (drvfs read-only) is verified in CI / on a WSL host;
see the PR body for what could not be run locally.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.sandbox.bwrap import (
    BwrapSandbox,
    BwrapSandboxError,
    _host_path_to_sandbox,
)

# ── helpers ──────────────────────────────────────────────────────────────


def _make_sandbox(*, use_wsl: bool = True, workspace: str = "/tmp/ws") -> BwrapSandbox:
    sb = BwrapSandbox(
        session_key="desktop:984",
        workspace=Path(workspace),
        sandbox_base_dir=Path("/tmp/miqi-sandboxes"),
        wsl_distro="Ubuntu",
        wsl_base_dir="/tmp/miqi-sandboxes",
    )
    sb._bwrap_path = "/usr/bin/bwrap"
    sb._use_wsl = use_wsl
    sb._linux_workspace = None
    return sb


def _bind_pairs(args: list[str]) -> list[tuple[str, str]]:
    return [
        (args[i + 1], args[i + 2])
        for i, a in enumerate(args[:-1])
        if a == "--bind"
    ]


def _index_of_triplet(args: list[str], flag: str, src: str) -> int:
    for i, a in enumerate(args[:-1]):
        if a == flag and args[i + 1] == src:
            return i
    raise AssertionError(f"{flag} {src} not in args")


def _indexes_of_triplet(args: list[str], flag: str, src: str) -> list[int]:
    """Every position of ``flag src src`` — a path bound twice needs both."""
    return [
        i for i, a in enumerate(args[:-1])
        if a == flag and args[i + 1] == src
    ]


# ── _host_path_to_sandbox ────────────────────────────────────────────────


class TestHostPathToSandbox:
    def test_windows_drive_path(self) -> None:
        assert _host_path_to_sandbox(r"C:\Users\x\Desktop\out") == "/mnt/c/Users/x/Desktop/out"

    def test_drive_letter_lowercased(self) -> None:
        assert _host_path_to_sandbox("D:/data/mof") == "/mnt/d/data/mof"

    def test_forward_slashes_accepted(self) -> None:
        assert _host_path_to_sandbox("C:/Users/x/out") == "/mnt/c/Users/x/out"

    def test_posix_path_unchanged(self) -> None:
        assert _host_path_to_sandbox("/home/miqi/out") == "/home/miqi/out"

    def test_unc_raises(self) -> None:
        with pytest.raises(BwrapSandboxError):
            _host_path_to_sandbox(r"\\wsl$\Ubuntu\home\x")

    def test_relative_raises(self) -> None:
        with pytest.raises(BwrapSandboxError):
            _host_path_to_sandbox("relative/dir")

    # #1007 review: the drive test used to be ``p[1] == ":"`` alone, so a
    # drive-RELATIVE spelling was silently mapped to a path nobody named
    # (``C:relative`` → ``/mnt/crelative``, ``C:`` → ``/mnt/c``) instead of
    # failing loudly as the docstring promises.  Only ``C:/…`` maps.

    def test_drive_relative_raises(self) -> None:
        with pytest.raises(BwrapSandboxError):
            _host_path_to_sandbox("C:relative")

    def test_bare_drive_letter_raises(self) -> None:
        with pytest.raises(BwrapSandboxError):
            _host_path_to_sandbox("C:")

    def test_drive_relative_with_separator_raises(self) -> None:
        with pytest.raises(BwrapSandboxError):
            _host_path_to_sandbox(r"C:foo\bar")

    def test_drive_absolute_forms_unchanged(self) -> None:
        """The tightened condition must not change any absolute spelling."""
        assert _host_path_to_sandbox("C:/") == "/mnt/c/"
        assert _host_path_to_sandbox(r"C:\x") == "/mnt/c/x"
        assert _host_path_to_sandbox("d:/data/mof") == "/mnt/d/data/mof"


# ── layer 2: /mnt is read-only ───────────────────────────────────────────


class TestMntReadOnly:
    def test_wsl_mount_is_ro_bind_try(self) -> None:
        sb = _make_sandbox(use_wsl=True)
        args = sb._build_bwrap_args("echo hi")
        assert _index_of_triplet(args, "--ro-bind-try", "/mnt") >= 0

    def test_no_writable_mnt_bind(self) -> None:
        sb = _make_sandbox(use_wsl=True)
        args = sb._build_bwrap_args("echo hi")
        assert ("/mnt", "/mnt") not in _bind_pairs(args)

    def test_no_mnt_mount_outside_wsl(self) -> None:
        sb = _make_sandbox(use_wsl=False)
        args = sb._build_bwrap_args("echo hi")
        assert "/mnt" not in args


# ── layer 1: per-call rw binds ───────────────────────────────────────────


class TestPerCallRwBinds:
    def test_per_call_bind_is_hard_bind_not_try(self) -> None:
        sb = _make_sandbox()
        args = sb._build_bwrap_args(
            "echo hi", extra_rw_binds=[r"C:\Users\x\Desktop\out"],
        )
        assert ("/mnt/c/Users/x/Desktop/out", "/mnt/c/Users/x/Desktop/out") in _bind_pairs(args)
        # Hard --bind only: --bind-try would silently drop a missing source.
        assert "--bind-try" not in args

    def test_rw_bind_comes_after_ro_mnt(self) -> None:
        sb = _make_sandbox()
        args = sb._build_bwrap_args(
            "echo hi", extra_rw_binds=[r"C:\Users\x\Desktop\out"],
        )
        ro_idx = _index_of_triplet(args, "--ro-bind-try", "/mnt")
        rw_idx = _index_of_triplet(args, "--bind", "/mnt/c/Users/x/Desktop/out")
        assert ro_idx < rw_idx, "rw bind must override the read-only /mnt mount"

    def test_workspace_and_static_roots_bindable_under_layer2(self) -> None:
        """Workspace on /mnt/c + a configured extra root stay writable."""
        sb = _make_sandbox(workspace=r"C:\Users\x\.miqi\workspace")
        args = sb._build_bwrap_args(
            "echo hi",
            extra_rw_binds=[
                r"C:\Users\x\.miqi\workspace",
                r"C:\Users\x\Desktop\shared_out",
            ],
        )
        pairs = _bind_pairs(args)
        assert ("/mnt/c/Users/x/.miqi/workspace",) * 2 in pairs
        assert ("/mnt/c/Users/x/Desktop/shared_out",) * 2 in pairs

    def test_multiple_binds_keep_order(self) -> None:
        sb = _make_sandbox()
        args = sb._build_bwrap_args(
            "echo hi", extra_rw_binds=["/a/one", "/b/two"],
        )
        pairs = _bind_pairs(args)
        assert pairs.index(("/a/one", "/a/one")) < pairs.index(("/b/two", "/b/two"))

    def test_no_per_call_binds_keeps_old_args(self) -> None:
        sb = _make_sandbox()
        assert sb._build_bwrap_args("echo hi") == sb._build_bwrap_args(
            "echo hi", extra_rw_binds=None,
        )

    def test_unmappable_bind_raises_not_silently_dropped(self) -> None:
        sb = _make_sandbox()
        with pytest.raises(BwrapSandboxError):
            sb._build_bwrap_args("echo hi", extra_rw_binds=["relative/path"])

    def test_static_extra_rw_binds_still_applied(self) -> None:
        """Constructor-level binds (sandbox creation) keep working."""
        sb = _make_sandbox()
        sb.extra_rw_binds = ["/srv/data"]
        args = sb._build_bwrap_args("echo hi")
        assert ("/srv/data", "/srv/data") in _bind_pairs(args)


# ── #1007 review: <ws>/sessions stays read-only ──────────────────────────


class TestCrossSessionGuard:
    """Re-opening the workspace root must not re-open other sessions.

    ``_exec_rw_binds`` always includes the workspace root, and
    ``<workspace>/sessions/**`` — every other session's files dir — lives
    under it, so layer 1 quietly undid layer 2's read-only ``/mnt`` for the
    cross-session subtree.  bwrap mounts in order, so the fix appends a
    read-only ``<ws>/sessions`` AFTER the rw workspace bind and then re-opens
    THIS session's own files dir after that.

    Pure argument-construction assertions: the mount ORDER is the whole
    mechanism, and it is observable without running bwrap (which needs
    WSL + bubblewrap and cannot run on the Windows dev host).
    """

    _WS = r"C:\Users\x\.miqi\workspace"
    _FILES = r"C:\Users\x\.miqi\workspace\sessions\desktop_1\files"
    _OUT = r"C:\Users\x\Desktop\out"
    _WS_SB = "/mnt/c/Users/x/.miqi/workspace"
    _SESSIONS_SB = "/mnt/c/Users/x/.miqi/workspace/sessions"
    _FILES_SB = "/mnt/c/Users/x/.miqi/workspace/sessions/desktop_1/files"

    def _args(self, extra_rw_binds: list[str], **kwargs) -> list[str]:
        sb = _make_sandbox(workspace=self._WS)
        return sb._build_bwrap_args(
            "echo hi",
            extra_rw_binds=extra_rw_binds,
            workspace_root=self._WS,
            session_files_dir=self._FILES,
            **kwargs,
        )

    def test_sessions_rebound_read_only_after_workspace(self) -> None:
        """(a) + (b): the ro sessions bind exists and follows the rw ws bind."""
        args = self._args([self._FILES, self._WS, self._OUT])
        ws_idx = _index_of_triplet(args, "--bind", self._WS_SB)
        ro_idx = _index_of_triplet(args, "--ro-bind-try", self._SESSIONS_SB)
        assert ws_idx < ro_idx, "a later ro bind must override the rw workspace"
        # The ro bind must not be a hard --ro-bind: the dir may legitimately
        # not exist yet, and this mount only ever narrows.
        assert ("--ro-bind", self._SESSIONS_SB) not in [
            (args[i], args[i + 1]) for i in range(len(args) - 1)
        ]

    def test_own_session_dir_still_writable_after_the_guard(self) -> None:
        """(c): this session's files dir is re-bound rw AFTER the ro bind."""
        args = self._args([self._FILES, self._WS, self._OUT])
        ro_idx = _index_of_triplet(args, "--ro-bind-try", self._SESSIONS_SB)
        own_idx = _indexes_of_triplet(args, "--bind", self._FILES_SB)
        assert len(own_idx) == 2, (
            "expected the session dir in the rw set AND re-opened after the "
            f"guard, got {own_idx}"
        )
        assert own_idx[-1] > ro_idx, "the session's own files dir must stay rw"

    def test_other_roots_untouched(self) -> None:
        """(4): memory/skills/extra roots keep their plain rw bind."""
        args = self._args([self._FILES, self._WS, self._OUT])
        assert _indexes_of_triplet(args, "--bind", "/mnt/c/Users/x/Desktop/out") == [
            _index_of_triplet(args, "--bind", "/mnt/c/Users/x/Desktop/out"),
        ]

    def test_guard_absent_when_workspace_root_not_writable(self) -> None:
        """(d): no rw bind of the ws root → the old arg list, unchanged."""
        args = self._args([self._FILES])
        assert self._SESSIONS_SB not in args

    def test_guard_absent_without_a_workspace_root(self) -> None:
        sb = _make_sandbox(workspace=self._WS)
        args = sb._build_bwrap_args(
            "echo hi", extra_rw_binds=[self._FILES, self._WS],
        )
        assert args == sb._build_bwrap_args(
            "echo hi", extra_rw_binds=[self._FILES, self._WS],
            workspace_root=None, session_files_dir=None,
        )
        assert self._SESSIONS_SB not in args

    def test_guard_absent_without_session_files_dir(self) -> None:
        """Custom workspace: no per-session layout, so none of it is touched.

        ``<project>/sessions`` there is the project's own directory (only
        snapshots live in miqi's subdirs), and making it read-only inside
        exec would break legitimate writes.
        """
        sb = _make_sandbox(workspace=self._WS)
        args = sb._build_bwrap_args(
            "echo hi", extra_rw_binds=[self._WS],
            workspace_root=self._WS, session_files_dir=None,
        )
        assert self._SESSIONS_SB not in args

    def test_ancestor_bind_also_triggers_the_guard(self) -> None:
        """A PARENT of the workspace re-opens sessions just as well."""
        sb = _make_sandbox(workspace=self._WS)
        args = sb._build_bwrap_args(
            "echo hi", extra_rw_binds=[r"C:\Users\x\.miqi"],
            workspace_root=self._WS, session_files_dir=self._FILES,
        )
        assert _index_of_triplet(args, "--ro-bind-try", self._SESSIONS_SB) >= 0

    def test_sibling_dir_is_not_matched_as_workspace(self) -> None:
        """Path-boundary check: ``…/workspace-other`` is not the workspace."""
        sb = _make_sandbox(workspace=self._WS)
        args = sb._build_bwrap_args(
            "echo hi",
            extra_rw_binds=[r"C:\Users\x\.miqi\workspace-other"],
            workspace_root=self._WS, session_files_dir=self._FILES,
        )
        assert self._SESSIONS_SB not in args


# ── run_command / run_command_streaming passthrough ──────────────────────


class TestRunCommandPassthrough:
    @pytest.mark.asyncio
    async def test_run_command_forwards_extra_rw_binds(self, monkeypatch) -> None:
        sb = _make_sandbox(use_wsl=False)
        sb._running = True
        seen: dict = {}

        def _fake_build(
            command, env=None, cwd=None, extra_rw_binds=None,
            workspace_root=None, session_files_dir=None,
        ):
            seen["binds"] = extra_rw_binds
            return ["/usr/bin/bwrap", "true"]

        monkeypatch.setattr(sb, "_build_bwrap_args", _fake_build)
        monkeypatch.setattr(sb, "_run_linux_command", AsyncMock(return_value=(0, "", "")))
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        monkeypatch.setattr(
            "miqi.sandbox.bwrap._create_subprocess_exec",
            AsyncMock(return_value=proc),
        )

        await sb.run_command("echo hi", extra_rw_binds=[r"C:\Users\x\out"])
        assert seen["binds"] == [r"C:\Users\x\out"]

    @pytest.mark.asyncio
    async def test_run_command_streaming_forwards_extra_rw_binds(self, monkeypatch) -> None:
        sb = _make_sandbox(use_wsl=False)
        sb._running = True
        seen: dict = {}

        def _fake_build(
            command, env=None, cwd=None, extra_rw_binds=None,
            workspace_root=None, session_files_dir=None,
        ):
            seen["binds"] = extra_rw_binds
            return ["/usr/bin/bwrap", "true"]

        monkeypatch.setattr(sb, "_build_bwrap_args", _fake_build)
        monkeypatch.setattr(
            sb, "_run_bwrap_streaming_native", AsyncMock(return_value=MagicMock()),
        )

        await sb.run_command_streaming("echo hi", extra_rw_binds=["/srv/out"])
        assert seen["binds"] == ["/srv/out"]

    @pytest.mark.asyncio
    async def test_run_command_default_is_none(self, monkeypatch) -> None:
        sb = _make_sandbox(use_wsl=False)
        sb._running = True
        seen: dict = {}

        def _fake_build(
            command, env=None, cwd=None, extra_rw_binds=None,
            workspace_root=None, session_files_dir=None,
        ):
            seen["binds"] = extra_rw_binds
            return ["/usr/bin/bwrap", "true"]

        monkeypatch.setattr(sb, "_build_bwrap_args", _fake_build)
        monkeypatch.setattr(sb, "_run_linux_command", AsyncMock(return_value=(0, "", "")))
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        monkeypatch.setattr(
            "miqi.sandbox.bwrap._create_subprocess_exec",
            AsyncMock(return_value=proc),
        )

        await sb.run_command("echo hi")
        assert seen["binds"] is None

    @pytest.mark.asyncio
    async def test_guard_kwargs_reach_build(self, monkeypatch) -> None:
        """The guard is dead code unless the caller's paths arrive here."""
        sb = _make_sandbox(use_wsl=False)
        sb._running = True
        seen: dict = {}

        def _fake_build(
            command, env=None, cwd=None, extra_rw_binds=None,
            workspace_root=None, session_files_dir=None,
        ):
            seen["workspace_root"] = workspace_root
            seen["session_files_dir"] = session_files_dir
            return ["/usr/bin/bwrap", "true"]

        monkeypatch.setattr(sb, "_build_bwrap_args", _fake_build)
        monkeypatch.setattr(sb, "_run_linux_command", AsyncMock(return_value=(0, "", "")))
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        monkeypatch.setattr(
            "miqi.sandbox.bwrap._create_subprocess_exec",
            AsyncMock(return_value=proc),
        )

        await sb.run_command(
            "echo hi",
            extra_rw_binds=[r"C:\Users\x\.miqi\workspace"],
            workspace_root=r"C:\Users\x\.miqi\workspace",
            session_files_dir=r"C:\Users\x\.miqi\workspace\sessions\k\files",
        )
        assert seen["workspace_root"] == r"C:\Users\x\.miqi\workspace"
        assert seen["session_files_dir"] == (
            r"C:\Users\x\.miqi\workspace\sessions\k\files"
        )
