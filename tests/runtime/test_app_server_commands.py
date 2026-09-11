"""Tests for command routing through AppServer (Phase 26.6)."""

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────


def _setup_server_with_session(fake_config, fake_provider, tmp_path):
    """Create AppServer with a RuntimeSession and register command handlers."""
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    server = AppServer(registry)
    return server, registry


# ── Thread commands ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_create(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import register_command_handlers

    server, registry = _setup_server_with_session(fake_config, fake_provider, tmp_path)
    # Create a session first
    session = await registry.create_session(
        client_id="c1", session_key="s1",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    register_command_handlers(server)

    response = await server.dispatch(
        request_id="r1", method="thread.create",
        params={"title": "My Thread"},
        client_id="c1", session_id=session.session_id,
    )
    assert "result" in response, f"Expected result, got {response}"
    assert "thread_id" in response["result"]
    assert "title" in response["result"]
    assert response["result"]["title"] == "My Thread"

    await registry.stop_all()


@pytest.mark.asyncio
async def test_thread_list(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import register_command_handlers

    server, registry = _setup_server_with_session(fake_config, fake_provider, tmp_path)
    session = await registry.create_session(
        client_id="c1", session_key="s1",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    register_command_handlers(server)

    # Create two threads
    await server.dispatch("r1", "thread.create", {"title": "T1"},
                          "c1", session.session_id)
    await server.dispatch("r2", "thread.create", {"title": "T2"},
                          "c1", session.session_id)

    # List
    response = await server.dispatch("r3", "thread.list", {},
                                      "c1", session.session_id)
    assert "result" in response
    threads = response["result"]["threads"]
    assert len(threads) >= 2
    titles = [t["title"] for t in threads]
    assert "T1" in titles
    assert "T2" in titles

    await registry.stop_all()


@pytest.mark.asyncio
async def test_thread_rename(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import register_command_handlers

    server, registry = _setup_server_with_session(fake_config, fake_provider, tmp_path)
    session = await registry.create_session(
        client_id="c1", session_key="s1",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    register_command_handlers(server)

    create_r = await server.dispatch("r1", "thread.create", {"title": "Old"},
                                      "c1", session.session_id)
    tid = create_r["result"]["thread_id"]

    rename_r = await server.dispatch("r2", "thread.rename",
                                      {"thread_id": tid, "title": "New"},
                                      "c1", session.session_id)
    assert "result" in rename_r
    assert rename_r["result"]["title"] == "New"

    await registry.stop_all()


@pytest.mark.asyncio
async def test_thread_archive(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import register_command_handlers

    server, registry = _setup_server_with_session(fake_config, fake_provider, tmp_path)
    session = await registry.create_session(
        client_id="c1", session_key="s1",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    register_command_handlers(server)

    create_r = await server.dispatch("r1", "thread.create", {"title": "T"},
                                      "c1", session.session_id)
    tid = create_r["result"]["thread_id"]

    response = await server.dispatch("r2", "thread.archive",
                                      {"thread_id": tid},
                                      "c1", session.session_id)
    assert "result" in response

    await registry.stop_all()


# ── Abort ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_abort_through_app_server(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import register_command_handlers

    server, registry = _setup_server_with_session(fake_config, fake_provider, tmp_path)
    session = await registry.create_session(
        client_id="c1", session_key="s1",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    register_command_handlers(server)

    response = await server.dispatch(
        request_id="r-abort", method="chat.abort",
        params={"thread_id": "default"},
        client_id="c1", session_id=session.session_id,
    )
    assert "result" in response
    assert response["result"]["aborted"] is True

    await registry.stop_all()


@pytest.mark.asyncio
async def test_abort_resolves_thread_from_session_key(fake_config, fake_provider, tmp_path):
    """chat.abort must target the SAME thread a turn registers its cancel event
    under.  chat.send (loop.py) falls back thread_id -> session_key; abort must
    mirror that, not hardcode "default" (#542)."""
    from miqi.protocol.commands import AbortTurn
    from miqi.runtime.app_server import register_command_handlers

    server, registry = _setup_server_with_session(fake_config, fake_provider, tmp_path)
    session = await registry.create_session(
        client_id="c1", session_key="s1",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    register_command_handlers(server)

    # No thread_id + session_key -> must resolve to the session_key thread.
    await server.dispatch(
        request_id="r1", method="chat.abort",
        params={"session_key": "s1"},
        client_id="c1", session_id=session.session_id,
    )
    sub = session._submissions.get_nowait()
    assert isinstance(sub, AbortTurn)
    assert sub.thread_id == "s1"

    # Explicit thread_id wins.
    await server.dispatch(
        request_id="r2", method="chat.abort",
        params={"session_key": "s1", "thread_id": "thread-xyz"},
        client_id="c1", session_id=session.session_id,
    )
    sub = session._submissions.get_nowait()
    assert isinstance(sub, AbortTurn)
    assert sub.thread_id == "thread-xyz"

    # Neither -> legacy "default" fallback (CLI path).
    await server.dispatch(
        request_id="r3", method="chat.abort",
        params={},
        client_id="c1", session_id=session.session_id,
    )
    sub = session._submissions.get_nowait()
    assert isinstance(sub, AbortTurn)
    assert sub.thread_id == "default"

    await registry.stop_all()


@pytest.mark.asyncio
async def test_abort_releases_bridge_turn_lock(fake_config, fake_provider, tmp_path):
    """#797: chat.abort must release the bridge-side turn lock so the user
    can immediately resend — the lock lives in BridgeRuntimeLoop
    (`_session_drain_tasks`) and is NOT freed by AbortTurn alone when the
    runtime is stuck on a blocking tool call."""
    from miqi.runtime.app_server import register_command_handlers

    server, registry = _setup_server_with_session(fake_config, fake_provider, tmp_path)
    session = await registry.create_session(
        client_id="c1", session_key="s1",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    register_command_handlers(server)

    released: list[str] = []

    def _release(session_id):
        released.append(session_id)
        return True

    registry.bridge_context["release_turn_lock"] = _release

    response = await server.dispatch(
        request_id="r-abort", method="chat.abort",
        params={"session_key": "s1"},
        client_id="c1", session_id=session.session_id,
    )
    assert "result" in response
    assert response["result"]["aborted"] is True
    assert released == [session.session_id]

    await registry.stop_all()


@pytest.mark.asyncio
async def test_abort_without_release_hook_still_works(fake_config, fake_provider, tmp_path):
    """#797: no bridge_context release hook (TUI/legacy) → abort behaves as
    before; the hook is optional DI, not a hard dependency."""
    from miqi.runtime.app_server import register_command_handlers

    server, registry = _setup_server_with_session(fake_config, fake_provider, tmp_path)
    session = await registry.create_session(
        client_id="c1", session_key="s1",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    register_command_handlers(server)

    response = await server.dispatch(
        request_id="r-abort", method="chat.abort",
        params={"thread_id": "default"},
        client_id="c1", session_id=session.session_id,
    )
    assert "result" in response
    assert response["result"]["aborted"] is True

    await registry.stop_all()


@pytest.mark.asyncio
async def test_abort_submit_failure_returns_recoverable_error_but_still_releases_lock(
    fake_config, fake_provider, tmp_path,
):
    """#797 / CodeRabbit: an AbortTurn submission failure must NOT report a
    successful abort (the turn keeps running — a false "stopped" state lets
    work continue under the hood).  The turn-lock release stays best-effort:
    it runs even when the submission failed, so the session is not left
    locked either way.  The error is recoverable so the client can retry.
    """
    from miqi.runtime.app_server import register_command_handlers

    server, registry = _setup_server_with_session(fake_config, fake_provider, tmp_path)
    session = await registry.create_session(
        client_id="c1", session_key="s1",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    register_command_handlers(server)

    released: list[str] = []

    def _release(session_id):
        released.append(session_id)
        return True

    registry.bridge_context["release_turn_lock"] = _release

    async def _broken_submit(*args, **kwargs):
        raise RuntimeError("session teardown race")

    # Simulate the runtime rejecting the AbortTurn (e.g. teardown race).
    session.submit = _broken_submit

    # dispatch converts AppServerError into an error envelope (it does not
    # raise) — the client must see ABORT_FAILED + recoverable, NOT a
    # successful {"aborted": true}.
    response = await server.dispatch(
        request_id="r-abort", method="chat.abort",
        params={"session_key": "s1"},
        client_id="c1", session_id=session.session_id,
    )
    assert "result" not in response
    assert response["error"] == "Failed to abort turn; the turn may still be running"
    assert response["code"] == "ABORT_FAILED"
    assert response["recoverable"] is True
    # The lock release is still best-effort-performed.
    assert released == [session.session_id]

    await registry.stop_all()


# ── Config ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_get_through_app_server(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import register_command_handlers

    server, registry = _setup_server_with_session(fake_config, fake_provider, tmp_path)
    register_command_handlers(server)

    # config.get is session-less (no session_id needed)
    response = await server.dispatch(
        request_id="r-cfg", method="config.get",
        params={}, client_id="c1", session_id=None,
    )
    # May return config data or an error depending on config availability
    assert "request_id" in response

    await registry.stop_all()


# ── Unknown command ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_command_returns_error():
    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    registry = ClientSessionRegistry()
    server = AppServer(registry)

    response = await server.dispatch(
        request_id="r-unknown", method="nonexistent.command",
        params={}, client_id="c1",
    )
    assert "error" in response
    assert response["code"] == "UNKNOWN_METHOD"


# ── Unauthorized command ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unauthorized_client_cannot_issue_session_commands(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import register_command_handlers

    server, registry = _setup_server_with_session(fake_config, fake_provider, tmp_path)
    session = await registry.create_session(
        client_id="c1", session_key="s1",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    register_command_handlers(server)

    # Client c2 is not authorized for this session
    response = await server.dispatch(
        "r-bad", "thread.list", {},
        "c2", session.session_id,
    )
    assert "error" in response
    assert response["code"] == "UNAUTHORIZED"

    await registry.stop_all()
