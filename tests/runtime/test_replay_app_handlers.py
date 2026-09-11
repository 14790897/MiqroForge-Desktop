"""Tests for replay/debug AppServer handlers — live-first/stored-fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from miqi.config.schema import Config
from miqi.runtime.app_server import AppServer, ClientSessionRegistry
from miqi.runtime.ledger_runtime import LedgerRuntime
from miqi.runtime.replay_app_handlers import register_replay_handlers
from miqi.runtime.thread_runtime import ThreadRuntime


def _server(tmp_path):
    registry = ClientSessionRegistry()
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    state = MagicMock()
    state.load_config.return_value = cfg
    registry.bridge_context["state"] = state
    server = AppServer(registry)
    register_replay_handlers(server)
    return server


async def _seed(db_path, *, session_id="client-a:default", thread_id="thread-1"):
    threads = ThreadRuntime(db_path, session_id=session_id)
    ledger = LedgerRuntime(db_path, session_id=session_id)
    await threads.initialize()
    await ledger.initialize()
    try:
        await threads.create_thread(title="Replay", thread_id=thread_id)
        await ledger.append_item(thread_id=thread_id, turn_id="turn-1", item_type="turn_started")
        await ledger.append_item(thread_id=thread_id, turn_id="turn-1", item_type="message", role="user", content="hello")
        await ledger.append_item(thread_id=thread_id, turn_id="turn-1", item_type="assistant_delta", content="hi")
        await ledger.append_item(thread_id=thread_id, turn_id="turn-1", item_type="turn_completed")
    finally:
        await ledger.close()
        await threads.close()


@pytest.mark.asyncio
async def test_legacy_replay_turns_works_without_live_session(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed(db)
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "replay.turns", {"thread_id": "thread-1"}, "client-a", None,
    )

    assert response["result"]["turns"] == ["turn-1"]


@pytest.mark.asyncio
async def test_debug_replay_thread_returns_document(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed(db)
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "debug/replay/thread",
        {"threadId": "thread-1", "includeRawLedger": True},
        "client-a",
        None,
    )

    doc = response["result"]["document"]
    assert doc["threadId"] == "thread-1"
    assert doc["documentHash"].startswith("sha256:")
    assert doc["rawLedgerItems"]


@pytest.mark.asyncio
async def test_debug_replay_messages_reports_history_ledger_matches_when_consistent(tmp_path):
    """debug/replay/messages reports matches=True when actual history matches ledger."""
    import time

    from miqi.runtime.history_runtime import HistoryItem
    from miqi.runtime.stored_runtime import StoredRuntimeReader

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed(db)
    # Seed history matching the ledger message from _seed (user "hello")
    reader = StoredRuntimeReader(db, client_id="client-a")
    await reader._write_history_items(
        "client-a:default", "thread-1",
        [HistoryItem("h1", "thread-1", "turn-1", "user", "hello", {}, time.time())],
    )
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "debug/replay/messages", {"threadId": "thread-1"}, "client-a", None,
    )

    result = response["result"]
    assert result["threadId"] == "thread-1"
    assert result["matches"] is True


@pytest.mark.asyncio
async def test_debug_replay_rejects_foreign_session(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed(db, session_id="client-b:default", thread_id="foreign")
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "debug/replay/thread",
        {"threadId": "foreign", "sessionId": "client-b:default"},
        "client-a",
        None,
    )

    assert response["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_debug_replay_diff_compares_documents(tmp_path):
    server = _server(tmp_path)
    left = {"version": 1, "threadId": "a", "turns": [], "providerMessages": []}
    right = {"version": 1, "threadId": "b", "turns": [], "providerMessages": []}

    response = await server.dispatch(
        "1", "debug/replay/diff",
        {"leftDocument": left, "rightDocument": right},
        "client-a",
        None,
    )

    assert response["result"]["diff"]["sameHash"] is False


@pytest.mark.asyncio
async def test_debug_replay_missing_thread_id_rejected(tmp_path):
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "debug/replay/thread", {}, "client-a", None,
    )

    assert response["code"] == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_debug_replay_unknown_thread_not_found(tmp_path):
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "debug/replay/thread", {"threadId": "missing"}, "client-a", None,
    )

    assert response["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_debug_replay_diff_requires_documents(tmp_path):
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "debug/replay/diff", {"leftDocument": {}}, "client-a", None,
    )

    assert response["code"] == "INVALID_PARAMS"


# ── debug/replay/turn metadata shape ─────────────────────────────────────


@pytest.mark.asyncio
async def test_debug_replay_turn_returns_metadata_shape(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed(db)
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "debug/replay/turn",
        {"threadId": "thread-1", "turnId": "turn-1"},
        "client-a", None,
    )

    result = response["result"]
    assert result["threadId"] == "thread-1"
    assert result["turnId"] == "turn-1"
    assert result["sessionId"] == "client-a:default"
    assert result["source"] == "stored"
    assert result["timeline"]["turn_id"] == "turn-1"
    assert result["rawLedgerItems"] == []


@pytest.mark.asyncio
async def test_debug_replay_turn_include_raw_ledger(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed(db)
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "debug/replay/turn",
        {"threadId": "thread-1", "turnId": "turn-1", "includeRawLedger": True},
        "client-a", None,
    )

    result = response["result"]
    assert len(result["rawLedgerItems"]) > 0
    assert all("itemType" in item for item in result["rawLedgerItems"])


@pytest.mark.asyncio
async def test_debug_replay_turn_include_raw_ledger_false_returns_empty(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed(db)
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "debug/replay/turn",
        {"threadId": "thread-1", "turnId": "turn-1", "includeRawLedger": False},
        "client-a", None,
    )

    assert response["result"]["rawLedgerItems"] == []


@pytest.mark.asyncio
async def test_debug_replay_turn_unknown_turn_null_timeline_with_metadata(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed(db)
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "debug/replay/turn",
        {"threadId": "thread-1", "turnId": "nonexistent"},
        "client-a", None,
    )

    result = response["result"]
    assert result["threadId"] == "thread-1"
    assert result["turnId"] == "nonexistent"
    assert result["sessionId"] == "client-a:default"
    assert result["source"] == "stored"
    assert result["timeline"] is None
    assert result["rawLedgerItems"] == []


@pytest.mark.asyncio
async def test_debug_replay_turn_foreign_session_unauthorized(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed(db, session_id="client-b:default", thread_id="foreign")
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "debug/replay/turn",
        {"threadId": "foreign", "turnId": "turn-1", "sessionId": "client-b:default"},
        "client-a", None,
    )

    assert response["code"] == "UNAUTHORIZED"


# ── debug/replay/messages and integrity: history-empty detection ──────────


@pytest.mark.asyncio
async def test_debug_replay_messages_reports_mismatch_when_history_empty(tmp_path):
    """When runtime_history_items is empty but ledger has messages,
    debug/replay/messages must report historyMessages=[], matches=False."""
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed(db)  # ledger has message, history is empty
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "debug/replay/messages", {"threadId": "thread-1"}, "client-a", None,
    )

    result = response["result"]
    assert result["historyMessages"] == []
    assert len(result["ledgerMessages"]) >= 1
    assert result["matches"] is False


@pytest.mark.asyncio
async def test_debug_replay_integrity_fails_when_history_empty_ledger_nonempty(tmp_path):
    """debug/replay/integrity must report ok=False when history is empty
    but ledger has messages."""
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed(db)  # ledger has message, history is empty
    server = _server(tmp_path)

    response = await server.dispatch(
        "1", "debug/replay/integrity", {"threadId": "thread-1"}, "client-a", None,
    )

    integrity = response["result"]["integrity"]
    assert integrity["ok"] is False
    prov_check = next(
        check for check in integrity["checks"]
        if check["name"] == "providerHistoryMatchesLedger"
    )
    assert prov_check["ok"] is False
