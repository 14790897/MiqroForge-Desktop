"""Unit tests for WSL sandbox path mapping functions (#474).

Tests _resolve_sandbox_path, _canonicalize_wsl_mnt_path, and
_sandbox_to_host_path. WSL-specific containment tests are Windows-only;
logic tests run everywhere.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miqi.agent.tools.filesystem import (
    _canonicalize_wsl_mnt_path,
    _resolve_sandbox_path,
    _sandbox_to_host_path,
)

_IS_WINDOWS = sys.platform == "win32"


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_wsl_sandbox():
    sb = MagicMock()
    sb._use_wsl = True
    sb.workspace = Path("/tmp/mock_ws")
    return sb


def _make_native_sandbox():
    sb = MagicMock()
    sb._use_wsl = False
    sb.workspace = Path("/tmp/mock_ws")
    return sb


def _as_mnt_path(host_path: Path, *extra: str) -> str:
    """Convert a host Path to a /mnt/<drive>/... sandbox path on Windows,
    or /mnt/c<path> on Linux (for unit testing only)."""
    p = host_path.resolve()
    if _IS_WINDOWS:
        drive = p.drive[0].lower()
        rest = p.as_posix()[2:].lstrip("/")
    else:
        drive = "c"
        rest = p.as_posix().lstrip("/")
    parts = [f"/mnt/{drive}", rest] + list(extra)
    return "/".join(parts)


# ── _canonicalize_wsl_mnt_path ───────────────────────────────────────────

class TestCanonicalizeWslMntPath:

    def test_normal_path_within_workspace(self, tmp_path):
        ws = tmp_path / "sub"
        ws.mkdir()
        result = _canonicalize_wsl_mnt_path(
            _as_mnt_path(ws, "readme.md"), ws
        )
        assert "readme.md" in result
        assert ".." not in result

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    def test_path_traversal_rejected(self, tmp_path):
        ws = tmp_path / "sub"
        ws.mkdir()
        mnt = _as_mnt_path(ws, "..", "secret.txt")
        with pytest.raises(PermissionError, match="超出|不在|根目录"):
            _canonicalize_wsl_mnt_path(mnt, ws)

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    def test_path_outside_workspace_rejected(self, tmp_path):
        ws = tmp_path / "sub"
        ws.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        with pytest.raises(PermissionError, match="超出|不在|根目录"):
            _canonicalize_wsl_mnt_path(_as_mnt_path(other, "file.txt"), ws)

    def test_no_workspace_returns_unchanged(self):
        result = _canonicalize_wsl_mnt_path(
            "/mnt/c/some/../file.txt", None
        )
        assert result == "/mnt/c/some/../file.txt"

    def test_non_mnt_path_returns_unchanged(self, tmp_path):
        result = _canonicalize_wsl_mnt_path(
            "/home/miqi/workspace/file.txt", tmp_path
        )
        assert result == "/home/miqi/workspace/file.txt"

    def test_canonicalize_resolves_dots(self, tmp_path):
        ws = tmp_path / "sub"
        ws.mkdir()
        result = _canonicalize_wsl_mnt_path(
            _as_mnt_path(ws, "nested", "..", "readme.md"), ws
        )
        assert ".." not in result
        assert result.endswith("/readme.md")

    def test_workspace_root_itself(self, tmp_path):
        ws = tmp_path / "sub"
        ws.mkdir()
        result = _canonicalize_wsl_mnt_path(_as_mnt_path(ws), ws)
        assert result.endswith("/sub")

    # ── issue #516: multi-root whitelist (host-global memory/ & skills/) ──

    def test_extra_root_none_preserves_single_root_behavior(self, tmp_path):
        """extra_roots=None must behave exactly like the old single-root path."""
        ws = tmp_path / "sub"
        ws.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        # Within workspace: accepted
        assert "readme.md" in _canonicalize_wsl_mnt_path(
            _as_mnt_path(ws, "readme.md"), ws, extra_roots=None
        )

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    def test_extra_root_allows_host_global_path(self, tmp_path):
        """A path inside an extra root is accepted even when outside the
        per-session workspace (issue #516: memory/MEMORY.md, skills/...)."""
        session_ws = tmp_path / "sessions" / "abc" / "files"
        session_ws.mkdir(parents=True)
        host_memory = tmp_path / "memory"
        host_memory.mkdir()
        result = _canonicalize_wsl_mnt_path(
            _as_mnt_path(host_memory, "MEMORY.md"),
            session_ws,
            extra_roots=[host_memory],
        )
        assert "MEMORY.md" in result
        assert "memory" in result

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    def test_extra_root_does_not_open_other_session_dir(self, tmp_path):
        """A path under ANOTHER session's files dir must still be rejected —
        per-session isolation is preserved (the #490/#505 red line)."""
        session_ws = tmp_path / "sessions" / "abc" / "files"
        session_ws.mkdir(parents=True)
        host_memory = tmp_path / "memory"
        host_memory.mkdir()
        host_skills = tmp_path / "skills"
        host_skills.mkdir()
        other_session = tmp_path / "sessions" / "other" / "files"
        other_session.mkdir(parents=True)
        with pytest.raises(PermissionError, match="超出|不在|根目录"):
            _canonicalize_wsl_mnt_path(
                _as_mnt_path(other_session, "secret.md"),
                session_ws,
                extra_roots=[host_memory, host_skills],
            )

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    def test_extra_root_traversal_into_outside_still_rejected(self, tmp_path):
        """A path that resolves outside every root via `..` is still rejected,
        even when extra_roots are configured."""
        session_ws = tmp_path / "sessions" / "abc" / "files"
        session_ws.mkdir(parents=True)
        host_memory = tmp_path / "memory"
        host_memory.mkdir()
        # From inside memory, traverse up via .. into an unrelated dir.
        mnt = _as_mnt_path(host_memory, "..", "evil.txt")
        with pytest.raises(PermissionError, match="超出|不在|根目录"):
            _canonicalize_wsl_mnt_path(mnt, session_ws, extra_roots=[host_memory])

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    def test_permission_error_mentions_extra_roots_config(self, tmp_path):
        """The rejection message points users to tools.extra_roots (issue #567)."""
        ws = tmp_path / "sub"
        ws.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        with pytest.raises(PermissionError, match=r"tools\.extra_roots"):
            _canonicalize_wsl_mnt_path(_as_mnt_path(other, "file.txt"), ws)


# ── _resolve_sandbox_path ────────────────────────────────────────────────

class TestResolveSandboxPathWSL:

    def test_wsl_windows_path_maps_to_mnt(self, tmp_path):
        """Under WSL sandbox on Windows, C:\\... maps to /mnt/..."""
        ws = tmp_path
        sb = _make_wsl_sandbox()
        if _IS_WINDOWS:
            win_path = str(ws.resolve() / "file.txt")
            result = _resolve_sandbox_path(win_path, ws, sb)
            assert result.startswith("/mnt/")
            assert result.endswith("/file.txt")
        else:
            # Linux: WSL sandbox not applicable, test fallback behavior
            result = _resolve_sandbox_path("readme.md", ws, sb)
            assert "readme.md" in result

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    def test_windows_path_outside_workspace_rejected(self, tmp_path):
        ws = tmp_path / "sub"
        ws.mkdir()
        sb = _make_wsl_sandbox()
        other = tmp_path / "other"
        other.mkdir()
        with pytest.raises(PermissionError, match="超出|不在|根目录"):
            _resolve_sandbox_path(str(other.resolve() / "file.txt"), ws, sb)

    def test_relative_path_under_wsl(self, tmp_path):
        """Relative paths resolve against workspace under WSL."""
        ws = tmp_path / "proj"
        ws.mkdir()
        sb = _make_wsl_sandbox()
        result = _resolve_sandbox_path("src/main.py", ws, sb)
        assert result.endswith("/src/main.py")

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    def test_relative_path_traversal_rejected(self, tmp_path):
        ws = tmp_path / "sub"
        ws.mkdir()
        sb = _make_wsl_sandbox()
        with pytest.raises(PermissionError, match="超出|不在|根目录"):
            _resolve_sandbox_path("../../secret.txt", ws, sb)

    def test_linux_path_kept_as_is(self, tmp_path):
        sb = _make_wsl_sandbox()
        result = _resolve_sandbox_path(
            "/home/miqi/workspace/file.txt", tmp_path, sb
        )
        assert result == "/home/miqi/workspace/file.txt"

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL /mnt/ containment Windows-only")
    def test_mnt_path_within_workspace(self, tmp_path):
        ws = tmp_path / "sub"
        ws.mkdir()
        sb = _make_wsl_sandbox()
        result = _resolve_sandbox_path(
            _as_mnt_path(ws, "file.txt"), ws, sb
        )
        assert "file.txt" in result

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    def test_mnt_path_outside_workspace_rejected(self, tmp_path):
        ws = tmp_path / "sub"
        ws.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        sb = _make_wsl_sandbox()
        with pytest.raises(PermissionError, match="超出|不在|根目录"):
            _resolve_sandbox_path(_as_mnt_path(other, "file.txt"), ws, sb)

    def test_native_sandbox_uses_workspace_remap(self, tmp_path):
        """Non-WSL sandbox still uses the old workspace remap."""
        ws = tmp_path
        sb = _make_native_sandbox()
        result = _resolve_sandbox_path("readme.md", ws, sb)
        assert result.endswith("/readme.md")

    def test_wsl_session_subdir(self, tmp_path):
        if not _IS_WINDOWS:
            pytest.skip("WSL session subdir mapping only meaningful on Windows")
        session_ws = tmp_path / "sessions" / "abc123" / "files"
        session_ws.mkdir(parents=True)
        sb = _make_wsl_sandbox()
        result = _resolve_sandbox_path("temp.txt", session_ws, sb)
        assert result.endswith("/sessions/abc123/files/temp.txt")

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    def test_wsl_host_global_path_via_extra_roots(self, tmp_path):
        """issue #516: a host-global path (memory/) outside the per-session
        workspace is accepted by _resolve_sandbox_path when shared_roots are
        passed, while a foreign session's dir is still rejected."""
        session_ws = tmp_path / "sessions" / "abc" / "files"
        session_ws.mkdir(parents=True)
        host_memory = tmp_path / "memory"
        host_memory.mkdir()
        other_session = tmp_path / "sessions" / "other" / "files"
        other_session.mkdir(parents=True)
        sb = _make_wsl_sandbox()

        # host-global memory path accepted
        ok = _resolve_sandbox_path(
            str(host_memory / "MEMORY.md"),
            session_ws,
            sb,
            extra_roots=[host_memory],
        )
        assert "MEMORY.md" in ok

        # foreign session path still rejected
        with pytest.raises(PermissionError, match="超出|不在|根目录"):
            _resolve_sandbox_path(
                str(other_session / "secret.md"),
                session_ws,
                sb,
                extra_roots=[host_memory],
            )

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    def test_root_workspace_read_allowed_with_session_isolation(self, tmp_path):
        """issue #613 follow-up: read tools resolve against the ROOT workspace
        (the working dir the system prompt advertises); session isolation is
        enforced via session_files_dir.  Root-workspace files must be readable
        even when per-session isolation is active."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        session_ws = ws / "sessions" / "abc" / "files"
        session_ws.mkdir(parents=True)
        other_session = ws / "sessions" / "other" / "files"
        other_session.mkdir(parents=True)
        sb = _make_wsl_sandbox()

        # Root workspace file: accepted (was rejected before the fix).
        ok = _resolve_sandbox_path(
            str(ws / "report.md"),
            ws,
            sb,
            session_files_dir=session_ws,
        )
        assert "/report.md" in ok

        # Own session dir: accepted.
        ok_own = _resolve_sandbox_path(
            str(session_ws / "x.txt"),
            ws,
            sb,
            session_files_dir=session_ws,
        )
        assert "x.txt" in ok_own

        # Other session dir: still rejected — isolation red line.
        with pytest.raises(PermissionError, match="隔离"):
            _resolve_sandbox_path(
                str(other_session / "secret.md"),
                ws,
                sb,
                session_files_dir=session_ws,
            )

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    def test_session_isolation_check_noop_without_session_files_dir(self, tmp_path):
        """When session_files_dir is None (no session isolation), the
        sessions/ dir is treated like any other workspace subdir."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        sb = _make_wsl_sandbox()
        ok = _resolve_sandbox_path(
            str(ws / "sessions" / "x" / "files" / "f.txt"),
            ws,
            sb,
        )
        assert "f.txt" in ok


# ── _sandbox_to_host_path ────────────────────────────────────────────────

class TestSandboxToHostPath:

    def test_mnt_path_converts_to_host(self, tmp_path):
        ws = tmp_path
        sb = _make_wsl_sandbox()
        result = _sandbox_to_host_path(
            _as_mnt_path(ws, "file.txt"), ws, sb
        )
        assert "file.txt" in result

    def test_home_miqi_workspace_conversion(self, tmp_path):
        ws = tmp_path
        sb = _make_wsl_sandbox()
        result = _sandbox_to_host_path(
            "/home/miqi/workspace/file.txt", ws, sb
        )
        assert "file.txt" in result

    def test_none_workspace_returns_unchanged(self):
        sb = _make_wsl_sandbox()
        result = _sandbox_to_host_path("/mnt/c/some/file.txt", None, sb)
        assert result == "/mnt/c/some/file.txt"

    def test_empty_path_returns_empty(self, tmp_path):
        sb = _make_wsl_sandbox()
        assert _sandbox_to_host_path("", tmp_path, sb) == ""
        assert _sandbox_to_host_path(None, tmp_path, sb) is None


# ── Issue #821: _user_roots injection into WriteFileTool ────────────────

class TestWriteFileUserRootsWSL:
    """Per-call user-mentioned roots authorize the user's output dirs under
    the WSL sandbox (issue #821), and are ignored when disabled."""

    def _make_manager(self, ws: Path):
        from unittest.mock import AsyncMock

        sb = _make_wsl_sandbox()
        sb.is_running = True
        sb.workspace = ws
        sb.run_command = AsyncMock(return_value=(0, "", ""))
        manager = MagicMock()
        manager.active_sandbox = sb
        manager.get_or_create = AsyncMock(return_value=sb)
        return manager

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    @pytest.mark.asyncio
    async def test_user_roots_allow_write_outside_workspace(self, tmp_path):
        from miqi.agent.tools.filesystem import WriteFileTool

        ws = tmp_path / "ws"
        ws.mkdir()
        out = tmp_path / "Desktop_out"
        out.mkdir()
        manager = self._make_manager(ws)
        tool = WriteFileTool(
            workspace=ws, sandbox_manager=manager, shared_roots=[ws],
        )
        target = out / "report.md"
        result = await tool.execute(
            str(target), "hello", _session_key="s1", _user_roots=[str(out)],
        )
        assert result.startswith("Successfully wrote")

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    @pytest.mark.asyncio
    async def test_without_user_roots_write_rejected(self, tmp_path):
        from miqi.agent.tools.filesystem import WriteFileTool

        ws = tmp_path / "ws"
        ws.mkdir()
        out = tmp_path / "Desktop_out"
        out.mkdir()
        manager = self._make_manager(ws)
        tool = WriteFileTool(
            workspace=ws, sandbox_manager=manager, shared_roots=[ws],
        )
        with pytest.raises(PermissionError, match="超出|不在|根目录"):
            await tool.execute(
                str(out / "report.md"), "hello", _session_key="s1",
            )

    @pytest.mark.skipif(not _IS_WINDOWS, reason="WSL containment Windows-only")
    @pytest.mark.asyncio
    async def test_disabled_flag_ignores_user_roots(self, tmp_path):
        from miqi.agent.tools.filesystem import WriteFileTool

        ws = tmp_path / "ws"
        ws.mkdir()
        out = tmp_path / "Desktop_out"
        out.mkdir()
        manager = self._make_manager(ws)
        tool = WriteFileTool(
            workspace=ws, sandbox_manager=manager, shared_roots=[ws],
            allow_user_roots=False,
        )
        with pytest.raises(PermissionError, match="超出|不在|根目录"):
            await tool.execute(
                str(out / "report.md"), "hello", _session_key="s1",
                _user_roots=[str(out)],
            )
