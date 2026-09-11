"""Tests for AppServer abstraction — dispatch, registry, middleware (Phase 26.1)."""

import asyncio

import pytest

# ── ClientSessionRegistry ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registry_create_and_get_session(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()
    try:
        session = await registry.create_session(
            client_id="client-1",
            session_key="my-session",
            config=fake_config,
            provider=fake_provider,
            workspace=tmp_path,
        )
        assert session is not None
        assert session.session_id.startswith("client-1:my-session")

        # Same client can retrieve it
        got = await registry.get_session("client-1", session.session_id)
        assert got is session
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_registry_unauthorized_client_cannot_access_session(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()
    try:
        session = await registry.create_session(
            client_id="client-A",
            session_key="private",
            config=fake_config,
            provider=fake_provider,
            workspace=tmp_path,
        )
        # Client B tries to access — fails
        got = await registry.get_session("client-B", session.session_id)
        assert got is None
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_registry_authorize_grants_access(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()
    try:
        session = await registry.create_session(
            client_id="client-A",
            session_key="shared",
            config=fake_config,
            provider=fake_provider,
            workspace=tmp_path,
        )
        # A authorizes B
        registry.authorize_client("client-A", session.session_id, "client-B")
        got = await registry.get_session("client-B", session.session_id)
        assert got is session
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_registry_list_sessions_only_returns_authorized(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()
    try:
        s1 = await registry.create_session(
            client_id="client-A", session_key="s1",
            config=fake_config, provider=fake_provider, workspace=tmp_path,
        )
        s2 = await registry.create_session(
            client_id="client-B", session_key="s2",
            config=fake_config, provider=fake_provider, workspace=tmp_path,
        )

        a_sessions = registry.list_sessions("client-A")
        assert s1.session_id in a_sessions
        assert s2.session_id not in a_sessions
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_registry_stop_session_removes_from_all_clients(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()
    session = await registry.create_session(
        client_id="client-A", session_key="s",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    registry.authorize_client("client-A", session.session_id, "client-B")

    await registry.stop_session(session.session_id)

    # Both clients lose access
    assert await registry.get_session("client-A", session.session_id) is None
    assert await registry.get_session("client-B", session.session_id) is None
    assert registry.list_sessions("client-A") == []


@pytest.mark.asyncio
async def test_registry_stop_all(fake_config, fake_provider, tmp_path):
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()
    await registry.create_session(
        client_id="c1", session_key="a",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )
    await registry.create_session(
        client_id="c2", session_key="b",
        config=fake_config, provider=fake_provider, workspace=tmp_path,
    )

    await registry.stop_all()
    assert registry.list_sessions("c1") == []
    assert registry.list_sessions("c2") == []


# ── AppServer dispatch ───────────────────────────────────────────────────


class _FakeRuntimeSession:
    """Minimal fake RuntimeSession for AppServer dispatch testing."""
    def __init__(self, session_id="test-session"):
        self.session_id = session_id
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def submit(self, submission):
        pass

    async def next_event(self, timeout=None):
        return None


@pytest.fixture
def fake_registry_with_session():
    from miqi.runtime.app_server import ClientSessionRegistry
    registry = ClientSessionRegistry()
    # Manually inject a fake session for testing dispatch
    fake = _FakeRuntimeSession("test-session")
    registry._sessions["test-session"] = fake
    registry._session_clients["test-session"] = {"client-1"}
    registry._client_sessions["client-1"] = {"test-session"}
    return registry, fake


@pytest.mark.asyncio
async def test_app_server_dispatches_registered_method(fake_registry_with_session):
    from miqi.runtime.app_server import AppServer

    registry, fake_session = fake_registry_with_session

    async def my_handler(request_id, params, client_id, session_id, registry_):
        return {"result": {"echo": params.get("msg", "")}}

    server = AppServer(registry)
    server.register_method("test.echo", my_handler)

    response = await server.dispatch(
        request_id="req-1",
        method="test.echo",
        params={"msg": "hello"},
        client_id="client-1",
        session_id="test-session",
    )
    assert response["request_id"] == "req-1"
    assert response["result"] == {"echo": "hello"}


@pytest.mark.asyncio
async def test_app_server_unknown_method_returns_error(fake_registry_with_session):
    from miqi.runtime.app_server import AppServer

    registry, _ = fake_registry_with_session
    server = AppServer(registry)

    response = await server.dispatch(
        request_id="req-1",
        method="nonexistent.method",
        params={},
        client_id="client-1",
        session_id="test-session",
    )
    assert "error" in response
    assert response["code"] == "UNKNOWN_METHOD"


@pytest.mark.asyncio
async def test_app_server_unauthorized_session_returns_error(fake_registry_with_session):
    from miqi.runtime.app_server import AppServer

    registry, _ = fake_registry_with_session
    server = AppServer(registry)

    async def my_handler(request_id, params, client_id, session_id, registry_):
        return {"result": {}}

    server.register_method("test.ok", my_handler)

    response = await server.dispatch(
        request_id="req-1",
        method="test.ok",
        params={},
        client_id="client-2",  # NOT authorized for test-session
        session_id="test-session",
    )
    assert "error" in response
    assert response["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_app_server_middleware_chain_runs_in_order(fake_registry_with_session):
    from miqi.runtime.app_server import AppServer

    registry, _ = fake_registry_with_session
    server = AppServer(registry)

    calls: list[str] = []

    async def mw1(req_id, method, params, client_id, session_id, next_handler):
        calls.append("mw1_before")
        result = await next_handler(req_id, method, params, client_id, session_id)
        calls.append("mw1_after")
        return result

    async def mw2(req_id, method, params, client_id, session_id, next_handler):
        calls.append("mw2_before")
        result = await next_handler(req_id, method, params, client_id, session_id)
        calls.append("mw2_after")
        return result

    async def handler(request_id, params, client_id, session_id, registry_):
        calls.append("handler")
        return {"result": "ok"}

    server.add_middleware(mw1)
    server.add_middleware(mw2)
    server.register_method("test.mw", handler)

    await server.dispatch(
        request_id="req-1", method="test.mw", params={},
        client_id="client-1", session_id="test-session",
    )
    assert calls == ["mw1_before", "mw2_before", "handler", "mw2_after", "mw1_after"]


@pytest.mark.asyncio
async def test_app_server_middleware_can_block_request(fake_registry_with_session):
    from miqi.runtime.app_server import AppServer

    registry, _ = fake_registry_with_session
    server = AppServer(registry)

    async def auth_mw(req_id, method, params, client_id, session_id, next_handler):
        if client_id == "blocked":
            return {
                "request_id": req_id,
                "error": "Blocked by middleware",
                "code": "UNAUTHORIZED",
                "recoverable": False,
            }
        return await next_handler(req_id, method, params, client_id, session_id)

    async def handler(request_id, params, client_id, session_id, registry_):
        return {"result": "ok"}

    server.add_middleware(auth_mw)
    server.register_method("test.block", handler)

    # Normal client ("client-1" is in the fake registry for this session)
    r1 = await server.dispatch("r1", "test.block", {}, "client-1", "test-session")
    assert "result" in r1, f"Expected result, got {r1}"

    # Blocked client stopped at middleware (also needs to be authorized for session
    # to reach the middleware — let's add "blocked" to the registry)
    registry._session_clients["test-session"].add("blocked")
    registry._client_sessions.setdefault("blocked", set()).add("test-session")

    r2 = await server.dispatch("r2", "test.block", {}, "blocked", "test-session")
    assert "error" in r2
    assert r2["code"] == "UNAUTHORIZED"  # from middleware, not session check


@pytest.mark.asyncio
async def test_app_server_handler_error_is_caught_and_sanitized(fake_registry_with_session):
    from miqi.runtime.app_server import AppServer

    registry, _ = fake_registry_with_session
    server = AppServer(registry)

    async def crashy_handler(request_id, params, client_id, session_id, registry_):
        raise ValueError("secret internal detail /home/user/secret")

    server.register_method("test.crash", crashy_handler)

    response = await server.dispatch(
        request_id="req-1",
        method="test.crash",
        params={},
        client_id="client-1",
        session_id="test-session",
    )
    assert "error" in response
    assert response["code"] == "INTERNAL"
    # Error message must be sanitized — no paths
    assert "/home" not in response["error"]
    assert "secret" not in response["error"]


# ── client_id compatibility shim ─────────────────────────────────────────


def test_registry_missing_client_id_raises_error():
    """Phase 27.5: missing client_id raises AppServerError."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="client_id is required"):
        registry.resolve_client_id(None)


def test_registry_explicit_client_id_accepted():
    from miqi.runtime.app_server import ClientSessionRegistry

    registry = ClientSessionRegistry()
    cid = registry.resolve_client_id("explicit-client")
    assert cid == "explicit-client"


# ── Phase 41: background task ownership ─────────────────────────────────


@pytest.mark.asyncio
async def test_app_server_cancels_owned_background_tasks_on_stop():

    from miqi.runtime.app_server import AppServer, ClientSessionRegistry

    server = AppServer(ClientSessionRegistry())
    await server.start()

    async def _forever():
        while True:
            await asyncio.sleep(1)

    task = server.create_background_task(_forever(), name="phase41-test")
    assert not task.done()

    await server.stop()

    assert task.done()
    assert task.cancelled() or task.exception() is not None
