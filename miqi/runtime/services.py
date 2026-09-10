"""Shared runtime services — builds and owns the service graph for one session.

This is the single factory that creates the full service graph (ToolRegistry,
ToolOrchestrator, AgentControl, TurnRunner, PluginManager, CapabilityResolver,
McpRuntime, etc.) for one session. Frontends should use RuntimeSession instead
of building services directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from miqi.execution.hook_runtime import HookRuntime


class RuntimeEventEmitter:
    """Event emitter that routes typed protocol events to a configurable sink."""

    def __init__(self, sink: Any | None = None):
        self._sink = sink

    async def emit(self, event: Any) -> None:
        if self._sink is None:
            return
        await self._sink(event)


@dataclass(frozen=True)
class RuntimeModelSettings:
    """Model configuration consumed by runtime-owned execution."""

    model: str
    temperature: float
    max_tokens: int
    max_tool_result_chars: int
    context_limit_chars: int


@dataclass
class RuntimeServices:
    """All services needed for a single runtime session."""

    session_id: str
    workspace: Path
    bus: Any
    provider: Any
    event_emitter: RuntimeEventEmitter
    model_settings: RuntimeModelSettings
    tool_registry: Any
    orchestrator: Any
    agent_registry: Any
    agent_control: Any
    tool_runtime: Any
    context_runtime: Any
    turn_runner: Any
    plugin_manager: Any | None = None
    agent_jobs: Any | None = None
    capability_resolver: Any | None = None
    session_state: Any | None = None
    history_runtime: Any | None = None
    thread_runtime: Any | None = None
    mcp_runtime: Any | None = None
    ledger_runtime: Any | None = None
    replay_runtime: Any | None = None
    hooks: HookRuntime | None = None
    agent_graph_store: Any | None = None
    sandbox_manager: Any | None = None

    @classmethod
    def from_config(
        cls,
        *,
        config: Any,
        provider: Any,
        session_id: str,
        workspace: Path,
        event_sink: Any | None = None,
        sandbox_manager: Any = None,
        agent_completion_callback: Any | None = None,
    ) -> "RuntimeServices":
        from miqi.bus.queue import MessageBus
        from miqi.execution.factory import create_default_orchestrator
        from miqi.plan.plan_tracker import PlanTracker
        from miqi.runtime.agent_control import AgentControl
        from miqi.runtime.agent_registry import AgentRegistry
        from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

        bus = MessageBus()
        defaults = config.agents.defaults
        effective_bypass = getattr(config, "effective_approval_bypass", None)
        approval_bypass = effective_bypass() if callable(effective_bypass) else getattr(config, "approvals", None)
        if bool(getattr(getattr(config, "approvals", None), "enabled", False)):
            logger.warning("Approval bypass is enabled for session {}; approval prompts may be skipped.", session_id)

        plan_tracker = PlanTracker()
        tool_registry = create_runtime_tool_registry(
            config=config,
            workspace=workspace,
            session_id=session_id,
            provider=provider,
            bus=bus,
            approval_callback=None,
            sandbox_manager=sandbox_manager,
            plan_tracker=plan_tracker,
        )

        model_settings = RuntimeModelSettings(
            model=defaults.model,
            temperature=defaults.temperature,
            max_tokens=defaults.max_tokens,
            max_tool_result_chars=defaults.max_tool_result_chars,
            context_limit_chars=defaults.context_limit_chars,
        )

        if hasattr(config, "observability") and getattr(config.observability, "enabled", False):
            from miqi.observability.otel import build_telemetry_sink
            telemetry_handle = build_telemetry_sink(config.observability)
            if telemetry_handle is not None:
                original_sink = event_sink
                async def _tee(event: Any) -> None:
                    if original_sink is not None:
                        await original_sink(event)
                    try:
                        await telemetry_handle(event)
                    except Exception:
                        pass
                event_sink = _tee

        emitter = RuntimeEventEmitter(event_sink)
        hook_runtime = HookRuntime()
        bwrap_available = (
            sandbox_manager is not None
            and sandbox_manager != "disabled"
            and getattr(sandbox_manager, "enabled", False)
            and getattr(sandbox_manager, "_initialized", False)
        )
        orchestrator = create_default_orchestrator(
            tool_registry=tool_registry,
            event_emitter=emitter,
            bwrap_available=bwrap_available,
            approval_bypass=approval_bypass,
        )

        agent_graph_db = workspace / ".miqi-runtime" / "agent_graph.db"
        from miqi.runtime.agent_graph_store import AgentGraphStore
        agent_graph_store = AgentGraphStore(agent_graph_db)

        registry = AgentRegistry()
        agent_control = AgentControl(
            session_id=session_id,
            registry=registry,
            event_emitter=emitter,
            workspace=workspace,
            provider=provider,
            orchestrator=orchestrator,
            tool_registry=tool_registry,
            hooks=hook_runtime,
            store=agent_graph_store,
            completion_callback=agent_completion_callback,
            sandbox_manager=sandbox_manager,
        )

        spawn_tool = tool_registry.get("spawn")
        if spawn_tool is not None and hasattr(spawn_tool, "_agent_control"):
            spawn_tool._agent_control = agent_control
            spawn_tool._event_emitter = emitter

        from miqi.runtime.context_runtime import ContextRuntime
        from miqi.runtime.tool_runtime import ToolRuntime
        from miqi.runtime.collaborative_turn_runner import CollaborativeTurnRunner

        tool_runtime = ToolRuntime(orchestrator=orchestrator)

        async def _summarize_for_compaction(msgs: list[dict[str, Any]], model: str) -> str:
            response = await provider.chat(
                messages=msgs,
                tools=None,
                model=model,
                temperature=0.3,
                max_tokens=4096,
            )
            return response.content or ""

        context_runtime = ContextRuntime(
            llm_call_fn=_summarize_for_compaction,
            context_limit_chars=defaults.context_limit_chars,
            hooks=hook_runtime,
        )

        from pathlib import Path as _Path
        from miqi.runtime.capabilities import CapabilityResolver
        from miqi.skills.plugin_manager import PluginManager
        from miqi.paths import get_miqi_home

        plugin_manager = PluginManager(
            user_plugins_dir=get_miqi_home() / "plugins",
            system_plugins_dir=_Path(__file__).parent.parent / "plugins",
            workspace=workspace,
            hook_runtime=hook_runtime,
        )
        capability_resolver = CapabilityResolver(tool_registry=tool_registry, plugin_manager=plugin_manager)

        from miqi.runtime.mcp_runtime import McpRuntime
        mcp_runtime = McpRuntime(plugin_manager=plugin_manager)

        runtime_db = workspace / ".miqi-runtime" / "runtime.db"
        from miqi.runtime.ledger_runtime import LedgerRuntime
        ledger_runtime = LedgerRuntime(runtime_db, session_id=session_id)
        orchestrator._ledger = ledger_runtime

        from miqi.runtime.replay_runtime import ReplayRuntime
        replay_runtime = ReplayRuntime(ledger_runtime)

        turn_runner = CollaborativeTurnRunner(
            provider=provider,
            tool_runtime=tool_runtime,
            context_runtime=context_runtime,
            event_emitter=emitter,
            max_iterations=defaults.max_tool_iterations,
            capability_resolver=capability_resolver,
            ledger_runtime=ledger_runtime,
            hooks=hook_runtime,
        )

        from miqi.runtime.agent_jobs import AgentJobRuntime
        from miqi.runtime.history_runtime import HistoryRuntime
        from miqi.runtime.session_state import SessionState
        from miqi.runtime.thread_runtime import ThreadRuntime

        history_runtime = HistoryRuntime(runtime_db, session_id=session_id)
        thread_runtime = ThreadRuntime(runtime_db, session_id=session_id)
        turn_runner._history = history_runtime

        session_state = SessionState(
            session_id=session_id,
            workspace=workspace,
            active_thread_id=f"{session_id}:default",
            config_snapshot=config,
        )

        services = cls(
            session_id=session_id,
            workspace=workspace,
            bus=bus,
            provider=provider,
            event_emitter=emitter,
            model_settings=model_settings,
            tool_registry=tool_registry,
            orchestrator=orchestrator,
            agent_registry=registry,
            agent_control=agent_control,
            tool_runtime=tool_runtime,
            context_runtime=context_runtime,
            turn_runner=turn_runner,
            plugin_manager=plugin_manager,
            capability_resolver=capability_resolver,
            session_state=session_state,
            history_runtime=history_runtime,
            thread_runtime=thread_runtime,
            mcp_runtime=mcp_runtime,
            ledger_runtime=ledger_runtime,
            replay_runtime=replay_runtime,
            hooks=hook_runtime,
            sandbox_manager=sandbox_manager,
        )

        agent_jobs = AgentJobRuntime(services=services, store=agent_graph_store)
        services.agent_jobs = agent_jobs
        services.agent_graph_store = agent_graph_store
        agent_control._agent_jobs = agent_jobs
        return services
