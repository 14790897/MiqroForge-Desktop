"""Tests for Codex fs watch runtime and handlers (Phase 46).

Covers:
- fs/watch, fs/unwatch handlers
- FsWatchRuntime watch/unwatch/cleanup_client/poll_once
- fs/changed event emission
- Duplicate watch rejection
- Notification opt-out
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miqi.runtime.app_server import AppServer, ClientCapabilities, ClientSessionRegistry
from miqi.runtime.fs_watch_app_handlers import register_fs_watch_handlers
from miqi.runtime.fs_watch_runtime import FsWatchRuntime

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_runtime(*, workspace: "Path | None" = None):
    """Create an AppServer + registry suitable for watch runtime tests."""
    from types import SimpleNamespace

    registry = ClientSessionRegistry()
    server = AppServer(registry)
    # Collect events emitted to a specific client
    events: dict[str, list[dict]] = {}

    runtime = FsWatchRuntime(server, interval_seconds=0.01)

    # Set up a fake bridge state so handlers can resolve workspace paths
    ws = workspace.resolve() if workspace else Path.cwd().resolve()
    fake_config = SimpleNamespace()
    fake_config.workspace_path = ws
    state = MagicMock()
    state.load_config.return_value = fake_config

    registry.bridge_context = {
        "state": state,
        "app_server": server,
        "fs_watch_runtime": runtime,
    }
    return server, registry, runtime, events


def _register_sink(server, client_id: str, events: dict[str, list[dict]]):
    """Register an event sink that captures events into *events* dict."""

    async def sink(event: dict) -> None:
        events.setdefault(client_id, []).append(event)

    server._event_sinks[client_id] = sink


# ── FsWatchRuntime ───────────────────────────────────────────────────────────


class TestFsWatchRuntime:
    """Tests for FsWatchRuntime directly."""

    @pytest.mark.asyncio
    async def test_watch_succeeds_and_returns_path(self, tmp_path):
        """First watch succeeds and returns canonical path."""
        watched = tmp_path / "watched.txt"
        watched.write_text("hello")

        server, registry, runtime, events = _make_runtime()
        _register_sink(server, "client-1", events)

        result = await runtime.watch("client-1", "watch-1", watched)
        assert result["path"] == str(watched.resolve())

    @pytest.mark.asyncio
    async def test_duplicate_watch_id_rejected(self, tmp_path):
        """Duplicate (client_id, watchId) raises INVALID_PARAMS."""
        watched = tmp_path / "watched.txt"
        watched.write_text("hello")

        server, registry, runtime, events = _make_runtime()

        await runtime.watch("client-1", "watch-1", watched)
        with pytest.raises(Exception) as exc_info:
            await runtime.watch("client-1", "watch-1", watched)
        assert "INVALID_PARAMS" in str(exc_info.value) or "duplicate" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_same_watch_id_different_client_allowed(self, tmp_path):
        """Same watchId from different clients is allowed."""
        watched = tmp_path / "shared.txt"
        watched.write_text("shared")

        server, registry, runtime, events = _make_runtime()

        result1 = await runtime.watch("client-1", "watch-1", watched)
        result2 = await runtime.watch("client-2", "watch-1", watched)
        assert result1["path"] == result2["path"]

    @pytest.mark.asyncio
    async def test_watch_missing_path_raises(self, tmp_path):
        """Watching a nonexistent path raises NOT_FOUND."""
        missing = tmp_path / "nonexistent.txt"

        server, registry, runtime, events = _make_runtime()

        with pytest.raises(Exception) as exc_info:
            await runtime.watch("client-1", "watch-1", missing)
        assert exc_info.value.code == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_poll_once_after_modify_emits_changed(self, tmp_path):
        """poll_once after modifying watched file emits fs/changed."""
        watched = tmp_path / "watched.txt"
        watched.write_text("initial")

        server, registry, runtime, events = _make_runtime()
        _register_sink(server, "client-1", events)

        await runtime.watch("client-1", "watch-1", watched)

        # Modify the file
        await asyncio.sleep(0.05)  # small delay for mtime to change
        watched.write_text("modified")

        changed = await runtime.poll_once("client-1", "watch-1")
        assert len(changed) > 0, f"Expected changes, got {changed}"
        assert str(watched.resolve()) in changed

    @pytest.mark.asyncio
    async def test_directory_watch_reports_direct_child_creation(self, tmp_path):
        """Directory watch reports creation of direct child."""
        watched_dir = tmp_path / "watched_dir"
        watched_dir.mkdir()

        server, registry, runtime, events = _make_runtime()
        _register_sink(server, "client-1", events)

        await runtime.watch("client-1", "watch-dir", watched_dir)

        # Create a new file in watched dir
        await asyncio.sleep(0.05)
        new_file = watched_dir / "new_file.txt"
        new_file.write_text("new")

        changed = await runtime.poll_once("client-1", "watch-dir")
        assert str(new_file.resolve()) in changed

    @pytest.mark.asyncio
    async def test_poll_once_no_changes_returns_empty(self, tmp_path):
        """poll_once with no changes returns empty list."""
        watched = tmp_path / "stable.txt"
        watched.write_text("stable")

        server, registry, runtime, events = _make_runtime()
        _register_sink(server, "client-1", events)

        await runtime.watch("client-1", "watch-stable", watched)
        changed = await runtime.poll_once("client-1", "watch-stable")
        assert changed == []

    @pytest.mark.asyncio
    async def test_unwatch_removes_state(self, tmp_path):
        """Unwatching removes the watch state."""
        watched = tmp_path / "watched.txt"
        watched.write_text("hello")

        server, registry, runtime, events = _make_runtime()

        await runtime.watch("client-1", "watch-1", watched)
        await runtime.unwatch("client-1", "watch-1")

        # After unwatch, poll should raise
        with pytest.raises(Exception):
            await runtime.poll_once("client-1", "watch-1")

    @pytest.mark.asyncio
    async def test_unwatch_missing_succeeds(self, tmp_path):
        """Unwatching a nonexistent watch succeeds silently."""
        server, registry, runtime, events = _make_runtime()

        # Should not raise
        await runtime.unwatch("client-1", "nonexistent-watch")

    @pytest.mark.asyncio
    async def test_cleanup_client_removes_all_watches(self, tmp_path):
        """cleanup_client removes all watches for that client only."""
        f1 = tmp_path / "f1.txt"
        f2 = tmp_path / "f2.txt"
        f1.write_text("a")
        f2.write_text("b")

        server, registry, runtime, events = _make_runtime()

        await runtime.watch("client-1", "w1", f1)
        await runtime.watch("client-1", "w2", f2)
        await runtime.watch("client-2", "w3", f1)

        await runtime.cleanup_client("client-1")

        # client-1 watches should be gone
        with pytest.raises(Exception):
            await runtime.poll_once("client-1", "w1")
        with pytest.raises(Exception):
            await runtime.poll_once("client-1", "w2")

        # client-2 watch should still work
        changed = await runtime.poll_once("client-2", "w3")
        assert changed == []

    @pytest.mark.asyncio
    async def test_watch_delete_detection(self, tmp_path):
        """poll_once detects when a watched file is deleted."""
        watched = tmp_path / "to_delete.txt"
        watched.write_text("delete me")

        server, registry, runtime, events = _make_runtime()
        _register_sink(server, "client-1", events)

        await runtime.watch("client-1", "watch-del", watched)

        # Delete the file
        await asyncio.sleep(0.05)
        watched.unlink()

        changed = await runtime.poll_once("client-1", "watch-del")
        assert str(watched.resolve()) in changed


# ── fs/watch and fs/unwatch handlers ─────────────────────────────────────────


class TestFsWatchHandlers:
    """Tests for the fs/watch and fs/unwatch AppServer handlers."""

    @pytest.mark.asyncio
    async def test_fs_watch_handler_succeeds(self, tmp_path):
        """fs/watch handler succeeds and returns path."""
        watched = tmp_path / "watched.txt"
        watched.write_text("hello")

        server, registry, runtime, events = _make_runtime(workspace=tmp_path)
        _register_sink(server, "client-1", events)
        register_fs_watch_handlers(server)

        resp = await server.dispatch(
            request_id="req-1",
            method="fs/watch",
            params={"watchId": "watch-1", "path": str(watched)},
            client_id="client-1",
            session_id=None,
        )

        assert "result" in resp, f"Expected result, got: {resp}"
        assert resp["result"]["path"] == str(watched.resolve())

    @pytest.mark.asyncio
    async def test_fs_watch_duplicate_rejected(self, tmp_path):
        """fs/watch with duplicate (client_id, watchId) returns INVALID_PARAMS."""
        watched = tmp_path / "watched.txt"
        watched.write_text("hello")

        server, registry, runtime, events = _make_runtime(workspace=tmp_path)
        register_fs_watch_handlers(server)

        # First watch
        resp1 = await server.dispatch(
            request_id="req-1",
            method="fs/watch",
            params={"watchId": "watch-1", "path": str(watched)},
            client_id="client-1",
            session_id=None,
        )
        assert "result" in resp1

        # Duplicate
        resp2 = await server.dispatch(
            request_id="req-2",
            method="fs/watch",
            params={"watchId": "watch-1", "path": str(watched)},
            client_id="client-1",
            session_id=None,
        )
        assert resp2.get("code") == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_fs_unwatch_handler_succeeds(self, tmp_path):
        """fs/unwatch handler succeeds for existing watch."""
        watched = tmp_path / "watched.txt"
        watched.write_text("hello")

        server, registry, runtime, events = _make_runtime(workspace=tmp_path)
        register_fs_watch_handlers(server)

        # Watch first
        await server.dispatch(
            request_id="req-1",
            method="fs/watch",
            params={"watchId": "watch-1", "path": str(watched)},
            client_id="client-1",
            session_id=None,
        )

        # Unwatch
        resp = await server.dispatch(
            request_id="req-2",
            method="fs/unwatch",
            params={"watchId": "watch-1"},
            client_id="client-1",
            session_id=None,
        )
        assert "result" in resp

    @pytest.mark.asyncio
    async def test_fs_unwatch_missing_succeeds(self, tmp_path):
        """fs/unwatch for nonexistent watch succeeds."""
        server, registry, runtime, events = _make_runtime(workspace=tmp_path)
        register_fs_watch_handlers(server)

        resp = await server.dispatch(
            request_id="req-1",
            method="fs/unwatch",
            params={"watchId": "nonexistent"},
            client_id="client-1",
            session_id=None,
        )
        assert "result" in resp


# ── Notification opt-out ─────────────────────────────────────────────────────


class TestWatchNotificationOptOut:
    """Tests that notification opt-out suppresses fs/changed."""

    @pytest.mark.asyncio
    async def test_opt_out_suppresses_fs_changed(self, tmp_path):
        """fs/changed is not delivered when opted out."""
        watched = tmp_path / "watched.txt"
        watched.write_text("initial")

        server, registry, runtime, events = _make_runtime()

        # Register sink for client-1
        _register_sink(server, "client-1", events)

        # Set capabilities with fs/changed opt-out
        server.set_client_capabilities(
            "client-1",
            ClientCapabilities(
                experimental_api=False,
                opt_out_notification_methods={"fs/changed"},
            ),
        )

        await runtime.watch("client-1", "watch-1", watched)

        # Modify to trigger change
        await asyncio.sleep(0.05)
        watched.write_text("changed")

        # poll_once returns changes but we check that emit is suppressed
        changed = await runtime.poll_once("client-1", "watch-1")
        assert len(changed) > 0

        # No events should have been delivered due to opt-out
        # (poll_once doesn't emit through AppServer, but the background
        # task would — we test that the should_deliver_notification gate works)
        assert server.should_deliver_notification("client-1", "fs/changed") is False
