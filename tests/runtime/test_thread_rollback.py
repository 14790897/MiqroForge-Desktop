"""Tests for Codex-style thread rollback semantics."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.runtime.app_server import AppServer, ClientSessionRegistry
from miqi.runtime.thread_app_handlers import register_codex_thread_handlers


def _make_mock_session_with_real_runtimes(session_id: str, db_base: Path):
    """Create a mock session with real ThreadRuntime/LedgerRuntime/ReplayRuntime."""
    from miqi.runtime.history_runtime import HistoryRuntime
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_runtime import ReplayRuntime
    from miqi.runtime.thread_runtime import ThreadRuntime

    db_path = db_base / "runtime.db"
    threads = ThreadRuntime(db_path, session_id=session_id)
    ledger = LedgerRuntime(db_path, session_id=session_id)
    replay = ReplayRuntime(ledger)
    history = HistoryRuntime(db_path, session_id=session_id)

    session = MagicMock()
    session.session_id = session_id
    session.services = MagicMock()
    session.services.thread_runtime = threads
    session.services.ledger_runtime = ledger
    session.services.replay_runtime = replay
    session.services.history_runtime = history
    session.stop = AsyncMock()

    return session


@pytest.mark.asyncio
async def test_thread_rollback_removes_last_turn_from_read(tmp_path):
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_thread_rollback_removes_last_turn_from_read")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()
    await session.services.history_runtime.initialize()

    await session.services.thread_runtime.create_thread(
        title="RB", thread_id="thread-rb",
    )
    await session.services.ledger_runtime.append_item(
        thread_id="thread-rb", turn_id="turn-1",
        item_type="turn_started",
    )
    await session.services.ledger_runtime.append_item(
        thread_id="thread-rb", turn_id="turn-1",
        item_type="message", role="user", content="one",
    )
    await session.services.ledger_runtime.append_item(
        thread_id="thread-rb", turn_id="turn-1",
        item_type="turn_completed",
    )
    await session.services.ledger_runtime.append_item(
        thread_id="thread-rb", turn_id="turn-2",
        item_type="turn_started",
    )
    await session.services.ledger_runtime.append_item(
        thread_id="thread-rb", turn_id="turn-2",
        item_type="message", role="user", content="two",
    )
    await session.services.ledger_runtime.append_item(
        thread_id="thread-rb", turn_id="turn-2",
        item_type="turn_completed",
    )

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    response = await server.dispatch(
        "req-1", "thread/rollback",
        {"threadId": "thread-rb", "dropLastTurns": 1},
        "client-1", session_id,
    )
    assert "result" in response, f"Expected result but got: {response}"
    turns = response["result"]["thread"]["turns"]
    assert [turn["id"] for turn in turns] == ["turn-1"], f"Got turns: {[t['id'] for t in turns]}"

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await session.services.history_runtime.close()
    await registry.stop_all()


@pytest.mark.asyncio
async def test_thread_rollback_needs_valid_session():
    registry = ClientSessionRegistry()
    server = AppServer(registry)
    register_codex_thread_handlers(server)
    response = await server.dispatch(
        "req-1", "thread/rollback",
        {"threadId": "t", "dropLastTurns": 1},
        "client-1", "nonexistent",
    )
    assert response["code"] == "UNAUTHORIZED"
    await registry.stop_all()
