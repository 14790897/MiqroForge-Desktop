"""Task runner — dispatches incoming submissions to the right handler.

Routes UserMessage through TurnRunner, handles AbortTurn, and emits
typed protocol events onto the shared event queue.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import uuid
from typing import Any

from loguru import logger

from miqi.protocol.commands import (
    AbortTurn,
    ApprovalResponse,
    CompactCommand,
    ConfigUpdate,
    RunUserShellCommand,
    SteerTurn,
    ThreadCommand,
    UserInputAnswer,
    UserMessage,
)
from miqi.protocol.events import (
    AgentMessageEvent,
    ApprovalResolvedEvent,
    CommandRejectedEvent,
    ConfigUpdatedEvent,
    ContextCompactedEvent,
    ErrorEvent,
    EventSeverity,
    TurnAbortedEvent,
    TurnCompleteEvent,
    TurnStartedEvent,
)

# System-prompt prefix per agent execution mode (plan/manual/edit/auto).
_MODE_PROMPTS = {
    "plan": (
        "【Agent 模式：规划 — 只读分析】你的角色是分析助手。"
        "你可以使用只读工具（搜索、读文件、查看代码）获取信息、分析问题、提供方案和建议。\n"
        "限制：不能修改文件，不能执行会改变环境的命令，不能创建或删除资源。\n"
        "请在回答中充分利用搜索、阅读等只读工具来获取信息并给出分析。"
        "如果用户请求修改，请描述修改方案和步骤，"
        "等待用户切换到「允许编辑」或「自动」模式后再执行。\n\n"
    ),
    "manual": (
        "【Agent 模式：手动】你的角色是协作者。你有全部工具，但每个操作需要用户确认。"
        "请逐步说明你打算做什么（改哪个文件、执行什么命令），等待用户逐一批准后再动手。\n\n"
    ),
    "edit": (
        "【Agent 模式：允许编辑】你的角色是工程师。直接修改文件，安全操作自动放行。"
        "危险操作（执行命令、网络请求、删除文件）需要用户确认。高效工作。\n\n"
    ),
    "auto": (
        "【Agent 模式：自动】你的角色是全权代理。完全自主执行，不中断询问。"
        "直接完成任务，注意安全底线。用户信任你的判断。\n\n"
    ),
}


def _classify_chain(exc: BaseException):
    """Classify an exception, unwrapping a FATAL wrapper via __cause__/__context__.

    Issue #529: an SDK error that escaped retry (or was re-wrapped by an
    intermediate layer) reaches TaskRunner as a raw exception with no
    ``ProviderError`` tag. ``classify_error`` on the outer wrapper may yield
    ``FATAL`` while the real cause — in ``__cause__``/``__context__`` — still
    carries a meaningful kind (TRANSIENT, RATE_LIMIT, ...). Only when the
    direct classification lands on FATAL do we look one level down the chain;
    a non-FATAL result (or no cause) is returned as-is.
    """
    from miqi.providers.resilience import ErrorKind, classify_error

    kind = classify_error(exc)
    if kind is ErrorKind.FATAL:
        cause = exc.__cause__ or exc.__context__
        if cause is not None and cause is not exc:
            cause_kind = classify_error(cause)
            if cause_kind is not ErrorKind.FATAL:
                return cause_kind
    return kind


class TaskRunner:
    """Dispatches submissions and converts TurnRunner output to typed events.

    Does NOT own services — it receives them from RuntimeSession.
    """

    def __init__(self, *, services: Any, event_queue: Any):
        self.services = services
        self._events = event_queue
        # Phase 14 follow-up: per-thread active turn cancellation event
        self._turn_cancel_events: dict[str, asyncio.Event] = {}
        # Phase 41: active turn tracking
        self._active_turn_ids: dict[str, str] = {}
        self._turn_steer_queues: dict[str, asyncio.Queue] = {}
        # Lazily initialised SessionManager for dual-write compatibility
        self._legacy_sm: Any = None

    async def _save_to_session_manager(self, *, role: str, content: str, **extra: Any) -> None:
        """Dual-write a message to the legacy SessionManager JSONL store.

        AppServer-managed sessions use HistoryRuntime (SQLite), but the
        sessions.get handler reads from SessionManager (JSONL).  This mirror
        write keeps both stores in sync so sidebar switching works.

        Extra keyword arguments (e.g. tool_calls, name, tool_call_id) are
        forwarded to ``add_message`` so the frontend can reconstruct file
        operations from stored messages when tracked_files.json is empty.
        """
        try:
            session_id: str = self.services.session_id
            # session_id format: "{client_id}:{session_key}"
            if ":" not in session_id:
                return  # Unknown format — skip
            client_id, session_key = session_id.split(":", 1)
            workspace = self.services.workspace
            if workspace is None:
                return
            from miqi.session.manager import SessionManager
            if self._legacy_sm is None:
                self._legacy_sm = SessionManager(workspace)
            # Pass client_id so the session gets owner_client_id in metadata.
            # The sessions_get_handler calls get_or_create with client_id and
            # raises REQUIRES_CLAIM for unowned sessions.
            session = self._legacy_sm.get_or_create(session_key, client_id=client_id)
            session.add_message(role, content, **extra)
            self._legacy_sm.save(session)
        except Exception:
            logger.warning("Failed to mirror message to legacy SessionManager", exc_info=True)

    # ── Phase 41: active turn and steering ────────────────────────────────

    def active_turn_id(self, thread_id: str) -> str | None:
        return self._active_turn_ids.get(thread_id)

    async def steer_turn(
        self,
        *,
        thread_id: str,
        expected_turn_id: str,
        content: str,
        input_items: list[dict[str, Any]],
        client_user_message_id: str | None,
    ) -> bool:
        active = self._active_turn_ids.get(thread_id)
        if active != expected_turn_id:
            return False
        queue = self._turn_steer_queues.get(expected_turn_id)
        if queue is None:
            return False
        await queue.put({
            "content": content,
            "input_items": input_items,
            "client_user_message_id": client_user_message_id,
        })
        return True

    # ── dispatch ──────────────────────────────────────────────────────────

    async def handle(self, submission: Any) -> None:
        """Route a submission to the correct handler."""
        if isinstance(submission, UserMessage):
            await self._handle_user_message(submission)
            return
        if isinstance(submission, SteerTurn):
            accepted = await self.steer_turn(
                thread_id=submission.thread_id,
                expected_turn_id=submission.expected_turn_id,
                content=submission.content,
                input_items=submission.input_items,
                client_user_message_id=submission.client_user_message_id,
            )
            if not accepted:
                await self._events.put(CommandRejectedEvent(
                    command_type="SteerTurn",
                    reason="Active turn not steerable",
                    recoverable=False,
                ))
            return
        if isinstance(submission, AbortTurn):
            # Phase 14 follow-up: signal cancellation to the active turn
            thread_id = getattr(submission, "thread_id", None) or "default"
            cancel_evt = self._turn_cancel_events.get(thread_id)
            if cancel_evt is not None:
                cancel_evt.set()

            # Phase 31.4: cancel any pending approvals for this thread
            # so waiting tool calls are unblocked and no orphan approvals
            # remain in the pending set.
            orchestrator = self.services.orchestrator
            cancel_fn = getattr(orchestrator, "cancel_approvals_for_thread", None)
            if callable(cancel_fn) and inspect.iscoroutinefunction(cancel_fn):
                await cancel_fn(thread_id, reason="Turn aborted by user.")

            await self._events.put(ErrorEvent(
                turn_id=str(uuid.uuid4())[:12],
                severity=EventSeverity.WARNING,
                message=f"Turn aborted for thread {thread_id}.",
                recoverable=True,
            ))
            return
        if isinstance(submission, ApprovalResponse):
            # Phase 18: resolve orchestrator approval
            orchestrator = self.services.orchestrator
            if orchestrator is None or not hasattr(orchestrator, "resolve_approval"):
                await self._events.put(CommandRejectedEvent(
                    command_type="ApprovalResponse",
                    reason="Runtime has no approval resolver",
                    recoverable=False,
                ))
                return
            result = orchestrator.resolve_approval(
                submission.approval_id,
                submission.decision,
            )
            # Phase 31.4: only emit terminal ApprovalResolvedEvent when
            # the orchestrator confirms the approval was actually resolved.
            # Invalid/nonexistent approvals emit CommandRejectedEvent instead.
            if result.resolved:
                await self._events.put(ApprovalResolvedEvent(
                    approval_id=result.approval_id,
                    decision=result.normalized_decision,
                    turn_id=result.turn_id,
                ))
            else:
                await self._events.put(CommandRejectedEvent(
                    command_type="ApprovalResponse",
                    reason=result.reason or "Approval resolution failed",
                    recoverable=False,
                ))
            return
        if isinstance(submission, ThreadCommand):
            await self._handle_thread_command(submission)
            return
        if isinstance(submission, ConfigUpdate):
            # Phase 18: mutate session state and emit ConfigUpdatedEvent.
            # All failure paths must emit CommandRejectedEvent, never crash.
            state = self.services.session_state
            if state is None or not hasattr(state, "apply_config_update"):
                await self._events.put(CommandRejectedEvent(
                    command_type="ConfigUpdate",
                    reason="Runtime has no mutable session state",
                    recoverable=False,
                ))
                return
            try:
                state.apply_config_update(submission.path, submission.value)
            except (ValueError, AttributeError, TypeError) as exc:
                await self._events.put(CommandRejectedEvent(
                    command_type="ConfigUpdate",
                    reason=str(exc),
                    recoverable=False,
                ))
                return
            await self._events.put(ConfigUpdatedEvent(
                path=submission.path,
                value=submission.value,
            ))
            return
        if isinstance(submission, CompactCommand):
            # Phase 19: trigger context compaction via ContextRuntime
            ctx_runtime = self.services.context_runtime
            history_runtime = self.services.history_runtime
            if ctx_runtime is None or history_runtime is None:
                await self._events.put(CommandRejectedEvent(
                    command_type="CompactCommand",
                    reason="Runtime has no context or history manager",
                    recoverable=False,
                ))
                return
            compact_turn_id = f"compact-{str(uuid.uuid4())[:12]}"
            try:
                result = await ctx_runtime.compact_thread(
                    history_runtime=history_runtime,
                    thread_id=submission.thread_id,
                    turn_id=compact_turn_id,
                    model=self.services.model_settings.model,
                )
            except Exception as exc:
                await self._events.put(CommandRejectedEvent(
                    command_type="CompactCommand",
                    reason=str(exc),
                    recoverable=True,
                ))
                return
            await self._events.put(ContextCompactedEvent(
                turn_id=compact_turn_id,
                thread_id=result.thread_id,
                messages_before=result.messages_before,
                messages_after=result.messages_after,
                tokens_saved=result.tokens_saved,
            ))
            return
        if isinstance(submission, RunUserShellCommand):
            await self._handle_user_shell_command(submission)
            return
        if isinstance(submission, UserInputAnswer):
            await self._events.put(CommandRejectedEvent(
                command_type=type(submission).__name__,
                reason=f"{type(submission).__name__} is reserved for future use",
                recoverable=True,
            ))
            return
        await self._events.put(CommandRejectedEvent(
            command_type=type(submission).__name__,
            reason=f"Unknown submission type: {type(submission).__name__}",
            recoverable=False,
        ))

    async def _handle_user_shell_command(self, cmd: RunUserShellCommand) -> None:
        command = (cmd.command or "").strip()
        thread_id = cmd.thread_id
        turn_id = cmd.turn_id

        if not command:
            await self._events.put(CommandRejectedEvent(
                command_type="RunUserShellCommand",
                reason="command is required",
                recoverable=False,
            ))
            return

        if not thread_id or not turn_id:
            await self._events.put(CommandRejectedEvent(
                command_type="RunUserShellCommand",
                reason="thread_id and turn_id are required",
                recoverable=False,
            ))
            return

        from types import SimpleNamespace

        from miqi.runtime.agent_registry import AgentRegistry
        from miqi.runtime.permission_profile import PermissionProfile
        from miqi.runtime.turn_context import TurnContext

        metadata = AgentRegistry().resolve("main")
        session_id = self.services.session_id
        client_id = session_id.split(":")[0] if ":" in session_id else ""
        turn = TurnContext(
            turn_id=turn_id,
            agent_metadata=metadata,
            thread_id=thread_id,
            workspace=self.services.workspace,
            model=self.services.model_settings.model,
            provider=self.services.provider,
            temperature=self.services.model_settings.temperature,
            max_tokens=self.services.model_settings.max_tokens,
            client_id=client_id,
            session_id=session_id,
        )
        turn.permission_profile = PermissionProfile(workspace=self.services.workspace)

        # Phase 42 fix: connect to thread cancel event so AbortTurn can cancel exec
        cancel_evt = self._turn_cancel_events.get(thread_id)
        if cancel_evt is not None:
            turn.cancel_event = cancel_evt

        ledger = self.services.ledger_runtime

        try:
            if cmd.standalone:
                self._active_turn_ids[thread_id] = turn_id
                # Ledger: record turn start
                if ledger is not None:
                    await ledger.append_item(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        item_type="turn_started",
                        payload={"agent_name": metadata.name, "source": "userShell"},
                    )
                await self._events.put(TurnStartedEvent(
                    turn_id=turn_id,
                    agent_name=metadata.name,
                    thread_id=thread_id,
                ))

            call = SimpleNamespace(
                id=f"user-shell-{turn_id}",
                name="exec",
                arguments={
                    "command": command,
                    **({"working_dir": cmd.cwd} if cmd.cwd else {}),
                    "_exec_source": "userShell",
                },
            )
            tool_runtime = self.services.tool_runtime
            if tool_runtime is None:
                err_msg = "Runtime has no tool runtime"
                if cmd.standalone:
                    if ledger is not None:
                        await ledger.append_item(
                            thread_id=thread_id,
                            turn_id=turn_id,
                            item_type="error",
                            payload={"recoverable": False, "source": "task_runner"},
                        )
                    await self._events.put(TurnCompleteEvent(
                        turn_id=turn_id,
                        thread_id=thread_id,
                        outcome="error",
                        tools_used=[],
                        token_usage={},
                    ))
                else:
                    await self._events.put(CommandRejectedEvent(
                        command_type="RunUserShellCommand",
                        reason=err_msg,
                        recoverable=False,
                    ))
                return

            ctx = await tool_runtime.execute_one(turn, call)
            if cmd.standalone:
                from miqi.execution.orchestrator import OrchestrationResult
                outcome = (
                    "success"
                    if ctx.status == OrchestrationResult.SUCCESS
                    else "error"
                )
                if ledger is not None:
                    await ledger.append_item(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        item_type="turn_completed",
                        payload={"outcome": outcome, "tools_used": ["exec"]},
                    )
                await self._events.put(TurnCompleteEvent(
                    turn_id=turn_id,
                    thread_id=thread_id,
                    outcome=outcome,
                    tools_used=["exec"],
                    token_usage={},
                ))
        except asyncio.CancelledError:
            if cmd.standalone and ledger is not None:
                await ledger.append_item(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_type="turn_aborted",
                    payload={"reason": "Shell command was cancelled."},
                )
            raise
        except Exception:
            logger.exception("User shell command failed for turn {}", turn_id)
            if cmd.standalone:
                if ledger is not None:
                    await ledger.append_item(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        item_type="error",
                        payload={"recoverable": False, "source": "task_runner"},
                    )
                await self._events.put(TurnCompleteEvent(
                    turn_id=turn_id,
                    thread_id=thread_id,
                    outcome="error",
                    tools_used=["exec"],
                    token_usage={},
                ))
            await self._events.put(ErrorEvent(
                turn_id=turn_id,
                severity=EventSeverity.ERROR,
                message="An internal error occurred while running the shell command.",
                recoverable=False,
            ))
        finally:
            if cmd.standalone:
                self._active_turn_ids.pop(thread_id, None)

    async def _handle_user_message(self, msg: UserMessage) -> None:
        turn_id = msg.turn_id or str(uuid.uuid4())[:12]
        thread_id = msg.thread_id or "cli:default"

        # Phase 14 follow-up: register a cancel event so AbortTurn can
        # signal this specific turn to stop. Reuse existing event if a
        # previous turn on the same thread hasn't been cleaned up yet — but
        # NOT if it's already set, or a fresh user message right after an
        # abort would inherit the previous turn's cancellation and die
        # "before start" (#542).
        cancel_evt = self._turn_cancel_events.get(thread_id)
        if cancel_evt is None or cancel_evt.is_set():
            cancel_evt = asyncio.Event()
            self._turn_cancel_events[thread_id] = cancel_evt

        # Phase 41: register steer queue and active turn id
        steer_queue: asyncio.Queue = asyncio.Queue()
        self._active_turn_ids[thread_id] = turn_id
        self._turn_steer_queues[turn_id] = steer_queue

        # Phase 17: get history runtime for persistence and loading
        history_runtime = self.services.history_runtime
        # Phase 24: get ledger runtime for append-only event recording
        ledger = self.services.ledger_runtime

        # Build TurnContext and run through TurnRunner (Phase 12)
        from miqi.runtime.agent_registry import AgentRegistry
        from miqi.runtime.turn_context import TurnContext

        metadata = AgentRegistry().resolve("main")
        # Phase 31.4: extract client_id from session_id (format: client_id:session_key).
        # This is a best-effort derivation; a dedicated client_id field on
        # RuntimeServices would be a future improvement.
        session_id = self.services.session_id
        client_id = session_id.split(":")[0] if ":" in session_id else ""
        turn = TurnContext(
            turn_id=turn_id,
            agent_metadata=metadata,
            thread_id=thread_id,
            workspace=self.services.workspace,
            model=self.services.model_settings.model,
            provider=self.services.provider,
            execution_policy=msg.mode or "edit",
            reasoning_mode=getattr(msg, "reasoning_mode", None),
            temperature=self.services.model_settings.temperature,
            max_tokens=self.services.model_settings.max_tokens,
            client_id=client_id,
            session_id=session_id,
        )
        # #680: fast caps the generation budget (2048) so answers land in the
        # 30s window; think keeps the full budget (desktop chain).
        if getattr(msg, "reasoning_mode", None) == "fast":
            turn.max_tokens = 2048

        # Phase 13: resolve capabilities and permission profile
        tools: list[dict[str, Any]] = []
        capability_resolver = self.services.capability_resolver
        if capability_resolver is not None:
            capabilities = capability_resolver.resolve(agent_metadata=metadata)
            turn.capabilities = capabilities
            tools = capabilities.tool_definitions
        else:
            tools = self.services.tool_registry.get_definitions()

        # ── Execution Policy ──────────────────────────────────────────
        # Three-layer: system prompt + tool set + approval flags.
        # Mode = Agent role, not permission preset.
        # Plan:   strategist — read-only, proposes approach
        # Manual: collaborator — all tools, each step confirmed by user
        # Edit:   developer  — all tools, safe auto, dangerous ask
        # Auto:   agent      — all tools, bypass approval entirely

        from miqi.runtime.tool_policy import PLAN_BLOCKED_TOOLS

        if turn.execution_policy == "plan":
            tools = [t for t in tools if t.get("name") not in PLAN_BLOCKED_TOOLS]
            # NOTE: Plan mode only exposes read-only tools (write/exec/spawn
            # removed by PLAN_BLOCKED_TOOLS above).  Setting bypass_approval
            # here skips approval prompts for safe read operations — it does
            # NOT grant write/execute permission.  Tool filtering (above) is
            # the security boundary.  The permission engine's deny-list still
            # wins in all modes.
            turn.bypass_approval = True

        if turn.execution_policy == "auto":
            turn.bypass_approval = True
        elif turn.execution_policy == "manual":
            turn.force_approval = True
        # edit: both flags False → normal approval flow

        mode_prompt = _MODE_PROMPTS.get(turn.execution_policy, "")
        effective_system_prompt = mode_prompt + metadata.system_prompt if mode_prompt else metadata.system_prompt
        # #680: reasoning mode (fast/think) — generation budget + prompt.
        # fast = answer-oriented (short tokens, answer-first prompt); think =
        # deep research (full budget, depth prompt).  Applied on the DESKTOP
        # chain (外部审阅 2026-08-24: previously KUN-only, desktop ignored it).
        reasoning_mode = getattr(turn, "reasoning_mode", None) or ""
        if reasoning_mode == "fast":
            from miqi.agent.agent_mode import FAST_PROMPT
            effective_system_prompt += f"\n\n{FAST_PROMPT}"
        elif reasoning_mode == "think":
            from miqi.agent.agent_mode import THINK_PROMPT
            effective_system_prompt += f"\n\n{THINK_PROMPT}"
        # 思考过程（reasoning_content）直接用中文展示，用户要求（#539 UI 反馈）。
        # 结构化思考：分层展开（理解需求→拆解→候选→计划），带编号/圆点列表，
        # 让思考过程像 DeepSeek Chat 一样清晰成规模。
        effective_system_prompt += (
            "\n\n请始终使用中文进行思考和回复。"
            "思考过程必须使用清晰的结构化格式：每个阶段用 1、2、3… 编号，"
            "每个要点用 - 圆点列表展开，不要大段连续文字。参考结构：\n"
            "1. 理解需求：…（要点用圆点列出）\n"
            "2. 拆解问题：…\n"
            "3. 候选方案：…（对比用圆点）\n"
            "4. 执行计划：…（步骤用编号）\n"
            "网络搜索时：优先用 web_search 获取结果列表，仅抓取与问题直接相关的"
            "具体文章页面，不要批量抓取 RSS 聚合源或新闻站点首页。"
        )

        # ask_user_confirm_card usage guidance (issue #646, 功能描述④) —
        # mirrors the KUN loop injection: when the tool is exposed to the
        # model, the prompt must tell it WHEN to call it.
        if any(
            (t.get("function", {}) or {}).get("name") == "ask_user_confirm_card"
            or t.get("name") == "ask_user_confirm_card"
            for t in tools
            if isinstance(t, dict)
        ):
            from miqi.agent.tools.ask_user_confirm import ASK_USER_CONFIRM_INSTRUCTION

            effective_system_prompt += "\n\n" + ASK_USER_CONFIRM_INSTRUCTION

        # 回答可视化与引用标注（issue #671）— 技术方案/流程/对比类回答
        # 追加 Mermaid 流程图与参考文献（前端 MarkdownContent 渲染 mermaid）
        from miqi.agent.answer_style import VISUAL_ANSWER_INSTRUCTION

        effective_system_prompt += "\n\n" + VISUAL_ANSWER_INSTRUCTION

        # ── Inject session workspace into the prompt ─────────────────────
        # The AI must know its working directory without needing `pwd`.
        # Inside a bwrap/WSL sandbox `pwd` returns the fixed sandbox path
        # (/home/miqi/workspace), which hides the user's chosen project
        # directory. State it explicitly so the AI reports the real
        # workspace (mirrors agent_control's subagent prompt).
        # The exec environment sentence reflects the LIVE sandbox state —
        # with the sandbox off, exec runs directly on the host and the AI
        # must not be told the WSL /mnt/c story.
        _ws = self.services.workspace
        if _ws is not None:
            from miqi.sandbox.manager import describe_exec_environment

            _exec_env = describe_exec_environment(
                getattr(self.services, "sandbox_manager", None),
                workspace=_ws,
            )
            effective_system_prompt = (
                effective_system_prompt
                + f"\n\n## 工作目录\n"
                f"你当前的工作目录是: {_ws}\n"
                f"文件工具（read_file / write_file / list_dir）在这个目录下进行。\n"
                f"注意：{_exec_env}\n"
                f"当用户问你工作目录时，请直接回答 {_ws}，不要说 /home/miqi/workspace。\n"
            )

        # ── Local skills injection (skills 精确调用评估的修复) ─────────
        # agent_registry 主提示词规则 7 要求 agent 先查 "Local Skills" 列表，
        # 但该列表此前从未注入，导致 agent 唯一的发现途径是 skill_manage(list)
        # （量化评估: 14 条直接提示词 11 条零技能接触）。这里按渐进披露注入
        # Layer 1（名称+描述+位置），技能正文仍由 agent 按需加载。
        if _ws is not None:
            try:
                from miqi.agent.skills import SkillsLoader

                _skills_summary = SkillsLoader(_ws).build_skills_summary(
                    description_max_chars=160,
                )
            except Exception:
                _skills_summary = None
            if _skills_summary:
                # 信息层面，强制先 skill_manage(list) 与下方注入的清单是冗余的：
                # <skills> 已含全部技能名称+描述+位置，list 返回同一份内容。
                # 仍强制先 list 是行为机制而非信息需求——评估（14 条语料）显示，
                # 仅注入清单时模型常直接跳过清单调内置工具（召回率 78.6%）；
                # 强制先 list 后模型必须逐条处理技能目录（注意力锚定 + 结果近因 +
                # 动作承诺），召回率 92.9%。代价是每回合多一次工具往返 +
                # list 输出 ~8-10K tokens 再次进入上下文。若未来要省这笔成本，
                # 可改为 TaskRunner 确定性预匹配：命中即自动注入技能全文，
                # 不再依赖模型自觉。
                effective_system_prompt += (
                    "\n\n## 本地技能清单（Local Skills）\n"
                    "以下技能是完成任务的标准流程（渐进披露第一层，只预载名称、描述和位置）。\n"
                    "【强制规则】处理用户请求时（纯寒暄/聊天除外），第一步必须先调用 "
                    "`skill_manage(action='list')` 获取全部可用技能及其描述；当请求与某个技能的 "
                    "description 匹配时，接着用 `skill_manage(action='view', name=<技能名>)`"
                    "（或 read_file 读取 <location> 指向的 SKILL.md）加载该技能全文，然后严格按其"
                    "说明执行——即使存在看似等价的内置工具（如 create_pptx / create_docx / "
                    "create_xlsx / create_pdf），也要优先走技能，技能正文会指明用哪个工具执行。\n"
                    "典型映射：做PPT→pptx-generator；写Word文档/周报→docx；做表格/Excel→xlsx；"
                    "生成PDF→pdf；查天气→weather；定时提醒→cron；搜/读论文→paper-research；"
                    "GitHub操作→github；总结要点→summarize；整理工作区→workspace-cleanup；"
                    "创建新技能→skill-creator。\n"
                    "`available=\"false\"` 的技能缺少依赖，需要先安装依赖。\n\n"
                    f"{_skills_summary}"
                )
                logger.info(
                    "skills injection: {} chars into system prompt (turn {})",
                    len(_skills_summary), turn_id,
                )

        # ── Search-first strategy (DeepSeek Flash style, #639) ─────────
        # 用户要求：搜索资料比模型自身知识更重要——回答前默认先搜索；
        # 思考中记录搜索动作，让用户看到搜索轨迹。
        effective_system_prompt += (
            "\n\n## 搜索优先\n"
            "回答用户问题前，默认先使用 web_search 搜索相关关键词（除非是纯逻辑、"
            "常识或模型内部确定的内容）。引用事实、时事、数据时必须以搜索结果为依据。"
            "搜索时在思考过程中记录动作，例如「搜索到 X 个结果」「浏览 Y 个页面」，"
            "让用户看到你的搜索轨迹。"
        )

        # ── Slash command injection (KWP / Cowork convention) ───────────
        # Detect /-prefixed user input, look up the command body in the
        # active plugins, and append it to the system prompt for this turn.
        # The user-visible content is stripped of the /cmd prefix.
        slash_content: str | None = None
        if msg.content and msg.content.startswith("/"):
            pm = self.services.plugin_manager
            if pm is not None and hasattr(pm, "get_slash_command"):
                parts = msg.content[1:].split(None, 1)
                # Allow namespacing: "/product-management:brainstorm"
                raw_name = parts[0] if parts else ""
                if ":" in raw_name:
                    raw_name = raw_name.split(":", 1)[1]
                cmd_name = raw_name.strip().lower()
                cmd_args = parts[1] if len(parts) > 1 else ""
                match = pm.get_slash_command(cmd_name)
                if match and match.get("status") == "active" and match.get("body"):
                    slash_content = match["body"]
                    if cmd_args:
                        slash_content += "\n\n## User arguments\n\n" + cmd_args
                    # Strip /cmd from the user-visible content.
                    msg = dataclasses.replace(
                        msg,
                        content=cmd_args if cmd_args else "(command invoked without arguments)",
                    )
                    logger.info(
                        "slash command invoked: /{} from plugin {}",
                        cmd_name,
                        match.get("plugin", "?"),
                    )
        if slash_content:
            effective_system_prompt = (
                effective_system_prompt
                + "\n\n## Active Slash Command\n\n"
                + slash_content
            )

        # ── #740: resume context — continue an interrupted turn ──────
        # Replan (not replay): feed the snapshot's half-generated content to
        # the model as context and instruct it to continue from where it
        # stopped, rather than restoring raw messages.
        resume_turn_id = getattr(msg, "resume_turn_id", None)
        resume_snapshot: dict[str, Any] | None = None
        if resume_turn_id and history_runtime is not None:
            try:
                resume_snapshot = await history_runtime.get_snapshot(resume_turn_id)
            except Exception as exc:
                logger.warning("resume: snapshot lookup failed for {}: {}", resume_turn_id, exc)
            # Scope/state validation: only resume a snapshot that belongs to
            # THIS thread and is in a recoverable state — otherwise a request
            # could inject another thread's partial response into this turn.
            if resume_snapshot and (
                resume_snapshot.get("thread_id") != thread_id
                or resume_snapshot.get("status") not in ("running", "interrupted")
            ):
                logger.warning(
                    "resume: snapshot {} rejected (thread={} status={})",
                    resume_turn_id, resume_snapshot.get("thread_id"), resume_snapshot.get("status"),
                )
                resume_snapshot = None
                resume_turn_id = None
            if resume_snapshot and (
                resume_snapshot.get("assistant_content") or resume_snapshot.get("reasoning_content")
            ):
                _half = resume_snapshot["assistant_content"][-2000:]
                _half_think = resume_snapshot["reasoning_content"][-800:]
                effective_system_prompt += (
                    "\n\n## 任务恢复（Resume）\n"
                    "你正在继续一个被中断的任务。以下是你上次已生成的内容，"
                    "请从上次中断处继续完成，不要重复已生成的部分。\n\n"
                    f"已生成回答（末尾部分）:\n{_half or '（尚未生成正文）'}\n\n"
                    + (f"上次思考过程（末尾部分）:\n{_half_think}\n\n" if _half_think else "")
                    + "请继续完成该任务。"
                )
                logger.info("resume: turn {} injected snapshot context", resume_turn_id)
            else:
                logger.warning("resume: no usable snapshot for {}", resume_turn_id)
                resume_turn_id = None

        # ── End Execution Policy ─────────────────────────────────────

        # Phase 13: attach permission profile for orchestrator
        from miqi.runtime.permission_profile import PermissionProfile
        turn.permission_profile = PermissionProfile(
            workspace=self.services.workspace,
        )

        try:
            # Phase 17: load history and start turn tracking
            if history_runtime is not None:
                await history_runtime.start_turn(turn_id, thread_id=thread_id)
                history = await history_runtime.load_messages(thread_id)
            else:
                history = []

            # Phase 24: record turn start in ledger
            if ledger is not None:
                await ledger.append_item(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_type="turn_started",
                    payload={"agent_name": metadata.name},
                )

            # Phase 19: auto-compact before turn if history exceeds budget
            ctx_runtime = self.services.context_runtime
            auto_limit = self.services.model_settings.context_limit_chars
            if history_runtime is not None and ctx_runtime is not None and auto_limit:
                token_limit = max(1, int(int(auto_limit) / 2.5))
                if ctx_runtime.should_auto_compact(history, token_limit):
                    try:
                        compact_result = await ctx_runtime.compact_thread(
                            history_runtime=history_runtime,
                            thread_id=thread_id,
                            turn_id=f"compact-{turn_id}",
                            model=turn.model,
                        )
                        await self._events.put(ContextCompactedEvent(
                            turn_id=turn_id,
                            thread_id=thread_id,
                            messages_before=compact_result.messages_before,
                            messages_after=compact_result.messages_after,
                            tokens_saved=compact_result.tokens_saved,
                        ))
                        # Reload compacted history for the turn
                        history = await history_runtime.load_messages(thread_id)
                    except Exception as compact_exc:
                        # Compaction failed — log and emit recoverable
                        # ErrorEvent, then proceed with unbounded history.
                        logger.exception(
                            "Auto-compact failed for thread {}: {}",
                            thread_id, compact_exc,
                        )
                        await self._events.put(ErrorEvent(
                            turn_id=turn_id,
                            severity=EventSeverity.WARNING,
                            message=(
                                f"Context compaction failed for thread "
                                f"{thread_id}: {compact_exc}"
                            ),
                            recoverable=True,
                        ))

            # Emit TurnStartedEvent
            await self._events.put(TurnStartedEvent(
                turn_id=turn_id,
                agent_name=metadata.name,
                thread_id=thread_id,
            ))

            # Persist the user message
            # #740: resume turns carry no real user input (content is a
            # placeholder) — skip persisting it so history stays clean.
            if not resume_turn_id:
                payload_fields: dict[str, Any] = {}
                if msg.input_items:
                    payload_fields["input_items"] = msg.input_items
                if msg.client_user_message_id:
                    payload_fields["client_user_message_id"] = msg.client_user_message_id
                # Issue #402: write JSONL FIRST so sessions.get (which reads
                # JSONL) sees the message even if a crash occurs before the
                # SQLite write completes.  The JSONL store is the legacy
                # single-source-of-truth for session overview; SQLite is
                # thread-scoped and recoverable from JSONL if needed.
                await self._save_to_session_manager(
                    role="user", content=msg.content)
                if history_runtime is not None:
                    await history_runtime.append_message(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        role="user",
                        content=msg.content,
                        payload={"message_fields": payload_fields},
                    )
                # Phase 24: record user message in ledger
                if ledger is not None:
                    await ledger.append_item(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        item_type="message",
                        role="user",
                        content=msg.content,
                        payload={"message_fields": payload_fields},
                    )

            # Check for abort before starting turn
            if cancel_evt.is_set():
                if history_runtime is not None:
                    await history_runtime.complete_turn(
                        turn_id,
                        status="aborted",
                        tools_used=[],
                        token_usage={},
                    )
                if ledger is not None:
                    await ledger.append_item(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        item_type="turn_aborted",
                        payload={"reason": "Turn aborted before start."},
                    )
                await self._events.put(TurnAbortedEvent(
                    turn_id=turn_id,
                    thread_id=thread_id,
                    reason="Turn aborted before start.",
                ))
                return

            # Publish the turn identity for the user-input resolver: the
            # model's tool args carry no thread/turn ids, and without them
            # remember scoping + turn cancellation silently break
            # (issue #646 / CodeRabbit #711).
            from miqi.agent.user_input_resolver import (
                clear_thread_context,
                set_thread_context,
            )

            set_thread_context(thread_id, turn_id)
            try:
                result = await self.services.turn_runner.run(
                    turn=turn,
                    user_content=msg.content,
                    system_prompt=effective_system_prompt,
                    tools=tools,
                    history=history,
                    cancel_event=cancel_evt,
                    steer_queue=steer_queue,
                )
            finally:
                clear_thread_context()

            # Persist assistant messages to all stores in a single pass.
            # Build the extra-fields mapping once per message so every
            # persistence destination receives the same metadata.
            for message in result.messages_delta:
                role = message["role"]
                content = message.get("content") or ""
                extra_fields = {k: v for k, v in message.items() if k not in ("role", "content")}

                # Issue #402: write JSONL FIRST so sessions.get sees the
                # message even if SQLite write fails later.  Forward all
                # extra fields (tool_calls, name, tool_call_id, etc.) so
                # the frontend can reconstruct file operations.
                await self._save_to_session_manager(
                    role=role, content=content, **extra_fields,
                )

                if history_runtime is not None:
                    await history_runtime.append_message(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        role=role,
                        content=content,
                        payload={"message_fields": extra_fields},
                    )

                if ledger is not None:
                    await ledger.append_item(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        item_type="message",
                        role=role,
                        content=content,
                        payload={"message_fields": extra_fields},
                    )

            if history_runtime is not None:
                await history_runtime.complete_turn(
                    turn_id,
                    status="completed",
                    tools_used=result.tools_used,
                    token_usage=result.token_usage,
                )
            # #740: resume succeeded — the old interrupted turn's snapshot is
            # no longer needed (its content is now part of this turn's reply).
            if resume_turn_id and history_runtime is not None:
                await history_runtime.delete_snapshot(resume_turn_id)
            # Phase 24: complete turn in ledger
            if ledger is not None:
                await ledger.append_item(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_type="turn_completed",
                    payload={
                        "final_content": result.final_content,
                        "token_usage": result.token_usage,
                    },
                )

            tool_calls: list[dict[str, Any]] = []
            for message in result.messages_delta:
                if message.get("role") == "assistant":
                    tool_calls.extend(message.get("tool_calls") or [])

            await self._events.put(AgentMessageEvent(
                turn_id=turn_id,
                content=result.final_content or "",
                finish_reason="stop",
                tool_calls=tool_calls,
                reasoning=result.reasoning,
                reasoning_elapsed_s=result.reasoning_elapsed_s,
            ))
            await self._events.put(TurnCompleteEvent(
                turn_id=turn_id,
                thread_id=thread_id,
                outcome="success",
                tools_used=result.tools_used,
                token_usage=result.token_usage,
            ))
        except asyncio.CancelledError:
            if history_runtime is not None:
                await history_runtime.complete_turn(
                    turn_id,
                    status="aborted",
                    tools_used=[],
                    token_usage={},
                )
            if ledger is not None:
                await ledger.append_item(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_type="turn_aborted",
                    payload={"reason": "Turn was cancelled."},
                )
            await self._events.put(TurnAbortedEvent(
                turn_id=turn_id,
                thread_id=thread_id,
                reason="Turn was cancelled.",
            ))
            raise
        except Exception as exc:
            # Phase 57: a ProviderError carries a classified error_kind from
            # the provider (rate_limit/auth/context_length/...). Surface the
            # category + recoverability and, for user-actionable kinds, the
            # provider's own message.
            #
            # Issue #529: a raw SDK exception that escaped retry (e.g. via a
            # streaming path that raised instead of yielding a terminal
            # finish_reason="error" event) is NOT a ProviderError, so the old
            # `prov_err = exc if isinstance(exc, ProviderError) else None`
            # collapsed it to error_kind=None / recoverable=False and showed
            # the generic message even for TRANSIENT (503/overload) failures.
            # Re-classify at this final boundary so the category + a useful
            # message survive — the metadata leak is fixed without changing
            # with_retry's / providers' exception contract. Re-wrap into a
            # ProviderError view so the mapping below stays uniform.
            from miqi.providers.resilience import ErrorKind, ProviderError
            if isinstance(exc, ProviderError):
                prov_err = exc
            else:
                prov_err = ProviderError(kind=_classify_chain(exc), message=str(exc))
            error_kind = prov_err.kind.value
            recoverable = prov_err.recoverable
            if history_runtime is not None:
                await history_runtime.complete_turn(
                    turn_id,
                    status="error",
                    tools_used=[],
                    token_usage={},
                )
            if ledger is not None:
                await ledger.append_item(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_type="error",
                    payload={
                        "recoverable": recoverable,
                        "source": "task_runner",
                        "error_kind": error_kind,
                    },
                )
            # Log full details server-side, send sanitized message to client.
            # User-actionable kinds (rate_limit/auth/context_length/
            # invalid_request) are safe + actionable, so surface the provider
            # message; everything else (transient/fatal/unknown) keeps the
            # generic message to avoid leaking internal details.
            #
            # Issue #529: TRANSIENT (503/overload/conn-reset retries exhausted)
            # is recoverable but the raw SDK text may leak URLs/paths/tokens,
            # so surface a fixed, non-leaking message (same posture as AUTH)
            # rather than prov_err.message. The wording avoids sanitizeUiMessage
            # replacement keywords ("connection error"/"timed out") so it
            # reaches the UI verbatim.
            logger.error("Agent processing error in turn {}: {}", turn_id, exc, exc_info=True)
            user_message = "处理消息时发生内部错误，请重试。"
            if prov_err.kind is ErrorKind.AUTH:
                # AUTH is sensitive — surface a fixed, non-leaking message
                # instead of the raw provider exception text (Plan 58.2).
                user_message = "模型服务认证失败，请检查 Provider 的 API Key、API Base 或当前模型配置。"
            elif prov_err.kind is ErrorKind.PAYMENT_REQUIRED:
                # Issue #528: 402 / balance / quota exhausted — account
                # status, not auth. Fixed non-leaking billing hint
                # (never the raw text), recoverable=False.
                user_message = "模型服务账户余额不足或额度已用尽，请充值或检查账户额度后重试。"
            elif prov_err.kind is ErrorKind.TRANSIENT:
                user_message = "模型服务暂时不可用或过载，请稍后重试。"
            elif prov_err.kind in (
                ErrorKind.RATE_LIMIT,
                ErrorKind.CONTEXT_LENGTH,
                ErrorKind.INVALID_REQUEST,
            ):
                user_message = prov_err.message or user_message
            await self._events.put(ErrorEvent(
                turn_id=turn_id,
                severity=EventSeverity.ERROR,
                message=user_message,
                recoverable=recoverable,
                error_kind=error_kind,
            ))
            await self._events.put(TurnCompleteEvent(
                turn_id=turn_id,
                thread_id=thread_id,
                outcome="error",
                tools_used=[],
                token_usage={},
            ))
        finally:
            # Only clear entries this turn still owns — a concurrent turn
            # on the same thread may have reused the cancel event and
            # overwritten the active turn id (PR #58 fix).
            if self._turn_cancel_events.get(thread_id) is cancel_evt:
                self._turn_cancel_events.pop(thread_id, None)
            if self._active_turn_ids.get(thread_id) == turn_id:
                self._active_turn_ids.pop(thread_id, None)
            self._turn_steer_queues.pop(turn_id, None)

    async def _handle_thread_command(self, cmd: ThreadCommand) -> None:
        """Phase 18: dispatch thread lifecycle actions to ThreadRuntime.

        All failure paths emit CommandRejectedEvent — no exceptions
        escape to the caller.
        """
        from miqi.protocol.events import (
            ThreadCreatedEvent,
            ThreadDeletedEvent,
            ThreadUpdatedEvent,
        )

        threads = self.services.thread_runtime
        if threads is None:
            await self._events.put(CommandRejectedEvent(
                command_type="ThreadCommand",
                reason="Runtime has no thread manager",
                recoverable=False,
            ))
            return

        if cmd.action == "new":
            try:
                thread = await threads.create_thread(
                    title=cmd.params.get("title", "New thread"),
                    thread_id=cmd.params.get("thread_id"),
                )
            except (KeyError, ValueError, AttributeError, TypeError) as exc:
                await self._events.put(CommandRejectedEvent(
                    command_type="ThreadCommand",
                    reason=str(exc),
                    recoverable=False,
                ))
                return
            await self._events.put(ThreadCreatedEvent(
                thread_id=thread.thread_id,
                title=thread.title,
                parent_thread_id=thread.parent_thread_id,
            ))
            return

        if cmd.action == "rename":
            if "title" not in cmd.params or not cmd.params["title"]:
                await self._events.put(CommandRejectedEvent(
                    command_type="ThreadCommand",
                    reason="rename requires a non-empty 'title' in params",
                    recoverable=False,
                ))
                return
            try:
                thread = await threads.rename_thread(cmd.thread_id, cmd.params["title"])
            except (KeyError, ValueError, AttributeError, TypeError) as exc:
                await self._events.put(CommandRejectedEvent(
                    command_type="ThreadCommand",
                    reason=str(exc),
                    recoverable=False,
                ))
                return
            await self._events.put(ThreadUpdatedEvent(
                thread_id=thread.thread_id,
                title=thread.title,
                status=thread.status,
            ))
            return

        if cmd.action == "archive":
            try:
                thread = await threads.archive_thread(cmd.thread_id)
            except (KeyError, ValueError, AttributeError, TypeError) as exc:
                await self._events.put(CommandRejectedEvent(
                    command_type="ThreadCommand",
                    reason=str(exc),
                    recoverable=False,
                ))
                return
            await self._events.put(ThreadUpdatedEvent(
                thread_id=thread.thread_id,
                title=thread.title,
                status=thread.status,
            ))
            return

        if cmd.action == "delete":
            try:
                await threads.delete_thread(cmd.thread_id)
            except (KeyError, ValueError, AttributeError, TypeError) as exc:
                await self._events.put(CommandRejectedEvent(
                    command_type="ThreadCommand",
                    reason=str(exc),
                    recoverable=False,
                ))
                return
            await self._events.put(ThreadDeletedEvent(thread_id=cmd.thread_id))
            return

        if cmd.action == "fork":
            try:
                thread = await threads.fork_thread(
                    cmd.thread_id,
                    title=cmd.params.get("title", "Forked thread"),
                )
            except (KeyError, ValueError, AttributeError, TypeError) as exc:
                await self._events.put(CommandRejectedEvent(
                    command_type="ThreadCommand",
                    reason=str(exc),
                    recoverable=False,
                ))
                return
            await self._events.put(ThreadCreatedEvent(
                thread_id=thread.thread_id,
                title=thread.title,
                parent_thread_id=thread.parent_thread_id,
            ))
            return

        await self._events.put(CommandRejectedEvent(
            command_type="ThreadCommand",
            reason=f"Unknown thread action: {cmd.action}",
            recoverable=False,
        ))
