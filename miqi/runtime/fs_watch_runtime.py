"""Codex-style filesystem watch runtime (Phase 46).

Provides ``FsWatchRuntime`` — a polling-based watch engine that tracks
filesystem snapshots and emits ``fs/changed`` notifications when
watched paths change.  Watches are scoped by ``(client_id, watch_id)``.

The runtime is owned through ``registry.bridge_context["fs_watch_runtime"]``
and cleaned up on client disconnect via AppServer cleanup hooks.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from miqi.runtime.app_server import AppServerError

# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class FsWatch:
    """A single filesystem watch tracked by FsWatchRuntime."""

    client_id: str
    watch_id: str
    path: Path
    snapshot: dict[str, tuple[bool, int, int]] = field(default_factory=dict)
    task: asyncio.Task | None = None


# ── Runtime ──────────────────────────────────────────────────────────────────


class FsWatchRuntime:
    """Polling-based filesystem watch engine.

    Each watch maintains a ``(exists, mtime_ns, size)`` snapshot of the
    watched path.  For directories the snapshot includes the directory
    itself and all direct children.

    Polling runs via ``AppServer.create_background_task``.  The interval
    defaults to 0.25 s; tests may inject a shorter interval.
    """

    def __init__(self, app_server: Any, *, interval_seconds: float = 0.25):
        self._app_server = app_server
        self._interval_seconds = interval_seconds
        self._watches: dict[tuple[str, str], FsWatch] = {}

    # ── public API ──────────────────────────────────────────────────────────

    async def watch(
        self, client_id: str, watch_id: str, path: Path,
    ) -> dict[str, Any]:
        """Start watching *path* for *client_id*.

        Returns the canonical path dict on success.

        Raises:
            AppServerError(INVALID_PARAMS) if the (client_id, watch_id)
                pair is already registered.
            AppServerError(NOT_FOUND) if *path* does not exist.
        """
        key = (client_id, watch_id)
        if key in self._watches:
            raise AppServerError(
                f"Duplicate watch id: {watch_id}",
                code="INVALID_PARAMS",
            )

        resolved = path.resolve(strict=False)
        if not resolved.exists():
            raise AppServerError(
                f"Path does not exist: {resolved}",
                code="NOT_FOUND",
            )

        watch = FsWatch(
            client_id=client_id,
            watch_id=watch_id,
            path=resolved,
            snapshot=self._snapshot(resolved),
        )

        # Create background polling task
        watch.task = self._app_server.create_background_task(
            self._poll_loop(key), name=f"fs-watch-{client_id}-{watch_id}",
        )

        self._watches[key] = watch
        logger.debug("FsWatchRuntime: started watch {} for client {}", watch_id, client_id)
        return {"path": str(resolved)}

    async def unwatch(self, client_id: str, watch_id: str) -> None:
        """Stop watching a previously registered watch.

        Missing watch succeeds silently (no-op).
        """
        key = (client_id, watch_id)
        watch = self._watches.pop(key, None)
        if watch is None:
            return
        if watch.task is not None and not watch.task.done():
            watch.task.cancel()
        logger.debug("FsWatchRuntime: stopped watch {} for client {}", watch_id, client_id)

    async def cleanup_client(self, client_id: str) -> None:
        """Remove all watches owned by *client_id*."""
        keys = [
            k for k, w in self._watches.items()
            if w.client_id == client_id
        ]
        for key in keys:
            watch = self._watches.pop(key, None)
            if watch is not None and watch.task is not None and not watch.task.done():
                watch.task.cancel()
        if keys:
            logger.debug(
                "FsWatchRuntime: cleaned up {} watch(es) for client {}",
                len(keys), client_id,
            )

    async def poll_once(self, client_id: str, watch_id: str) -> list[str]:
        """Take one snapshot and return changed paths (for testing).

        Raises:
            KeyError if the (client_id, watch_id) pair is unknown.
        """
        key = (client_id, watch_id)
        if key not in self._watches:
            raise KeyError(f"Unknown watch: {client_id}/{watch_id}")
        return await self._poll(key)

    # ── internal ────────────────────────────────────────────────────────────

    def _snapshot(self, path: Path) -> dict[str, tuple[bool, int, int]]:
        """Build a filesystem snapshot for *path* and its direct children."""
        snap: dict[str, tuple[bool, int, int]] = {}

        def _add(p: Path) -> None:
            try:
                st = p.stat()
                snap[str(p)] = (True, st.st_mtime_ns, st.st_size)
            except OSError:
                snap[str(p)] = (False, 0, 0)

        _add(path)
        if path.is_dir():
            try:
                for child in path.iterdir():
                    _add(child)
            except OSError:
                pass

        return snap

    def _diff(
        self,
        old: dict[str, tuple[bool, int, int]],
        new: dict[str, tuple[bool, int, int]],
    ) -> list[str]:
        """Return sorted list of changed paths between two snapshots."""
        changed: set[str] = set()
        all_keys = set(old.keys()) | set(new.keys())
        for k in all_keys:
            ov = old.get(k)
            nv = new.get(k)
            if ov != nv:
                changed.add(k)
        return sorted(changed)

    async def _poll(self, key: tuple[str, str]) -> list[str]:
        """Poll one watch and emit fs/changed if there are changes."""
        watch = self._watches.get(key)
        if watch is None:
            return []

        old_snap = watch.snapshot
        new_snap = self._snapshot(watch.path)
        changed = self._diff(old_snap, new_snap)

        if changed:
            watch.snapshot = new_snap
            await self._app_server.emit_client_event(
                watch.client_id,
                "fs/changed",
                {
                    "watchId": watch.watch_id,
                    "changedPaths": changed,
                },
            )

        return changed

    async def _poll_loop(self, key: tuple[str, str]) -> None:
        """Background polling loop that runs until cancelled."""
        try:
            while True:
                await asyncio.sleep(self._interval_seconds)
                if key not in self._watches:
                    break
                try:
                    await self._poll(key)
                except Exception as exc:
                    logger.warning(
                        "FsWatchRuntime: poll error for {}: {}", key, exc,
                    )
        except asyncio.CancelledError:
            pass
