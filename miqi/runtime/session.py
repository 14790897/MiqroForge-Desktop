"""Runtime session — the Codex-like entry point for all frontends.

Owns submissions queue, event queue, TaskRunner, and service lifecycle.
Frontends should use RuntimeSession.create() + start/stop/submit/next_event.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any

from loguru import logger as _session_logger

from miqi.execution.hook_runtime import (
    HookPoint,
    HookRuntime,
    LifecycleHookContext,
)
from miqi.runtime.services import RuntimeServices
from miqi.runtime.task_runner import TaskRunner


class RuntimeSession:
    """A single runtime session with submission queue and event stream.

    Usage:
        runtime = RuntimeSession.create(config=..., provider=..., ...)
        await runtime.start()
        await runtime.submit(UserMessage(content="hello"))
        while True:
            event = await runtime.next_event(timeout=120)
            if isinstance(event, AgentMessageEvent):
                print(event.content)
                break
        await runtime.stop()
    """

    def __init__(
        self,
        *,
        services: RuntimeServices,
        event_queue: asyncio.Queue | None = None,
        hooks: HookRuntime | None = None,
    ):
        self.services = services
        self.session_id = services.session_id
        self._hooks = hooks
        self._submissions: asyncio.Queue[Any] = asyncio.Queue()
        self._events: asyncio.Queue[Any] = event_queue or asyncio.Queue()
        self._runner = TaskRunner(services=services, event_queue=self._events)
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        # Phase 14 follow-up: track active turn for abort
        self._active_turn_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        # Phase 14 follow-up v2: pending submissions queued during active turn
        self._pending: list[Any] = []
        # Phase 14 follow-up v2: per-runtime ask lock (used by RuntimeClient)
        self._ask_lock = asyncio.Lock()
        # Phase 24 hardening: turn_id → thread_id map so events that
        # carry only turn_id (ExecCommandBeginEvent etc.) can resolve
        # the correct ledger thread.
        self._turn_thread_map: dict[str, str] = {}
        # Phase 41 hardening v2: per-thread turn reservation to close
        # the race window between turn/start check and UserMessage submit.
        self._turn_reservations: dict[str, str] = {}
        self._reservation_lock = asyncio.Lock()
        # Phase 42 fix: track auxiliary tasks (shell commands during active turns)
        # so they can be cancelled on abort and cleaned up on stop.
        self._pending_aux_tasks: set[asyncio.Task] = set()
        # MCP integration: config snapshot, connection tasks, and a
        # connect-once guard (set by create() / start()).
        self._config: Any = None
        self._mcp_tasks: list[asyncio.Task] = []
        self._mcp_keep_alive: asyncio.Event | None = None
        self._mcp_connected: bool = False

    @classmethod
    def create(
        cls,
        *,
        config: Any,
        provider: Any,
        session_id: str,
        workspace: Path,
        sandbox_manager: Any = None,
        agent_completion_callback: Any | None = None,
    ) -> "RuntimeSession":
        """Create a RuntimeSession from config and provider.

        All typed events emitted by the orchestrator, AgentControl, spawn-tool,
        and other services flow into the session's event queue and become
        available through next_event(). This includes tool-call-begin/end,
        sub-agent-spawned/completed, turn-start/complete, and error events.
        """
        # Shared event queue — services push into it, consumers read from it.
        # 4096 maxsize provides backpressure: a runaway agent that floods
        # events (e.g. thousands of tool calls) will block at put() instead
        # of growing the queue unboundedly (#328).
        events: asyncio.Queue[Any] = asyncio.Queue(maxsize=4096)

        services = RuntimeServices.from_config(
            config=config,
            provider=provider,
            session_id=session_id,
            workspace=workspace,
            event_sink=events.put,  # asyncio.Queue.put is a coroutine sink
            sandbox_manager=sandbox_manager,
            agent_completion_callback=agent_completion_callback,
        )
        runtime = cls(
            services=services,
            event_queue=events,
            hooks=getattr(services, "hooks", None),
        )
        runtime._config = config
        return runtime

    async def start(self) -> None:
        """Start the background dispatch loop and initialize runtime stores."""
        # Phase 17: initialize persistent history and thread stores
        history = getattr(self.services, "history_runtime", None)
        if history is not None:
            await history.initialize()
        threads = getattr(self.services, "thread_runtime", None)
        if threads is not None:
            await threads.initialize()
            default_thread_id = self.services.session_state.active_thread_id
            existing = await threads.get_thread(default_thread_id)
            if existing is None:
                await threads.create_thread(
                    thread_id=default_thread_id,
                    title="Default",
                )
        # Phase 24: initialize append-only event ledger
        ledger = getattr(self.services, "ledger_runtime", None)
        if ledger is not None:
            await ledger.initialize()

        # MCP integration: connect configured MCP servers (tools.mcpServers)
        # and register their tools into the session's ToolRegistry.  Runs
        # before the dispatch task starts so the tools are advertised on the
        # first turn.  Config changes take effect for the NEXT session
        # (hot-reload tier: 修改后新建会话生效).
        await self._connect_mcp()

        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self._run())

        # Phase 51.3: lifecycle hook for session start.
        if self._hooks is not None:
            await self._hooks.run(
                HookPoint.SESSION_START,
                LifecycleHookContext(
                    hook_point=HookPoint.SESSION_START,
                    data={"session_id": self.session_id},
                ),
            )

    async def _connect_mcp(self) -> None:
        """Connect configured MCP servers and register their tools (Phase MCP).

        Reads ``config.tools.mcp_servers`` and delegates to
        ``miqi.agent.tools.mcp.connect_mcp_servers``, which registers an
        ``MCPToolWrapper`` per tool (or a lazy gateway) into the shared
        ToolRegistry.  Connection is attempted exactly once per session —
        a failing server is logged and skipped, never blocking session
        startup.  Each connection lives in its own task; ``stop()`` sets
        the keep-alive event and awaits the tasks so stdio subprocesses
        are torn down inside the tasks that spawned them.
        """
        if self._mcp_connected:
            return
        self._mcp_connected = True

        tools_cfg = getattr(self._config, "tools", None) if self._config is not None else None
        mcp_servers = getattr(tools_cfg, "mcp_servers", None) or {}
        if not mcp_servers:
            return

        from miqi.agent.tools.mcp import connect_mcp_servers

        keep_alive = asyncio.Event()
        try:
            from pathlib import Path as _Path

            self._mcp_tasks = await connect_mcp_servers(
                mcp_servers,
                self.services.tool_registry,
                keep_alive,
                workspace=_Path(self._config.workspace_path) if self._config else None,
            )
        except BaseException:
            _session_logger.exception("MCP server connection failed during session start")
            return
        self._mcp_keep_alive = keep_alive

    async def stop(self) -> None:
        """Stop the dispatch loop and tear down agent resources."""
        # Phase 51.3: lifecycle hooks for session end and stop.
        if self._hooks is not None:
            end_ctx = LifecycleHookContext(
                hook_point=HookPoint.SESSION_END,
                data={"session_id": self.session_id},
            )
            await self._hooks.run(HookPoint.SESSION_END, end_ctx)
            stop_ctx = LifecycleHookContext(
                hook_point=HookPoint.STOP,
                data={"session_id": self.session_id},
            )
            await self._hooks.run(HookPoint.STOP, stop_ctx)

        self._stopped.set()
        # Phase 41 hardening v2: release all turn reservations
        async with self._reservation_lock:
            self._turn_reservations.clear()
        # Phase 42 fix: cancel and await all pending auxiliary tasks (shell commands)
        # so subprocess cleanup runs before the task references are discarded.
        await self._cancel_aux_tasks()
        if self._task is not None:
            self._task.cancel()
            # Cancel active turn task if still running
            if self._active_turn_task is not None and not self._active_turn_task.done():
                self._active_turn_task.cancel()
            await asyncio.gather(
                self._task,
                *([self._active_turn_task] if self._active_turn_task is not None and not self._active_turn_task.done() else []),
                return_exceptions=True,
            )
        # Phase 48: legacy agent_loop shim removed — no-op lifecycle calls deleted.
        # Phase 22 hardening: close persistent aiosqlite connections before
        # the event loop shuts down to avoid background-thread leaks.
        history = getattr(self.services, "history_runtime", None)
        if history is not None:
            await history.close()
        threads = getattr(self.services, "thread_runtime", None)
        if threads is not None:
            await threads.close()
        # Phase 24: close append-only event ledger
        ledger = getattr(self.services, "ledger_runtime", None)
        if ledger is not None:
            await ledger.close()
        # MCP integration: release the connection tasks so stdio server
        # subprocesses are terminated (each task closes its own stack).
        if getattr(self, "_mcp_keep_alive", None) is not None:
            self._mcp_keep_alive.set()
            await asyncio.gather(*self._mcp_tasks, return_exceptions=True)
            self._mcp_keep_alive = None
            self._mcp_tasks = []
        # A restarted session must reconnect (the connections above are gone).
        self._mcp_connected = False

    async def submit(self, submission: Any) -> None:
        """Submit a command to the runtime (UserMessage, AbortTurn, etc.)."""
        await self._submissions.put(submission)

    async def next_event(self, timeout: float | None = None) -> Any | None:
        """Wait for the next typed event from the runtime.

        Returns None on timeout, otherwise a protocol event dataclass.
        """
        try:
            if timeout is None:
                event = await self._events.get()
            else:
                event = await asyncio.wait_for(self._events.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        # Phase 24: mirror selected events into the append-only ledger
        await self._mirror_event_to_ledger(event)
        return event

    # ── Phase 25: replay/debug API ───────────────────────────────────────

    async def list_turns(self, thread_id: str) -> list[str]:
        """Return turn_ids for a thread in ledger sequence order."""
        replay = getattr(self.services, "replay_runtime", None)
        if replay is None:
            return []
        return await replay.list_turns(thread_id)

    async def get_turn_replay(self, thread_id: str, turn_id: str) -> Any | None:
        """Return a TurnTimeline reconstructed from ledger items."""
        replay = getattr(self.services, "replay_runtime", None)
        if replay is None:
            return None
        return await replay.get_turn_timeline(thread_id, turn_id)

    # ── Phase 41: active turn reservation and steering wrappers ──────────

    def active_turn_id(self, thread_id: str) -> str | None:
        """Return the turn ID that is actively running or reserved for *thread_id*.

        Checks TaskRunner first (turn is actually executing), then falls back
        to the session-level reservation (turn/start has reserved the slot but
        the UserMessage hasn't been picked up by the dispatch loop yet).
        """
        runner_active = self._runner.active_turn_id(thread_id)
        if runner_active is not None:
            return runner_active
        return self._turn_reservations.get(thread_id)

    async def try_reserve_turn(self, thread_id: str, turn_id: str) -> bool:
        """Atomically check-and-reserve a turn slot for *thread_id*.

        Returns True if the reservation was successful (no active turn and
        no prior reservation for this thread), False otherwise.

        The caller MUST release the reservation via
        :meth:`release_turn_reservation` when the turn finishes, errors, or
        is aborted.  Reservations are also cleared on :meth:`stop`.
        """
        async with self._reservation_lock:
            if self._runner.active_turn_id(thread_id) is not None:
                return False
            if thread_id in self._turn_reservations:
                return False
            self._turn_reservations[thread_id] = turn_id
            return True

    async def release_turn_reservation(self, thread_id: str) -> None:
        """Release a turn reservation so a new turn/start can proceed."""
        async with self._reservation_lock:
            self._turn_reservations.pop(thread_id, None)

    async def steer_turn(
        self,
        *,
        thread_id: str,
        expected_turn_id: str,
        content: str,
        input_items: list[dict[str, Any]],
        client_user_message_id: str | None,
    ) -> bool:
        return await self._runner.steer_turn(
            thread_id=thread_id,
            expected_turn_id=expected_turn_id,
            content=content,
            input_items=input_items,
            client_user_message_id=client_user_message_id,
        )

    async def interrupt_turn(self, *, thread_id: str, turn_id: str) -> bool:
        from miqi.protocol.commands import AbortTurn

        active = self.active_turn_id(thread_id)
        if active != turn_id:
            return False
        await self.submit(AbortTurn(thread_id=thread_id))
        return True

    async def get_provider_messages(self, thread_id: str) -> list[dict[str, Any]]:
        """Return provider-compatible message dicts from ledger."""
        replay = getattr(self.services, "replay_runtime", None)
        if replay is None:
            return []
        return await replay.get_provider_messages(thread_id)

    async def _mirror_event_to_ledger(self, event: Any) -> None:
        """Record selected runtime events as immutable ledger items.

        Only events with a matching item_type in the mapping are recorded.
        Maintains a turn_id → thread_id map so events that carry only
        turn_id (e.g. ExecCommandBeginEvent) resolve to the correct thread.
        """
        ledger = getattr(self.services, "ledger_runtime", None)
        if ledger is None:
            return

        turn_id: str | None = getattr(event, "turn_id", None)
        event_thread_id: str | None = getattr(event, "thread_id", None)

        # Record mapping whenever both fields are present on the event.
        # This allows later events that carry only turn_id to resolve
        # the correct thread for ledger storage.
        if turn_id is not None and event_thread_id is not None:
            self._turn_thread_map[turn_id] = event_thread_id

        # Resolve thread_id: prefer explicit thread_id, then map lookup,
        # then fall back to turn_id itself, then "session".
        if event_thread_id is not None:
            thread_id = event_thread_id
        elif turn_id is not None:
            thread_id = self._turn_thread_map.get(turn_id, turn_id)
        else:
            thread_id = "session"

        event_type = getattr(event, "type", event.__class__.__name__)
        # Phase 31.8 single-writer rule (fix):
        #   - exec/approval lifecycle items are written at source
        #     (ToolOrchestrator for approvals, ExecTool for exec events)
        #   - RuntimeSession._mirror_event_to_ledger only handles events
        #     that have NO source-level ledger writer:
        #     command_rejected, error, warning, context_compacted.
        #   - Mirroring exec_command_* and approval_* here would create
        #     duplicates because the source already writes them.
        item_type = {
            "command_rejected": "command_rejected",
            "error": "error",
            "warning": "warning",
            "context_compacted": "context_compacted",
        }.get(event_type)
        if item_type is None:
            return
        payload = getattr(event, "__dict__", {}).copy()
        payload.pop("type", None)
        # Phase 25: use dataclasses.asdict() for safe serialization when
        # available — handles Enums, nested dataclasses, etc.
        from dataclasses import asdict, is_dataclass
        if is_dataclass(event):
            payload = asdict(event)
            payload.pop("type", None)
        else:
            payload = getattr(event, "__dict__", {}).copy()
            payload.pop("type", None)
        await ledger.append_item(
            thread_id=thread_id,
            turn_id=turn_id,
            item_type=item_type,
            content=str(getattr(event, "message", getattr(event, "delta", "")) or ""),
            payload=payload,
        )

    async def _cancel_aux_tasks(self) -> None:
        """Cancel all pending auxiliary tasks with proper cleanup lifecycle.

        Snapshot → cancel unfinished → await gather (return_exceptions=True)
        → discard from the set.  This gives subprocess cleanup a chance to
        execute before the task references are discarded.
        """
        if not self._pending_aux_tasks:
            return
        snapshot = list(self._pending_aux_tasks)
        for t in snapshot:
            if not t.done():
                t.cancel()
        if snapshot:
            await asyncio.gather(*snapshot, return_exceptions=True)
        for t in snapshot:
            self._pending_aux_tasks.discard(t)

    async def _run(self) -> None:
        """Main dispatch loop: dequeue submissions → TaskRunner.handle().

        Phase 14 follow-up v2: non-blocking dispatch that can dequeue AbortTurn
        while a UserMessage turn is running. Non-AbortTurn submissions arriving
        during an active turn are queued in _pending and processed after the
        current turn completes (FIFO, no silent drops).
        """
        from miqi.protocol.commands import AbortTurn, RunUserShellCommand, SteerTurn

        while not self._stopped.is_set():
            # If no turn is running, dequeue next submission (pending first)
            async with self._lock:
                if self._active_turn_task is None:
                    submission = None
                    if self._pending:
                        submission = self._pending.pop(0)
                    elif not self._submissions.empty():
                        submission = self._submissions.get_nowait()
                    else:
                        try:
                            submission = await asyncio.wait_for(
                                self._submissions.get(), timeout=0.5,
                            )
                        except asyncio.TimeoutError:
                            continue
                        except asyncio.CancelledError:
                            break

                    if submission is None:
                        continue

                    if isinstance(submission, AbortTurn):
                        await self._runner.handle(submission)
                        continue

                    if isinstance(submission, SteerTurn):
                        await self._runner.handle(submission)
                        continue

                    # Spawn new turn
                    self._active_turn_task = asyncio.create_task(
                        self._runner.handle(submission)
                    )
                    continue  # Back to top of loop to enter the wait

            # A turn is running — wait for EITHER it to finish, a new
            # submission, or any pending auxiliary task (shell command).
            assert self._active_turn_task is not None
            get_task = asyncio.create_task(self._submissions.get())
            waitables: list[asyncio.Task] = [self._active_turn_task, get_task]
            # Phase 42 fix: include pending auxiliary tasks so their completion
            # is handled promptly and exceptions are surfaced.
            aux_snapshot = list(self._pending_aux_tasks)
            waitables.extend(aux_snapshot)
            done, pending = await asyncio.wait(
                waitables,
                return_when=asyncio.FIRST_COMPLETED,
            )

            for d in done:
                if d is self._active_turn_task:
                    # Turn completed — clear active, surface exceptions
                    async with self._lock:
                        finished = self._active_turn_task
                        self._active_turn_task = None
                    if finished is not None and finished.done():
                        try:
                            await finished
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            _session_logger.exception(
                                "RuntimeSession: unhandled exception in active turn task"
                            )
                            from miqi.protocol.events import ErrorEvent
                            await self._events.put(
                                ErrorEvent(
                                    turn_id="session",
                                    message="Turn task failed with an internal error. Check runtime logs.",
                                    error_kind="internal",
                                )
                            )
                    # Cancel the get_task waiter (no longer needed)
                    if not get_task.done():
                        get_task.cancel()
                    # Phase 42 fix: cancel and await pending auxiliary tasks
                    # so subprocess cleanup completes before discarding.
                    await self._cancel_aux_tasks()
                elif d is get_task:
                    # New submission arrived while turn was running
                    submission = d.result()
                    if isinstance(submission, AbortTurn):
                        had_active = False
                        cancelled_task = None
                        async with self._lock:
                            if self._active_turn_task is not None:
                                cancelled_task = self._active_turn_task
                                self._active_turn_task.cancel()
                                had_active = True
                        if not get_task.done():
                            get_task.cancel()
                        # Phase 42 fix: cancel and await auxiliary tasks
                        # (shell commands) so subprocess cleanup runs before
                        # we await the cancelled active-turn task.
                        await self._cancel_aux_tasks()
                        if had_active:
                            # Phase 41 hardening: cancel approvals even when
                            # bypassing TaskRunner.handle(AbortTurn) to avoid
                            # leaving orphan approvals in the pending set.
                            thread_id = getattr(submission, "thread_id", None) or "default"
                            orchestrator = getattr(self._runner.services, "orchestrator", None)
                            cancel_fn = getattr(orchestrator, "cancel_approvals_for_thread", None)
                            if callable(cancel_fn) and inspect.iscoroutinefunction(cancel_fn):
                                await cancel_fn(thread_id, reason="Turn aborted by user.")
                            # Await the cancelled task to guarantee cleanup
                            # (history completion, ledger append, event emission)
                            # completes before the next loop iteration.
                            if cancelled_task is not None:
                                await self._await_cancelled_turn_task(
                                    cancelled_task,
                                    submission,
                                )
                                async with self._lock:
                                    if self._active_turn_task is cancelled_task:
                                        self._active_turn_task = None
                        else:
                            await self._runner.handle(submission)
                    elif isinstance(submission, SteerTurn):
                        # Handle steering inline (fast, just puts content in a queue)
                        await self._runner.handle(submission)
                    elif isinstance(submission, RunUserShellCommand):
                        # Phase 42 fix: spawn shell command as background task
                        # instead of blocking the dispatch loop, so AbortTurn
                        # can still be dequeued and processed.
                        task = asyncio.create_task(
                            self._runner.handle(submission),
                            name=f"aux-shell:{submission.turn_id}",
                        )
                        self._pending_aux_tasks.add(task)
                    else:
                        # Queue non-AbortTurn/non-SteerTurn/non-RunUserShellCommand
                        # for processing after this turn
                        async with self._lock:
                            self._pending.append(submission)
                elif d in self._pending_aux_tasks:
                    # Phase 42 fix: an auxiliary task (shell command) completed.
                    # Surface any exception to avoid silent failures.
                    self._pending_aux_tasks.discard(d)
                    if d.done():
                        try:
                            await d
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            _session_logger.exception(
                                "RuntimeSession: unhandled exception in auxiliary task {}",
                                d.get_name() if hasattr(d, "get_name") else "?",
                            )
                            from miqi.protocol.events import ErrorEvent as _ErrEvt2
                            await self._events.put(
                                _ErrEvt2(
                                    turn_id="session",
                                    message="Auxiliary task failed with an internal error.",
                                    error_kind="internal",
                                )
                            )
                else:
                    if not get_task.done():
                        get_task.cancel()

            # Clean up any pending get_task that wasn't handled
            for p in pending:
                if p is not self._active_turn_task and p not in self._pending_aux_tasks and not p.done():
                    p.cancel()

    async def _await_cancelled_turn_task(
        self,
        cancelled_task: asyncio.Task,
        submission: Any,
    ) -> None:
        """Drain a cancelled turn without surfacing cleanup failures as chat errors."""
        try:
            await cancelled_task
        except asyncio.CancelledError:
            pass
        except Exception:
            _session_logger.exception(
                "RuntimeSession: cancelled turn cleanup failed"
            )
            from miqi.protocol.events import TurnAbortedEvent

            thread_id = getattr(submission, "thread_id", None) or "default"
            await self._events.put(
                TurnAbortedEvent(
                    turn_id="session",
                    thread_id=thread_id,
                    reason="Turn aborted; cleanup failed after cancellation. Check runtime logs.",
                )
            )
