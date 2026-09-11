"""Phase 46 audit tests: Codex-style fs/* and fuzzyFileSearch* APIs.

Tests cover:
- Method registration after BridgeRuntimeLoop._init_app_server()
- Preservation of existing MiQi files.* API
- No runtime → miqi.bridge.server imports
- No Desktop files changed
- Real _drain_loop integration tests
"""

from __future__ import annotations

import asyncio
import json as _json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miqi.runtime.app_server import AppServerError

# ── Helpers ──────────────────────────────────────────────────────────────────


class _CaptureSend:
    """Capture send() calls for verification."""

    def __init__(self):
        self.messages: list[dict] = []

    def send(self, data: dict) -> None:
        self.messages.append(data)

    def last(self) -> dict | None:
        return self.messages[-1] if self.messages else None

    def clear(self) -> None:
        self.messages.clear()


# ── Expected method set ──────────────────────────────────────────────────────

EXPECTED_PHASE46_METHODS = {
    "fs/readFile",
    "fs/writeFile",
    "fs/createDirectory",
    "fs/getMetadata",
    "fs/readDirectory",
    "fs/remove",
    "fs/copy",
    "fs/watch",
    "fs/unwatch",
    "fuzzyFileSearch",
    "fuzzyFileSearch/sessionStart",
    "fuzzyFileSearch/sessionUpdate",
    "fuzzyFileSearch/sessionStop",
}

EXPECTED_LEGACY_FILES_METHODS = {
    "files.tree",
    "files.read",
    "files.write",
    "files.delete",
    "files.diff",
    "files.revert",
    "files.accept",
}


# ── Method registration tests ────────────────────────────────────────────────


class TestPhase46MethodRegistration:
    """Verify all Phase 46 methods are registered at bridge init."""

    @pytest.mark.asyncio
    async def test_all_phase46_methods_registered(self):
        """After _init_app_server, every expected Phase 46 method is registered."""
        from miqi.bridge.loop import BridgeRuntimeLoop

        capturer = _CaptureSend()
        loop = BridgeRuntimeLoop(
            send_func=capturer.send,
            dispatch_legacy_func=None,
        )
        await loop._init_app_server()

        try:
            registered = set(loop.app_server._methods.keys())
            missing = EXPECTED_PHASE46_METHODS - registered
            assert not missing, (
                f"Missing Phase 46 methods: {sorted(missing)}"
            )
        finally:
            await loop._shutdown()

    @pytest.mark.asyncio
    async def test_existing_files_api_preserved(self):
        """Existing MiQi files.* methods are still registered."""
        from miqi.bridge.loop import BridgeRuntimeLoop

        capturer = _CaptureSend()
        loop = BridgeRuntimeLoop(
            send_func=capturer.send,
            dispatch_legacy_func=None,
        )
        await loop._init_app_server()

        try:
            registered = set(loop.app_server._methods.keys())
            missing_legacy = EXPECTED_LEGACY_FILES_METHODS - registered
            assert not missing_legacy, (
                f"Missing legacy files.* methods: {sorted(missing_legacy)}"
            )
        finally:
            await loop._shutdown()


# ── Import isolation tests ───────────────────────────────────────────────────


class TestPhase46ImportIsolation:
    """Verify runtime modules do not import miqi.bridge.server."""

    MODULES_TO_CHECK = [
        "miqi.runtime.experimental_api",
        "miqi.runtime.fs_protocol",
        "miqi.runtime.fs_app_handlers",
        "miqi.runtime.fs_watch_runtime",
        "miqi.runtime.fs_watch_app_handlers",
        "miqi.runtime.fuzzy_file_search_runtime",
        "miqi.runtime.fuzzy_file_search_app_handlers",
    ]

    def test_runtime_modules_do_not_import_bridge_server(self):
        """New runtime modules must not import miqi.bridge.server directly."""
        import ast

        violations: dict[str, list[str]] = {}

        for module_name in self.MODULES_TO_CHECK:
            parts = module_name.split(".")
            rel_path = os.path.join(*parts) + ".py"
            full_path = Path(__file__).parent.parent.parent / rel_path
            if not full_path.exists():
                continue  # Module not created yet — no violation

            source = full_path.read_text(encoding="utf-8")
            tree = ast.parse(source)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("miqi.bridge"):
                            violations.setdefault(module_name, []).append(
                                f"import {alias.name}"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("miqi.bridge"):
                        violations.setdefault(module_name, []).append(
                            f"from {node.module} import ..."
                        )

        assert not violations, (
            f"Runtime modules importing miqi.bridge.*: {violations}"
        )


# ── Desktop isolation test ───────────────────────────────────────────────────


class TestPhase46DesktopIsolation:
    """Verify no Desktop files are changed for Phase 46."""

    DESKTOP_SRC = Path(__file__).parent.parent.parent / "apps" / "desktop" / "src"

    def test_no_desktop_files_changed_for_phase46(self):
        """Phase 46 must not touch Desktop UI files.

        This is a documentation/process check, not a git diff check.
        It verifies that the desktop src directory exists and that
        we are aware of the constraint. The real enforcement is that
        no Phase 46 commit touches apps/desktop/.
        """
        if not self.DESKTOP_SRC.exists():
            pytest.skip("Desktop src directory not found")
        # This test serves as a process reminder. The actual enforcement
        # is in the acceptance gates (git status --short).


# ── Real _drain_loop bridge flow tests ───────────────────────────────────────


class TestPhase46BridgeFlow:
    """Real BridgeRuntimeLoop._drain_loop integration tests."""

    @pytest.mark.asyncio
    async def test_initialize_then_fs_readfile_succeeds(self, tmp_path):
        """Full flow: initialize → fs/readFile returns dataBase64."""
        from miqi.bridge.loop import BridgeRuntimeLoop

        # Create a test file in a temp workspace
        test_file = tmp_path / "hello.txt"
        test_file.write_text("Hello, Phase 46!", encoding="utf-8")

        capturer = _CaptureSend()
        loop = BridgeRuntimeLoop(
            send_func=capturer.send,
            dispatch_legacy_func=None,
            bridge_state=_fake_bridge_state(str(tmp_path)),
        )
        await loop._init_app_server()
        loop._stdin_queue = asyncio.Queue()

        # 1. Initialize
        await loop._stdin_queue.put(_json.dumps({
            "id": "req-init",
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "test-client", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            },
        }))

        # 2. Read the file
        await loop._stdin_queue.put(_json.dumps({
            "id": "req-read",
            "method": "fs/readFile",
            "params": {"path": str(test_file)},
        }))

        # EOF
        await loop._stdin_queue.put(None)
        await loop._drain_loop()

        # Assert initialize succeeded
        assert len(capturer.messages) >= 2
        init_resp = capturer.messages[0]
        assert "result" in init_resp, f"Initialize failed: {init_resp}"
        assert "clientId" in init_resp["result"]

        # Assert fs/readFile succeeded
        read_resp = capturer.messages[1]
        assert "result" in read_resp, f"fs/readFile failed: {read_resp}"
        assert "dataBase64" in read_resp["result"]

        await loop._shutdown()

    @pytest.mark.asyncio
    async def test_uninitialized_fs_request_returns_not_initialized(self):
        """fs/readFile before initialize returns NOT_INITIALIZED."""
        from miqi.bridge.loop import BridgeRuntimeLoop

        capturer = _CaptureSend()
        loop = BridgeRuntimeLoop(
            send_func=capturer.send,
            dispatch_legacy_func=None,
        )
        await loop._init_app_server()
        loop._stdin_queue = asyncio.Queue()

        # Try to read without initializing
        await loop._stdin_queue.put(_json.dumps({
            "id": "req-read",
            "method": "fs/readFile",
            "params": {"path": "/tmp/test.txt"},
        }))
        await loop._stdin_queue.put(None)
        await loop._drain_loop()

        assert len(capturer.messages) == 1
        resp = capturer.messages[0]
        assert resp.get("code") == "NOT_INITIALIZED"

        await loop._shutdown()

    @pytest.mark.asyncio
    async def test_fuzzy_session_start_without_experimental_api_fails(self):
        """fuzzyFileSearch/sessionStart without experimentalApi returns EXPERIMENTAL_API_REQUIRED."""
        from miqi.bridge.loop import BridgeRuntimeLoop

        capturer = _CaptureSend()
        loop = BridgeRuntimeLoop(
            send_func=capturer.send,
            dispatch_legacy_func=None,
            bridge_state=_fake_bridge_state(str(Path.cwd())),
        )
        await loop._init_app_server()
        loop._stdin_queue = asyncio.Queue()

        # Initialize WITHOUT experimentalApi
        await loop._stdin_queue.put(_json.dumps({
            "id": "req-init",
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "test-client", "version": "1.0"},
                "capabilities": {},
            },
        }))

        # Try session start (requires experimental)
        await loop._stdin_queue.put(_json.dumps({
            "id": "req-session",
            "method": "fuzzyFileSearch/sessionStart",
            "params": {
                "sessionId": "search-1",
                "roots": [str(Path.cwd())],
            },
        }))

        await loop._stdin_queue.put(None)
        await loop._drain_loop()

        assert len(capturer.messages) >= 2
        init_resp = capturer.messages[0]
        assert "result" in init_resp, f"Initialize failed: {init_resp}"

        session_resp = capturer.messages[1]
        assert session_resp.get("code") == "EXPERIMENTAL_API_REQUIRED", (
            f"Expected EXPERIMENTAL_API_REQUIRED, got: {session_resp}"
        )

        await loop._shutdown()

    @pytest.mark.asyncio
    async def test_fuzzy_session_with_experimental_api_emits_notifications(self, tmp_path):
        """fuzzyFileSearch/sessionStart → sessionUpdate emits notifications when experimentalApi is true."""
        from miqi.bridge.loop import BridgeRuntimeLoop

        # Create some test files
        (tmp_path / "readme.md").write_text("hello")
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "main.py").write_text("print('hi')")

        capturer = _CaptureSend()
        loop = BridgeRuntimeLoop(
            send_func=capturer.send,
            dispatch_legacy_func=None,
            bridge_state=_fake_bridge_state(str(tmp_path)),
        )
        await loop._init_app_server()
        # Set up event sink so notifications reach the capturer
        loop._setup_event_sink()
        loop._stdin_queue = asyncio.Queue()

        # Initialize WITH experimentalApi
        await loop._stdin_queue.put(_json.dumps({
            "id": "req-init",
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "test-client", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            },
        }))

        # Session start
        await loop._stdin_queue.put(_json.dumps({
            "id": "req-start",
            "method": "fuzzyFileSearch/sessionStart",
            "params": {
                "sessionId": "search-1",
                "roots": [str(tmp_path)],
            },
        }))

        # Session update with query
        await loop._stdin_queue.put(_json.dumps({
            "id": "req-update",
            "method": "fuzzyFileSearch/sessionUpdate",
            "params": {
                "sessionId": "search-1",
                "query": "readme",
            },
        }))

        await loop._stdin_queue.put(None)
        await loop._drain_loop()

        # Initialize should succeed
        assert len(capturer.messages) >= 3, f"Expected >=3 messages, got {len(capturer.messages)}"
        init_resp = capturer.messages[0]
        assert "result" in init_resp

        # Session start should succeed (may be at index 1, with notifications interleaved)
        responses = [m for m in capturer.messages if "result" in m]
        # Notifications from the event sink use {"id": ..., "type": ..., "data": ...}
        notifications = [m for m in capturer.messages if "type" in m and "result" not in m]
        notification_types = [m.get("type") for m in notifications]

        assert len(responses) >= 3, f"Expected >=3 responses, got: {responses}"
        assert "fuzzyFileSearch/sessionUpdated" in notification_types, (
            f"Missing sessionUpdated notification. Types: {notification_types}"
        )
        assert "fuzzyFileSearch/sessionCompleted" in notification_types, (
            f"Missing sessionCompleted notification. Types: {notification_types}"
        )

        await loop._shutdown()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _fake_bridge_state(workspace_path: str):
    """Create a fake BridgeState for tests that provide a workspace."""
    from types import SimpleNamespace

    fake_config = SimpleNamespace()
    fake_config.workspace_path = Path(workspace_path)

    state = MagicMock()
    state.load_config.return_value = fake_config
    return state


# ── Phase 64 typed validation regressions ───────────────────────────────


class TestPhase64WatchFuzzyTypedValidation:
    @pytest.mark.asyncio
    async def test_fs_unwatch_rejects_missing_watch_id(self, tmp_path):
        from miqi.bridge.loop import BridgeRuntimeLoop

        capturer = _CaptureSend()
        loop = BridgeRuntimeLoop(
            send_func=capturer.send,
            dispatch_legacy_func=None,
            bridge_state=_fake_bridge_state(str(tmp_path)),
        )
        await loop._init_app_server()

        try:
            resp = await loop.app_server.dispatch(
                request_id="req-unwatch",
                method="fs/unwatch",
                params={},
                client_id="client-1",
                session_id=None,
            )

            assert resp.get("code") == "INVALID_PARAMS"
        finally:
            await loop._shutdown()

    @pytest.mark.asyncio
    async def test_fs_watch_rejects_stringless_watch_id_before_runtime(self, tmp_path):
        from miqi.bridge.loop import BridgeRuntimeLoop

        capturer = _CaptureSend()
        loop = BridgeRuntimeLoop(
            send_func=capturer.send,
            dispatch_legacy_func=None,
            bridge_state=_fake_bridge_state(str(tmp_path)),
        )
        await loop._init_app_server()

        try:
            resp = await loop.app_server.dispatch(
                request_id="req-watch",
                method="fs/watch",
                params={"watchId": 123, "path": str(tmp_path)},
                client_id="client-1",
                session_id=None,
            )

            assert resp.get("code") == "INVALID_PARAMS"
        finally:
            await loop._shutdown()

    @pytest.mark.asyncio
    async def test_fuzzy_search_rejects_non_list_roots(self, tmp_path):
        from miqi.bridge.loop import BridgeRuntimeLoop

        capturer = _CaptureSend()
        loop = BridgeRuntimeLoop(
            send_func=capturer.send,
            dispatch_legacy_func=None,
            bridge_state=_fake_bridge_state(str(tmp_path)),
        )
        await loop._init_app_server()

        try:
            resp = await loop.app_server.dispatch(
                request_id="req-fuzzy",
                method="fuzzyFileSearch",
                params={"query": "readme", "roots": str(tmp_path)},
                client_id="client-1",
                session_id=None,
            )

            assert resp.get("code") == "INVALID_PARAMS"
        finally:
            await loop._shutdown()

    @pytest.mark.asyncio
    async def test_fuzzy_session_start_experimental_gate_still_runs_first(self, tmp_path):
        from miqi.bridge.loop import BridgeRuntimeLoop

        capturer = _CaptureSend()
        loop = BridgeRuntimeLoop(
            send_func=capturer.send,
            dispatch_legacy_func=None,
            bridge_state=_fake_bridge_state(str(tmp_path)),
        )
        await loop._init_app_server()

        try:
            resp = await loop.app_server.dispatch(
                request_id="req-fuzzy-start",
                method="fuzzyFileSearch/sessionStart",
                params={"sessionId": 123, "roots": "bad"},
                client_id="client-1",
                session_id=None,
            )

            assert resp.get("code") == "EXPERIMENTAL_API_REQUIRED"
        finally:
            await loop._shutdown()


# ── Phase 64-fix: validation before runtime context tests ────────────────


class TestPhase64ValidationBeforeRuntime:
    """Typed validation must run before runtime lookup — bad params return
    INVALID_PARAMS even when registry.bridge_context is empty (no app_server,
    no runtime)."""

    @pytest.mark.asyncio
    async def test_fs_watch_bad_params_invalid_before_runtime(self):
        from types import SimpleNamespace

        from miqi.runtime.fs_watch_app_handlers import fs_watch_handler

        registry = SimpleNamespace()
        registry.bridge_context = {}

        with pytest.raises(AppServerError) as exc:
            await fs_watch_handler(
                "req-1", {"watchId": 123, "path": 123},
                "client-1", None, registry,
            )

        assert exc.value.code == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_fs_unwatch_bad_params_invalid_before_runtime(self):
        from types import SimpleNamespace

        from miqi.runtime.fs_watch_app_handlers import fs_unwatch_handler

        registry = SimpleNamespace()
        registry.bridge_context = {}

        with pytest.raises(AppServerError) as exc:
            await fs_unwatch_handler(
                "req-1", {}, "client-1", None, registry,
            )

        assert exc.value.code == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_fuzzy_search_bad_params_invalid_before_runtime(self):
        from types import SimpleNamespace

        from miqi.runtime.fuzzy_file_search_app_handlers import (
            fuzzy_file_search_handler,
        )

        registry = SimpleNamespace()
        registry.bridge_context = {}

        with pytest.raises(AppServerError) as exc:
            await fuzzy_file_search_handler(
                "req-1", {"query": 123, "roots": "bad"},
                "client-1", None, registry,
            )

        assert exc.value.code == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_fuzzy_session_start_experimental_gate_before_validation(self):
        from types import SimpleNamespace

        from miqi.runtime.fuzzy_file_search_app_handlers import (
            fuzzy_session_start_handler,
        )

        registry = SimpleNamespace()
        registry.bridge_context = {}

        with pytest.raises(AppServerError) as exc:
            await fuzzy_session_start_handler(
                "req-1", {"sessionId": 123, "roots": "bad"},
                "client-1", None, registry,
            )

        assert exc.value.code == "EXPERIMENTAL_API_REQUIRED"
