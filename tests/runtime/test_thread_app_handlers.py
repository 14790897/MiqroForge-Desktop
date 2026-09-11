"""Tests for Codex-style thread AppServer handlers."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.runtime.app_server import AppServer, ClientSessionRegistry
from miqi.runtime.thread_app_handlers import register_codex_thread_handlers

# ── helpers ─────────────────────────────────────────────────────────────


def _make_mock_session(session_id: str):
    """Create a mock RuntimeSession with thread/ledger/replay services."""

    session = MagicMock()
    session.session_id = session_id

    session.services = MagicMock()
    session.services.session_id = session_id
    session.stop = AsyncMock()
    session.stop_all = AsyncMock()

    return session


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


# ── thread/start ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_start_creates_thread(tmp_path):
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_thread_start_creates_thread")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()

    registry = ClientSessionRegistry()
    # Manually insert session to bypass create_session
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    response = await server.dispatch(
        "req-1", "thread/start",
        {"title": "Hello", "threadId": "thread-test-1"},
        "client-1", session_id,
    )

    assert "result" in response, f"Expected result but got: {response}"
    thread = response["result"]["thread"]
    assert thread["name"] == "Hello"
    assert thread["status"]["type"] == "idle"
    assert thread["id"] == "thread-test-1"

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await registry.stop_all()


# ── thread/read ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_read_without_turns(tmp_path):
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_thread_read_without_turns")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()

    await session.services.thread_runtime.create_thread(
        title="Read", thread_id="thread-read",
    )

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    response = await server.dispatch(
        "req-1", "thread/read",
        {"threadId": "thread-read"},
        "client-1", session_id,
    )
    assert response["result"]["thread"]["id"] == "thread-read"
    assert response["result"]["thread"]["turns"] == []

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await registry.stop_all()


# ── thread/turns/items/list ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_turns_items_list_is_unsupported():
    registry = ClientSessionRegistry()
    server = AppServer(registry)
    register_codex_thread_handlers(server)
    response = await server.dispatch(
        "req-1", "thread/turns/items/list",
        {"threadId": "t", "turnId": "x"},
        "client-1", None,
    )
    assert response["code"] == "UNSUPPORTED_METHOD"
    await registry.stop_all()


# ── thread/name/set ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_name_set_renames(tmp_path):
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_thread_name_set_renames")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()

    await session.services.thread_runtime.create_thread(
        title="Old", thread_id="thread-rename-me",
    )

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    response = await server.dispatch(
        "req-1", "thread/name/set",
        {"threadId": "thread-rename-me", "name": "New Name"},
        "client-1", session_id,
    )
    assert response["result"]["thread"]["name"] == "New Name"

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await registry.stop_all()


# ── thread/resume ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_resume_reads_existing_thread(tmp_path):
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_thread_resume_reads_existing_thread")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()

    await session.services.thread_runtime.create_thread(
        title="Resumeable", thread_id="thread-resume-1",
    )

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    response = await server.dispatch(
        "req-1", "thread/resume",
        {"threadId": "thread-resume-1"},
        "client-1", session_id,
    )
    assert response["result"]["thread"]["name"] == "Resumeable"

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await registry.stop_all()


# ── thread/loaded/list ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_loaded_list_returns_client_threads(tmp_path):
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_thread_loaded_list_returns_client_threads")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()

    await session.services.thread_runtime.create_thread(
        title="Loaded", thread_id="loaded-1",
    )

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    response = await server.dispatch(
        "req-1", "thread/loaded/list", {},
        "client-1", None,
    )
    assert "loaded-1" in response["result"]["threadIds"]

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await registry.stop_all()


# ── security: unauthorized access ────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_read_unauthorized():
    registry = ClientSessionRegistry()
    server = AppServer(registry)
    register_codex_thread_handlers(server)
    response = await server.dispatch(
        "req-1", "thread/read",
        {"threadId": "nonexistent"},
        "client-1", "client-1:default",
    )
    assert response["code"] == "UNAUTHORIZED"
    await registry.stop_all()


# ── Phase 36.8: fork copies provider history ────────────────────────────


@pytest.mark.asyncio
async def test_thread_fork_copies_provider_messages(tmp_path):
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_thread_fork_copies_provider_messages")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()
    await session.services.history_runtime.initialize()

    await session.services.thread_runtime.create_thread(
        title="Source", thread_id="source",
    )
    # Add history (provider-visible) messages
    await session.services.history_runtime.append_message(
        thread_id="source", turn_id="turn-1", role="user", content="hello"
    )
    # Add ledger items
    await session.services.ledger_runtime.append_item(
        thread_id="source", turn_id="turn-1",
        item_type="message", role="user", content="hello",
    )

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    forked = await server.dispatch(
        "req-1", "thread/fork",
        {"threadId": "source", "title": "Child"},
        "client-1", session_id,
    )
    child_id = forked["result"]["thread"]["id"]
    messages = await session.services.history_runtime.load_messages(child_id)
    assert messages == [{"role": "user", "content": "hello"}]

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await session.services.history_runtime.close()
    await registry.stop_all()


# ── Phase 36.9: thread notifications ───────────────────────────────────


@pytest.mark.asyncio
async def test_thread_start_emits_thread_started(tmp_path):
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_thread_start_emits_thread_started")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    events = []
    async def event_sink(envelope):
        events.append(envelope)
    server.set_event_sink("client-1", event_sink)

    await server.dispatch(
        "req-1", "thread/start",
        {"threadId": "notify-1", "title": "Notify"},
        "client-1", session_id,
    )
    assert any(e.get("event") == "thread/started" for e in events), (
        f"Expected thread/started event in {events}"
    )

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await registry.stop_all()


@pytest.mark.asyncio
async def test_thread_name_set_emits_name_updated(tmp_path):
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_thread_name_set_emits_name_updated")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()
    await session.services.thread_runtime.create_thread(
        title="Old", thread_id="t1",
    )

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    events = []
    async def event_sink(envelope):
        events.append(envelope)
    server.set_event_sink("client-1", event_sink)
    server.subscribe("client-1", session_id)  # ensure client is subscribed

    await server.dispatch(
        "req-1", "thread/name/set",
        {"threadId": "t1", "name": "New"},
        "client-1", session_id,
    )
    assert any(e.get("event") == "thread/name/updated" for e in events), (
        f"Expected thread/name/updated event in {events}"
    )

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await registry.stop_all()


@pytest.mark.asyncio
async def test_thread_rollback_emits_rollback_event(tmp_path):
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_thread_rollback_emits")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()
    await session.services.history_runtime.initialize()

    await session.services.thread_runtime.create_thread(title="RB", thread_id="thread-rb")
    await session.services.ledger_runtime.append_item(
        thread_id="thread-rb", turn_id="turn-1",
        item_type="turn_started",
    )
    await session.services.ledger_runtime.append_item(
        thread_id="thread-rb", turn_id="turn-1",
        item_type="turn_completed",
    )

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    events = []
    async def event_sink(envelope):
        events.append(envelope)
    server.set_event_sink("client-1", event_sink)
    server.subscribe("client-1", session_id)

    await server.dispatch(
        "req-1", "thread/rollback",
        {"threadId": "thread-rb", "dropLastTurns": 1},
        "client-1", session_id,
    )
    assert any(e.get("event") == "thread/rollback" for e in events), (
        f"Expected thread/rollback event in {events}"
    )

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await session.services.history_runtime.close()
    await registry.stop_all()


# ── Phase 36 hardening: real registry integration tests ───────────────────


@pytest.mark.asyncio
async def test_real_create_session_no_double_namespace(tmp_path):
    """_get_or_create_session must produce 'client-1:default', not 'client-1:client-1:default'."""
    from unittest.mock import AsyncMock, MagicMock, patch

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    from miqi.config.schema import Config
    config = Config()
    config.agents.defaults.workspace = str(workspace)

    mock_provider = AsyncMock()
    mock_provider.chat = AsyncMock(return_value=MagicMock(content="ok"))

    class _MockBridgeState:
        def load_config(self):
            return config

    registry = ClientSessionRegistry()
    registry.bridge_context = {"state": _MockBridgeState()}

    with patch("miqi.providers.factory.make_provider", return_value=mock_provider):
        server = AppServer(registry)
        register_codex_thread_handlers(server)

        response = await server.dispatch(
            "req-1", "thread/start",
            {"threadId": "t1", "title": "Hello"},
            "client-1", None,
        )

    assert "result" in response, f"Expected result but got: {response}"
    session_keys = list(registry._sessions.keys())
    assert "client-1:default" in session_keys, (
        f"Expected 'client-1:default' in sessions but got: {session_keys}"
    )
    assert "client-1:client-1:default" not in session_keys, (
        f"Double namespace detected! sessions keys: {session_keys}"
    )
    assert response["result"]["thread"]["sessionId"] == "client-1:default"

    await registry.stop_all()


@pytest.mark.asyncio
async def test_create_session_with_explicit_session_key(tmp_path):
    """When params has sessionKey='project-a', registry key must be 'client-1:project-a'."""
    from unittest.mock import AsyncMock, MagicMock, patch

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    from miqi.config.schema import Config
    config = Config()
    config.agents.defaults.workspace = str(workspace)

    mock_provider = AsyncMock()
    mock_provider.chat = AsyncMock(return_value=MagicMock(content="ok"))

    class _MockBridgeState:
        def load_config(self):
            return config

    registry = ClientSessionRegistry()
    registry.bridge_context = {"state": _MockBridgeState()}

    with patch("miqi.providers.factory.make_provider", return_value=mock_provider):
        server = AppServer(registry)
        register_codex_thread_handlers(server)

        response = await server.dispatch(
            "req-1", "thread/start",
            {"threadId": "t2", "title": "Project A", "sessionKey": "project-a"},
            "client-1", None,
        )

    assert "result" in response, f"Expected result but got: {response}"
    session_keys = list(registry._sessions.keys())
    assert "client-1:project-a" in session_keys, (
        f"Expected 'client-1:project-a' in sessions but got: {session_keys}"
    )
    assert response["result"]["thread"]["sessionId"] == "client-1:project-a"

    await registry.stop_all()


@pytest.mark.asyncio
async def test_create_session_with_namespaced_session_key_does_not_double_namespace(tmp_path):
    """When params has sessionKey='client-1:project-a' (already namespaced),
    _get_or_create_session must strip the client_id prefix and produce
    'client-1:project-a', NOT 'client-1:client-1:project-a'."""
    from unittest.mock import AsyncMock, MagicMock, patch

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    from miqi.config.schema import Config
    config = Config()
    config.agents.defaults.workspace = str(workspace)

    mock_provider = AsyncMock()
    mock_provider.chat = AsyncMock(return_value=MagicMock(content="ok"))

    class _MockBridgeState:
        def load_config(self):
            return config

    registry = ClientSessionRegistry()
    registry.bridge_context = {"state": _MockBridgeState()}

    with patch("miqi.providers.factory.make_provider", return_value=mock_provider):
        server = AppServer(registry)
        register_codex_thread_handlers(server)

        # sessionKey is already namespaced — simulate a Codex client that
        # sends the full session ID as the key
        response = await server.dispatch(
            "req-1", "thread/start",
            {"threadId": "t3", "title": "Namespaced Key", "sessionKey": "client-1:project-a"},
            "client-1", None,
        )

    assert "result" in response, f"Expected result but got: {response}"
    session_keys = list(registry._sessions.keys())
    # Must NOT produce double namespace
    assert "client-1:client-1:project-a" not in session_keys, (
        f"Double namespace detected! sessions keys: {session_keys}"
    )
    assert "client-1:project-a" in session_keys, (
        f"Expected 'client-1:project-a' in sessions but got: {session_keys}"
    )
    assert response["result"]["thread"]["sessionId"] == "client-1:project-a"

    await registry.stop_all()


@pytest.mark.asyncio
async def test_dispatch_session_id_respected_over_params(tmp_path):
    """When dispatch passes session_id='client-1:project-a', the handler
    must use that session even when params has no sessionKey."""
    session_id = "client-1:project-a"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_dispatch_session_id_respected")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    # dispatch with explicit session_id; params do NOT carry sessionKey
    response = await server.dispatch(
        "req-1", "thread/start",
        {"threadId": "thread-in-project-a", "title": "In Project A"},
        "client-1", session_id,
    )

    assert "result" in response, f"Expected result but got: {response}"
    # Must have used the existing session, not created a new one
    assert response["result"]["thread"]["sessionId"] == "client-1:project-a"
    assert len(registry._sessions) == 1, (
        f"Expected 1 session, got {len(registry._sessions)}: {list(registry._sessions.keys())}"
    )
    assert "client-1:project-a" in registry._sessions

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await registry.stop_all()


@pytest.mark.asyncio
async def test_thread_start_then_read_with_session_id(tmp_path):
    """After thread/start, thread/read using the returned sessionId must work."""
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_thread_start_then_read")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    # Step 1: create thread
    start_resp = await server.dispatch(
        "req-1", "thread/start",
        {"threadId": "read-back", "title": "Read Back"},
        "client-1", session_id,
    )
    assert "result" in start_resp
    returned_session_id = start_resp["result"]["thread"]["sessionId"]
    assert returned_session_id == session_id

    # Step 2: read the same thread using the returned sessionId
    read_resp = await server.dispatch(
        "req-2", "thread/read",
        {"threadId": "read-back"},
        "client-1", returned_session_id,
    )
    assert "result" in read_resp, f"Expected result but got: {read_resp}"
    assert read_resp["result"]["thread"]["id"] == "read-back"
    assert read_resp["result"]["thread"]["name"] == "Read Back"

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await registry.stop_all()


@pytest.mark.asyncio
async def test_real_create_session_start_then_read_back(tmp_path):
    """End-to-end: thread/start via real create_session (session_id=None),
    then thread/read using the returned sessionId — no manual _sessions injection."""
    from unittest.mock import AsyncMock, MagicMock, patch

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    from miqi.config.schema import Config
    config = Config()
    config.agents.defaults.workspace = str(workspace)

    mock_provider = AsyncMock()
    mock_provider.chat = AsyncMock(return_value=MagicMock(content="ok"))

    class _MockBridgeState:
        def load_config(self):
            return config

    registry = ClientSessionRegistry()
    registry.bridge_context = {"state": _MockBridgeState()}

    with patch("miqi.providers.factory.make_provider", return_value=mock_provider):
        server = AppServer(registry)
        register_codex_thread_handlers(server)

        # Step 1: thread/start with session_id=None → real create_session path
        start_resp = await server.dispatch(
            "req-1", "thread/start",
            {"threadId": "e2e-thread", "title": "E2E Test", "sessionKey": "e2e-project"},
            "client-1", None,
        )

    assert "result" in start_resp, f"Expected result but got: {start_resp}"
    returned_session_id = start_resp["result"]["thread"]["sessionId"]
    assert returned_session_id == "client-1:e2e-project"
    assert "client-1:e2e-project" in registry._sessions, (
        f"Session not found in registry: {list(registry._sessions.keys())}"
    )

    # Step 2: thread/read using the returned sessionId
    read_resp = await server.dispatch(
        "req-2", "thread/read",
        {"threadId": "e2e-thread"},
        "client-1", returned_session_id,
    )
    assert "result" in read_resp, f"Expected result but got: {read_resp}"
    assert read_resp["result"]["thread"]["id"] == "e2e-thread"
    assert read_resp["result"]["thread"]["name"] == "E2E Test"
    assert read_resp["result"]["thread"]["sessionId"] == "client-1:e2e-project"

    await registry.stop_all()


@pytest.mark.asyncio
async def test_cross_client_cannot_read(tmp_path):
    """client-2 must not read a thread owned by client-1."""
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_cross_client_read")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()
    await session.services.thread_runtime.create_thread(
        title="Private", thread_id="private-thread",
    )

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    # client-2 tries to read using client-1's session_id
    response = await server.dispatch(
        "req-1", "thread/read",
        {"threadId": "private-thread"},
        "client-2", session_id,
    )
    assert response["code"] == "UNAUTHORIZED", (
        f"Expected UNAUTHORIZED for cross-client read, got: {response}"
    )

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await registry.stop_all()


@pytest.mark.asyncio
async def test_cross_client_cannot_fork(tmp_path):
    """client-2 must not fork a thread owned by client-1."""
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_cross_client_fork")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()
    await session.services.thread_runtime.create_thread(
        title="Source", thread_id="source-thread",
    )

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    response = await server.dispatch(
        "req-1", "thread/fork",
        {"threadId": "source-thread", "title": "Stolen"},
        "client-2", session_id,
    )
    assert response["code"] == "UNAUTHORIZED", (
        f"Expected UNAUTHORIZED for cross-client fork, got: {response}"
    )

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await registry.stop_all()


@pytest.mark.asyncio
async def test_cross_client_cannot_rollback(tmp_path):
    """client-2 must not rollback a thread owned by client-1."""
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_cross_client_rollback")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()
    await session.services.thread_runtime.create_thread(
        title="Target", thread_id="target-thread",
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
        {"threadId": "target-thread", "dropLastTurns": 1},
        "client-2", session_id,
    )
    assert response["code"] == "UNAUTHORIZED", (
        f"Expected UNAUTHORIZED for cross-client rollback, got: {response}"
    )

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await registry.stop_all()


@pytest.mark.asyncio
async def test_dispatch_none_session_id_falls_back_to_params(tmp_path):
    """When dispatch passes session_id=None, handler must derive from params.sessionKey."""
    session_id = "client-1:default"
    session = _make_mock_session_with_real_runtimes(session_id, tmp_path / "test_dispatch_none_fallback")
    await session.services.thread_runtime.initialize()
    await session.services.ledger_runtime.initialize()

    registry = ClientSessionRegistry()
    registry._sessions[session_id] = session
    registry._client_sessions.setdefault("client-1", set()).add(session_id)
    registry._session_clients.setdefault(session_id, set()).add("client-1")
    registry._last_activity[session_id] = 0

    # Also register a second session for the same client
    session2_id = "client-1:project-b"
    session2 = _make_mock_session_with_real_runtimes(session2_id, tmp_path / "test_dispatch_none_fallback_b")
    await session2.services.thread_runtime.initialize()
    await session2.services.ledger_runtime.initialize()
    registry._sessions[session2_id] = session2
    registry._client_sessions.setdefault("client-1", set()).add(session2_id)
    registry._session_clients.setdefault(session2_id, set()).add("client-1")
    registry._last_activity[session2_id] = 0

    server = AppServer(registry)
    register_codex_thread_handlers(server)

    # dispatch with session_id=None, params.sessionKey="project-b" →
    # handler must look up "client-1:project-b"
    response = await server.dispatch(
        "req-1", "thread/start",
        {"threadId": "t-fallback", "title": "Fallback", "sessionKey": "project-b"},
        "client-1", None,
    )
    assert "result" in response, f"Expected result but got: {response}"
    assert response["result"]["thread"]["sessionId"] == "client-1:project-b"

    await session.services.thread_runtime.close()
    await session.services.ledger_runtime.close()
    await session2.services.thread_runtime.close()
    await session2.services.ledger_runtime.close()
    await registry.stop_all()
