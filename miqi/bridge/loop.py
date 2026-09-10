"""Bridge runtime loop — persistent asyncio event loop for the bridge process.

Phase 27.1: Replaces the per-request asyncio.run() pattern with a single
persistent event loop. stdin is read by a background thread and pushed
into an asyncio.Queue; the persistent loop drains the queue and dispatches
each request through AppServer (for migrated methods) or legacy _dispatch().
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from typing import Any

from loguru import logger

CHAT_DRAIN_IDLE_TIMEOUT_SECONDS = 600

# #798: the frontend watchdog reports "后端 60s 无响应" after 60s without
# progress events, but a turn may legitimately stay silent for minutes
# (model thinking, long exec with no stdout).  The drain emits a heartbeat
# progress event this often so the frontend sees liveness; heartbeats carry
# only {stream: "heartbeat"} and the renderer skips displaying them.
CHAT_HEARTBEAT_INTERVAL_SECONDS = 10


# A drain task that outlives this is considered stale (stuck on WSL sandbox
# creation, which ignores asyncio cancellation) — the turn lock is force-
# released so the session recovers instead of waiting for TTL eviction (#563).
STALE_TURN_TIMEOUT = 300.0  # seconds (5 min)


class BridgeRuntimeLoop:
    """Persistent asyncio event loop for the bridge transport.

    Owns the AppServer, stdin reader, and dispatch loop. Created once
    in main() and runs for the lifetime of the bridge process.

    Usage:
        bridge = BridgeRuntimeLoop(
            send_func=_send,
            dispatch_legacy_func=_dispatch,
            dev_mode=False,
        )
        bridge.start()  # blocks until stdin closes
    """

    def __init__(
        self,
        *,
        send_func: Any = None,
        dispatch_legacy_func: Any = None,
        bridge_state: Any = None,
        dev_mode: bool = False,
    ):
        self._send = send_func
        self._dispatch_legacy = dispatch_legacy_func
        self._bridge_state = bridge_state  # BridgeState for config/provider
        self._dev_mode = dev_mode
        self._loop: asyncio.AbstractEventLoop | None = None
        self._app_server: Any = None
        self._stdin_queue: asyncio.Queue | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._pending_tasks: set[asyncio.Task] = set()
        self._terminal_sent: set[str] = set()  # prevent duplicate terminal events
        self._active_chat_tasks: dict[str, asyncio.Task] = {}  # req_id → drain task
        self._session_drain_tasks: dict[str, asyncio.Task] = {}  # session_id → drain
        # #797: drain tasks released by chat.abort (popped from
        # _session_drain_tasks but NOT cancelled) — they keep draining in the
        # background until the aborted turn's terminal event arrives, and the
        # next chat.send's drain awaits them so a stale terminal event can
        # never be misread as the new request's terminal.
        self._released_drain_tasks: dict[str, asyncio.Task] = {}
        # Phase 45: Codex-style connection state (initialize handshake)
        self._connection_state: Any = None  # Created in _init_app_server

    # ── public API ─────────────────────────────────────────────────────────

    @property
    def app_server(self) -> Any:
        """Return the AppServer instance (for tests)."""
        return self._app_server

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """Return the persistent event loop (for tests)."""
        return self._loop

    def start(self) -> None:
        """Create persistent loop, run the bridge, clean up.

        Blocks until stdin closes (EOF). Catches KeyboardInterrupt
        for graceful Ctrl+C shutdown.
        """
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        except KeyboardInterrupt:
            logger.info("BridgeRuntimeLoop interrupted")
        finally:
            # Cancel any remaining tasks
            try:
                self._cancel_all_tasks()
            except Exception:
                pass
            # Shutdown async generators
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()
            logger.info("BridgeRuntimeLoop stopped")

    # ── main coroutine ─────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Main coroutine running on the persistent loop.

        Sequence:
        1. Create and start AppServer
        2. Register event sink for Desktop transport
        3. Start stdin reader thread
        4. Drain request queue (blocking)
        5. Shutdown
        """
        # 1. Create AppServer
        await self._init_app_server()

        # 2. Register event sink so AppServer events reach Desktop stdout
        self._setup_event_sink()

        # 3. Expose _app_server global so legacy code can find it
        self._publish_app_server()

        # 4. Start stdin reader thread
        self._stdin_queue = asyncio.Queue(maxsize=256)
        self._shutdown_event = asyncio.Event()

        reader_thread = threading.Thread(
            target=self._stdin_reader,
            daemon=True,
            name="bridge-stdin-reader",
        )
        reader_thread.start()
        logger.info("BridgeRuntimeLoop: stdin reader started")

        # 5. Signal ready to Desktop (Electron bridge.ts waits for this)
        self._send({"type": "ready"})

        # 5.5. Start sandbox manager initialization in background.
        # First-run auto-install of WSL deps (apt-get) can take 60-120 s,
        # so we fire-and-forget it here after the ready signal to avoid
        # blocking the bridge handshake timeout.
        asyncio.create_task(self._init_sandbox_manager())

        # 6. Drain request queue
        await self._drain_loop()

        # 7. Shutdown
        await self._shutdown()

    # ── Sandbox manager initialization (background) ─────────────────────────

    async def _init_sandbox_manager(self) -> None:
        """Initialize the sandbox manager as a background task.

        Runs after the "ready" handshake so that first-run WSL dependency
        auto-install (apt-get install bubblewrap coreutils rsync) does not
        block the bridge startup timeout.

        On first-time installs the sandbox starts disabled so tools run
        on the host.  This method re-enables it before initialize() so
        that deps actually get installed.  get_or_create() still returns
        None until _initialized is set (by the end of initialize()).
        """
        if self._bridge_state is None:
            return
        try:
            self._bridge_state._ensure_sandbox_manager()
        except Exception:
            return
        sandbox_mgr = getattr(self._bridge_state, "_sandbox_manager", None)
        if sandbox_mgr is None or sandbox_mgr == "disabled":
            return

        # ── Auto-enable BEFORE initialize() ───────────────────────
        # The SandboxManager is created with enabled=False so tools
        # run locally during dep install.  Flip it to True now so
        # initialize() actually runs is_available() + install deps.
        # get_or_create() still returns None because _initialized is
        # still False, so tools keep running locally until the end
        # of this method.
        #
        # IMPORTANT: We set enabled=True in memory now (required for
        # initialize() to proceed), but we do NOT persist it to config
        # until initialize() succeeds.  If initialize() fails, the
        # config stays enabled=False so the sandbox retries on next
        # bridge restart instead of being permanently stuck.
        need_auto_enable = not sandbox_mgr.enabled
        if need_auto_enable:
            sandbox_mgr.enabled = True

        try:
            _init_start = time.monotonic()
            logger.info("Sandbox manager initialization starting (cold start may take 2-5 min)...")
            res = sandbox_mgr.initialize()
            if hasattr(res, "__await__"):
                ok = await res
            else:
                ok = False  # mock in tests — initialize() is not async
            _elapsed = time.monotonic() - _init_start
            logger.info(
                "Sandbox manager initialization finished in {:.1f}s: success={}",
                _elapsed, ok,
            )
        except Exception as exc:
            logger.warning("Sandbox manager initialization failed: {}", exc)
            # If we auto-enabled in memory but init failed, reset so
            # the next bridge restart retries instead of skipping.
            if need_auto_enable:
                sandbox_mgr.enabled = False
            if self._app_server is not None:
                try:
                    await self._app_server.emit_client_event(
                        "desktop",
                        "sandbox.ready",
                        {"enabled": getattr(sandbox_mgr, "enabled", True), "initialized": False, "error": str(exc)},
                    )
                except Exception:
                    pass
            return

        if ok:
            log_msg = "Sandbox manager initialized"
            if need_auto_enable:
                log_msg += " (auto-enabled after first-time install)"
                # Persist enabled=True NOW that init succeeded.
                # This avoids the one-way-door: if we save enabled=True
                # before initialize() and it fails, the config is stuck
                # in an inconsistent state forever.
                try:
                    from miqi.config.loader import save_config
                    config = self._bridge_state.load_config()
                    config.tools.sandbox.enabled = True
                    save_config(config)
                except Exception as exc:
                    logger.warning(
                        "sandbox auto-enable: config save failed: {}", exc,
                    )
            logger.info(log_msg)
        else:
            logger.info(
                "Sandbox manager not available — tools will run in RESTRICTED mode"
            )

        # Notify the frontend so the settings toggle updates.
        if self._app_server is not None:
            try:
                await self._app_server.emit_client_event(
                    "desktop",
                    "sandbox.ready",
                    {"enabled": getattr(sandbox_mgr, "enabled", True), "initialized": ok},
                )
            except Exception:
                pass  # best-effort notification
    # ── AppServer initialization ───────────────────────────────────────────

    async def _init_app_server(self) -> None:
        """Create AppServer with ClientSessionRegistry and register handlers."""
        from miqi.runtime.app_server import (
            AppServer,
            ClientSessionRegistry,
            register_command_handlers,
            register_replay_handlers,
        )

        registry = ClientSessionRegistry()
        # Phase 35 hardening: populate bridge_context so runtime handlers
        # can access shared state through the registry instead of importing
        # miqi.bridge.server directly.
        registry.bridge_context = {
            "state": self._bridge_state,
            "plugin_manager": (
                getattr(self._bridge_state, "_plugin_manager", None)
                if self._bridge_state else None
            ),
            "orchestrator": (
                getattr(self._bridge_state, "_orchestrator", None)
                if self._bridge_state else None
            ),
            "experience_store": None,  # lazily set by experience_handlers
        }
        self._app_server = AppServer(registry)
        # Phase 45: expose AppServer in bridge_context so handlers can
        # check client capabilities (e.g., experimentalApi).
        registry.bridge_context["app_server"] = self._app_server
        # #797: expose the turn-lock release so chat.abort (app_server
        # command handler) can free the bridge-side lock immediately.
        registry.bridge_context["release_turn_lock"] = self.release_turn_lock
        await self._app_server.start()

        import miqi.runtime.protocol_specs as protocol_specs

        # Register bridge-owned handlers
        self._app_server.register_method("status", self._status_handler, spec=protocol_specs.STATUS)

        # Register sandbox runtime toggle
        self._app_server.register_method(
            "sandbox.setEnabled", self._sandbox_set_enabled_handler,
        )

        # Register #854: allow_system_installs runtime toggle (no restart)
        self._app_server.register_method(
            "sandbox.setAllowSystemInstalls",
            self._sandbox_set_allow_system_installs_handler,
        )

        # Register Phase 27.3: chat.send through AppServer
        self._app_server.register_method("chat.send", self._chat_send_handler)

        # Register #740: discard an interrupted turn's execution snapshot
        self._app_server.register_method(
            "chat.discard_resume", self._chat_discard_resume_handler,
        )

        # Register Phase 27.4: agent.spawn/kill through AppServer
        self._app_server.register_method("agent.spawn", self._agent_spawn_handler)
        self._app_server.register_method("agent.kill", self._agent_kill_handler)

        # Register Phase 28.5: agent.list/get through same AgentControl as spawn/kill
        from miqi.runtime.agent_handlers import (
            agent_get_handler,
            agent_list_handler,
        )
        self._app_server.register_method("agent.list", agent_list_handler)
        self._app_server.register_method("agent.get", agent_get_handler)

        # Register Phase 26.5 replay handlers
        register_replay_handlers(self._app_server)

        # Register Phase 26.6 command handlers (thread.*, chat.abort)
        register_command_handlers(self._app_server)

        # Register Phase 36: Codex-style thread handlers
        from miqi.runtime.thread_app_handlers import register_codex_thread_handlers
        register_codex_thread_handlers(self._app_server)

        # Sandbox manager initialization is deferred to _run() after the
        # "ready" signal so that slow first-run auto-install of WSL
        # dependencies (apt-get) does not block the bridge handshake.
        # See _run() → _init_sandbox_manager().

        # Register Phase 37: Codex-style plugin and marketplace handlers
        from miqi.runtime.plugin_app_handlers import register_plugin_app_handlers
        register_plugin_app_handlers(self._app_server)

        # Register Phase 37: Codex-style MCP status handlers
        from miqi.runtime.mcp_app_handlers import register_mcp_app_handlers
        register_mcp_app_handlers(self._app_server)

        # Register Phase 37: Codex-style skills and hooks handlers
        from miqi.runtime.skills_app_handlers import register_skills_app_handlers
        register_skills_app_handlers(self._app_server)

        # Register Phase 38: Codex-style model, feature, permission, config handlers
        from miqi.runtime.model_app_handlers import register_model_app_handlers
        register_model_app_handlers(self._app_server)
        from miqi.runtime.feature_app_handlers import register_feature_app_handlers
        register_feature_app_handlers(self._app_server)
        from miqi.runtime.permission_profile_app_handlers import (
            register_permission_profile_app_handlers,
        )
        register_permission_profile_app_handlers(self._app_server)
        from miqi.runtime.config_app_handlers import register_config_app_handlers
        register_config_app_handlers(self._app_server)

        # Register Phase 28.2: approvals.* handlers (session-scoped)
        from miqi.runtime.approval_handlers import (
            approvals_add_permanent_handler,
            approvals_clear_permanent_handler,
            approvals_history_handler,
            approvals_list_handler,
            approvals_resolve_handler,
        )
        self._app_server.register_method("approvals.list", approvals_list_handler)
        self._app_server.register_method("approvals.resolve", approvals_resolve_handler)
        self._app_server.register_method("approvals.clear_permanent", approvals_clear_permanent_handler)
        self._app_server.register_method("approvals.add_permanent", approvals_add_permanent_handler)
        self._app_server.register_method("approvals.history", approvals_history_handler)

        # Register userInput.resolve (issue #646: ask_user_confirm_card)
        from miqi.runtime.app_server import AppServerError

        async def _user_input_resolve_handler(
            request_id: str, params: dict, client_id: str,
            session_id: str | None, registry: Any,
        ) -> dict:
            """Resolve a pending confirm card (desktop → shared user-input gate)."""
            from miqi.agent.user_input_resolver import (
                pending_thread_for_input,
                resolve_user_input,
            )

            input_id = params.get("input_id", "")
            if not input_id:
                raise AppServerError("input_id is required", code="INVALID_PARAMS")
            # Authorization: only a client that owns the session the card
            # belongs to may resolve it — mirrors approvals.resolve
            # (CodeRabbit #711). The card's thread maps to its session key,
            # and registry session ids are "{client_id}:{session_key}".
            owner_thread = pending_thread_for_input(input_id)
            if owner_thread is not None:
                from miqi.agent.user_input_resolver import session_for_thread

                owner_session = session_for_thread(owner_thread) or owner_thread
                owned = any(
                    sid == owner_session or sid.endswith(f":{owner_session}")
                    for sid in registry.list_sessions(client_id)
                )
                if not owned:
                    raise AppServerError(
                        "Not authorized to resolve this card", code="UNAUTHORIZED",
                    )
            choice_id = params.get("choice_id", "")
            choice_label = params.get("choice_label", "")
            remember = bool(params.get("remember", False))
            answers = {}
            if choice_id:
                answers = {"choice_id": choice_id, "choice_label": choice_label}
            resolved = resolve_user_input(input_id, answers, remember=remember)
            return {"result": {"resolved": resolved}}

        self._app_server.register_method("userInput.resolve", _user_input_resolve_handler)

        # Register Phase 28.3: config.* handlers
        from miqi.runtime.config_handlers import (
            config_get_handler,
            config_update_handler,
        )
        self._app_server.register_method("config.get", config_get_handler, spec=protocol_specs.CONFIG_GET)
        self._app_server.register_method("config.update", config_update_handler, spec=protocol_specs.CONFIG_UPDATE)

        # Register Phase 28.4: sessions.* handlers
        from miqi.runtime.session_handlers import (
            sessions_archive_handler,
            sessions_claim_legacy_handler,
            sessions_clear_tracked_files_handler,
            sessions_delete_handler,
            sessions_get_handler,
            sessions_get_tracked_files_handler,
            sessions_list_archived_handler,
            sessions_list_handler,
            sessions_list_recent_workspaces_handler,
            sessions_rename_handler,
            sessions_unarchive_handler,
        )
        self._app_server.register_method("sessions.list", sessions_list_handler, spec=protocol_specs.SESSIONS_LIST)
        self._app_server.register_method("sessions.get", sessions_get_handler, spec=protocol_specs.SESSIONS_GET)
        self._app_server.register_method("sessions.delete", sessions_delete_handler, spec=protocol_specs.SESSIONS_DELETE)
        self._app_server.register_method("sessions.archive", sessions_archive_handler, spec=protocol_specs.SESSIONS_ARCHIVE)
        self._app_server.register_method("sessions.unarchive", sessions_unarchive_handler, spec=protocol_specs.SESSIONS_UNARCHIVE)
        self._app_server.register_method("sessions.list_archived", sessions_list_archived_handler, spec=protocol_specs.SESSIONS_LIST_ARCHIVED)
        self._app_server.register_method("sessions.get_tracked_files", sessions_get_tracked_files_handler, spec=protocol_specs.SESSIONS_GET_TRACKED_FILES)
        self._app_server.register_method("sessions.clear_tracked_files", sessions_clear_tracked_files_handler, spec=protocol_specs.SESSIONS_CLEAR_TRACKED_FILES)
        self._app_server.register_method("sessions.claim_legacy", sessions_claim_legacy_handler, spec=protocol_specs.SESSIONS_CLAIM_LEGACY)
        self._app_server.register_method("sessions.list_recent_workspaces", sessions_list_recent_workspaces_handler)
        self._app_server.register_method("sessions.rename", sessions_rename_handler, spec=protocol_specs.SESSIONS_RENAME)

        # Register Phase 30: files.* handlers (client-scoped ownership)
        from miqi.runtime.file_handlers import (
            files_accept_handler,
            files_delete_handler,
            files_diff_handler,
            files_read_handler,
            files_revert_handler,
            files_tree_handler,
            files_write_handler,
        )
        self._app_server.register_method("files.tree", files_tree_handler)
        self._app_server.register_method("files.read", files_read_handler)
        self._app_server.register_method("files.write", files_write_handler)
        self._app_server.register_method("files.delete", files_delete_handler)
        self._app_server.register_method("files.diff", files_diff_handler)
        self._app_server.register_method("files.revert", files_revert_handler)
        self._app_server.register_method("files.accept", files_accept_handler)

        # Register documents.* handlers
        from miqi.documents.documents_parse_handler import (
            documents_parse_handler,
        )
        self._app_server.register_method("documents.parse", documents_parse_handler)

        # Register Phase 35.2: providers.* handlers
        from miqi.runtime.provider_handlers import (
            providers_activate_handler,
            providers_deactivate_handler,
            providers_list_handler,
            providers_test_handler,
            providers_update_handler,
        )
        self._app_server.register_method("providers.list", providers_list_handler)
        self._app_server.register_method("providers.test", providers_test_handler)
        self._app_server.register_method("providers.update", providers_update_handler)
        self._app_server.register_method("providers.activate", providers_activate_handler)
        self._app_server.register_method("providers.deactivate", providers_deactivate_handler)

        # Register Phase 35.2: channels.* handlers
        from miqi.runtime.channel_handlers import (
            channels_list_handler,
            channels_update_handler,
        )
        self._app_server.register_method("channels.list", channels_list_handler)
        self._app_server.register_method("channels.update", channels_update_handler)

        # Register Phase 35.2: permissions.* handlers
        from miqi.runtime.permission_handlers import (
            permissions_get_handler,
            permissions_permanent_add_handler,
            permissions_permanent_remove_handler,
            permissions_update_handler,
        )
        self._app_server.register_method("permissions.get", permissions_get_handler)
        self._app_server.register_method("permissions.update", permissions_update_handler)
        self._app_server.register_method("permissions.permanent.add", permissions_permanent_add_handler)
        self._app_server.register_method("permissions.permanent.remove", permissions_permanent_remove_handler)

        # Register Phase 35.3: plugins.* handlers
        from miqi.runtime.plugin_handlers import (
            plugins_install_handler,
            plugins_list_handler,
            plugins_toggle_handler,
            plugins_uninstall_handler,
        )
        self._app_server.register_method("plugins.list", plugins_list_handler)
        self._app_server.register_method("plugins.install", plugins_install_handler)
        self._app_server.register_method("plugins.uninstall", plugins_uninstall_handler)
        self._app_server.register_method("plugins.toggle", plugins_toggle_handler)

        # Register Phase 35.4: mcp.* handlers
        from miqi.runtime.mcp_handlers import (
            mcp_delete_handler,
            mcp_list_handler,
            mcp_upsert_handler,
        )
        self._app_server.register_method("mcp.list", mcp_list_handler)
        self._app_server.register_method("mcp.upsert", mcp_upsert_handler)
        self._app_server.register_method("mcp.delete", mcp_delete_handler)

        # Register Phase 35.5: skills.* handlers
        from miqi.runtime.skill_handlers import (
            skills_create_handler,
            skills_delete_handler,
            skills_get_handler,
            skills_list_handler,
            skills_open_folder_handler,
            skills_upload_handler,
        )
        self._app_server.register_method("skills.list", skills_list_handler)
        self._app_server.register_method("skills.get", skills_get_handler)
        self._app_server.register_method("skills.open_folder", skills_open_folder_handler)
        self._app_server.register_method("skills.create", skills_create_handler)
        self._app_server.register_method("skills.upload", skills_upload_handler)
        self._app_server.register_method("skills.delete", skills_delete_handler)

        # Register Phase 35.6: cron.* handlers
        from miqi.runtime.cron_handlers import (
            cron_create_handler,
            cron_delete_handler,
            cron_list_handler,
            cron_run_handler,
            cron_runs_handler,
            cron_toggle_handler,
            cron_update_handler,
        )
        self._app_server.register_method("cron.list", cron_list_handler)
        self._app_server.register_method("cron.create", cron_create_handler)
        self._app_server.register_method("cron.update", cron_update_handler)
        self._app_server.register_method("cron.delete", cron_delete_handler)
        self._app_server.register_method("cron.toggle", cron_toggle_handler)
        self._app_server.register_method("cron.run", cron_run_handler)
        self._app_server.register_method("cron.runs", cron_runs_handler)

        # Register Phase 35.7: memory.* handlers
        from miqi.runtime.memory_handlers import (
            memory_delete_handler,
            memory_get_handler,
            memory_lesson_unlearn_handler,
            memory_lessons_handler,
            memory_list_handler,
            memory_update_handler,
        )
        self._app_server.register_method("memory.list", memory_list_handler)
        self._app_server.register_method("memory.get", memory_get_handler)
        self._app_server.register_method("memory.update", memory_update_handler)
        self._app_server.register_method("memory.delete", memory_delete_handler)
        self._app_server.register_method("memory.lessons", memory_lessons_handler)
        self._app_server.register_method("memory.lesson.unlearn", memory_lesson_unlearn_handler)

        # Register Phase 35.7: experience.* handlers (colon-style names)
        from miqi.runtime.experience_handlers import (
            experience_delete_handler,
            experience_list_handler,
            experience_search_handler,
            experience_toggle_handler,
        )
        self._app_server.register_method("experience:list", experience_list_handler)
        self._app_server.register_method("experience:delete", experience_delete_handler)
        self._app_server.register_method("experience:toggle", experience_toggle_handler)
        self._app_server.register_method("experience:search", experience_search_handler)

        # Register Phase 35.8: feedback handlers
        from miqi.runtime.feedback_handlers import (
            feedback_list_handler,
            feedback_submit_handler,
        )
        self._app_server.register_method("feedback:submit", feedback_submit_handler)
        self._app_server.register_method("feedback:list", feedback_list_handler)

        # Register Phase 35.9: diagnostic handlers
        from miqi.runtime.diagnostic_handlers import python_check_handler
        self._app_server.register_method("python.check", python_check_handler, spec=protocol_specs.PYTHON_CHECK)

        # Register Phase 41: Codex-style active turn handlers
        from miqi.runtime.turn_app_handlers import register_codex_turn_handlers
        register_codex_turn_handlers(self._app_server)

        # Register Phase 42: Codex-style thread/shellCommand handler
        from miqi.runtime.shell_command_app_handlers import register_shell_command_handlers
        register_shell_command_handlers(self._app_server)

        # Register Phase 43: Codex-style workbench command/exec and process/* handlers
        from miqi.runtime.workbench_command_app_handlers import register_workbench_command_handlers
        from miqi.runtime.workbench_process_app_handlers import register_workbench_process_handlers
        register_workbench_command_handlers(self._app_server)
        register_workbench_process_handlers(self._app_server)

        # Phase 44: register workbench process state handlers (list/read/history)
        from miqi.runtime.workbench_process_state_app_handlers import (
            register_workbench_process_state_handlers,
        )
        register_workbench_process_state_handlers(self._app_server)

        # Phase 45: register Codex-style initialize/initialized handlers
        from miqi.runtime.initialize_protocol import (
            ConnectionState,
            register_initialize_handler,
        )
        self._connection_state = ConnectionState()
        register_initialize_handler(self._app_server)

        # Phase 46: register Codex-style fs/* handlers
        from miqi.runtime.fs_app_handlers import register_fs_handlers
        from miqi.runtime.fs_watch_app_handlers import register_fs_watch_handlers
        from miqi.runtime.fuzzy_file_search_app_handlers import (
            register_fuzzy_file_search_handlers,
        )
        register_fs_handlers(self._app_server)
        register_fs_watch_handlers(self._app_server)
        register_fuzzy_file_search_handlers(self._app_server)

        # Phase 43: register client cleanup hook so workbench processes
        # are killed when a client disconnects or AppServer stops.
        async def _kill_client_processes(client_id: str) -> None:
            wpr = registry.bridge_context.get("workbench_process_runtime")
            if wpr is not None:
                await wpr.kill_client(client_id)

        # Phase 46: cleanup hook for fs watch and fuzzy search runtimes
        async def _cleanup_phase46_client_resources(client_id: str) -> None:
            fs_watch_runtime = registry.bridge_context.get("fs_watch_runtime")
            if fs_watch_runtime is not None:
                await fs_watch_runtime.cleanup_client(client_id)

            fuzzy_runtime = registry.bridge_context.get(
                "fuzzy_file_search_runtime",
            )
            if fuzzy_runtime is not None:
                fuzzy_runtime.cleanup_client(client_id)

        self._app_server.add_client_cleanup_hook(_kill_client_processes)
        self._app_server.add_client_cleanup_hook(_cleanup_phase46_client_resources)

        logger.info(
            "BridgeRuntimeLoop: AppServer initialized with {} methods",
            len(self._app_server._methods),
        )

    async def _status_handler(
        self, _request_id: str, _params: dict, _client_id: str,
        _session_id: str | None, _registry: Any,
    ) -> dict:
        """Bridge status check — session-less handler."""
        from miqi.paths import get_config_path
        config_exists = get_config_path()

        # Check whether bwrap sandbox is actually usable
        sandbox_available = False
        if self._bridge_state is not None:
            sm = getattr(self._bridge_state, "_sandbox_manager", None)
            if sm is not None and sm != "disabled":
                sandbox_available = getattr(sm, "enabled", False) and getattr(sm, "_initialized", False)

        return {
            "result": {
                "status": "ok",
                "configured": config_exists.exists(),
                "python_version": sys.version,
                "sandbox_available": sandbox_available,
            },
        }

    # ── turn-lock release (issue #797) ───────────────────────────────────

    def release_turn_lock(self, session_id: str) -> bool:
        """Release the bridge-side turn lock for *session_id* without
        killing the underlying drain task (#797).

        chat.abort only submits AbortTurn to the runtime; the drain task
        ends (and the lock frees) only when it reads a terminal event from
        the session queue.  If the runtime is stuck on a blocking tool call
        (WSL subprocesses don't respond to asyncio cancellation) no
        terminal event arrives and the session stays locked until the
        STALE_TURN_TIMEOUT guard — rejecting every new message with
        TURN_IN_PROGRESS.

        This pops the drain task from _session_drain_tasks (freeing the
        lock immediately) but leaves it running in the background: it keeps
        consuming the aborted turn's events and emits the terminal event
        for the ORIGINAL request, so nothing stale leaks into a later
        drain.  Bounded: the drain's own 600s idle timeout terminates it.
        """
        if not session_id:
            return False
        task = self._session_drain_tasks.pop(session_id, None)
        if task is None or task.done():
            return False
        self._released_drain_tasks[session_id] = task
        # Drop the _active_chat_tasks entry so shutdown/gc accounting no
        # longer treats the released drain as an in-flight chat request.
        for req_id, t in list(self._active_chat_tasks.items()):
            if t is task:
                self._active_chat_tasks.pop(req_id, None)
                break
        task.add_done_callback(
            lambda t: self._released_drain_tasks.pop(session_id, None)
            if self._released_drain_tasks.get(session_id) is t else None
        )
        logger.warning(
            "chat.abort: released turn lock for session {} "
            "(drain keeps draining in background until terminal event)",
            session_id,
        )
        return True

    async def _await_released_predecessor(self, session_id: str) -> None:
        """#797: wait for a released (aborted) predecessor drain to finish.

        A chat.send that lands while the aborted turn is still draining
        must not consume that turn's terminal event as its own.  The
        predecessor (if any) is the older queue waiter, but waiting on it
        explicitly removes the race entirely: when it ends it has consumed
        every event of the aborted turn, so this drain only ever sees its
        own turn's events.
        """
        predecessor = self._released_drain_tasks.get(session_id)
        if predecessor is not None and not predecessor.done():
            try:
                await asyncio.wait([predecessor])
            except Exception as exc:
                # A predecessor failure must not block this drain; it is
                # bounded by its own idle timeout and will be cleaned up.
                logger.debug(
                    "chat.send: released predecessor drain errored while awaited: {}", exc
                )

    # ── chat.send handler ──────────────────────────────────────────────────

    async def _chat_send_handler(
        self, request_id: str, params: dict, client_id: str,
        session_id: str | None, registry: Any,
    ) -> dict:
        """AppServer handler for chat.send.

        Submits the user message to RuntimeSession, spawns a background
        task to drain events, and returns immediately with {"accepted": true}.
        Streaming events are forwarded through AppServer.emit_event().
        """
        from miqi.protocol.commands import UserMessage

        content = params.get("content") or ""
        resume_turn_id = params.get("resume_turn_id")
        if not content and not resume_turn_id:
            from miqi.runtime.app_server import AppServerError

            raise AppServerError(
                "content (or resume_turn_id) is required", code="INVALID_PARAMS"
            )

        session_key = params.get("session_key", "desktop:default")
        thread_id = params.get("thread_id", session_key)

        # Persist any explicit workspace param into the session metadata
        # UNCONDITIONALLY (not only when the runtime is created): the sandbox
        # workspace resolver (server.py:_session_workspace) and later sends
        # read it from there. The UI picker persists via sessions.get(
        # workspace=...); the API-only chat.send path must do the same or
        # exec's sandbox stays on the DEFAULT workspace for a custom-workspace
        # session (caught by the #607 MOF e2e).
        ws_param = params.get("workspace")
        if ws_param:
            try:
                from pathlib import Path as _Path

                from miqi.session.manager import SessionManager

                _sm = SessionManager(self._bridge_state.load_config().workspace_path)
                _validated = _sm.get_or_create(
                    session_key, client_id=client_id, workspace=_Path(ws_param)
                )
                _sm.save(_validated)
                logger.info("chat.send: persisted session workspace: {}", _validated.metadata.get("workspace"))
            except Exception as exc:
                logger.debug("chat.send: failed to persist session workspace: {}", exc)

        # Get or create RuntimeSession
        runtime_id = session_id or f"{client_id}:{session_key}"
        runtime = await registry.get_session(client_id, runtime_id)

        if runtime is None:
            if self._bridge_state is None:
                from miqi.runtime.app_server import AppServerError

                raise AppServerError(
                    "Bridge state not available for session creation",
                    code="INTERNAL",
                )
            config = self._bridge_state.load_config()

            # Resolve per-session workspace only when the runtime is actually
            # created — doing it on every chat.send would read the session
            # file from disk each message and discard the result.
            # 1. chat.send payload (authoritative — set by frontend)
            # 2. Session metadata (fallback)
            # 3. Global config workspace_path (final fallback)
            from pathlib import Path as _Path

            session_workspace: _Path | None = None

            # 1. Direct from chat.send params (preferred) — validate like session
            #    metadata (absolute path, no traversal) so a malicious/typo path
            #    can't steer the RuntimeSession or sandbox outside allowed roots.
            ws_param = params.get("workspace")
            if ws_param:
                try:
                    from miqi.session.manager import SessionManager

                    session_workspace = SessionManager._validate_workspace(_Path(ws_param))
                except Exception as exc:
                    # Invalid/insecure path → ignore and fall through to
                    # metadata/config resolution.
                    logger.debug("chat.send: invalid workspace param, falling back: {}", exc)
                    session_workspace = None

            # 2. Fallback: read from session metadata on disk
            if session_workspace is None:
                try:
                    from miqi.session.manager import SessionManager

                    sm = SessionManager(config.workspace_path)
                    sess = sm.get_or_create(session_key, client_id=client_id)
                    ws_meta = sess.metadata.get("workspace")
                    if ws_meta:
                        session_workspace = _Path(ws_meta)
                except Exception as exc:
                    logger.debug("chat.send: failed to read session workspace metadata: {}", exc)

            from miqi.providers.factory import make_provider

            try:
                provider = make_provider(config)
            except ValueError as exc:
                from miqi.runtime.app_server import AppServerError
                logger.warning("make_provider failed during chat.send: {}", exc)
                raise AppServerError(
                    "No API key configured — set one in Settings > Models",
                    code="NO_API_KEY",
                ) from exc
            self._bridge_state._ensure_sandbox_manager()
            sandbox_manager = getattr(self._bridge_state, "_sandbox_manager", None)
            if sandbox_manager == "disabled":
                sandbox_manager = None
            runtime = await registry.create_session(
                client_id=client_id,
                session_key=session_key,
                config=config,
                provider=provider,
                workspace=session_workspace or config.workspace_path,
                sandbox_manager=sandbox_manager,
            )

        # Reasoning mode (issue #680): persist fast/think into the thread
        # metadata so the KUN loop reads it on every model step (max_tokens,
        # round fuse, search fan-out all key off thread.metadata.mode).
        # ── Parse document attachments before submitting ────────────────
        # Extract text from uploaded documents (PDF/Office/MD) and inject
        # into the message content so the LLM can immediately understand them.
        attachments_raw = params.get("attachments") or []
        if attachments_raw:
            # Ensure config is available (may not be set if session already existed)
            try:
                _ = config
            except NameError:
                config = self._bridge_state.load_config() if self._bridge_state else None
            if config is None:
                from miqi.runtime.app_server import AppServerError
                raise AppServerError("Config not available for attachment save", code="INTERNAL")
            import asyncio as _asyncio
            import base64 as _b64
            import re as _re
            from pathlib import Path as _Path

            ws_root = config.workspace_path
            # Save to session files directory so tools + documents.parse find it
            safe_key = session_key.replace(":", "_")
            dest_dir = ws_root / "sessions" / safe_key / "files"
            dest_dir.mkdir(parents=True, exist_ok=True)

            async def _emit_doc_progress(name: str, stage: str, message: str) -> None:
                try:
                    await self._app_server.emit_client_event(client_id, "progress", {
                        "type": "doc_progress",
                        "file": name,
                        "stage": stage,
                        "message": message,
                    })
                except Exception:
                    pass

            async def _decode_and_parse(att: dict) -> tuple[str, str] | None:
                """Decode attachment, save to disk, parse content."""
                name = (att.get("name") or "").strip()
                data_b64 = (att.get("data_base64") or "").strip()
                if not name or not data_b64:
                    return None
                try:
                    raw = _b64.b64decode(data_b64)
                except Exception as exc:
                    logger.warning("chat.send: base64 decode failed for {}: {}", name, exc)
                    return None

                safe_name = _re.sub(r'[<>:"/\\\\|?*]', '_', name)
                dest = dest_dir / safe_name
                counter = 0
                while dest.exists():
                    stem, ext = (safe_name.rsplit(".", 1) + [""])[:2]
                    counter += 1
                    dest = dest_dir / f"{stem}_{counter}.{ext}" if ext else dest_dir / f"{stem}_{counter}"
                dest.write_bytes(raw)
                await _emit_doc_progress(name, "saved", f"Saved ({len(raw) // 1024} KB)")

                # Parse document and extract text (offload to thread to avoid
                # blocking the persistent bridge event-loop).
                try:
                    from miqi.documents.document_parser import is_supported_document, parse_document
                    if is_supported_document(dest):
                        await _emit_doc_progress(name, "extracting", "Extracting text...")
                        result = await _asyncio.to_thread(parse_document, dest, max_chars=100_000)
                        text = result["text"]
                        ocr = result.get("ocr_used", False)
                        tag = " (OCR)" if ocr else ""
                        await _emit_doc_progress(name, "extracted",
                            f"Extracted {len(text):,} chars{tag}")
                        logger.info(
                            "chat.send: extracted {} chars from {} ocr={}",
                            len(text), name, ocr,
                        )
                        return (name, text)
                except Exception as exc:
                    logger.warning("chat.send: parse failed for {}: {}", name, exc)
                return (name, "")

            tasks = [_decode_and_parse(att) for att in attachments_raw]
            parsed = await _asyncio.gather(*tasks)

            doc_texts = []
            for r in parsed:
                if r is None:
                    continue
                doc_name, doc_text = r
                if doc_text:
                    doc_texts.append(
                        f"\n\n--- Document: {doc_name} ---\n{doc_text}\n--- End of {doc_name} ---"
                    )
                else:
                    doc_texts.append(
                        f"\n\n[Uploaded: {doc_name} — use pdf_read or read_file tool to access]"
                    )
                _emit_doc_progress(doc_name, "ready", "Ready")

            if doc_texts:
                content = content + "\n".join(doc_texts)

        # Reject duplicate turns for the same session BEFORE submitting:
        # cancelling the old drain task leaves abandoned sandbox creation
        # running (WSL subprocesses don't respond to asyncio cancellation),
        # which poisons the _creating flag and causes the next turn's tool
        # calls to fall back to local (non-sandboxed) execution. Also avoid
        # queueing a message that will be reported as rejected.
        mode = params.get("mode", "edit")
        logger.info(f"chat.send received mode={mode}")
        old = self._session_drain_tasks.get(runtime_id)
        if old is not None and not old.done():
            # Stale-turn guard: a drain task stuck on WSL sandbox creation
            # never completes, which would lock the session until TTL eviction.
            # After STALE_TURN_TIMEOUT of inactivity treat it as dead and
            # force-release the lock so the user can start a new turn (#563).
            last_activity = getattr(old, "_miqi_last_activity", None) or getattr(
                old, "_miqi_created_at", None
            )
            if last_activity is None or time.monotonic() - last_activity < STALE_TURN_TIMEOUT:
                from miqi.runtime.app_server import AppServerError

                raise AppServerError(
                    "A turn is already in progress for this session",
                    code="TURN_IN_PROGRESS",
                )
            logger.warning(
                "chat.send: stale turn for session {} inactive for {}s — force releasing turn lock",
                runtime_id, STALE_TURN_TIMEOUT,
            )
            self._session_drain_tasks.pop(runtime_id, None)
            old.cancel()  # best-effort; WSL sandbox creation may ignore it

        # Persist reasoning mode AFTER the TURN_IN_PROGRESS check: a rejected
        # duplicate request must not flip the active turn's mode (CodeRabbit #741).
        # NOTE: RuntimeSession has NO `thread_store` attribute (that lives on
        # KunRuntime) — write through services.thread_runtime (SQLite) instead.
        # Log failures loudly so a broken chain can't silently swallow the mode
        # (外部审阅 2026-08-24: AttributeError was previously swallowed by
        # logger.debug, so mode never persisted in production).
        mode_param = params.get("reasoning_mode") or params.get("mode")
        if mode_param in ("fast", "think"):
            try:
                # 双保险：services 可能为 None（审阅复核点 1）
                thread_runtime = getattr(getattr(runtime, "services", None), "thread_runtime", None)
                if thread_runtime is not None:
                    existing = await thread_runtime.get_thread(thread_id)
                    if existing is None:
                        try:
                            # New session's first message may arrive before any
                            # thread.create — create a minimal record so the
                            # mode is not lost.  Idempotent: a concurrent
                            # threads.start may win the INSERT race; IntegrityError
                            # is fine — we update metadata next anyway.
                            await thread_runtime.create_thread(title="New thread", thread_id=thread_id)
                        except sqlite3.IntegrityError:
                            # 并发 threads.start 已建同 id 线程 - 竞争正常, 忽略
                            pass
                    await thread_runtime.update_metadata(thread_id, {"mode": mode_param})
            except Exception as exc:
                logger.warning("chat.send: failed to persist reasoning mode: {}", exc)

        # Submit the user message
        await runtime.submit(UserMessage(
            content=content or "",
            thread_id=thread_id,
            mode=mode,
            resume_turn_id=resume_turn_id,
            # #680: pass the reasoning mode into the turn executor so the
            # desktop chain can apply the generation budget/prompts.
            reasoning_mode=mode_param if mode_param in ("fast", "think") else None,
        ))

        # Subscribe client to session events so emit_event delivers to the sink.
        # Must happen AFTER the TURN_IN_PROGRESS check to avoid leaking a
        # subscription when the duplicate-turn error is raised (#326).
        self._app_server.subscribe(client_id, runtime_id)

        # Spawn background drain task
        task = asyncio.create_task(
            self._drain_chat_events(
                request_id=request_id,
                runtime=runtime,
                thread_id=thread_id,
                session_id=runtime_id,
                session_key=session_key,
                client_id=client_id,
            )
        )
        task._miqi_created_at = time.monotonic()  # for the stale-turn guard
        self._active_chat_tasks[request_id] = task
        self._session_drain_tasks[runtime_id] = task
        # Clean up task reference when done
        task.add_done_callback(
            lambda t: self._active_chat_tasks.pop(request_id, None)
        )
        task.add_done_callback(
            lambda t: self._session_drain_tasks.pop(runtime_id, None)
            if self._session_drain_tasks.get(runtime_id) is t else None
        )

        logger.info(
            "chat.send: submitted turn for client={} session={} thread={}",
            client_id, runtime_id, thread_id,
        )
        return {"result": {"accepted": True}}

    async def _chat_discard_resume_handler(
        self, request_id: str, params: dict, client_id: str,
        session_id: str | None, registry: Any,
    ) -> dict:
        """#740: discard an interrupted turn's execution snapshot (重新开始).

        Active sessions delete via the live HistoryRuntime connection; inactive
        ones (after restart) fall back to a throwaway HistoryRuntime over the
        workspace db. Never raises for a missing snapshot.
        """
        from miqi.runtime.app_server import AppServerError

        resume_turn_id = params.get("resume_turn_id")
        if not resume_turn_id:
            raise AppServerError("resume_turn_id is required", code="INVALID_PARAMS")

        session_key = params.get("session_key", "desktop:default")
        runtime_id = session_id or f"{client_id}:{session_key}"
        runtime = await registry.get_session(client_id, runtime_id)
        deleted = False

        if runtime is not None:
            hr = getattr(runtime.services, "history_runtime", None)
            if hr is not None:
                await hr.delete_snapshot(resume_turn_id)
                deleted = True

        if not deleted and self._bridge_state is not None:
            try:
                from pathlib import Path as _Path

                from miqi.runtime.history_runtime import HistoryRuntime
                from miqi.session.manager import SessionManager

                config = self._bridge_state.load_config()
                sm = SessionManager(config.workspace_path)
                sess = sm.get_or_create(session_key, client_id=client_id)
                ws = sess.metadata.get("workspace") or config.workspace_path
                db_path = _Path(ws) / ".miqi-runtime" / "runtime.db"
                if db_path.exists():
                    hr = HistoryRuntime(db_path, session_id=runtime_id)
                    await hr.initialize()
                    try:
                        await hr.delete_snapshot(resume_turn_id)
                        deleted = True
                    finally:
                        await hr.close()
            except Exception as exc:
                logger.warning("discard_resume fallback failed: {}", exc)

        return {"result": {"deleted": deleted}}

    async def _drain_chat_events(
        self,
        request_id: str,
        runtime: Any,
        thread_id: str,
        session_id: str,
        client_id: str,
        session_key: str = "",
    ) -> None:
        """Background task: drain events from RuntimeSession and forward them.

        Runs on the persistent loop. Forwards progress/approval events via
        AppServer.emit_event(). Sends terminal event (final/error/aborted)
        when the turn completes.
        """
        app_server = self._app_server

        # #797: a chat.send that lands while the aborted turn is still
        # draining (lock released by chat.abort, drain kept alive in the
        # background) must wait for that predecessor to finish — otherwise a
        # stale TurnAbortedEvent from the old turn could be emitted as THIS
        # request's terminal.  The new turn cannot start until the old one
        # has fully cleaned up anyway (RuntimeSession is single-turn), so
        # this never delays a live turn.
        await self._await_released_predecessor(session_id)

        async def _emit(
            event_type: str,
            data: Any,
            *,
            refresh_activity: bool = True,
        ) -> int:
            """Emit a non-terminal event through AppServer fanout.

            Returns the number of clients the event was handed off to
            (0 = silently skipped) — delivery-sensitive callers (billing)
            treat a zero as a failed handoff.
            """
            # Inject session_key so the frontend can filter events
            # by session, preventing cross-session message leaks (#212).
            if isinstance(data, dict):
                data["session_key"] = session_key
            # Refresh inactivity timestamp: an active turn keeps producing
            # events and must never be force-released as stale (#563 review).
            # Heartbeats pass refresh_activity=False — they prove the
            # transport is alive, but a hung turn must still be released
            # by the STALE_TURN_TIMEOUT guard (#798 review).
            if refresh_activity:
                active = self._session_drain_tasks.get(session_id)
                if active is not None and not active.done():
                    active._miqi_last_activity = time.monotonic()
            return await app_server.emit_event(
                session_id, event_type, data,
                request_id=request_id,
            )

        async def _emit_terminal(event_type: str, data: Any) -> bool:
            """Send a terminal event for this chat request.

            Terminal chat events settle the Electron pending request, so they
            must not depend on session fanout. Fanout can legitimately skip
            delivery when a client is unsubscribed or a sink is missing; the
            current request still needs a deterministic terminal response.
            """
            if request_id in self._terminal_sent:
                return False
            self._terminal_sent.add(request_id)
            # Inject session_key so the frontend can filter (#212)
            if isinstance(data, dict):
                data["session_key"] = session_key
            self._send({
                "id": request_id,
                "type": event_type,
                "data": data,
            })
            return True

        heartbeat_task: asyncio.Task | None = None
        reasoning_chunks = 0
        reasoning_chars = 0

        try:
            from dataclasses import asdict, is_dataclass

            # Wire the shared user-input emitter so ask_user_confirm_card can
            # push user_input_requested events to THIS session (issue #646).
            # Keyed by session_key — the resolver dispatches by the turn's
            # thread_id, which equals session_key on the chat.send path.
            from miqi.agent.user_input_resolver import set_user_input_emitter

            async def _user_input_emitter(payload: dict) -> None:
                await _emit("user_input_requested", payload)

            set_user_input_emitter(session_key, _user_input_emitter)

            # Slurm MCP 计费握手（issue #927）：MCP 工具执行前经此通道向
            # Desktop 发起扣费请求；作业提交成功后回传作业 ID 补进历史。
            from miqi.agent.billing_resolver import set_billing_charge_emitter

            async def _billing_charge_emitter(payload: dict) -> int:
                # 返回送达的客户端数：MCP 工具侧据此决定是否标记作业已
                # 报告（0 送达不标记，下一次 RUNNING 轮询重试）。
                return await _emit("slurm_job_running", payload)

            # 双键注册：MCP 工具侧拿到的 _session_key 是 client 前缀的
            # session_id（f"{client_id}:{session_key}"），与 drain 的
            # session_key 不是同一个键——两个键都注册才能命中。
            set_billing_charge_emitter(session_key, _billing_charge_emitter)
            set_billing_charge_emitter(session_id, _billing_charge_emitter)

            from miqi.agent.user_input_resolver import set_thread_session

            set_thread_session(thread_id, session_key)

            # #798: heartbeat so the frontend watchdog sees backend
            # liveness during long silent stretches (model thinking,
            # exec with no stdout).  The drain's own idle timeout is
            # 600s; the frontend fires at 60s without this.
            async def _heartbeat() -> None:
                try:
                    while True:
                        await asyncio.sleep(CHAT_HEARTBEAT_INTERVAL_SECONDS)
                        await _emit(
                            "progress",
                            {"stream": "heartbeat"},
                            refresh_activity=False,
                        )
                except asyncio.CancelledError:
                    pass

            heartbeat_task = asyncio.create_task(_heartbeat())

            from miqi.protocol.events import (
                AgentMessageDeltaEvent,
                AgentMessageEvent,
                AgentReasoningEvent,
                ApprovalResolvedEvent,
                ErrorEvent,
                ExecCommandBeginEvent,
                ExecCommandEndEvent,
                ExecCommandOutputDeltaEvent,
                ToolCallBeginEvent,
                ToolCallEndEvent,
                ToolCallOutputDeltaEvent,
                TurnAbortedEvent,
                TurnCompleteEvent,
                TurnStartedEvent,
            )

            while True:
                # Keep in sync with CHAT_BACKEND_DRAIN_TIMEOUT_MS in
                # apps/desktop/src/main/bridge.ts.
                event = await runtime.next_event(timeout=CHAT_DRAIN_IDLE_TIMEOUT_SECONDS)
                if event is None:
                    logger.warning(
                        "chat.send drain idle timeout after {}s "
                        "(request={} session={} thread={})",
                        CHAT_DRAIN_IDLE_TIMEOUT_SECONDS,
                        request_id,
                        session_id,
                        thread_id,
                    )
                    await _emit_terminal("error", {
                        "code": "TIMEOUT",
                        "message": (
                            f"Turn 超时（"
                            f"{CHAT_DRAIN_IDLE_TIMEOUT_SECONDS}s）"
                        ),
                    })
                    break

                if isinstance(event, AgentMessageEvent):
                    final_payload = {
                        "content": event.content,
                        "aborted": False,
                        "turn_id": event.turn_id,
                        "tool_calls": event.tool_calls,
                    }
                    if event.reasoning:
                        final_payload["reasoning"] = event.reasoning
                    if event.reasoning_elapsed_s is not None:
                        final_payload["reasoning_elapsed_s"] = event.reasoning_elapsed_s
                    await _emit_terminal("final", final_payload)
                    # Do NOT break — consume the TurnCompleteEvent that
                    # follows so the next drain task starts with a clean queue.
                    continue

                if isinstance(event, TurnAbortedEvent):
                    await _emit_terminal("aborted", {
                        "reason": event.reason,
                        "turn_id": event.turn_id,
                    })
                    break

                if isinstance(event, ErrorEvent):
                    await _emit_terminal("error", {
                        "message": event.message,
                        "code": event.error_kind or "ERROR",
                        "turn_id": event.turn_id,
                    })
                    break

                if isinstance(event, TurnCompleteEvent):
                    # If we already sent final (AgentMessageEvent above),
                    # don't send another — just exit cleanly.
                    if request_id not in self._terminal_sent:
                        await _emit_terminal("final", {
                            "content": "",
                            "aborted": False,
                            "status": "completed",
                            "turn_id": event.turn_id,
                        })
                    break

                # Exec output deltas: forward with the top-level shape that
                # ChatConsole.tsx expects (stream / delta / tool_call_id)
                # so inline terminal output updates in real time.
                if isinstance(event, ExecCommandOutputDeltaEvent):
                    await _emit("progress", {
                        "stream": event.stream,
                        "delta": event.delta,
                        "tool_call_id": event.tool_call_id,
                    })
                    continue

                # Tool output deltas (paper_search_result cards etc.):
                # same top-level delta shape — ChatConsole parses
                # data.delta for structured payloads (issue #668 regression).
                if isinstance(event, ToolCallOutputDeltaEvent):
                    await _emit("progress", {
                        "delta": event.delta,
                        "tool_call_id": event.tool_call_id,
                        "tool_hint": True,
                    })
                    continue

                # Forward reasoning deltas live so the UI can render a
                # streaming thinking block (DeepSeek-R1 / Kimi thinking
                # models). Issue #539.
                if isinstance(event, AgentReasoningEvent):
                    reasoning_chunks += 1
                    reasoning_chars += len(event.content)
                    if reasoning_chunks % 10 == 0:
                        logger.info(
                            "forwarding reasoning_delta #{} (len={}) for turn={}",
                            reasoning_chunks, len(event.content), event.turn_id,
                        )
                    await _emit("progress", {
                        "stream": "reasoning",
                        "delta": event.content,
                    })
                    continue

                # Turn lifecycle: the turn_id lets the frontend correlate
                # terminal events (final/aborted/error) with the active turn
                # and drop stale events from superseded turns (#542).
                if isinstance(event, TurnStartedEvent):
                    await _emit("progress", {
                        "stream": "turn",
                        "turn_id": event.turn_id,
                    })
                    continue

                # Internal runtime events that should never appear in
                # the chat message stream.  See Issue #35.
                if isinstance(event, (
                    AgentMessageDeltaEvent,   # streaming delta; final content via AgentMessageEvent
                    ApprovalResolvedEvent,     # approval lifecycle; not chat content
                    ExecCommandBeginEvent,     # exec lifecycle; rendered via ToolCallBeginEvent
                    ExecCommandEndEvent,       # exec lifecycle; rendered via ToolCallEndEvent
                )):
                    continue

                # Forward all other events as progress
                event_name = event.__class__.__name__
                if is_dataclass(event):
                    payload = asdict(event)
                else:
                    payload = getattr(event, "__dict__", {})

                if event_name == "ApprovalRequestedEvent":
                    await _emit("approval_request", payload)
                elif isinstance(event, ToolCallBeginEvent):
                    await _emit("progress", {
                        "text": event.tool_display or event.tool_name,
                        "tool_hint": True,
                        "tool_call_id": event.tool_call_id,
                        "tool_args": event.arguments,
                    })
                elif isinstance(event, ToolCallEndEvent):
                    await _emit("progress", {
                        "text": f"{event.tool_name} ({event.duration_ms}ms)",
                        "tool_hint": True,
                        "tool_call_id": event.tool_call_id,
                        "tool_output": event.output_preview,
                    })
                else:
                    await _emit("progress", {
                        "event": event_name,
                        "data": payload,
                    })

        except asyncio.CancelledError:
            await _emit_terminal("aborted", {
                "message": "Chat aborted by user",
            })
        except Exception as exc:
            logger.warning(
                "chat.send drain error for request {}: {}", request_id, exc,
            )
            # Sanitize before sending to UI — raw exception may contain paths/URLs
            raw = str(exc)
            if len(raw) > 300:
                raw = raw[:300] + "…"
            await _emit_terminal("error", {
                "message": f"Bridge 事件循环错误：{raw}",
            })
        finally:
            # Stop the heartbeat — the turn is done (or the drain died).
            if reasoning_chunks:
                logger.info(
                    "chat.send drain done: forwarded {} reasoning chunks "
                    "({} chars) for session={}",
                    reasoning_chunks, reasoning_chars, session_id,
                )
            if heartbeat_task is not None:
                heartbeat_task.cancel()
            # Unwire THIS session's user-input emitter and thread mapping so
            # a finished turn's emitter can't linger and capture cards for a
            # later turn. Keyed per session — concurrent sessions keep their
            # own channels (CodeRabbit #711).
            from miqi.agent.user_input_resolver import (
                clear_thread_session,
                set_user_input_emitter,
            )

            clear_thread_session(thread_id)
            set_user_input_emitter(session_key, None)

    # ── agent.spawn / agent.kill handlers ──────────────────────────────────

    async def _agent_spawn_handler(
        self, request_id: str, params: dict, client_id: str,
        session_id: str | None, registry: Any,
    ) -> dict:
        """AppServer handler for agent.spawn."""
        from miqi.runtime.app_server import AppServerError

        session = await registry.get_session(client_id, session_id)
        if session is None:
            raise AppServerError("Not authorized", code="UNAUTHORIZED")

        ac = session.services.agent_control
        if ac is None:
            raise AppServerError(
                "Agent control not initialized", code="INTERNAL",
            )

        # #984 review: roots arriving over IPC bypass
        # ``extract_user_mentioned_roots`` and would reach
        # ``TurnContext.user_mentioned_roots`` — i.e. both the bwrap rw binds
        # and the command guard's write scope — unfiltered.  They are
        # re-filtered here, at the trust boundary, before they are stored on
        # the job.  (Defence in depth: the Desktop IPC has no ``user_roots``
        # field yet, so this path is not reachable from the shipped client.)
        from miqi.agent.tools.user_roots import sanitize_user_roots

        user_roots = sanitize_user_roots(
            params.get("user_roots") or [],
            workspace=getattr(ac, "workspace", None),
        )

        agent = await ac.spawn(
            agent_type=params.get("agent_type", "code-agent"),
            task=params.get("task", ""),
            label=params.get("label"),
            # #984: the Desktop may forward the parent turn's authorized
            # output roots; without them the sub-agent gets none (the server
            # has no other root source for this channel).
            user_roots=user_roots,
        )
        return {"result": {"agent_id": agent.agent_id, "thread_id": agent.thread_id}}

    async def _agent_kill_handler(
        self, request_id: str, params: dict, client_id: str,
        session_id: str | None, registry: Any,
    ) -> dict:
        """AppServer handler for agent.kill."""
        from miqi.runtime.app_server import AppServerError

        session = await registry.get_session(client_id, session_id)
        if session is None:
            raise AppServerError("Not authorized", code="UNAUTHORIZED")

        agent_id = params.get("agent_id", "")
        if agent_id:
            ac = session.services.agent_control
            if ac is not None:
                await ac.kill(agent_id)
        return {"result": {"killed": bool(agent_id)}}

    # ── event sink ─────────────────────────────────────────────────────────

    def _setup_event_sink(self) -> None:
        """Register the Desktop transport event sink on AppServer.

        Translates AppServer event envelopes to the legacy bridge
        wire format that Desktop expects:
          AppServer: {"request_id": ..., "event": ..., "data": ...}
          Bridge:    {"id": ..., "type": ..., "data": ...}
        """
        send = self._send

        async def _desktop_sink(envelope: dict) -> None:
            send({
                "id": envelope.get("request_id"),
                "type": envelope["event"],
                "data": envelope["data"],
            })

        self._app_server.set_event_sink("desktop", _desktop_sink)
        logger.debug("BridgeRuntimeLoop: desktop event sink registered")

    def _publish_app_server(self) -> None:
        """Set the _app_server module global in server.py for backward compat."""
        try:
            import miqi.bridge.server as bridge_module

            bridge_module._app_server = self._app_server
        except Exception:
            pass

    # ── stdin reader ───────────────────────────────────────────────────────

    def _stdin_reader(self) -> None:
        """Read lines from stdin and push them into the async queue.

        Runs in a daemon thread. Uses run_coroutine_threadsafe() to
        safely push items into the queue owned by the persistent loop.
        A None sentinel is pushed when stdin closes (EOF).
        """
        loop = self._loop
        queue = self._stdin_queue
        if loop is None or queue is None:
            return
        try:
            for line in sys.stdin:
                raw = line.strip()
                try:
                    asyncio.run_coroutine_threadsafe(queue.put(raw), loop)
                except Exception:
                    # Loop likely closed — stop reading
                    break
        except Exception:
            pass
        finally:
            # Signal EOF
            try:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop)
            except Exception:
                pass

    # ── dispatch ───────────────────────────────────────────────────────────

    async def _drain_loop(self) -> None:
        """Drain the stdin queue and dispatch each request.

        Phase 45: Enforces Codex-style initialize handshake:
        - Before initialize: only initialize/initialized allowed;
          all other methods return NOT_INITIALIZED.
        - After initialize: repeated initialize returns ALREADY_INITIALIZED.
        - initialized notification is silent (no response).
        - Normal requests use the connection's client_id; conflicting
          per-request client_id is rejected with INVALID_PARAMS.

        For methods registered on AppServer, dispatch through
        AppServer.dispatch() on the persistent loop.
        For legacy methods, call the sync dispatch function directly.
        """
        queue = self._stdin_queue
        if queue is None:
            logger.error("BridgeRuntimeLoop: stdin queue not initialized")
            return

        # ── Concurrent dispatch ─────────────────────────────────────────
        # Issue: a single slow request (e.g. first-time chat.send that
        # triggers sandbox install) used to block every subsequent request
        # because _drain_loop awaited dispatch serially.  We now dispatch
        # each line as an independent task so a slow chat.send does not
        # delay a fast thread/start (which is on the critical path for
        # the user's first message in a new conversation).
        #
        # Concurrency is bounded by a semaphore so a flood of stdin
        # lines cannot spawn an unbounded number of in-flight tasks.
        in_flight: set[asyncio.Task] = set()
        max_concurrent = 16
        sem = asyncio.Semaphore(max_concurrent)
        # Lazy init lock: must be created on the running event loop.
        # asyncio.Lock() is safe to create here (we are inside _drain_loop,
        # which is awaited from _run on the persistent loop).
        self._init_lock = asyncio.Lock()

        def _spawn(line: str) -> None:
            async def _run() -> None:
                async with sem:
                    await self._dispatch_one_line(line)

            task = asyncio.create_task(_run())
            in_flight.add(task)
            task.add_done_callback(in_flight.discard)

        while True:
            line = await queue.get()
            if line is None:  # EOF sentinel
                logger.info("BridgeRuntimeLoop: stdin closed, stopping dispatch")
                # Wait for any in-flight dispatch tasks to finish so that
                # their stdout responses are flushed before we return.
                if in_flight:
                    await asyncio.gather(*in_flight, return_exceptions=True)
                break
            if not line:
                continue
            _spawn(line)

    async def _dispatch_one_line(self, line: str) -> None:
        """Dispatch a single stdin line as an independent request.

        Extracted from _drain_loop so that a slow handler (e.g. a first-time
        chat.send that triggers sandbox install) does not block subsequent
        requests on the stdin queue.  The outer _drain_loop wraps each call
        in a fire-and-forget task; the AppServer's internal lock still
        serializes state-mutating operations on the session registry.
        """
        send = self._send
        app_server = self._app_server
        dispatch_legacy = self._dispatch_legacy
        conn_state = self._connection_state

        req_id = "?"
        try:
            req = json.loads(line)
            method = req.get("method", "")
            # Notifications may not have an 'id' field
            req_id = req.get("id", "?")
            params = req.get("params", {})

            # ── Phase 45: initialize handshake gate ──────────────────

            if method == "initialize":
                # Phase 45 hardening: reject repeated initialize
                # at the transport level before calling AppServer.
                if conn_state is not None and conn_state.initialized:
                    send({
                        "id": req_id,
                        "error": "Already initialized",
                        "code": "ALREADY_INITIALIZED",
                        "recoverable": False,
                    })
                    return

                response = await app_server.dispatch(
                    request_id=req_id,
                    method=method,
                    params=params,
                    client_id="pre-init",
                    session_id=None,
                )
                # If initialize succeeded, update connection state.
                # Use a per-loop lock to make initialize + connection-state
                # mutation atomic with respect to other in-flight tasks.
                async with self._init_lock:
                    if conn_state is not None and not conn_state.initialized:
                        if "result" in response:
                            result = response["result"]
                            cid = result.get("clientId")
                            if cid:
                                conn_state.client_id = cid
                                conn_state.initialized = True
                                conn_state.initialized_ack = False
                                # Re-register event sink under the connected client_id
                                self._migrate_event_sink(cid)
                send(response)
                return

            if method == "initialized":
                # Notification — no response, just advance state
                if conn_state is not None and conn_state.initialized:
                    conn_state.initialized_ack = True
                # Do NOT send a response for notifications
                return

            # ── NOT_INITIALIZED gate ────────────────────────────────

            if conn_state is None or not conn_state.initialized:
                send({
                    "id": req_id,
                    "error": "Not initialized",
                    "code": "NOT_INITIALIZED",
                    "recoverable": False,
                })
                return

            # ── Per-request client_id conflict check ────────────────

            params_client_id = (
                params.get("client_id")
                or params.get("caller_id")
                or params.get("user_id")
            )
            if params_client_id and params_client_id != conn_state.client_id:
                send({
                    "id": req_id,
                    "error": (
                        f"client_id mismatch: request claims "
                        f"{params_client_id} but connection is "
                        f"{conn_state.client_id}"
                    ),
                    "code": "INVALID_PARAMS",
                    "recoverable": False,
                })
                return

            # ── Normal dispatch with connection client_id ───────────

            client_id = conn_state.client_id or self._resolve_client_id(params)
            session_id = params.get("session_key") or params.get("session_id")
            # Namespace session_key with client_id so the registry
            # lookup matches {client_id}:{session_key} (create_session format).
            # session_key may already contain ":" (e.g. "desktop:default").
            if session_id and client_id:
                prefix = f"{client_id}:"
                if not session_id.startswith(prefix):
                    session_id = f"{prefix}{session_id}"

            # Check if this method is registered on AppServer
            if method in getattr(app_server, "_methods", {}):
                response = await app_server.dispatch(
                    request_id=req_id,
                    method=method,
                    params=params,
                    client_id=client_id,
                    session_id=session_id,
                )
                send(response)
            elif dispatch_legacy is not None:
                # Legacy handler path (sync functions)
                dispatch_legacy(req_id, method, params)
            else:
                send({
                    "id": req_id,
                    "error": f"Unknown method: {method}",
                    "code": "UNKNOWN_METHOD",
                    "recoverable": False,
                })
        except json.JSONDecodeError as exc:
            logger.warning("BridgeRuntimeLoop: invalid JSON: {}", exc)
            try:
                send({"id": req_id, "error": "Invalid JSON"})
            except Exception:
                pass
        except Exception:
            logger.error(
                "BridgeRuntimeLoop: unhandled error: {}",
                traceback.format_exc(),
            )
            try:
                send({"id": req_id, "error": "Internal bridge error"})
            except Exception:
                pass

    def _migrate_event_sink(self, client_id: str) -> None:
        """Re-register the Desktop event sink under the initialized *client_id*.

        After initialize, the connection has a stable client_id.  Move the
        sink from the legacy ``"desktop"`` key to the derived key so that
        ``emit_client_event`` and ``emit_event`` route to the right sink.
        Callers that still use ``"desktop"`` directly continue to work —
        the original sink registration is left in place.
        """
        app_server = self._app_server
        if app_server is None:
            return
        desktop_sink = app_server._event_sinks.get("desktop")
        if desktop_sink is not None:
            app_server.set_event_sink(client_id, desktop_sink)
            logger.debug(
                "BridgeRuntimeLoop: event sink migrated to client_id={}",
                client_id,
            )

    def _resolve_client_id(self, params: dict) -> str:
        """Resolve client_id from request params.

        Phase 27.5: client_id is REQUIRED in production mode. Missing
        client_id raises AppServerError with code INVALID_PARAMS.
        In dev_mode, a predictable dev- prefix is used for convenience.
        """
        raw = params.get("client_id") or params.get("caller_id") or params.get("user_id")
        if raw:
            return raw
        if self._dev_mode:
            return f"dev-{uuid.uuid4().hex[:6]}"
        from miqi.runtime.app_server import AppServerError

        raise AppServerError(
            "client_id is required",
            code="INVALID_PARAMS",
            recoverable=False,
        )

    # ── shutdown ───────────────────────────────────────────────────────────

    # ── Sandbox runtime toggle ─────────────────────────────────────────────
    async def _sandbox_set_enabled_handler(
        self, request_id: str, params: dict, client_id: str,
        session_id: str | None, registry: Any,
    ) -> dict:
        """AppServer handler for sandbox.setEnabled.

        Enables or disables the bwrap sandbox at runtime without restarting.
        When disabling, active sandboxes are destroyed.  When enabling, a new
        SandboxManager is created and initialized.  The config is persisted
        so the setting survives bridge restarts.
        """
        enabled = params.get("enabled", True)
        if not isinstance(enabled, bool):
            from miqi.runtime.app_server import AppServerError
            raise AppServerError(
                "sandbox.setEnabled: 'enabled' must be a boolean",
                code="INVALID_PARAMS",
            )

        if self._bridge_state is None:
            from miqi.runtime.app_server import AppServerError
            raise AppServerError(
                "Bridge state not available", code="INTERNAL",
            )

        # ── Persist to config ─────────────────────────────────────────
        from miqi.config.loader import save_config
        config = self._bridge_state.load_config()
        config.tools.sandbox.enabled = enabled
        try:
            save_config(config)
        except Exception as exc:
            logger.error("sandbox.setEnabled: config save failed: {}", exc)
            raise AppServerError(
                "Failed to save config", code="INTERNAL",
            ) from exc

        old_mgr = getattr(self._bridge_state, "_sandbox_manager", None)

        if enabled:
            # ── Enable ────────────────────────────────────────────────
            if old_mgr is not None and old_mgr != "disabled":
                return {"result": {"enabled": True, "already": True}}

            from miqi.sandbox.manager import SandboxManager

            sb_cfg = getattr(config.tools, "sandbox", None)
            new_mgr = SandboxManager(
                workspace=config.workspace_path,
                share_net=getattr(sb_cfg, "share_net", True),
                enabled=True,
                max_sandboxes=getattr(sb_cfg, "max_sandboxes", 10),
                auto_cleanup=getattr(sb_cfg, "auto_cleanup", True),
                auto_install_deps=getattr(sb_cfg, "auto_install_deps", True),
                allow_system_installs=getattr(sb_cfg, "allow_system_installs", False),
                wsl_distro=getattr(sb_cfg, "wsl_distro", ""),
                wsl_base_dir=getattr(sb_cfg, "wsl_base_dir", "/tmp/miqi-sandboxes"),
            )
            self._bridge_state._sandbox_manager = new_mgr
            # Initialize in background (may trigger apt-get)
            asyncio.create_task(self._init_sandbox_manager())
            logger.info(
                "sandbox.setEnabled: enabled (init in background) "
                "(client={})", client_id,
            )
            return {"result": {"enabled": True, "initializing": True}}

        else:
            # ── Disable ───────────────────────────────────────────────
            destroyed = 0
            if old_mgr is not None and old_mgr != "disabled":
                # Mark disabled BEFORE destroying, so existing sessions
                # that still hold a reference to this manager will see
                # get_or_create() return None instead of recreating
                # sandboxes.
                old_mgr.enabled = False
                try:
                    destroyed = await old_mgr.destroy_all()
                except Exception as exc:
                    logger.warning(
                        "sandbox.setEnabled: destroy_all error: {}", exc,
                    )
            self._bridge_state._sandbox_manager = "disabled"
            logger.info(
                "sandbox.setEnabled: disabled, destroyed {} sandbox(es) "
                "(client={})", destroyed, client_id,
            )
            return {"result": {"enabled": False, "destroyed": destroyed}}

    async def _sandbox_set_allow_system_installs_handler(
        self, request_id: str, params: dict, client_id: str,
        session_id: str | None, registry: Any,
    ) -> dict:
        """#854: sandbox.setAllowSystemInstalls — runtime toggle, no restart.

        统一入口（外部审阅 #854；#875 review 09-02 P2 修订）：runtime 属性
        与 config 持久化原子成对——与确认卡「允许并记住」共享
        ``apply_system_installs_toggle``（同一实现防止行为漂移）；本 handler
        额外刷新 bridge 内存态，失败按 fail-closed 抛 AppServerError（设置页
        UI 语义：开关不得停留在"已开启"而实际未生效）。
        """
        if not isinstance(params, dict):
            from miqi.runtime.app_server import AppServerError

            raise AppServerError(
                "sandbox.setAllowSystemInstalls: params must be an object",
                code="INVALID_PARAMS",
            )
        enabled = params.get("enabled")
        if not isinstance(enabled, bool):
            from miqi.runtime.app_server import AppServerError

            raise AppServerError(
                "sandbox.setAllowSystemInstalls: 'enabled' must be a boolean",
                code="INVALID_PARAMS",
            )
        if self._bridge_state is None:
            from miqi.runtime.app_server import AppServerError

            raise AppServerError(
                "Bridge state not available", code="INTERNAL",
            )

        # 统一入口（外部审阅 #854；#875 review 09-02 P2）：与确认卡
        # 「允许并记住」共享 apply_system_installs_toggle——config 持久化
        # （共享锁 fresh-read，与卡 approver、extra-root persister 同一把锁）
        # 在前、runtime 切换在后（fail-closed），两条路径同一实现。
        from miqi.runtime.tool_registry_factory import apply_system_installs_toggle

        mgr = getattr(self._bridge_state, "_sandbox_manager", None)
        persist_failed, runtime_failed = apply_system_installs_toggle(
            enabled, None if mgr == "disabled" else mgr,
        )
        if persist_failed:
            logger.error("sandbox.setAllowSystemInstalls: config save failed")
            from miqi.runtime.app_server import AppServerError

            raise AppServerError(
                "Failed to save config", code="INTERNAL",
            )
        if runtime_failed:
            # #875 review: config is already persisted, but the runtime
            # toggle did NOT apply.  Returning success here would show
            # "已开启" in the UI while the sandbox still denies installs
            # (UI=true / config=true / runtime=false).  Surface the
            # failure so the toggle stays off; a restart picks up the
            # persisted config.
            from miqi.runtime.app_server import AppServerError

            raise AppServerError(
                "Runtime update failed (config saved; restart to apply)",
                code="INTERNAL",
            )
        # 刷新 bridge 内存态（save_config 已失效 loader 缓存，重新加载）
        self._bridge_state.config = self._bridge_state.load_config()
        logger.info(
            "sandbox.setAllowSystemInstalls: {} (client={})", enabled, client_id,
        )
        return {"result": {"allowSystemInstalls": enabled}}

    async def _shutdown(self) -> None:
        """Graceful shutdown sequence.

        1. Cancel active chat drain tasks
        2. Stop AppServer (stops all RuntimeSessions, cancels TTL task)
        3. Clear terminal tracking
        4. Signal shutdown complete
        """
        logger.info(
            "BridgeRuntimeLoop: starting graceful shutdown "
            "({} active chat tasks)",
            len(self._active_chat_tasks),
        )

        # 1. Cancel active chat drain tasks
        for req_id, task in list(self._active_chat_tasks.items()):
            if not task.done():
                logger.debug(
                    "BridgeRuntimeLoop: cancelling chat task {}", req_id,
                )
                task.cancel()
        # Wait briefly for cancellations to propagate
        if self._active_chat_tasks:
            await asyncio.gather(
                *list(self._active_chat_tasks.values()),
                return_exceptions=True,
            )
        self._active_chat_tasks.clear()
        self._session_drain_tasks.clear()
        # #797: released drains (aborted turns still draining in the
        # background) are bounded by their own 600s idle timeout, but at
        # shutdown cancel them so the loop can close cleanly.
        for task in list(self._released_drain_tasks.values()):
            if not task.done():
                task.cancel()
        if self._released_drain_tasks:
            await asyncio.gather(
                *list(self._released_drain_tasks.values()),
                return_exceptions=True,
            )
        self._released_drain_tasks.clear()

        # 2. Stop AppServer (stops RuntimeSessions, cancels TTL, etc.)
        if self._app_server is not None:
            try:
                await self._app_server.stop()
            except Exception as exc:
                logger.warning(
                    "BridgeRuntimeLoop: error stopping AppServer: {}", exc,
                )

        # 3. Clean up experience store singleton (closes TraceStore SQLite)
        try:
            from miqi.runtime.experience_handlers import _cleanup_experience_store
            _cleanup_experience_store()
        except Exception as exc:
            logger.warning(
                "BridgeRuntimeLoop: error cleaning up experience store: {}", exc,
            )

        # 4. Clean up terminal tracking
        self._terminal_sent.clear()

        # 4. Signal shutdown complete
        if self._shutdown_event is not None:
            self._shutdown_event.set()

        logger.info("BridgeRuntimeLoop: shutdown complete")

    def _cancel_all_tasks(self) -> None:
        """Cancel all pending asyncio tasks on the loop.

        Called during loop cleanup in start()'s finally block.
        Must be called while the loop is still running.
        """
        if self._loop is None:
            return
        pending = [
            t for t in asyncio.all_tasks(self._loop)
            if not t.done()
        ]
        if not pending:
            return
        logger.info(
            "BridgeRuntimeLoop: cancelling {} pending task(s)", len(pending),
        )
        for task in pending:
            task.cancel()
        # Give tasks a moment to cancel
        try:
            self._loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True),
            )
        except Exception:
            pass
