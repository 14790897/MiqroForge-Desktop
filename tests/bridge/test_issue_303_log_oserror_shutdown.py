"""Unit tests for miqi.bridge.server._log OSError handling (issue #303)."""
from __future__ import annotations

import io
import sys

import pytest


class _FailingStderr:
    """Simulates a closed/invalidated stderr on Windows/PyInstaller shutdown."""

    def write(self, data: str) -> int:
        raise OSError(22, "Invalid argument")

    def flush(self) -> None:
        raise OSError(22, "Invalid argument")


class _FlakyStderr:
    """Fails entirely on the first call, then recovers.

    Both write() and flush() raise on the first print() call, so neither
    can toggle a flag.  We fix this by fail() raising once, then auto-reset.
    """

    def __init__(self) -> None:
        self._failed = False

    def write(self, data: str) -> int:
        if not self._failed:
            self._failed = True
            raise OSError(22, "Invalid argument")
        return len(data)

    def flush(self) -> None:
        if not self._failed:
            self._failed = True
            raise OSError(22, "Invalid argument")


def test_log_gracefully_swallows_oserror(monkeypatch) -> None:
    """_log() should return None when sys.stderr raises OSError."""
    from miqi.bridge.server import _log

    monkeypatch.setattr(sys, "stderr", _FailingStderr())

    # Must NOT raise — this is the exact scenario from issue #303
    try:
        _log("Bridge server stopped")
    except OSError as exc:
        pytest.fail(f"_log raised OSError unexpectedly: {exc}")


def test_log_works_after_transient_oserror(monkeypatch) -> None:
    """_log() should recover after a transient stderr failure."""
    from miqi.bridge.server import _log

    flaky = _FlakyStderr()
    monkeypatch.setattr(sys, "stderr", flaky)

    # First call should be swallowed (stderr raises, sets _failed=True)
    _log("first call — stderr broken")

    # Second call should succeed (stderr is recovered)
    _log("second call — stderr recovered")
    # No exception = success. _failed was set to True by the first call's
    # write() failure, so the second call's write() and flush() both pass.


def test_log_still_writes_to_stderr_normally() -> None:
    """_log() writes to stderr when it's healthy (no regression)."""
    from miqi.bridge.server import _log

    buf = io.StringIO()
    old_stderr = sys.stderr
    try:
        sys.stderr = buf
        _log("healthy message")
        output = buf.getvalue()
        assert "healthy message" in output
        assert "[miqi-bridge]" in output
    finally:
        sys.stderr = old_stderr


def test_log_handles_broken_flush(monkeypatch) -> None:
    """_log() should handle OSError during flush() as well."""

    class _WriteOkFlushFail:
        def write(self, data: str) -> int:
            return len(data)

        def flush(self) -> None:
            raise OSError(22, "Invalid argument")

    from miqi.bridge.server import _log

    monkeypatch.setattr(sys, "stderr", _WriteOkFlushFail())
    try:
        _log("shutdown message")
    except OSError as exc:
        pytest.fail(f"_log raised OSError during flush: {exc}")


def test_log_handles_none_stderr(monkeypatch) -> None:
    """_log() should handle sys.stderr being None (extreme edge case)."""
    from miqi.bridge.server import _log

    monkeypatch.setattr(sys, "stderr", None)
    try:
        _log("message with no stderr")
    except Exception as exc:
        pytest.fail(f"_log raised unexpected exception: {exc}")
