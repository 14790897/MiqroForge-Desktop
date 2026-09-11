"""Multi-agent control plane — spawn, fork, monitor, and communicate."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from miqi.execution.hook_runtime import (
    HookPoint,
    HookRuntime,
    LifecycleHookContext,
)
from miqi.protocol.events import (
    AgentMessageEvent,
    AgentStatus,
    ErrorEvent,
    EventSeverity,
    SubAgentCompletedEvent,
    SubAgentSpawnedEvent,
    ToolCallBeginEvent,
    ToolCallEndEvent,
    TurnCompleteEvent,
    TurnStartedEvent,
)
from miqi.runtime.agent_graph_store import AgentGraphStore
from miqi.runtime.agent_registry import AgentMetadata, AgentRegistry
from miqi.runtime.agent_status import AgentStateMachine
from miqi.runtime.workspace_logging import append_workspace_log
from miqi.utils.tool_text_guard import (
    LEAK_NOTICE,
    sanitize_tool_call_text,
    tool_names_from_definitions,
)


@dataclass
class LiveAgent:
    """A running agent instance."""
    agent_id: str
    thread_id: str
    metadata: AgentMetadata
    state: AgentStateMachine
    parent_agent_id: str | None = None
    spawned_at: float = field(default_factory=lambda: __import__("time").time())
    # Spawn-time context (used for frontend subagent result rendering)
    label: str | None = None
    task: str | None = None
    result: str | None = None
    error: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    completed_at: float | None = None
    # Guard against duplicate frontend delivery: kill() emits aborted, then
    # the cancelled _run_agent task's finally emits again — only the first
    # terminal event should reach the renderer.
    completion_emitted: bool = False


def build_session_context(
    workspace: Path,
    session_id: str,
    sandbox_manager: Any = None,
) -> str:
    """Per-turn session context injected into the agent system prompt.

    Describes the working directory and the ACTUAL exec environment
    (sandbox active or direct host execution) so the AI issues paths and
    shell syntax that match reality instead of a hard-coded sandbox story.
    """
    from miqi.sandbox.manager import describe_exec_environment

    exec_env = describe_exec_environment(sandbox_manager, workspace=workspace)
    return (
        f"\n\n## 工作目录\n"
        f"你当前的工作目录是: {workspace}\n"
        f"文件工具（read_file / write_file / list_dir）在这个目录下进行。\n"
        f"注意：{exec_env}\n"
        f"当用户问你工作目录时，请直接回答 {workspace}，不要说 /home/miqi/workspace。\n"
        f"MIQI_SESSION_KEY={session_id}"
    )


class AgentControl:
    """Control plane for multi-agent operations.

    Each root session has one AgentControl instance shared with all
    sub-agents spawned from that root. This keeps the registry scoped
    to one session tree.
    """

    def __init__(
        self,
        session_id: str,
        registry: AgentRegistry,
        event_emitter: Any,  # EventEmitter — sends events to bridge
        workspace: Path,
        *,
        provider: Any = None,  # LLMProvider
        orchestrator: Any = None,  # ToolOrchestrator
        tool_registry: Any = None,  # ToolRegistry — for role-filtered tool definitions
        agent_jobs: Any = None,  # AgentJobRuntime — Phase 13
        hooks: HookRuntime | None = None,
        store: AgentGraphStore | None = None,
        completion_callback: Callable[[dict], Awaitable[None]] | None = None,
        max_concurrent: int = 3,
        sandbox_manager: Any = None,  # live sandbox state for prompt context
    ):
        self.session_id = session_id
        self.registry = registry
        self._events = event_emitter
        self.workspace = workspace
        self._provider = provider
        self._orchestrator = orchestrator
        self._tool_registry = tool_registry
        self._agent_jobs = agent_jobs
        self._hooks = hooks
        self._store = store
        self._completion_callback = completion_callback
        self._sandbox_manager = sandbox_manager
        # Issue #246: cap concurrently-running subagents (default 3, the
        # legacy SubagentManager limit).  Terminal agents (completed/error/
        # aborted) do not count; killed agents are removed from the registry.
        self.max_concurrent = max_concurrent
        self._agents: dict[str, LiveAgent] = {}  # agent_id → LiveAgent
        self._thread_agents: dict[str, str] = {}  # thread_id → agent_id
        self._lock = asyncio.Lock()
        self._running_tasks: dict[str, asyncio.Task] = {}  # agent_id → background task

    async def spawn(
        self,
        agent_type: str,
        task: str,
        *,
        parent_agent_id: str | None = None,
        label: str | None = None,
        fork_history: bool = False,
        model_override: str | None = None,
    ) -> LiveAgent:
        """Spawn a new agent to handle a task.

        Args:
            agent_type: Name of the agent type (e.g. "code-agent")
            task: The task description
            parent_agent_id: ID of the spawning agent (None for root)
            label: Human-readable task label
            fork_history: Whether to copy parent's conversation history
            model_override: Override the default model for this agent

        Returns:
            LiveAgent handle for the spawned agent
        """
        metadata = self.registry.resolve(agent_type)

        # Issue #246: enforce max_concurrent BEFORE starting the job — if the
        # session already runs the limit of subagents, refuse to spawn so we
        # don't leak a queued job.  Terminal agents (completed/error/aborted)
        # do not count; a registered-but-not-yet-completed agent counts as
        # running (its state is THINKING from the moment spawn() registers it).
        async with self._lock:
            running = sum(
                1
                for a in self._agents.values()
                if a.state.current
                not in (AgentStatus.COMPLETED, AgentStatus.ERROR, AgentStatus.ABORTED)
            )
            if running >= self.max_concurrent:
                raise RuntimeError(
                    f"Cannot spawn subagent: {self.max_concurrent} subagents "
                    "already running. Wait for one to finish before spawning "
                    "another."
                )

        # Phase 13: when AgentJobRuntime is available, start the job FIRST
        # so the event carries the correct job-allocated IDs.
        if self._agent_jobs is not None:
            job = await self._agent_jobs.start(
                agent_type=agent_type,
                task=task,
                parent_thread_id=parent_agent_id or self.session_id,
            )
            agent_id = job.job_id
            thread_id = job.thread_id
        else:
            agent_id = str(uuid.uuid4())[:12]
            thread_id = f"{self.session_id}:{agent_id}"

        agent = LiveAgent(
            agent_id=agent_id,
            thread_id=thread_id,
            metadata=metadata,
            state=AgentStateMachine(),
            parent_agent_id=parent_agent_id,
            label=label,
            task=task,
        )

        async with self._lock:
            self._agents[agent_id] = agent
            self._thread_agents[thread_id] = agent_id

        if self._store is not None and parent_agent_id is not None:
            self._store.add_edge(
                parent_agent_id=parent_agent_id,
                child_agent_id=agent_id,
                child_thread_id=thread_id,
            )

        # Emit SpawnedEvent with the FINAL IDs
        await self._events.emit(SubAgentSpawnedEvent(
            parent_turn_id=parent_agent_id or self.session_id,
            sub_agent_id=agent_id,
            sub_thread_id=thread_id,
            agent_type=agent_type,
            task_label=label or task[:40],
        ))

        # Phase 51.3: fire sub-agent lifecycle start hook.
        if self._hooks is not None:
            await self._hooks.run(
                HookPoint.SUBAGENT_START,
                LifecycleHookContext(
                    hook_point=HookPoint.SUBAGENT_START,
                    data={
                        "agent_id": agent_id,
                        "thread_id": thread_id,
                        "agent_type": agent_type,
                        "task": task,
                        "parent_agent_id": parent_agent_id,
                    },
                ),
            )

        if self._agent_jobs is not None:
            # Job already started above — just transition and return
            agent.state.transition(AgentStatus.THINKING)
            logger.info(
                "Spawned agent {} via AgentJobRuntime (type: {})",
                agent.agent_id, agent_type,
            )
            return agent

        # Legacy direct execution: start background task if provider is available
        if self._provider is not None:
            agent.state.transition(AgentStatus.THINKING)
            task_ref = asyncio.create_task(self._run_agent(agent, task))
            self._running_tasks[agent_id] = task_ref
            task_ref.add_done_callback(lambda _t: self._running_tasks.pop(agent_id, None))

        logger.info(
            "Spawned agent {} of type {} for thread {}",
            agent_id, agent_type, thread_id,
        )

        return agent

    async def fork(
        self,
        source_thread_id: str,
        agent_type: str | None = None,
        last_n_turns: int | None = None,
    ) -> LiveAgent:
        """Fork an existing thread into a new agent.

        Args:
            source_thread_id: The thread to fork from
            agent_type: Agent type for the fork (defaults to source agent type)
            last_n_turns: Copy only the last N turns of history

        Returns:
            LiveAgent handle for the forked agent
        """
        source_agent_id = self._thread_agents.get(source_thread_id)
        if source_agent_id is None:
            raise ValueError(f"Unknown thread: {source_thread_id}")

        source = self._agents[source_agent_id]
        fork_type = agent_type or source.metadata.name

        metadata = self.registry.resolve(fork_type)
        agent_id = str(uuid.uuid4())[:12]
        fork_thread_id = f"{self.session_id}:{agent_id}"

        agent = LiveAgent(
            agent_id=agent_id,
            thread_id=fork_thread_id,
            metadata=metadata,
            state=AgentStateMachine(),
            parent_agent_id=source.parent_agent_id,
        )

        async with self._lock:
            self._agents[agent_id] = agent
            self._thread_agents[fork_thread_id] = agent_id

        if self._store is not None:
            self._store.add_edge(
                parent_agent_id=source_agent_id,
                child_agent_id=agent_id,
                child_thread_id=fork_thread_id,
            )

        # Emit spawned event for forked agent
        await self._events.emit(SubAgentSpawnedEvent(
            parent_turn_id=source_thread_id,
            sub_agent_id=agent_id,
            sub_thread_id=fork_thread_id,
            agent_type=fork_type,
            task_label=f"fork from {source_thread_id}",
        ))

        # Fire sub-agent lifecycle start hook
        if self._hooks is not None:
            await self._hooks.run(
                HookPoint.SUBAGENT_START,
                LifecycleHookContext(
                    hook_point=HookPoint.SUBAGENT_START,
                    data={
                        "agent_id": agent_id,
                        "thread_id": fork_thread_id,
                        "agent_type": fork_type,
                        "task": f"fork from {source_thread_id}",
                        "parent_agent_id": source_agent_id,
                    },
                ),
            )

        logger.info(
            "Forked thread {} → {} (type: {})",
            source_thread_id, fork_thread_id, fork_type,
        )

        return agent

    async def kill(self, agent_id: str) -> None:
        """Kill a running agent and cancel its background task.

        Removes the agent from the registry and cancels the asyncio Task
        so the sub-agent does not continue running or emit completed events.

        Phase 13: If AgentJobRuntime owns this agent, delegates kill to
        the job runtime to ensure the background job task is cancelled.
        """
        # Phase 13: delegate job cancellation to AgentJobRuntime
        if self._agent_jobs is not None:
            try:
                self._agent_jobs.get(agent_id)  # verify it's a managed job
                await self._agent_jobs.kill(agent_id)
            except KeyError:
                pass

        # Cancel background task first (outside lock to avoid deadlock)
        task = self._running_tasks.pop(agent_id, None)
        if task is not None and not task.done():
            task.cancel()

        async with self._lock:
            agent = self._agents.pop(agent_id, None)
            if agent is None:
                return
            self._thread_agents.pop(agent.thread_id, None)
            agent.state.transition(AgentStatus.ABORTED)

        logger.info("Killed agent {}", agent_id)

        # The agent was removed from the registry, so the job/legacy run
        # teardown can no longer reach it — emit the completion (aborted)
        # here so the frontend still renders the ❌ status.
        await self._emit_completed(agent, status="aborted")

    async def notify_completed(self, agent_id: str, status: str) -> None:
        """Notify the frontend of a subagent completion (AgentJobRuntime path).

        Phase 13: when AgentJobRuntime owns the agent, the subagent runs in
        ``AgentJobRuntime._run`` (via TurnRunner.run_agent_job) — NOT in
        ``AgentControl._run_agent`` — so the ``finally`` completion hook there
        never fires.  AgentJobRuntime calls this from its own ``_run`` finally.
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            return
        # Sync result/error from the job — the AgentJobRuntime path never
        # sets LiveAgent.result/error (TurnRunner returns them to the job).
        if self._agent_jobs is not None:
            try:
                job = self._agent_jobs.get(agent_id)
                if job is not None:
                    if job.result:
                        agent.result = job.result
                    if job.error:
                        agent.error = job.error
            except KeyError:
                pass
        # Keep the LiveAgent state machine in sync with the job status
        # (the job path never transitions it).
        terminal = (AgentStatus.COMPLETED, AgentStatus.ERROR, AgentStatus.ABORTED)
        if agent.state.current not in terminal:
            target = {
                "completed": AgentStatus.COMPLETED,
                "error": AgentStatus.ERROR,
                "aborted": AgentStatus.ABORTED,
            }.get(status)
            if target is not None:
                agent.state.transition(target)
        await self._emit_completed(agent, status=status)

    async def get_status(self, agent_id: str) -> AgentStatus:
        """Get the current status of an agent."""
        agent = self._agents.get(agent_id)
        if agent is None:
            raise KeyError(f"Unknown agent: {agent_id}")
        return agent.state.current

    def list_agents(self) -> list[dict[str, Any]]:
        """List all agents with their status and result preview.

        Merges job status from AgentJobRuntime when available (Phase 13).
        """
        result = []
        for a in self._agents.values():
            entry = {
                "agent_id": a.agent_id,
                "thread_id": a.thread_id,
                "type": a.metadata.display_name,
                "status": a.state.current.value,
                "parent": a.parent_agent_id,
                "label": a.metadata.description,
                "result_preview": (a.result or "")[:200],
                "error": a.error,
                "completed_at": a.completed_at,
            }
            # Phase 13: overlay job status when available
            if self._agent_jobs is not None:
                try:
                    job = self._agent_jobs.get(a.agent_id)
                    entry["status"] = job.status
                    entry["result_preview"] = (job.result or "")[:200]
                    entry["error"] = job.error
                    entry["completed_at"] = job.completed_at
                except KeyError:
                    pass
            result.append(entry)
        return result

    def get_agent_detail(self, agent_id: str) -> dict[str, Any]:
        """Get detailed information about an agent including full messages.

        Merges job result from AgentJobRuntime when available (Phase 13).
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            raise KeyError(f"Unknown agent: {agent_id}")
        detail = {
            "agent_id": agent.agent_id,
            "thread_id": agent.thread_id,
            "type": agent.metadata.display_name,
            "status": agent.state.current.value,
            "parent": agent.parent_agent_id,
            "result": agent.result,
            "error": agent.error,
            "messages": agent.messages,
            "spawned_at": agent.spawned_at,
            "completed_at": agent.completed_at,
        }
        # Phase 13: overlay job result when available
        if self._agent_jobs is not None:
            try:
                job = self._agent_jobs.get(agent_id)
                detail["status"] = job.status
                detail["result"] = job.result
                detail["error"] = job.error
                detail["completed_at"] = job.completed_at
            except KeyError:
                pass
        return detail

    async def _run_agent(self, agent: LiveAgent, task: str) -> None:
        """Execute the agent's task loop.

        This is the core integration point between:
        - AgentControl (multi-agent lifecycle)
        - TurnContext (per-turn configuration)
        - ToolOrchestrator (unified tool execution)
        - EventEmitter (typed event output)

        All tool calls go through ToolOrchestrator. Sub-agents receive
        role-filtered tool definitions based on their agent type.
        Results, errors, and messages are persisted on LiveAgent.
        """
        import json
        import time as _time
        import uuid as _uuid

        turn_id = str(_uuid.uuid4())[:12]
        tools_used: list[str] = []

        # Build role-filtered tool definitions
        tool_defs = None
        if self._tool_registry is not None:
            allowed = set(agent.metadata.available_tools)
            tool_defs = [
                spec for spec in self._tool_registry.get_definitions()
                if spec.get("function", {}).get("name") in allowed
            ]

        try:
            # Only transition if not already in THINKING (spawn may have pre-set it)
            if agent.state.current != AgentStatus.THINKING:
                agent.state.transition(AgentStatus.THINKING)
            # Inject session workspace info so the AI knows where its files live
            _session_context = build_session_context(
                workspace=self.workspace,
                session_id=self.session_id,
                sandbox_manager=self._sandbox_manager,
            )
            agent.messages = [
                {"role": "system", "content": agent.metadata.system_prompt + _session_context},
                {"role": "user", "content": task},
            ]

            # Emit turn started
            await self._events.emit(TurnStartedEvent(
                turn_id=turn_id,
                agent_name=agent.metadata.display_name,
                thread_id=agent.thread_id,
            ))

            # Build TurnContext
            from miqi.runtime.turn_context import TurnContext
            turn_ctx = TurnContext(
                turn_id=turn_id,
                agent_metadata=agent.metadata,
                thread_id=agent.thread_id,
                workspace=self.workspace,
                model=self._provider.get_default_model() if self._provider is not None else "gpt-4o",
                provider=None,  # Set by caller after spawn
                temperature=0.1,
                max_tokens=8192,
            )

            messages = agent.messages

            # ── Main turn loop ──────────────────────────────────
            final_content: str | None = None
            max_iterations = agent.metadata.max_iterations

            for iteration in range(1, max_iterations + 1):
                # 1. Call LLM (use TurnContext provider, fall back to self._provider)
                _provider = turn_ctx.provider or self._provider
                if _provider is None:
                    final_content = (
                        "Agent provider not wired. "
                        "AgentControl._run_agent() requires a provider."
                    )
                    break

                response = await _provider.chat(
                    messages=messages,
                    tools=tool_defs,
                    model=turn_ctx.model,
                    temperature=turn_ctx.temperature,
                    max_tokens=turn_ctx.max_tokens,
                )

                # 2. Handle tool calls
                if response.has_tool_calls:
                    agent.state.transition(AgentStatus.EXECUTING)

                    # Fail fast if orchestrator is not available
                    if self._orchestrator is None:
                        raise RuntimeError(
                            "ToolOrchestrator must be configured before "
                            "sub-agent tool execution."
                        )

                    tool_call_dicts = []
                    tool_results: list[str] = []
                    for tc in response.tool_calls:
                        tools_used.append(tc.name)

                        # Emit tool begin event
                        hint = self._format_tool_hint(tc.name, tc.arguments)
                        await self._events.emit(ToolCallBeginEvent(
                            turn_id=turn_id,
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            tool_display=hint,
                            arguments=tc.arguments,
                        ))

                        # Route through ToolOrchestrator (sole execution path — no fallback)
                        from miqi.execution.orchestrator import (
                            OrchestrationResult,
                            ToolExecutionContext,
                        )
                        ctx = ToolExecutionContext(
                            tool_name=tc.name,
                            tool_call_id=tc.id,
                            arguments=tc.arguments,
                            turn_id=turn_id,
                            thread_id=agent.thread_id,
                            agent_type=agent.metadata.name,
                            # Phase 31.4: sub-agent context — client/session
                            # inherited from the parent TurnContext when available
                            client_id=turn_ctx.client_id,
                            session_id=turn_ctx.session_id,
                        )
                        # #821: sub-agents inherit the parent task's
                        # user-mentioned output dirs (the spawn prompt usually
                        # echoes them).
                        try:
                            from miqi.agent.tools.user_roots import extract_user_mentioned_roots

                            ctx.user_mentioned_roots = [
                                str(r) for r in extract_user_mentioned_roots(
                                    [task], workspace=self.workspace,
                                )
                            ]
                        except Exception:
                            ctx.user_mentioned_roots = []
                        ctx = await self._orchestrator.execute(ctx)
                        result = ctx.result or ""
                        success = ctx.status == OrchestrationResult.SUCCESS
                        duration = ctx.duration_ms

                        # Log tool call to workspace for debugging / audit
                        session_key = ":".join(
                            filter(None, (ctx.client_id, ctx.session_id))
                        ) or None
                        log_level = "INFO" if success else "ERROR"
                        args_preview = json.dumps(tc.arguments, ensure_ascii=False)[:500]
                        result_preview = (result or "")[:200]
                        append_workspace_log(
                            self.workspace,
                            (
                                f"tool_call [{tc.name}] success={success} "
                                f"duration={duration}ms session={session_key}\n"
                                f"  args: {args_preview}\n"
                                f"  result: {result_preview}"
                            ),
                            level=log_level,
                            source="tool",
                            session_key=session_key,
                        )

                        # Emit tool end event
                        preview = (result or "")[:200]
                        await self._events.emit(ToolCallEndEvent(
                            turn_id=turn_id,
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            success=success,
                            output_preview=preview,
                            output_size=len(result or ""),
                            duration_ms=duration,
                        ))

                        # Accumulate result for message history
                        tool_results.append(result or "")

                        # Build tool call dict for message history
                        tool_call_dicts.append({
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(
                                    tc.arguments, ensure_ascii=False
                                ),
                            },
                        })

                    # Add assistant message with tool calls (must come BEFORE tool results)
                    messages.append({
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": tool_call_dicts,
                    })

                    # Add tool results for each tool call
                    for tc, result in zip(tool_call_dicts, tool_results):
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "name": tc["function"]["name"],
                            "content": result or "",
                        })

                    # Reflect prompt for next iteration
                    messages.append({
                        "role": "system",
                        "content": (
                            "Check if the task is complete. "
                            "If more tools are needed, call them now. "
                            "If the task is done, provide a clear final answer."
                        ),
                    })

                    agent.state.transition(AgentStatus.THINKING)

                else:
                    # No tool calls — final response
                    final_content = self._strip_think(response.content)
                    final_content, was_modified = sanitize_tool_call_text(
                        final_content, tool_names_from_definitions(tool_defs)
                    )
                    if was_modified:
                        # The sub-agent wrote a tool call as plain text — it is
                        # never executed. Replace the internal placeholder with
                        # a human-readable notice instead of leaking it to the
                        # parent agent / user (issue #532).
                        final_content = final_content.replace(
                            LEAK_NOTICE,
                            "（检测到工具调用文本未通过正式接口执行，已忽略）",
                        )
                    # Record assistant response in messages
                    messages.append({
                        "role": "assistant",
                        "content": final_content,
                    })
                    if final_content:
                        await self._events.emit(AgentMessageEvent(
                            turn_id=turn_id,
                            content=final_content,
                            finish_reason=(
                                response.finish_reason or "stop"
                            ),
                        ))
                    break

            # Handle iteration exhaustion
            if final_content is None:
                summary = (
                    ", ".join(dict.fromkeys(tools_used))
                    if tools_used else "none"
                )
                final_content = (
                    f"Reached maximum iterations ({max_iterations}). "
                    f"Tools used: {summary}. "
                    f"Try breaking your task into smaller steps."
                )

            # Persist results on LiveAgent
            agent.result = final_content
            agent.messages = messages
            agent.completed_at = _time.time()

            # Turn complete
            agent.state.transition(AgentStatus.COMPLETED)
            await self._events.emit(TurnCompleteEvent(
                turn_id=turn_id,
                thread_id=agent.thread_id,
                outcome="success",
                tools_used=list(dict.fromkeys(tools_used)),
            ))
            await self._events.emit(SubAgentCompletedEvent(
                sub_agent_id=agent.agent_id,
                sub_thread_id=agent.thread_id,
                outcome="success",
                summary=(final_content or "")[:100],
            ))

        except asyncio.CancelledError:
            # Only transition if not already in a terminal state (kill may have
            # transitioned to ABORTED before the CancelledError propagated here)
            if agent.state.current not in (
                AgentStatus.ABORTED, AgentStatus.COMPLETED, AgentStatus.ERROR,
            ):
                agent.state.transition(AgentStatus.ABORTED)
            agent.error = "Cancelled"
            agent.completed_at = _time.time()
        except Exception as e:
            agent.state.transition(AgentStatus.ERROR)
            agent.error = str(e)
            agent.completed_at = _time.time()
            logger.error(
                "Agent {} turn error: {}", agent.agent_id, e
            )
            await self._events.emit(ErrorEvent(
                turn_id=turn_id,
                severity=EventSeverity.ERROR,
                message=str(e),
                recoverable=False,
            ))
        finally:
            # Phase 51.3: fire sub-agent lifecycle end hook on every completion path.
            if self._hooks is not None:
                try:
                    await self._hooks.run(
                        HookPoint.SUBAGENT_END,
                        LifecycleHookContext(
                            hook_point=HookPoint.SUBAGENT_END,
                            data={
                                "agent_id": agent.agent_id,
                                "thread_id": agent.thread_id,
                                "status": agent.state.current.value,
                            },
                        ),
                    )
                except Exception:
                    logger.exception(
                        "SUBAGENT_END hook failed for agent {}",
                        agent.agent_id,
                    )

            # Push the subagent completion to the frontend callback (if any).
            # This is what feeds the `chat:subagent_result` IPC event — the
            # frontend's ChatConsole renders subagent results exclusively from
            # that channel (Phase 13 removed the legacy SubagentManager
            # result_callback, leaving no producer; the Desktop transport
            # wires this callback to AppServer.emit_client_event).
            await self._emit_completed(agent)

    async def _emit_completed(
        self, agent: LiveAgent, status: str | None = None,
    ) -> None:
        """Notify the frontend that a subagent reached a terminal state.

        Payload matches the Desktop contract ``ChatSubagentResult``
        (apps/desktop/src/shared/ipc.ts): task_id, label, task, result,
        status ("ok" | "error" | "aborted"), session_key.
        """
        # Idempotent delivery: kill() emits aborted first; the cancelled
        # _run_agent task's finally would then emit again.  Only the first
        # terminal event reaches the frontend.
        if agent.completion_emitted:
            return
        agent.completion_emitted = True
        if self._completion_callback is None:
            return
        if status is None:
            state = agent.state.current.value  # completed | error | aborted
            status = "ok" if state == "completed" else state
        elif status == "completed":
            # ChatConsole keys on status === "ok" for the ✅ icon — normalize
            # the job status ("completed") to the ChatSubagentResult contract.
            status = "ok"
        try:
            await self._completion_callback({
                "task_id": agent.agent_id,
                "label": agent.label or agent.task or agent.agent_id,
                "task": agent.task or "",
                "result": agent.result or agent.error or "",
                "status": status,
                "session_key": self._renderer_session_key(),
            })
        except Exception:
            logger.warning(
                "Agent completion callback failed for {} ({})",
                agent.agent_id, agent.metadata.name,
            )

    def _renderer_session_key(self) -> str:
        """Strip the client_id prefix so the payload matches the renderer's
        session key format.

        ChatConsole's subagent handler compares ``data.session_key`` against
        ``currentSessionRef`` (e.g. ``"desktop:<ts>"``), while
        ``AgentControl.session_id`` is the namespaced
        ``"miqi-desktop:desktop:<ts>"`` — a raw pass-through silently drops
        every event.
        """
        sid = self.session_id
        if ":" in sid:
            return sid.split(":", 1)[1]
        return sid

    @staticmethod
    def _format_tool_hint(name: str, args: dict) -> str:
        """Format a tool call as a concise display hint.

        Path-like and command args show the target value (truncated at 50
        chars); every other arg shows only the parameter name. Values like
        paper titles, URLs, or queries are long strings that would leak
        into the hint instead of a concise call summary (issue #532).
        """
        if not args:
            return name
        for key in ("path", "file_path", "filename", "outPath", "command"):
            val = args.get(key)
            if isinstance(val, str) and val:
                if len(val) > 50:
                    return f'{name}("{val[:50]}…")'
                return f'{name}("{val}")'
        key = next(iter(args), "")
        return f"{name}({key}=…)" if key else name

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks from model output."""
        if not text:
            return None
        import re
        result = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
        return result or None
