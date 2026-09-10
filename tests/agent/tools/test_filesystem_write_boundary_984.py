"""File-tool write boundary (#984 PR1).

Two fixes:
  * ``_sandbox_write_file`` computed its parent with a *single-quoted*
    command substitution, so ``mkdir -p '$(dirname "…")'`` created a literal
    ``$(dirname "…")`` directory and every write into a new subdirectory
    failed with rc=1.  The parent is now computed in Python.
  * authorized roots that do not exist yet are created on the host before the
    sandbox binds them (a hard ``--bind`` would otherwise fail).
"""

from __future__ import annotations

import base64
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from miqi.agent.tools.filesystem import (
    _sandbox_write_file,
    bootstrap_sandbox_roots,
)

_IS_WINDOWS = sys.platform == "win32"
_BASH = shutil.which("bash")
_FS_LOGGER = "miqi.agent.tools.filesystem"


class _RecordingSandbox:
    """Sandbox stub that records the command and can really execute it."""

    def __init__(self, *, run: bool = False):
        self.commands: list[str] = []
        self.kwargs: list[dict] = []
        self._run = run

    async def run_command(self, command: str, **kwargs):
        self.commands.append(command)
        self.kwargs.append(kwargs)
        if not self._run:
            return (0, "", "")
        proc = subprocess.run(  # noqa: S603 — fixed test command
            [_BASH, "-c", command], capture_output=True, text=True,
        )
        return (proc.returncode, proc.stdout, proc.stderr)


# ── _sandbox_write_file mkdir fix ────────────────────────────────────────


class TestSandboxWriteFileMkdir:
    @pytest.mark.asyncio
    async def test_parent_computed_in_python(self) -> None:
        sandbox = _RecordingSandbox()
        await _sandbox_write_file(sandbox, "/mnt/c/out/sub/file.txt", "hi")
        cmd = sandbox.commands[0]
        assert "mkdir -p '/mnt/c/out/sub'" in cmd
        assert "$(" not in cmd, "no shell command substitution (#984)"
        assert "dirname" not in cmd

    @pytest.mark.asyncio
    async def test_parent_stays_single_quoted(self) -> None:
        """Double quotes would turn $ and ` in Windows names into injection."""
        sandbox = _RecordingSandbox()
        await _sandbox_write_file(sandbox, "/mnt/c/out/a$b/file.txt", "hi")
        assert "mkdir -p '/mnt/c/out/a$b'" in sandbox.commands[0]

    @pytest.mark.asyncio
    async def test_single_quote_in_parent_escaped(self) -> None:
        sandbox = _RecordingSandbox()
        await _sandbox_write_file(sandbox, "/mnt/c/o'brien/file.txt", "hi")
        assert "mkdir -p '/mnt/c/o'\\''brien'" in sandbox.commands[0]

    @pytest.mark.asyncio
    async def test_root_level_path_uses_dot(self) -> None:
        sandbox = _RecordingSandbox()
        await _sandbox_write_file(sandbox, "/file.txt", "hi")
        assert "mkdir -p '.'" in sandbox.commands[0]

    @pytest.mark.asyncio
    async def test_content_is_base64_encoded(self) -> None:
        sandbox = _RecordingSandbox()
        await _sandbox_write_file(sandbox, "/mnt/c/out/f.txt", "hello 世界")
        encoded = base64.b64encode("hello 世界".encode("utf-8")).decode("ascii")
        assert f"echo '{encoded}' | base64 -d > '/mnt/c/out/f.txt'" in sandbox.commands[0]

    @pytest.mark.asyncio
    async def test_extra_rw_binds_forwarded(self, tmp_path: Path) -> None:
        """Without the binds the write hits EROFS under the read-only /mnt."""
        sandbox = _RecordingSandbox()
        await _sandbox_write_file(
            sandbox, "/mnt/c/out/f.txt", "x", extra_rw_binds=[tmp_path],
        )
        assert sandbox.kwargs[0]["extra_rw_binds"] == [str(tmp_path)]

    @pytest.mark.asyncio
    async def test_no_binds_sends_none(self) -> None:
        sandbox = _RecordingSandbox()
        await _sandbox_write_file(sandbox, "/mnt/c/out/f.txt", "x")
        assert sandbox.kwargs[0]["extra_rw_binds"] is None

    @pytest.mark.asyncio
    async def test_failure_raises_ioerror(self) -> None:
        class _Fail:
            async def run_command(self, command: str, **kwargs):
                return (1, "", "no such dir")

        with pytest.raises(IOError, match="Cannot write"):
            await _sandbox_write_file(_Fail(), "/mnt/c/out/f.txt", "x")

    @pytest.mark.asyncio
    @pytest.mark.skipif(_BASH is None, reason="bash not available")
    async def test_new_subdirectory_write_succeeds_for_real(self, tmp_path: Path) -> None:
        """Regression: the old form failed on any not-yet-created parent."""
        sandbox = _RecordingSandbox(run=True)
        target = (tmp_path / "fresh" / "nested" / "out.txt").as_posix()
        await _sandbox_write_file(sandbox, target, "written by #984")
        assert (tmp_path / "fresh" / "nested" / "out.txt").read_text(
            encoding="utf-8"
        ) == "written by #984"
        # The buggy form created a literal '$(dirname "...")' directory.
        assert not (tmp_path / "$(dirname ").exists()


# ── bootstrap_sandbox_roots ──────────────────────────────────────────────


class TestBootstrapSandboxRoots:
    def test_creates_missing_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b"
        created = bootstrap_sandbox_roots([target])
        assert target.is_dir()
        assert created == [str(target)]

    def test_existing_dir_untouched(self, tmp_path: Path) -> None:
        assert bootstrap_sandbox_roots([tmp_path]) == []

    def test_unc_skipped(self) -> None:
        assert bootstrap_sandbox_roots([r"\\wsl$\Ubuntu\home\x"]) == []

    def test_relative_skipped(self) -> None:
        assert bootstrap_sandbox_roots(["relative/dir"]) == []

    def test_invalid_entries_skipped(self) -> None:
        assert bootstrap_sandbox_roots([None, 123, b"bytes"]) == []

    @pytest.mark.skipif(not _IS_WINDOWS, reason="POSIX paths are WSL-native on Windows")
    def test_wsl_native_path_skipped_on_windows(self) -> None:
        assert bootstrap_sandbox_roots(["/home/miqi/out"]) == []

    @pytest.mark.skipif(_IS_WINDOWS, reason="POSIX paths are host paths on POSIX")
    def test_posix_path_created_on_posix(self, tmp_path: Path) -> None:
        target = tmp_path / "posix_out"
        assert bootstrap_sandbox_roots([target]) == [str(target)]

    def test_multiple_roots(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a", tmp_path / "b"
        assert bootstrap_sandbox_roots([a, b]) == [str(a), str(b)]
        assert a.is_dir() and b.is_dir()

    # ── #1007 review: drive-relative spellings name no fixed root ────────

    @pytest.mark.parametrize("raw", ["C:out", "C:", "C:relative", r"C:foo\bar"])
    def test_drive_relative_not_created(self, raw: str, monkeypatch) -> None:
        """``C:out`` is relative to the C: drive's CWD — not a bind source.

        The old ``len(s) >= 2 and s[1] == ":"`` judge treated it as a drive
        path, so bootstrap mkdir-ed a directory nobody named and handed it to
        the sandbox as an rw ``--bind`` source (the same tightening as
        ``bwrap._host_path_to_sandbox``, 34907420).  ``Path.mkdir`` is
        recorded, not run: the pre-fix code created the junk directory for
        real (on POSIX it landed in the test runner's CWD).
        """
        calls: list = []
        monkeypatch.setattr(
            Path, "mkdir", lambda self, **kw: calls.append(self),
        )
        # Nothing exists yet — the "new output dir" case this function is
        # written for.  Without it ``C:`` (the drive's current directory)
        # short-circuits on ``exists()`` and the regression hides.
        monkeypatch.setattr(Path, "exists", lambda self: False)
        assert bootstrap_sandbox_roots([raw]) == []
        assert calls == [], f"drive-relative {raw!r} reached mkdir"

    @pytest.mark.skipif(not _IS_WINDOWS, reason="drive spellings are Windows-only")
    def test_drive_absolute_still_created(self, tmp_path: Path) -> None:
        """The tightened judge must not change any absolute spelling."""
        target = tmp_path / "drive_abs" / "sub"
        assert bootstrap_sandbox_roots([target]) == [str(target)]
        assert target.is_dir()


# ── #1007 review: the log calls are stdlib-logging, so ``%s`` not ``{}`` ──


class TestBootstrapSandboxRootsLogging:
    """``_log`` is a ``logging.Logger`` (filesystem.py:16): it formats with
    ``msg % args``.  The ``{}`` placeholders raised ``TypeError: not all
    arguments converted`` INSIDE logging, so the message was dropped and only
    a bare ``--- Logging error ---`` reached stderr — the operator saw neither
    the path nor the reason.
    """

    _LOGGER = _FS_LOGGER

    @staticmethod
    def _messages(caplog) -> list[str]:
        return [r.getMessage() for r in caplog.records if r.name == _FS_LOGGER]

    def test_create_failure_logs_path_and_reason(
        self, tmp_path: Path, monkeypatch, caplog,
    ) -> None:
        target = tmp_path / "sub" / "out"

        def _boom(self, *args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "mkdir", _boom)
        with caplog.at_level(logging.WARNING, logger=self._LOGGER):
            assert bootstrap_sandbox_roots([target]) == []
        assert "cannot create" in caplog.text
        assert self._messages(caplog) == [
            f"bootstrap_sandbox_roots: cannot create {target}: disk full"
        ]

    def test_created_roots_are_logged(self, tmp_path: Path, caplog) -> None:
        target = tmp_path / "made" / "later"
        with caplog.at_level(logging.INFO, logger=self._LOGGER):
            created = bootstrap_sandbox_roots([target])
        assert created == [str(target)]
        assert "bootstrap_sandbox_roots: created" in caplog.text
        assert self._messages(caplog) == [
            f"bootstrap_sandbox_roots: created {[str(target)]}"
        ]
