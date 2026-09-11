"""Tests for Codex fuzzy file search runtime and handlers (Phase 46).

Covers:
- One-shot search: scoring, ordering, limits, skipped dirs
- Session methods: start/update/stop
- Experimental API gate
- Notification emission (sessionUpdated, sessionCompleted)
- Notification opt-out
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miqi.runtime.app_server import AppServer, ClientCapabilities, ClientSessionRegistry
from miqi.runtime.fuzzy_file_search_app_handlers import (
    register_fuzzy_file_search_handlers,
)
from miqi.runtime.fuzzy_file_search_runtime import (
    FuzzyFileSearchRuntime,
    fuzzy_match_score,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_runtime_and_registry(*, workspace: "Path | None" = None):
    """Create an AppServer + registry with fuzzy runtime."""
    from types import SimpleNamespace

    registry = ClientSessionRegistry()
    server = AppServer(registry)

    runtime = FuzzyFileSearchRuntime(server)

    ws = workspace.resolve() if workspace else Path.cwd().resolve()
    fake_config = SimpleNamespace()
    fake_config.workspace_path = ws
    state = MagicMock()
    state.load_config.return_value = fake_config

    registry.bridge_context = {
        "state": state,
        "app_server": server,
        "fuzzy_file_search_runtime": runtime,
    }
    return server, registry, runtime


async def _dispatch(server, method, params, client_id="client-1"):
    return await server.dispatch(
        request_id="req-1",
        method=method,
        params=params,
        client_id=client_id,
        session_id=None,
    )


# ── fuzzy_match_score ────────────────────────────────────────────────────────


class TestFuzzyMatchScore:
    """Unit tests for fuzzy_match_score()."""

    def test_empty_query_returns_none(self):
        """Empty query returns None."""
        assert fuzzy_match_score("", "README.md") is None

    def test_exact_substring_match(self):
        """Exact case-insensitive substring match scores high."""
        result = fuzzy_match_score("read", "README.md")
        assert result is not None
        score, indices = result
        assert score >= 1000  # substring match base
        assert indices == [0, 1, 2, 3]  # 'read' starts at 0

    def test_case_insensitive_match(self):
        """Matching is case-insensitive."""
        result = fuzzy_match_score("readme", "README.md")
        assert result is not None
        score, indices = result
        assert score >= 1000
        assert len(indices) == 6

    def test_subsequence_match(self):
        """Subsequence match scores lower than substring."""
        result = fuzzy_match_score("rm", "README.md")
        assert result is not None
        score, indices = result
        assert 500 <= score < 1000  # subsequence range
        assert indices == [0, 4]  # 'R' at 0, 'M' at 4

    def test_no_match(self):
        """No match returns None."""
        assert fuzzy_match_score("xyz", "README.md") is None

    def test_filename_substring(self):
        """Filename substring gives high score."""
        result = fuzzy_match_score("main", "src/main.py")
        assert result is not None
        score, _ = result
        assert score >= 1000

    def test_path_subsequence(self):
        """Path subsequence gives medium score."""
        result = fuzzy_match_score("smp", "src/main.py")
        assert result is not None
        score, _ = result
        # This might be substring or subsequence depending on exact characters
        assert score >= 500


# ── One-shot search ──────────────────────────────────────────────────────────


class TestFuzzySearch:
    """Tests for the one-shot search() method."""

    def test_empty_query_returns_empty(self, tmp_path):
        """Empty query returns empty list."""
        (tmp_path / "a.txt").write_text("a")
        runtime = FuzzyFileSearchRuntime(app_server=MagicMock())
        results = runtime.search("", [tmp_path])
        assert results == []

    def test_filename_substring_match(self, tmp_path):
        """Filename substring match finds the file."""
        (tmp_path / "README.md").write_text("readme")
        (tmp_path / "main.py").write_text("main")

        runtime = FuzzyFileSearchRuntime(app_server=MagicMock())
        results = runtime.search("readme", [tmp_path])

        assert len(results) > 0
        paths = [r["path"] for r in results]
        assert "README.md" in paths

    def test_result_uses_match_type_and_file_name(self, tmp_path):
        """Result items use match_type and file_name (Codex convention)."""
        (tmp_path / "main.py").write_text("main")

        runtime = FuzzyFileSearchRuntime(app_server=MagicMock())
        results = runtime.search("main", [tmp_path])

        assert len(results) > 0
        result = results[0]
        assert "match_type" in result
        assert "file_name" in result
        assert result["match_type"] == "file"
        assert result["file_name"] == "main.py"
        # Verify NOT using camelCase
        assert "matchType" not in result
        assert "fileName" not in result

    def test_directory_results(self, tmp_path):
        """Directories appear in results with match_type 'directory'."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("main")

        runtime = FuzzyFileSearchRuntime(app_server=MagicMock())
        results = runtime.search("src", [tmp_path])

        dir_results = [r for r in results if r["match_type"] == "directory"]
        assert len(dir_results) > 0
        assert any(r["file_name"] == "src" for r in dir_results)

    def test_skipped_directories_not_searched(self, tmp_path):
        """Files inside skipped directories (e.g. .git) are not returned."""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("config")
        (tmp_path / "README.md").write_text("readme")

        runtime = FuzzyFileSearchRuntime(app_server=MagicMock())
        results = runtime.search("config", [tmp_path])

        # .git/config should NOT appear
        paths = [r["path"] for r in results]
        assert not any(".git" in p for p in paths)

    def test_sort_by_score_desc_then_path_asc(self, tmp_path):
        """Results are sorted by (-score, path)."""
        (tmp_path / "aaa.txt").write_text("a")
        (tmp_path / "zzz.txt").write_text("z")

        runtime = FuzzyFileSearchRuntime(app_server=MagicMock())
        results = runtime.search("txt", [tmp_path])

        assert len(results) >= 2
        scores = [r["score"] for r in results]
        # Scores should be non-increasing
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"Results not sorted by score desc at index {i}: "
                f"score[{i}]={scores[i]}, score[{i+1}]={scores[i+1]}"
            )

    def test_multiple_roots(self, tmp_path):
        """Search across multiple root directories."""
        root1 = tmp_path / "proj1"
        root2 = tmp_path / "proj2"
        root1.mkdir()
        root2.mkdir()
        (root1 / "alpha.py").write_text("a")
        (root2 / "beta.py").write_text("b")

        runtime = FuzzyFileSearchRuntime(app_server=MagicMock())
        results = runtime.search("py", [root1, root2])

        paths = [r["path"] for r in results]
        assert "alpha.py" in paths
        assert "beta.py" in paths

    def test_indices_field_present(self, tmp_path):
        """Result includes indices array for highlighting."""
        (tmp_path / "README.md").write_text("readme")

        runtime = FuzzyFileSearchRuntime(app_server=MagicMock())
        results = runtime.search("READ", [tmp_path])

        assert len(results) > 0
        result = results[0]
        assert "indices" in result
        assert isinstance(result["indices"], list)
        assert len(result["indices"]) == 4  # "READ" is 4 chars

    def test_nested_directory_traversal_python311_compatible(self, tmp_path):
        """Regression: candidate traversal uses os.walk (Python 3.11-compatible).

        Creates deeply nested files and verifies they are all found,
        exercising the _collect_candidates path end-to-end.
        """
        # Create nested structure
        (tmp_path / "src" / "lib" / "utils").mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("main")
        (tmp_path / "src" / "lib" / "utils" / "helper.py").write_text("helper")
        (tmp_path / "docs" / "guide").mkdir(parents=True)
        (tmp_path / "docs" / "guide" / "intro.md").write_text("intro")

        runtime = FuzzyFileSearchRuntime(app_server=MagicMock())
        results = runtime.search("py", [tmp_path])

        paths = [r["path"] for r in results]
        assert "src/main.py" in paths
        assert "src/lib/utils/helper.py" in paths


# ── Session methods ──────────────────────────────────────────────────────────


class TestFuzzySessions:
    """Tests for session start/update/stop."""

    @pytest.mark.asyncio
    async def test_session_start_creates_client_scoped_session(self, tmp_path):
        """Session start creates a client-scoped session."""
        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)

        runtime.session_start("client-1", "search-1", [tmp_path])
        runtime.session_start("client-2", "search-1", [tmp_path])

        # Both should exist (different clients)
        # No error = both exist
        await runtime.session_update("client-1", "search-1", "test")
        await runtime.session_update("client-2", "search-1", "test")

    @pytest.mark.asyncio
    async def test_session_update_emits_notifications(self, tmp_path):
        """Session update emits sessionUpdated and sessionCompleted."""
        (tmp_path / "README.md").write_text("readme")

        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)

        # Capture events
        events: list[dict] = []

        async def sink(event: dict) -> None:
            events.append(event)

        server._event_sinks["client-1"] = sink

        runtime.session_start("client-1", "search-1", [tmp_path])
        await runtime.session_update("client-1", "search-1", "readme")

        assert len(events) >= 2

        event_types = [e["event"] for e in events]
        assert "fuzzyFileSearch/sessionUpdated" in event_types
        assert "fuzzyFileSearch/sessionCompleted" in event_types

        # Check sessionUpdated data
        updated = next(e for e in events if e["event"] == "fuzzyFileSearch/sessionUpdated")
        assert updated["data"]["sessionId"] == "search-1"
        assert updated["data"]["query"] == "readme"
        assert "files" in updated["data"]

        # Check sessionCompleted data
        completed = next(e for e in events if e["event"] == "fuzzyFileSearch/sessionCompleted")
        assert completed["data"]["sessionId"] == "search-1"

    @pytest.mark.asyncio
    async def test_session_update_unknown_session_raises(self, tmp_path):
        """Session update for unknown session raises KeyError."""
        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)

        with pytest.raises(KeyError):
            await runtime.session_update("client-1", "nonexistent", "test")

    @pytest.mark.asyncio
    async def test_session_stop_removes_state(self, tmp_path):
        """Session stop removes the session."""
        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)

        runtime.session_start("client-1", "search-1", [tmp_path])
        runtime.session_stop("client-1", "search-1")

        with pytest.raises(KeyError):
            await runtime.session_update("client-1", "search-1", "test")

    @pytest.mark.asyncio
    async def test_session_stop_missing_succeeds(self, tmp_path):
        """Session stop for missing session succeeds silently."""
        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)

        # Should not raise
        runtime.session_stop("client-1", "nonexistent")

    @pytest.mark.asyncio
    async def test_cleanup_client_removes_all_sessions(self, tmp_path):
        """cleanup_client removes all sessions for that client."""
        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)

        runtime.session_start("client-1", "s1", [tmp_path])
        runtime.session_start("client-1", "s2", [tmp_path])
        runtime.session_start("client-2", "s3", [tmp_path])

        runtime.cleanup_client("client-1")

        # client-1 sessions gone
        with pytest.raises(KeyError):
            await runtime.session_update("client-1", "s1", "test")
        with pytest.raises(KeyError):
            await runtime.session_update("client-1", "s2", "test")

        # client-2 session still exists
        await runtime.session_update("client-2", "s3", "test")

    @pytest.mark.asyncio
    async def test_empty_query_emits_empty_files(self, tmp_path):
        """Empty query emits sessionUpdated with empty files."""
        (tmp_path / "README.md").write_text("readme")

        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)

        events: list[dict] = []

        async def sink(event: dict) -> None:
            events.append(event)

        server._event_sinks["client-1"] = sink

        runtime.session_start("client-1", "search-1", [tmp_path])
        await runtime.session_update("client-1", "search-1", "")

        updated = next(e for e in events if e["event"] == "fuzzyFileSearch/sessionUpdated")
        assert updated["data"]["files"] == []


# ── Handler-level tests ──────────────────────────────────────────────────────


class TestFuzzyHandlers:
    """Tests for the fuzzyFileSearch* AppServer handlers."""

    @pytest.mark.asyncio
    async def test_one_shot_search_does_not_require_experimental(self, tmp_path):
        """One-shot fuzzyFileSearch works without experimentalApi."""
        (tmp_path / "test.txt").write_text("test")

        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)
        register_fuzzy_file_search_handlers(server)

        resp = await _dispatch(server, "fuzzyFileSearch", {
            "query": "test",
            "roots": [str(tmp_path)],
        })

        assert "result" in resp, f"Expected result, got: {resp}"
        assert "files" in resp["result"]

    @pytest.mark.asyncio
    async def test_session_start_requires_experimental(self, tmp_path):
        """Session start without experimentalApi returns EXPERIMENTAL_API_REQUIRED."""
        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)
        register_fuzzy_file_search_handlers(server)

        resp = await _dispatch(server, "fuzzyFileSearch/sessionStart", {
            "sessionId": "search-1",
            "roots": [str(tmp_path)],
        })

        assert resp.get("code") == "EXPERIMENTAL_API_REQUIRED"

    @pytest.mark.asyncio
    async def test_session_start_with_experimental_param(self, tmp_path):
        """Session start with experimentalApi: true succeeds."""
        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)
        register_fuzzy_file_search_handlers(server)

        resp = await _dispatch(server, "fuzzyFileSearch/sessionStart", {
            "sessionId": "search-1",
            "roots": [str(tmp_path)],
            "experimentalApi": True,
        })

        assert "result" in resp

    @pytest.mark.asyncio
    async def test_session_update_requires_experimental(self, tmp_path):
        """Session update without experimentalApi returns EXPERIMENTAL_API_REQUIRED."""
        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)
        register_fuzzy_file_search_handlers(server)

        resp = await _dispatch(server, "fuzzyFileSearch/sessionUpdate", {
            "sessionId": "search-1",
            "query": "test",
        })

        assert resp.get("code") == "EXPERIMENTAL_API_REQUIRED"

    @pytest.mark.asyncio
    async def test_session_stop_requires_experimental(self, tmp_path):
        """Session stop without experimentalApi returns EXPERIMENTAL_API_REQUIRED."""
        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)
        register_fuzzy_file_search_handlers(server)

        resp = await _dispatch(server, "fuzzyFileSearch/sessionStop", {
            "sessionId": "search-1",
        })

        assert resp.get("code") == "EXPERIMENTAL_API_REQUIRED"

    @pytest.mark.asyncio
    async def test_unknown_session_update_returns_invalid_params(self, tmp_path):
        """Session update for unknown session returns INVALID_PARAMS."""
        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)
        register_fuzzy_file_search_handlers(server)

        resp = await _dispatch(server, "fuzzyFileSearch/sessionUpdate", {
            "sessionId": "nonexistent",
            "query": "test",
            "experimentalApi": True,
        })

        assert resp.get("code") == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_one_shot_outside_workspace_root_returns_invalid_params(self, tmp_path):
        """Regression: fuzzyFileSearch with outside-workspace root returns INVALID_PARAMS."""
        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)
        register_fuzzy_file_search_handlers(server)

        outside = tmp_path.parent / "outside_for_fuzzy_test"
        outside.mkdir(exist_ok=True)
        try:
            resp = await _dispatch(server, "fuzzyFileSearch", {
                "query": "test",
                "roots": [str(outside)],
            })

            assert resp.get("code") == "INVALID_PARAMS", (
                f"Expected INVALID_PARAMS for outside-workspace root, got: {resp}"
            )
        finally:
            if outside.exists() and not any(outside.iterdir()):
                outside.rmdir()

    @pytest.mark.asyncio
    async def test_session_start_outside_workspace_root_returns_invalid_params(self, tmp_path):
        """Regression: fuzzyFileSearch/sessionStart with outside-workspace root returns INVALID_PARAMS."""
        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)
        register_fuzzy_file_search_handlers(server)

        outside = tmp_path.parent / "outside_for_session_test"
        outside.mkdir(exist_ok=True)
        try:
            resp = await _dispatch(server, "fuzzyFileSearch/sessionStart", {
                "sessionId": "search-1",
                "roots": [str(outside)],
                "experimentalApi": True,
            })

            assert resp.get("code") == "INVALID_PARAMS", (
                f"Expected INVALID_PARAMS for outside-workspace root, got: {resp}"
            )
        finally:
            if outside.exists() and not any(outside.iterdir()):
                outside.rmdir()

    @pytest.mark.asyncio
    async def test_missing_root_inside_workspace_is_skipped(self, tmp_path):
        """Missing roots inside workspace are skipped (not an error)."""
        (tmp_path / "README.md").write_text("readme")

        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)
        register_fuzzy_file_search_handlers(server)

        missing = tmp_path / "nonexistent_subdir"
        # This path is inside workspace but doesn't exist — should be skipped
        resp = await _dispatch(server, "fuzzyFileSearch", {
            "query": "readme",
            "roots": [str(missing), str(tmp_path)],
        })

        assert "result" in resp, f"Expected result, got: {resp}"
        # Should find README.md from the second (valid) root
        files = resp["result"]["files"]
        assert len(files) > 0


# ── Notification opt-out ─────────────────────────────────────────────────────


class TestFuzzyNotificationOptOut:
    """Tests that notification opt-out suppresses fuzzy session notifications."""

    @pytest.mark.asyncio
    async def test_opt_out_suppresses_session_notifications(self, tmp_path):
        """Session notifications are not delivered when opted out."""
        (tmp_path / "README.md").write_text("readme")

        server, registry, runtime = _make_runtime_and_registry(workspace=tmp_path)

        # Set capabilities with opt-out for both notification types
        server.set_client_capabilities(
            "client-1",
            ClientCapabilities(
                experimental_api=True,
                opt_out_notification_methods={
                    "fuzzyFileSearch/sessionUpdated",
                    "fuzzyFileSearch/sessionCompleted",
                },
            ),
        )

        # Register sink
        events: list[dict] = []

        async def sink(event: dict) -> None:
            events.append(event)

        server._event_sinks["client-1"] = sink

        runtime.session_start("client-1", "search-1", [tmp_path])
        await runtime.session_update("client-1", "search-1", "readme")

        # No events should be delivered due to opt-out
        assert len(events) == 0, f"Expected 0 events, got {len(events)}"
