"""Shared runtime services — builds and owns the service graph for one session.

This is the single factory that creates the full service graph (ToolRegistry,
ToolOrchestrator, AgentControl, TurnRunner, PluginManager, CapabilityResolver,
McpRuntime, etc.) for one session. Frontends should use RuntimeSession instead
of building services directly.

Phase 48: RuntimeServices owns the service graph directly. Model configuration
is carried by the immutable RuntimeModelSettings value object.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from miqi.execution.hook_runtime import HookRuntime


def _resolve_exec_timeout_ms(config: Any) -> int | None:
    """Per-call exec timeout from ``tools.exec.timeout`` (seconds) → ms.

    The SandboxSelection timeout must not silently cap commands below the
    user's configured exec timeout (the engine's 30 s default previously
    overrode a configured 60 s).  Returns None to keep the engine default.
    """
    try:
        tools_cfg = getattr(config, "tools", None)
        exec_cfg = getattr(tools_cfg, "exec", None) if tools_cfg is not None else None
        timeout_s = getattr(exec_cfg, "timeout", None)
        if isinstance(timeout_s, (int, float)) and timeout_s > 0:
            return int(timeout_s * 1000)
    except Exception:
        pass
    return None


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
    """All services needed for a single runtime session.

    Owns the full service graph for a single session — ToolRegistry,
    ToolOrchestrator, AgentControl, TurnRunner, PluginManager,
    CapabilityResolver, McpRuntime, and all related wiring.
    Created once per session via from_config().
    """

    session_id: str
    workspace: Path
    bus: Any  # MessageBus
    provider: Any
    event_emitter: RuntimeEventEmitter
    model_settings: RuntimeModelSettings  # immutable model config from config.agents.defaults
    tool_registry: Any
    orchestrator: Any
    agent_registry: Any  # AgentRegistry
    agent_control: Any  # AgentControl
    tool_runtime: Any  # ToolRuntime (Phase 12)
    context_runtime: Any  # ContextRuntime (Phase 12)
    turn_runner: Any  # TurnRunner (Phase 12)
    # Phase 13
    plugin_manager: Any | None = None
    agent_jobs: Any | None = None  # AgentJobRuntime
    capability_resolver: Any | None = None  # CapabilityResolver
    # Phase 17: session / thread / history runtime
    session_state: Any | None = None
    history_runtime: Any | None = None
    thread_runtime: Any | None = None
    # Phase 21: MCP runtime adapter
    mcp_runtime: Any | None = None
    # Phase 24: append-only event ledger
    ledger_runtime: Any | None = None
    # Phase 25: replay/debug runtime
    replay_runtime: Any | None = None
    # Phase 51.3: shared lifecycle hook runtime
    hooks: HookRuntime | None = None
    # Phase 52: shared agent graph persistence
    agent_graph_store: Any | None = None
    # Live sandbox manager reference (enabled/_initialized reflect current
    # state) — used by prompt builders for an accurate exec environment story.
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
        """Build the full service graph from a Config + provider.

        Returns a RuntimeServices ready for use by RuntimeSession.
        """
        # Lazy imports to avoid circular imports
        from miqi.bus.queue import MessageBus
        from miqi.execution.factory import create_default_orchestrator
        from miqi.plan.plan_tracker import PlanTracker
        from miqi.runtime.agent_control import AgentControl
        from miqi.runtime.agent_registry import AgentRegistry
        from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

        bus = MessageBus()
        defaults = config.agents.defaults
        effective_bypass = getattr(config, "effective_approval_bypass", None)
        approval_bypass = (
            effective_bypass()
            if callable(effective_bypass)
            else getattr(config, "approvals", None)
        )
        if bool(getattr(getattr(config, "approvals", None), "enabled", False)):
            logger.warning(
                "Approval bypass is enabled for session {}; approval prompts may be skipped.",
                session_id,
            )

        # Historical (Phase 22): runtime-owned tool registry (replaced AgentLoop._register_default_tools)
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

        # Immutable model configuration for runtime-owned execution
        model_settings = RuntimeModelSettings(
            model=defaults.model,
            temperature=defaults.temperature,
            max_tokens=defaults.max_tokens,
            max_tool_result_chars=defaults.max_tool_result_chars,
            context_limit_chars=defaults.context_limit_chars,
        )

        # Phase 59: tee telemetry sink when enabled (additive, no-op by default).
        # Telemetry failures are silently swallowed — they never break a turn.
        if (
            hasattr(config, "observability")
            and getattr(config.observability, "enabled", False)
        ):
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

        # 实时求值而非冻结（#875 产品发现）：沙箱管理器初始化是 ready 信号
        # 后的异步后台任务——会话创建早于初始化时，冻结的 False 会让该会话
        # 的 exec 永远落到 NONE（宿主机无隔离直连），沙箱就绪后也拿不到
        # BWRAP 选择（静默绕过用户配置的隔离，系统安装路由同样失效）。
        # 传 lambda 让 SandboxPolicyEngine 每次 select() 读取当前状态。
        def _bwrap_available_now() -> bool:
            return (
                sandbox_manager is not None
                and sandbox_manager != "disabled"
                and getattr(sandbox_manager, "enabled", False)
                and getattr(sandbox_manager, "_initialized", False)
            )

        orchestrator = create_default_orchestrator(
            tool_registry=tool_registry,
            event_emitter=emitter,
            bwrap_available=_bwrap_available_now,
            approval_bypass=approval_bypass,
            exec_timeout_ms=_resolve_exec_timeout_ms(config),
        )

        # Phase 52: shared agent graph persistence (created before AgentControl)
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

        # Wire SpawnTool into AgentControl
        spawn_tool = tool_registry.get("spawn")
        if spawn_tool is not None and hasattr(spawn_tool, "_agent_control"):
            spawn_tool._agent_control = agent_control
            spawn_tool._event_emitter = emitter

        # Phase 12: runtime-owned turn execution components
        from miqi.runtime.context_runtime import ContextRuntime
        from miqi.runtime.tool_runtime import ToolRuntime
        from miqi.runtime.turn_runner import TurnRunner

        tool_runtime = ToolRuntime(orchestrator=orchestrator)

        # Phase 19 follow-up: wire real ContextCompressor via provider.chat()
        async def _summarize_for_compaction(
            msgs: list[dict[str, Any]], model: str,
        ) -> str:
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

        # Phase 13: capability resolver (requires PluginManager and ToolRegistry)
        from pathlib import Path as _Path

        from miqi.paths import get_miqi_home
        from miqi.runtime.capabilities import CapabilityResolver
        from miqi.skills.plugin_manager import PluginManager

        plugin_manager = PluginManager(
            user_plugins_dir=get_miqi_home() / "plugins",
            system_plugins_dir=_Path(__file__).parent.parent / "plugins",
            workspace=workspace,
            hook_runtime=hook_runtime,
        )

        capability_resolver = CapabilityResolver(
            tool_registry=tool_registry,
            plugin_manager=plugin_manager,
        )

        # Phase 21: MCP runtime adapter
        from miqi.runtime.mcp_runtime import McpRuntime
        mcp_runtime = McpRuntime(plugin_manager=plugin_manager)

        # Phase 24: ledger runtime (created early so TurnRunner can use it)
        runtime_db = workspace / ".miqi-runtime" / "runtime.db"
        from miqi.runtime.ledger_runtime import LedgerRuntime

        ledger_runtime = LedgerRuntime(runtime_db, session_id=session_id)

        # Phase 31.8: wire ledger into orchestrator so exec/approval events
        # are recorded for replay.
        orchestrator._ledger = ledger_runtime

        # Phase 25: replay runtime (wraps ledger for reconstruction)
        from miqi.runtime.replay_runtime import ReplayRuntime

        replay_runtime = ReplayRuntime(ledger_runtime)

        turn_runner = TurnRunner(
            provider=provider,
            tool_runtime=tool_runtime,
            context_runtime=context_runtime,
            event_emitter=emitter,
            max_iterations=defaults.max_tool_iterations,
            capability_resolver=capability_resolver,
            ledger_runtime=ledger_runtime,
            hooks=hook_runtime,
        )

        # Phase 13: AgentJobRuntime (depends on TurnRunner)
        from miqi.runtime.agent_jobs import AgentJobRuntime

        # Phase 17: session state, history runtime, thread runtime
        from miqi.runtime.history_runtime import HistoryRuntime
        from miqi.runtime.session_state import SessionState
        from miqi.runtime.thread_runtime import ThreadRuntime

        history_runtime = HistoryRuntime(runtime_db, session_id=session_id)
        thread_runtime = ThreadRuntime(runtime_db, session_id=session_id)
        # #740: wire history runtime into TurnRunner for execution snapshots
        turn_runner._history = history_runtime

        session_state = SessionState(
            session_id=session_id,
            workspace=workspace,
            active_thread_id=f"{session_id}:default",
            config_snapshot=config,
        )

        # Build partial services so AgentJobRuntime can reference them
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

        # Wire AgentJobRuntime into AgentControl (Phase 13 delegation)
        agent_control._agent_jobs = agent_jobs

        return services

    # ── Hot config reload (#789) ─────────────────────────────────────────
    def apply_config_update(
        self,
        new_config: Any,
        *,
        changed_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Hot-apply a saved config to this runtime session without restart.

        Issue #789: after ``config.update`` / ``config/batchWrite`` /
        ``providers.update`` persist a new config, this method refreshes the
        runtime-owned components so the NEXT turn uses the new values.

        *changed_paths* (tier-A paths from ``classify_config_update``) gates
        every step — a save that did not touch providers/model must not
        rebuild the provider, must not clobber the context compressor's
        incremental summary state, and must not resurrect allowlist patterns
        (2026-08-26 review: the classifier table and this applier share a
        contract; a tier-A label is only valid when a real step exists).

        Steps and their gates:
        1. Provider rebuild (providers.* / agents.defaults.model) — an
           in-flight turn keeps its captured provider (turn_runner._running).
        2. Model settings rebuild (model-settings paths).
        3. Config snapshot refresh (always — per-turn readers).
        4. Approval bypass sync (approvals.* / agents.command_approval).
        5. Permanent allowlist replace (agents.permanent_approvals).
        6. Context compressor closure rebuild — only when the provider was
           actually rebuilt or context_limit_chars changed (preserves the
           five-phase incremental summary + failure cooldown otherwise).

        Failures are logged and keep the previous value (rollback semantics)
        — a failed hot-apply never leaves the runtime half-updated.  When the
        provider rebuild fails while the model changed, the previous model is
        kept too (an old provider object paired with a NEW model name would
        400 on the next turn).

        Returns:
            dict with ``provider_rebuilt`` flag — True when the rebuild
            succeeded (the turn_runner swapped the reference immediately, or
            parked it for adoption at the next turn when one was running);
            False only when ``make_provider`` failed and the old provider
            stayed in place.
        """
        applied: dict[str, Any] = {"provider_rebuilt": False}
        paths = changed_paths or []

        def touched(*prefixes: str) -> bool:
            return any(
                p == pref or p.startswith(pref + ".")
                for p in paths
                for pref in prefixes
            )

        defaults = new_config.agents.defaults

        # 1. Provider rebuild — gated on provider/model changes; an in-flight
        #    turn keeps the provider it captured at turn start and the
        #    replacement is parked on the runner for adoption at the start of
        #    the NEXT turn (#1 review + deferred swap).
        if touched("providers", "agents.defaults.model"):
            try:
                from miqi.providers.factory import make_provider

                new_provider = make_provider(new_config)
                if new_provider is not None:
                    self.provider = new_provider
                    applied["provider_rebuilt"] = True
                    turn_runner = getattr(self, "turn_runner", None)
                    if turn_runner is not None and hasattr(
                        turn_runner, "_provider"
                    ):
                        if getattr(turn_runner, "_running", False):
                            # A running turn must keep its captured provider +
                            # model string (no 400 risk); the runner adopts the
                            # new provider at the start of the next run().
                            turn_runner._pending_provider = new_provider
                        else:
                            turn_runner._provider = new_provider
                    # Sub-agent control path (#9 review): keep AgentControl on
                    # the same provider as the main turn.
                    agent_control = getattr(self, "agent_control", None)
                    if agent_control is not None and hasattr(
                        agent_control, "_provider"
                    ):
                        agent_control._provider = new_provider
            except Exception as exc:
                logger.warning(
                    "apply_config_update: provider rebuild failed, keeping old provider: {}",
                    exc,
                )

        # 2. Model settings (immutable dataclass — rebuild) — gated.
        if touched(
            "agents.defaults.model",
            "agents.defaults.temperature",
            "agents.defaults.max_tokens",
            "agents.defaults.max_tool_result_chars",
            "agents.defaults.context_limit_chars",
            "agents.defaults.max_tool_iterations",
            "agents.defaults.name",
        ):
            # Rollback guard (#789 review): if the provider rebuild above
            # failed while the model changed, the runtime keeps the OLD
            # provider object — pairing it with the NEW model name would
            # 400 on the next turn. Keep the previous model until a save
            # succeeds (no half-updated provider/model state).
            model = defaults.model
            if (
                not applied.get("provider_rebuilt")
                and self.model_settings is not None
                and getattr(self.model_settings, "model", None) != defaults.model
            ):
                logger.warning(
                    "apply_config_update: provider rebuild failed while the "
                    "model changed; keeping the previous model to avoid a "
                    "provider/model mismatch",
                )
                model = self.model_settings.model
            self.model_settings = RuntimeModelSettings(
                model=model,
                temperature=defaults.temperature,
                max_tokens=defaults.max_tokens,
                max_tool_result_chars=defaults.max_tool_result_chars,
                context_limit_chars=defaults.context_limit_chars,
            )
            # Iteration cap on TurnRunner is captured at construction.
            # max_tool_iterations is in the outer gate so a save that ONLY
            # changes the iteration cap still applies it (2nd review: it was
            # nested inside the model-settings gate — a lone iteration-cap
            # save reported "已生效" but never reached this line).
            turn_runner = getattr(self, "turn_runner", None)
            if turn_runner is not None and hasattr(
                turn_runner, "_max_iterations"
            ):
                turn_runner._max_iterations = defaults.max_tool_iterations

        # 3. Config snapshot (per-turn readers) — always cheap, always fresh.
        if self.session_state is not None:
            self.session_state.config_snapshot = new_config

        # 4. Approval bypass — gated on approval policy paths.
        if touched("approvals", "agents.command_approval"):
            try:
                permissions = getattr(self.orchestrator, "permissions", None)
                if permissions is not None and hasattr(
                    permissions, "approval_bypass"
                ):
                    effective_bypass = getattr(
                        new_config, "effective_approval_bypass", None
                    )
                    permissions.approval_bypass = (
                        effective_bypass()
                        if callable(effective_bypass)
                        else getattr(new_config, "approvals", None)
                    )
            except Exception as exc:
                logger.warning(
                    "apply_config_update: approval bypass update failed: {}", exc
                )

        # 5. Permanent approval allowlist — replace to match config exactly,
        #    only when the save actually touched it (an unrelated save must
        #    not clobber runtime-approved patterns, #7 review).
        if touched("agents.permanent_approvals"):
            try:
                patterns = (
                    getattr(new_config.agents, "permanent_approvals", None) or []
                )
                from miqi.agent.command_approval import replace_permanent_allowlist

                replace_permanent_allowlist(set(patterns))
            except Exception as exc:
                logger.warning(
                    "apply_config_update: permanent allowlist update failed: {}",
                    exc,
                )

        # 6. Context compressor closure — rebuild ONLY when the provider was
        #    actually rebuilt or the compression threshold changed (#4/#5).
        if applied.get("provider_rebuilt") or touched(
            "agents.defaults.context_limit_chars"
        ):
            try:
                context_runtime = getattr(self, "context_runtime", None)
                if context_runtime is not None and hasattr(
                    context_runtime, "set_llm_call_fn"
                ):

                    async def _llm_for_compaction(
                        msgs: list[dict[str, Any]], model: str,
                    ) -> str:
                        response = await self.provider.chat(
                            messages=msgs,
                            tools=None,
                            model=model,
                            temperature=0.3,
                            max_tokens=4096,
                        )
                        return response.content or ""

                    context_runtime.set_llm_call_fn(
                        _llm_for_compaction,
                        context_limit_chars=defaults.context_limit_chars,
                    )
            except Exception as exc:
                logger.warning(
                    "apply_config_update: context compressor refresh failed: {}",
                    exc,
                )

        return applied
