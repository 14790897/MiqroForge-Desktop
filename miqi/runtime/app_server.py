"""AppServer — transport-agnostic protocol boundary and session registry.

Phase 26: Owns client→session mapping, method dispatch, middleware chain,
and event fanout. Transport adapters (DesktopBridge, CLI, TUI, Gateway)
call AppServer; they do NOT own business logic or session state.

Design principles:
- Transport-agnostic: no stdin/stdout/HTTP/WebSocket knowledge.
- client_id is the stable caller identity; session_id is the runtime scope.
- Middleware runs before handler dispatch (auth, rate-limit, logging).
- Error responses are sanitized — no internal paths or tracebacks to clients.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine

from loguru import logger

from miqi.runtime.protocol_registry import (
    MethodScope,
    MethodStability,
    ProtocolMethodSpec,
    ProtocolRegistry,
    legacy_method_spec,
)

# ── Handler/middleware signatures ────────────────────────────────────────

# Handler: receives parsed request, returns result dict or raises
Handler = Callable[
    [str, dict[str, Any], str, str | None, "ClientSessionRegistry"],
    Coroutine[Any, Any, dict[str, Any]],
]

# Middleware: wraps the next call in chain
Middleware = Callable[
    [str, str, dict[str, Any], str, str | None,
     Callable[..., Coroutine[Any, Any, dict[str, Any]]]],
    Coroutine[Any, Any, dict[str, Any]],
]


# ── Error codes ──────────────────────────────────────────────────────────

class AppServerError(Exception):
    """Typed error from handler or middleware that AppServer catches."""
    def __init__(self, message: str, *, code: str = "INTERNAL", recoverable: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.recoverable = recoverable


# ── ClientSessionRegistry ────────────────────────────────────────────────


class ClientSessionRegistry:
    """Manages client_id ↔ session_id relationships with TTL eviction.

    One client can access many sessions. One session can have many
    authorized clients. Sessions are created explicitly; authorization
    is explicit (not implied by key format).
    """

    def __init__(self, *, idle_timeout_seconds: int = 3600):
        self._client_sessions: dict[str, set[str]] = {}   # client_id → {session_id}
        self._session_clients: dict[str, set[str]] = {}   # session_id → {client_id}
        self._sessions: dict[str, Any] = {}               # session_id → RuntimeSession
        self._last_activity: dict[str, float] = {}         # session_id → timestamp
        self._idle_timeout = idle_timeout_seconds
        self._lock = asyncio.Lock()
        # Phase 35 hardening: bridge_context holds shared state for handler DI.
        # Populated by BridgeRuntimeLoop during init. Handlers read from here
        # instead of importing miqi.bridge.server directly.
        self.bridge_context: dict[str, Any] = {}

    # ── client_id resolution ─────────────────────────────────────────────

    def resolve_client_id(self, raw: str | None) -> str:
        """Resolve a client_id.

        Phase 27.5: client_id is REQUIRED. Missing client_id raises
        AppServerError. There is no longer a default or shim.
        """
        if raw:
            return raw
        raise AppServerError(
            "client_id is required",
            code="INVALID_PARAMS",
            recoverable=False,
        )

    # ── session lifecycle ────────────────────────────────────────────────

    async def create_session(
        self,
        *,
        client_id: str,
        session_key: str,
        config: Any,
        provider: Any,
        workspace: Path,
        sandbox_manager: Any = None,
    ) -> Any:
        """Create a new RuntimeSession and authorize the creating client."""
        from miqi.runtime.session import RuntimeSession

        # Phase 26: session_id is namespaced client_id:session_key
        # In Phase 27, switch to a pure opaque session_id with display name.
        session_id = f"{client_id}:{session_key}"

        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                # Session already exists — ensure client is authorized
                self._client_sessions.setdefault(client_id, set()).add(session_id)
                self._session_clients.setdefault(session_id, set()).add(client_id)
                self._last_activity[session_id] = time.time()
                return existing

            # Phase N: forward subagent completions to the creating client's
            # event sink as `subagent_result`.  This is the only producer of
            # the Desktop `chat:subagent_result` IPC event since Phase 13
            # removed the legacy SubagentManager result_callback — without
            # it the frontend never renders subagent results.
            # NOTE: emit_client_event lives on AppServer, not on this
            # registry — resolve it via bridge_context (populated by
            # BridgeRuntimeLoop during init).
            async def _on_agent_completed(data: dict) -> None:
                try:
                    app_server = self.bridge_context.get("app_server")
                    if app_server is None:
                        return
                    await app_server.emit_client_event(
                        client_id, "subagent_result", data,
                    )
                except Exception:
                    pass  # sink missing/unavailable — never break agent teardown

            runtime = RuntimeSession.create(
                config=config,
                provider=provider,
                session_id=session_id,
                workspace=workspace,
                sandbox_manager=sandbox_manager,
                agent_completion_callback=_on_agent_completed,
            )
            await runtime.start()

            self._sessions[session_id] = runtime
            self._client_sessions.setdefault(client_id, set()).add(session_id)
            self._session_clients[session_id] = {client_id}
            self._last_activity[session_id] = time.time()
        logger.info(
            "ClientSessionRegistry: created session {} for client {}",
            session_id, client_id,
        )
        return runtime

    async def get_session(self, client_id: str, session_id: str) -> Any | None:
        """Return RuntimeSession if client is authorized, else None."""
        authorized = self._session_clients.get(session_id, set())
        if client_id not in authorized:
            return None
        self._last_activity[session_id] = time.time()
        return self._sessions.get(session_id)

    def session_exists(self, session_id: str) -> bool:
        """Return True if *session_id* exists in the registry, regardless of
        caller authorization. Used by handlers that must distinguish
        \"session does not exist\" from \"client is not authorized\"."""
        return session_id in self._sessions

    def authorize_client(
        self, owner_client_id: str, session_id: str, target_client_id: str,
    ) -> bool:
        """Grant another client access to a session.

        Only an existing authorized client can grant access to others.
        """
        authorized = self._session_clients.get(session_id, set())
        if owner_client_id not in authorized:
            return False
        self._client_sessions.setdefault(target_client_id, set()).add(session_id)
        self._session_clients[session_id].add(target_client_id)
        return True

    def list_sessions(self, client_id: str) -> list[str]:
        """Return session_ids this client is authorized for."""
        return sorted(self._client_sessions.get(client_id, set()))

    def get_session_client_ids(self, session_id: str) -> set[str]:
        """Return all client_ids authorized for a session."""
        return self._session_clients.get(session_id, set()).copy()

    async def stop_session(self, session_id: str) -> None:
        """Stop a RuntimeSession and remove it from all client mappings."""
        runtime = self._sessions.pop(session_id, None)
        if runtime is None:
            return
        try:
            await runtime.stop()
        except Exception as exc:
            logger.warning(
                "ClientSessionRegistry: error stopping session {}: {}",
                session_id, exc,
            )
        # Remove from all client mappings
        for client_set in self._client_sessions.values():
            client_set.discard(session_id)
        self._session_clients.pop(session_id, None)
        self._last_activity.pop(session_id, None)

    async def stop_all(self) -> None:
        """Stop all sessions (shutdown hook)."""
        session_ids = list(self._sessions.keys())
        for sid in session_ids:
            await self.stop_session(sid)

    async def evict_idle_sessions(self) -> list[str]:
        """Evict sessions idle beyond TTL. Returns list of evicted session_ids."""
        now = time.time()
        evicted: list[str] = []
        for sid, last_active in list(self._last_activity.items()):
            if now - last_active > self._idle_timeout:
                logger.info(
                    "ClientSessionRegistry: evicting idle session {} (last active {}s ago)",
                    sid, int(now - last_active),
                )
                await self.stop_session(sid)
                evicted.append(sid)
        return evicted

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    @property
    def client_count(self) -> int:
        return len(self._client_sessions)


# ── ClientCapabilities (Phase 45) ─────────────────────────────────────────


@dataclass
class ClientCapabilities:
    """Per-client capabilities negotiated during initialize.

    Fields mirror Codex app-server InitializeRequest.capabilities.
    Experimental APIs are gated behind ``experimental_api``.
    ``opt_out_notification_methods`` is an exact-match set of method
    names the client does not wish to receive as notifications.
    """

    experimental_api: bool = False
    opt_out_notification_methods: set[str] = field(default_factory=set)
    client_info: dict[str, Any] = field(default_factory=dict)


# ── AppServer ────────────────────────────────────────────────────────────


class AppServer:
    """Transport-agnostic protocol server with method dispatch and middleware.

    Usage:
        registry = ClientSessionRegistry()
        server = AppServer(registry)
        server.register_method("chat.send", chat_send_handler)
        server.add_middleware(auth_middleware)
        response = await server.dispatch(req_id, method, params, client_id, session_id)
    """

    def __init__(self, registry: ClientSessionRegistry, *, ttl_interval_seconds: int = 300):
        self.registry = registry
        self._methods: dict[str, Handler] = {}
        self._middleware: list[Middleware] = []
        self._ttl_interval = ttl_interval_seconds
        self._ttl_task: asyncio.Task | None = None
        self._running = False
        # Phase 61: typed protocol catalog storage
        self._protocol = ProtocolRegistry()
        register_protocol_handlers(self)
        # Phase 26.4: event subscription
        # session_id → {client_id}
        self._subscriptions: dict[str, set[str]] = {}
        # client_id → async callable(event_dict)
        self._event_sinks: dict[str, Any] = {}
        # Phase 41: background tasks owned by AppServer shutdown
        self._background_tasks: set[asyncio.Task] = set()
        # Phase 43: client cleanup hooks — called when a client disconnects
        # or when stop() cleans up all clients.
        self._client_cleanup_hooks: list[Callable[[str], Any]] = []
        # Phase 45: per-client capabilities negotiated during initialize
        self._client_capabilities: dict[str, ClientCapabilities] = {}

    # ── method registration ──────────────────────────────────────────────

    def register_method(
        self,
        method: str,
        handler: Handler,
        *,
        spec: ProtocolMethodSpec | None = None,
    ) -> None:
        """Register a handler for a method name and optional protocol spec.

        Handler signature:
            async def handler(request_id, params, client_id, session_id, registry) -> dict

        The returned dict should be either {"result": ...} on success or
        raise AppServerError on expected errors. Unexpected exceptions
        are caught and sanitized by the dispatch layer.
        """
        if spec is not None and spec.method != method:
            raise ValueError(
                f"Protocol spec method {spec.method!r} does not match registered method {method!r}"
            )
        self._methods[method] = handler
        self._protocol.add(spec or legacy_method_spec(method))
        logger.debug("AppServer: registered method {}", method)

    def protocol_catalog(self) -> dict[str, Any]:
        """Return JSON-serializable protocol catalog for registered methods."""
        return self._protocol.to_catalog()

    def protocol_method_names(self) -> set[str]:
        """Return registered protocol method names."""
        return self._protocol.methods()

    def protocol_schema(self) -> dict[str, Any]:
        """Return JSON Schema document for the registered protocol catalog."""
        return self._protocol.to_json_schema()

    # ── middleware ───────────────────────────────────────────────────────

    def add_middleware(self, middleware: Middleware) -> None:
        """Add middleware to the chain. Middleware runs in registration order.

        Middleware signature:
            async def mw(request_id, method, params, client_id, session_id, next_handler) -> dict

        Middleware can:
        - Modify params before passing to next_handler
        - Return early (block the request) without calling next_handler
        - Modify the response from next_handler
        """
        self._middleware.append(middleware)

    # ── dispatch ─────────────────────────────────────────────────────────

    async def dispatch(
        self,
        request_id: str,
        method: str,
        params: dict[str, Any],
        client_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a request through middleware chain to the handler.

        Returns a response envelope: {"request_id": ..., "result": ...}
        or an error envelope: {"request_id": ..., "error": ..., "code": ..., "recoverable": ...}

        This is the single entry point for all transport adapters.
        """
        try:
            return await self._dispatch_inner(request_id, method, params, client_id, session_id)
        except AppServerError as exc:
            logger.warning(
                "AppServer: error dispatching {} {} (client={}): {}",
                method, request_id, client_id, exc.message,
            )
            return {
                "request_id": request_id,
                "error": exc.message,
                "code": exc.code,
                "recoverable": exc.recoverable,
            }
        except Exception:
            logger.exception(
                "AppServer: internal error dispatching {} {} (client={})",
                method, request_id, client_id,
            )
            return {
                "request_id": request_id,
                "error": "Internal error",
                "code": "INTERNAL",
                "recoverable": False,
            }

    async def _dispatch_inner(
        self,
        request_id: str,
        method: str,
        params: dict[str, Any],
        client_id: str,
        session_id: str | None,
    ) -> dict[str, Any]:
        # 1. Look up handler
        handler = self._methods.get(method)
        if handler is None:
            return {
                "request_id": request_id,
                "error": f"Unknown method: {method}",
                "code": "UNKNOWN_METHOD",
                "recoverable": False,
            }

        # 2. Check session authorization (if session-scoped).
        #    Skip for chat.send/chat.abort (auto-create sessions) and
        #    sessions.get/sessions.list (read disk data, not registry state).
        if session_id is not None and method not in (
            "chat.send", "chat.abort",
            "sessions.get", "sessions.list",
            "sessions.get_tracked_files",
            "sessions.delete", "sessions.archive", "sessions.unarchive",
            "sessions.rename",
        ):
            session = await self.registry.get_session(client_id, session_id)
            if session is None:
                return {
                    "request_id": request_id,
                    "error": f"Not authorized for session {session_id}",
                    "code": "UNAUTHORIZED",
                    "recoverable": False,
                }

        # 3. Build the middleware chain ending in the handler
        async def _handler_wrapper(
            req_id: str, meth: str, p: dict[str, Any], cid: str, sid: str | None,
        ) -> dict[str, Any]:
            return await handler(req_id, p, cid, sid, self.registry)

        # Wrap handler through middleware chain (outermost first)
        wrapped: Any = _handler_wrapper
        for mw in reversed(self._middleware):
            outer = mw
            inner = wrapped

            async def _mw_bound(
                req_id: str, meth: str, p: dict[str, Any], cid: str, sid: str | None,
                _outer=outer, _inner=inner,
            ) -> dict[str, Any]:
                return await _outer(req_id, meth, p, cid, sid, _inner)

            wrapped = _mw_bound

        result = await wrapped(request_id, method, params, client_id, session_id)

        # 4. Ensure response has request_id
        if "request_id" not in result:
            result = {"request_id": request_id, **result}
        return result

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the AppServer and background TTL eviction task."""
        if self._running:
            return
        self._running = True
        self._ttl_task = asyncio.create_task(self._run_ttl_loop())
        logger.info("AppServer started (TTL interval={}s)", self._ttl_interval)

    def create_background_task(self, coro: Any, *, name: str | None = None) -> asyncio.Task:
        """Create a background task owned by AppServer shutdown."""
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(lambda t: self._background_tasks.discard(t))
        return task

    async def stop(self) -> None:
        """Stop the AppServer, cancel TTL task, and stop all sessions."""
        self._running = False
        if self._ttl_task is not None and not self._ttl_task.done():
            self._ttl_task.cancel()
            try:
                await self._ttl_task
            except asyncio.CancelledError:
                pass
            self._ttl_task = None
        # Phase 43: run cleanup hooks for all known clients before tearing
        # down sessions. This ensures workbench processes, file watchers,
        # and other client-owned resources are cleaned up.
        known_clients = list(self._event_sinks.keys())
        for client_id in known_clients:
            await self._run_client_cleanup_hooks(client_id)
        # Phase 41: cancel owned background tasks
        if self._background_tasks:
            tasks = list(self._background_tasks)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._background_tasks.clear()
        await self.registry.stop_all()
        logger.info("AppServer stopped")

    async def _run_ttl_loop(self) -> None:
        """Background task that periodically evicts idle sessions."""
        while self._running:
            try:
                await asyncio.sleep(self._ttl_interval)
                if not self._running:
                    break
                evicted = await self.registry.evict_idle_sessions()
                if evicted:
                    logger.info(
                        "AppServer TTL: evicted {} idle session(s): {}",
                        len(evicted), evicted,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("AppServer TTL loop error: {}", exc)

    # ── event subscription and fanout ────────────────────────────────────

    def subscribe(self, client_id: str, session_id: str) -> None:
        """Start forwarding events from session_id to client_id.

        Client must be authorized for the session (verified against
        the ClientSessionRegistry). Unauthorized clients are silently
        ignored — no subscription is created and no error is raised,
        because subscription requests can come from untrusted transports.
        """
        authorized = self.registry._session_clients.get(session_id, set())
        if client_id not in authorized:
            logger.warning(
                "AppServer: refusing subscription for unauthorized client {} "
                "to session {}", client_id, session_id,
            )
            return
        self._subscriptions.setdefault(session_id, set()).add(client_id)
        logger.debug("AppServer: client {} subscribed to session {}", client_id, session_id)

    def unsubscribe(self, client_id: str, session_id: str) -> None:
        """Stop forwarding events from session_id to client_id."""
        subs = self._subscriptions.get(session_id)
        if subs is not None:
            subs.discard(client_id)
            if not subs:
                del self._subscriptions[session_id]
        logger.debug("AppServer: client {} unsubscribed from session {}", client_id, session_id)

    def add_client_cleanup_hook(self, hook: Callable[[str], Any]) -> None:
        """Register a cleanup hook called when a client disconnects.

        *hook* receives the client_id.  It may be sync or async —
        AppServer will ``await`` the result if it is a coroutine.

        Hooks are called in registration order.  Exceptions are logged
        and do not prevent remaining hooks from running.
        """
        self._client_cleanup_hooks.append(hook)

    async def _run_client_cleanup_hooks(self, client_id: str) -> None:
        """Run all registered cleanup hooks for *client_id*.

        Hook exceptions are logged server-side and never propagate.
        """
        for hook in self._client_cleanup_hooks:
            try:
                result = hook(client_id)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning(
                    "AppServer: cleanup hook failed for client {}: {}",
                    client_id, exc,
                )

    def set_event_sink(self, client_id: str, sink: Any) -> None:
        """Register an async callable that receives event dicts for a client.

        The sink is called as: await sink({"event": ..., "data": ..., "request_id": ...})
        Transport adapters register their output function here.
        """
        self._event_sinks[client_id] = sink

    async def remove_event_sink(self, client_id: str) -> None:
        """Remove the event sink for a client (e.g., on disconnect).

        Runs client cleanup hooks before removing subscriptions.
        """
        # Phase 43: run cleanup hooks first so process handles are
        # killed before the event sink is removed.
        await self._run_client_cleanup_hooks(client_id)
        self._event_sinks.pop(client_id, None)
        # Also unsubscribe from all sessions
        for subs in list(self._subscriptions.values()):
            subs.discard(client_id)

    async def emit_event(
        self,
        session_id: str,
        event_type: str,
        data: Any,
        *,
        request_id: str | None = None,
    ) -> int:
        """Emit an event to all clients subscribed to a session.

        Clients without a registered sink are silently skipped.
        Respects per-client notification opt-out (Phase 45).

        Returns the number of clients whose sink accepted the envelope —
        callers with delivery-sensitive semantics (billing handoff) can use
        it to distinguish a silently-skipped emit from a handed-off one.
        """
        subs = self._subscriptions.get(session_id, set())
        if not subs:
            return 0

        envelope = {
            "request_id": request_id,
            "event": event_type,
            "data": data,
        }

        delivered = 0
        for client_id in list(subs):
            if await self._deliver_to_client(client_id, event_type, envelope):
                delivered += 1
        return delivered

    async def emit_client_event(
        self,
        client_id: str,
        event_type: str,
        data: Any,
        *,
        request_id: str | None = None,
    ) -> None:
        """Emit an event directly to a specific client via its sink.

        Unlike :meth:`emit_event`, this does not require the client to be
        subscribed to a session. The client must have a registered event
        sink via :meth:`set_event_sink`. Silently skipped if no sink exists.
        Respects per-client notification opt-out (Phase 45).
        """
        envelope: dict[str, Any] = {
            "request_id": request_id,
            "event": event_type,
            "data": data,
        }
        await self._deliver_to_client(client_id, event_type, envelope)

    # ── client capabilities (Phase 45) ───────────────────────────────────

    def set_client_capabilities(
        self, client_id: str, capabilities: ClientCapabilities,
    ) -> None:
        """Store per-connection capabilities for *client_id*."""
        self._client_capabilities[client_id] = capabilities

    def get_client_capabilities(self, client_id: str) -> ClientCapabilities | None:
        """Return capabilities for *client_id* or None."""
        return self._client_capabilities.get(client_id)

    def is_experimental_enabled(self, client_id: str) -> bool:
        """Return True if the client has negotiated experimentalApi."""
        caps = self._client_capabilities.get(client_id)
        if caps is None:
            return False
        return caps.experimental_api

    def should_deliver_notification(
        self, client_id: str, event_type: str,
    ) -> bool:
        """Return False if *event_type* is in the client's opt-out set.

        Only exact-match suppresses. Unknown opt-out names are silently
        accepted — they match nothing except themselves.
        """
        caps = self._client_capabilities.get(client_id)
        if caps is None:
            return True  # no capabilities → deliver everything
        return event_type not in caps.opt_out_notification_methods

    # ── event emission with opt-out (Phase 45) ───────────────────────────

    async def _deliver_to_client(
        self,
        client_id: str,
        event_type: str,
        envelope: dict[str, Any],
    ) -> bool:
        """Deliver event envelope to *client_id* if not opted out.

        Returns True only when the client's sink accepted the envelope;
        opt-out / missing sink / sink exception all count as not delivered
        (the sink exception is logged, mirroring the previous behavior).
        """
        if not self.should_deliver_notification(client_id, event_type):
            return False
        sink = self._event_sinks.get(client_id)
        if sink is None:
            return False
        try:
            await sink(envelope)
        except Exception as exc:
            logger.warning(
                "AppServer: failed to deliver event {} to client {}: {}",
                event_type, client_id, exc,
            )
            return False
        return True


# ── Bridge context helpers (Phase 35 hardening) ──────────────────────────


def get_bridge_state(registry: Any) -> Any:
    """Extract BridgeState from registry.bridge_context.

    Handlers call this instead of importing miqi.bridge.server directly.
    Returns the BridgeState object or raises AppServerError.
    """
    ctx = getattr(registry, "bridge_context", {})
    state = ctx.get("state")
    if state is None:
        raise AppServerError("Bridge state not available", code="INTERNAL")
    return state


def get_bridge_context(registry: Any, key: str, default: Any = None) -> Any:
    """Get an arbitrary value from registry.bridge_context."""
    ctx = getattr(registry, "bridge_context", {})
    return ctx.get(key, default)


# ── Protocol introspection handlers ──────────────────────────────────────


def register_protocol_handlers(server: "AppServer") -> None:
    """Register protocol introspection handlers."""

    async def _protocol_catalog(request_id, params, client_id, session_id, registry):
        return {"result": server.protocol_catalog()}

    server.register_method(
        "protocol/catalog",
        _protocol_catalog,
        spec=ProtocolMethodSpec(
            method="protocol/catalog",
            stability=MethodStability.STABLE,
            scope=MethodScope.CONNECTION,
            params_schema={"type": "object"},
            result_schema={"type": "object"},
            description="Return the App Server protocol method catalog.",
        ),
    )


# ── Built-in handler registration ────────────────────────────────────────


def register_replay_handlers(server: "AppServer") -> None:
    """Register replay/debug API handlers on an AppServer instance."""
    from miqi.runtime.replay_app_handlers import register_replay_handlers as _register

    _register(server)


def register_command_handlers(server: "AppServer") -> None:
    """Register command handlers (thread, abort, config) on AppServer.

    These handlers delegate to RuntimeSession for all operations,
    ensuring the runtime has full visibility into state mutations.
    Called by transport adapters during init.
    """
    from miqi.runtime.thread_request_models import validate_thread_params

    # ── thread.create ────────────────────────────────────────────────────
    async def _thread_create(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread.create", params)
        session = await registry.get_session(client_id, session_id)
        if session is None:
            raise AppServerError("Not authorized", code="UNAUTHORIZED")
        threads = session.services.thread_runtime
        if threads is None:
            raise AppServerError("Thread runtime not available", code="INTERNAL")
        thread = await threads.create_thread(
            title=typed.title or "New thread",
            thread_id=typed.thread_id,
        )
        return {"result": {
            "thread_id": thread.thread_id,
            "title": thread.title,
            "parent_thread_id": thread.parent_thread_id,
        }}

    # ── thread.list ──────────────────────────────────────────────────────
    async def _thread_list(request_id, params, client_id, session_id, registry):
        validate_thread_params("thread.list", params)
        session = await registry.get_session(client_id, session_id)
        if session is None:
            raise AppServerError("Not authorized", code="UNAUTHORIZED")
        threads = session.services.thread_runtime
        if threads is None:
            return {"result": {"threads": []}}
        result = await threads.list_threads()
        return {"result": {"threads": [
            {"thread_id": t.thread_id, "title": t.title, "status": t.status}
            for t in result
        ]}}

    # ── thread.rename ────────────────────────────────────────────────────
    async def _thread_rename(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread.rename", params)
        session = await registry.get_session(client_id, session_id)
        if session is None:
            raise AppServerError("Not authorized", code="UNAUTHORIZED")
        threads = session.services.thread_runtime
        if threads is None:
            raise AppServerError("Thread runtime not available", code="INTERNAL")
        thread = await threads.rename_thread(typed.thread_id, typed.title)
        return {"result": {"thread_id": thread.thread_id, "title": thread.title}}

    # ── thread.archive ───────────────────────────────────────────────────
    async def _thread_archive(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread.archive", params)
        session = await registry.get_session(client_id, session_id)
        if session is None:
            raise AppServerError("Not authorized", code="UNAUTHORIZED")
        threads = session.services.thread_runtime
        if threads is None:
            raise AppServerError("Thread runtime not available", code="INTERNAL")
        thread = await threads.archive_thread(typed.thread_id)
        return {"result": {"thread_id": thread.thread_id, "status": thread.status}}

    # ── thread.delete ────────────────────────────────────────────────────
    async def _thread_delete(request_id, params, client_id, session_id, registry):
        typed = validate_thread_params("thread.delete", params)
        session = await registry.get_session(client_id, session_id)
        if session is None:
            raise AppServerError("Not authorized", code="UNAUTHORIZED")
        threads = session.services.thread_runtime
        if threads is None:
            raise AppServerError("Thread runtime not available", code="INTERNAL")
        await threads.delete_thread(typed.thread_id)
        return {"result": {"deleted": True}}

    # ── chat.abort ───────────────────────────────────────────────────────
    async def _chat_abort(request_id, params, client_id, session_id, registry):
        """Submit AbortTurn, then release the bridge-side turn lock (#797).

        The release is best-effort and runs even when the AbortTurn
        submission fails (a submit failure must not leave the session
        locked).  But a failed submission must NOT be reported as a
        successful abort — the turn keeps running, and reporting success
        would let the client show a false "stopped" state while the work
        continues.  Surface it as a recoverable ABORT_FAILED instead.
        """
        from miqi.protocol.commands import AbortTurn

        typed = validate_thread_params("chat.abort", params)
        session = await registry.get_session(client_id, session_id)
        if session is None:
            raise AppServerError("Not authorized", code="UNAUTHORIZED")
        # Match chat.send's thread resolution (loop.py): thread_id falls back to
        # the session_key, NOT a hardcoded "default".  A turn started without an
        # explicit thread registers its cancel event under session_key, so an
        # abort that resolved to "default" could never signal it (#542).
        thread_id = typed.thread_id or params.get("session_key") or "default"
        abort_submitted = False
        try:
            await session.submit(AbortTurn(thread_id=thread_id))
            abort_submitted = True
        except Exception as exc:
            # The abort must still release the turn lock below — a submit
            # failure (e.g. runtime teardown race) must not leave the session
            # locked.  Log and continue with the release.
            logger.warning("chat.abort: AbortTurn submit failed: {}", exc)
        # #797: interrupt must free the bridge-side turn lock immediately.
        # AbortTurn alone only ends the drain task when the runtime emits a
        # terminal event; a runtime stuck on a blocking tool call (WSL
        # subprocess ignores asyncio cancellation) never does, leaving the
        # session rejecting every new message with TURN_IN_PROGRESS for up
        # to STALE_TURN_TIMEOUT.  Release pops the drain from
        # _session_drain_tasks (it keeps draining in the background).
        release = get_bridge_context(registry, "release_turn_lock")
        if callable(release):
            try:
                release(session_id)
            except Exception as exc:
                logger.warning("chat.abort: turn-lock release failed: {}", exc)
        if not abort_submitted:
            raise AppServerError(
                "Failed to abort turn; the turn may still be running",
                code="ABORT_FAILED",
                recoverable=True,
            )
        return {"result": {"aborted": True}}

    import miqi.runtime.protocol_specs as protocol_specs

    server.register_method("thread.create", _thread_create, spec=protocol_specs.THREAD_CREATE_COMPAT)
    server.register_method("thread.list", _thread_list, spec=protocol_specs.THREAD_LIST_COMPAT)
    server.register_method("thread.rename", _thread_rename, spec=protocol_specs.THREAD_RENAME_COMPAT)
    server.register_method("thread.archive", _thread_archive, spec=protocol_specs.THREAD_ARCHIVE_COMPAT)
    server.register_method("thread.delete", _thread_delete, spec=protocol_specs.THREAD_DELETE_COMPAT)
    server.register_method("chat.abort", _chat_abort, spec=protocol_specs.CHAT_ABORT)
    logger.info("AppServer: registered command handlers")
