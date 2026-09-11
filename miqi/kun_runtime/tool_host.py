"""Tool host adapter for KUN runtime.

Wraps MiQi ``ToolRegistry`` to expose KUN ``ToolHost`` semantics:
- ``listTools(context)`` → list of ``dict`` (KUN ModelToolSpec)
- ``execute(call, context, onProgress)`` → ``dict`` (ToolHostResult)

Supports concurrency classification (parallel-safe / path-scoped / never-parallel)
and approval gating via the ToolHostContext.

Aligns with KUN ``ports/tool-host.ts`` and ``adapters/tool/local-tool-host.ts``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from loguru import logger

from miqi.agent.tools.registry import ToolRegistry

# ═══════════════════════════════════════════════════════════════════════════════
# Types
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ToolCallLike:
    """A tool call as received from the model, ready for dispatch."""
    call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    tool_kind: str | None = None
    provider_id: str | None = None


@dataclass
class ToolHostContext:
    """Context passed to the tool host when executing tool calls."""
    thread_id: str
    turn_id: str
    workspace: str
    thread_mode: str | None = None
    approval_policy: str = "auto"
    autonomy_mode: str = "supervised"  # collab gate: plan/manual/supervised/autonomous
    abort_signal: Any = None  # CancellationToken
    active_skill_ids: list[str] = field(default_factory=list)
    allowed_tool_names: list[str] | None = None
    memory_policy: dict[str, Any] = field(default_factory=dict)
    delegation_policy: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)

    # Callbacks
    await_approval: Callable[[dict[str, Any]], Coroutine[Any, Any, str]] | None = None
    await_user_input: Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]] | None = None

    # Reasoning mode (issue #680): fast = Answer-oriented, parallel search.
    mode: str | None = None
    search_strategy: Any = None
    parallel_limit: int | None = None

    # Directories the user mentioned in their message this turn (issue #821).
    # The agent loop extracts them from user_message items; the tool host
    # injects them as ``_user_roots`` so file tools can read/write the
    # user-requested output dirs (e.g. Desktop/test_result) without static
    # tools.extra_roots config.
    user_mentioned_roots: list[str] = field(default_factory=list)


@dataclass
class ToolHostResult:
    """Result of executing a tool call."""
    item: dict[str, Any]


# ═══════════════════════════════════════════════════════════════════════════════
# Parallel-safe classification (matching MiQi ToolRegistry + KUN)
# ═══════════════════════════════════════════════════════════════════════════════

_PARALLEL_SAFE_NAMES = frozenset({"read", "grep", "find", "ls", "list_dir", "read_file", "web_search", "web_fetch", "paper_search", "paper_get"})
_NEVER_PARALLEL_NAMES = frozenset({"exec", "bash", "message", "spawn", "cron", "write", "edit", "delete", "move", "apply_patch", "edit_diff"})
_MAX_PARALLEL_TOOL_CALLS = 3

# AI-initiated user confirmation (issue #646): blocking human-in-the-loop tool
ASK_USER_CONFIRM_TOOL = "ask_user_confirm_card"

# Tools that receive the injected ``_session_key`` (mirrors the legacy
# ToolOrchestrator._execute_in_sandbox set).  Session isolation
# (sessions/<key>/files) can only engage when the tool knows which session
# is executing — the KUN tool host is the single execution point here, so it
# must do the same injection or file writes land in the shared root.
# graph_render is included for parity with the legacy orchestrator
# (CodeRabbit #761) — it writes svg/html artifacts and reads source JSON.
_SESSION_KEY_TOOLS = frozenset({
    "exec",
    "write_file", "edit_file", "delete_file", "apply_patch",
    "read_file", "list_dir",
    "docx_write", "pptx_write", "xlsx_write",
    "create_docx", "create_pptx", "create_xlsx",
    "create_pdf", "pdf_write", "pdf_read",
    "edit_docx", "append_xlsx",
    "paper_download",
    "graph_render",
})

# File tools that accept the injected ``_user_roots`` — directories the
# user mentioned in their message (issue #821), auto-sensed by the agent
# loop.  Only file-ish tools consume them; injecting into every tool would
# be harmless but pointless.
_USER_ROOTS_TOOLS = frozenset({
    "write_file", "edit_file", "read_file", "list_dir",
    "apply_patch", "graph_render",
})


# ═══════════════════════════════════════════════════════════════════════════════
# MiQiToolHost
# ═══════════════════════════════════════════════════════════════════════════════


class MiQiToolHost:
    """KUN ToolHost backed by MiQi ``ToolRegistry``.

    Delegates tool listing and execution to the registry while providing
    KUN-compatible return types.
    """

    def __init__(self, registry: ToolRegistry, read_tracker: bool = False):
        self._registry = registry
        self._read_tracker: dict[str, set[str]] = {} if read_tracker else None

    async def list_tools(self, context: ToolHostContext | None = None) -> list[dict[str, Any]]:
        """Return tool specs in KUN ``ModelToolSpec`` format.

        If *context* has ``allowed_tool_names``, only those tools are returned.
        """
        definitions = self._registry.get_definitions()
        result: list[dict[str, Any]] = []

        for defn in definitions:
            fn = defn.get("function", defn) if isinstance(defn, dict) else {}
            name = fn.get("name", "")
            if context and context.allowed_tool_names is not None:
                if name not in context.allowed_tool_names:
                    continue
            result.append({
                "name": name,
                "description": fn.get("description", ""),
                "inputSchema": fn.get("parameters", {}),
                "toolKind": _classify_tool_kind(name),
                "providerId": "builtin",
                "providerKind": "built-in",
            })
        return result

    async def execute(
        self,
        call: ToolCallLike,
        context: ToolHostContext,
        on_progress: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None = None,
    ) -> ToolHostResult:
        """Execute a single tool call and return a KUN ToolResultItem.

        If *context* has ``await_approval`` and the tool requires approval,
        the approval gate is invoked before execution.
        """
        tool_name = call.tool_name

        # Enforce allowed-tool-names restriction
        if context.allowed_tool_names is not None and tool_name not in context.allowed_tool_names:
            return ToolHostResult(item={
                "kind": "tool_result",
                "id": f"item_{context.turn_id}_{call.call_id}",
                "turnId": context.turn_id,
                "threadId": context.thread_id,
                "role": "tool",
                "status": "failed",
                "createdAt": _now_iso(),
                "toolName": tool_name,
                "callId": call.call_id,
                "toolKind": _classify_tool_kind(tool_name),
                "output": f"Tool '{tool_name}' is not allowed in this context",
                "isError": True,
            })

        # Check if tool exists
        if not self._registry.has(tool_name):
            return ToolHostResult(item={
                "kind": "tool_result",
                "id": f"item_{context.turn_id}_{call.call_id}",
                "turnId": context.turn_id,
                "threadId": context.thread_id,
                "role": "tool",
                "status": "failed",
                "createdAt": _now_iso(),
                "toolName": tool_name,
                "callId": call.call_id,
                "toolKind": _classify_tool_kind(tool_name),
                "output": f"Tool '{tool_name}' not found",
                "isError": True,
            })

        # Parse arguments
        args = call.arguments
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                args = {}

        if context.await_approval is not None and _requires_approval(tool_name, context.approval_policy):
            decision = await context.await_approval({
                "threadId": context.thread_id,
                "turnId": context.turn_id,
                "toolName": tool_name,
                "toolKind": _classify_tool_kind(tool_name),
                "summary": f"Run tool '{tool_name}'",
                "arguments": args,
            })
            if decision != "allow":
                return ToolHostResult(item={
                    "kind": "tool_result",
                    "id": f"item_{context.turn_id}_{call.call_id}",
                    "turnId": context.turn_id,
                    "threadId": context.thread_id,
                    "role": "tool",
                    "status": "failed",
                    "createdAt": _now_iso(),
                    "finishedAt": _now_iso(),
                    "toolName": tool_name,
                    "callId": call.call_id,
                    "toolKind": _classify_tool_kind(tool_name),
                    "output": f"Tool '{tool_name}' was denied by approval policy",
                    "isError": True,
                })

        # Collaboration gate (issue #646, design v2): the harness — not the
        # model — decides when a card is required. ask_user_confirm_card itself
        # is exempt (it IS the card). External transfers / payments confirm in
        # every autonomy mode; writes/exec confirm per the mode matrix.
        from miqi.execution.collab_policy import (
            AutonomyMode,
            CollabVerdict,
        )
        from miqi.execution.collab_policy import (
            evaluate as collab_evaluate,
        )

        if tool_name != ASK_USER_CONFIRM_TOOL:
            try:
                collab_verdict = collab_evaluate(tool_name, AutonomyMode(context.autonomy_mode))
            except (ValueError, KeyError):
                # Unparsable mode → evaluate under the most conservative mode
                # instead of defaulting to ALLOW (CodeRabbit #711).
                collab_verdict = collab_evaluate(tool_name, AutonomyMode.MANUAL)
            if collab_verdict == CollabVerdict.DENY:
                # DENY blocks in every context — including headless runs with
                # no user-input channel (CodeRabbit #711).
                return ToolHostResult(item={
                    "kind": "tool_result",
                    "id": f"item_{context.turn_id}_{call.call_id}",
                    "turnId": context.turn_id,
                    "threadId": context.thread_id,
                    "role": "tool",
                    "status": "failed",
                    "createdAt": _now_iso(),
                    "finishedAt": _now_iso(),
                    "toolName": tool_name,
                    "callId": call.call_id,
                    "toolKind": _classify_tool_kind(tool_name),
                    "output": f"Tool '{tool_name}' is blocked in {context.autonomy_mode} mode",
                    "isError": True,
                })
            if (
                collab_verdict == CollabVerdict.CONFIRM
                and context.await_user_input is not None
                # Reasoning mode (issue #680): fast = 信息型操作直接执行，
                # 不弹确认卡（用户：极速模式完全不可能快）。权限型仍由
                # ExecutionPolicy/approval 门控制。
                and context.mode != "fast"
            ):
                gate_result = await context.await_user_input({
                    "threadId": context.thread_id,
                    "turnId": context.turn_id,
                    "toolName": tool_name,
                    "title": f"确认执行：{tool_name}",
                    "message": f"该操作需要你确认后才会执行（当前模式：{context.autonomy_mode}）。",
                    "choices": [
                        {"id": "confirm", "label": "确认执行"},
                        {"id": "cancel", "label": "取消"},
                    ],
                    "timeout_seconds": 120,
                })
                answers = gate_result.get("answers") or {}
                if gate_result.get("status") != "submitted" or answers.get("choice_id") != "confirm":
                    return ToolHostResult(item={
                        "kind": "tool_result",
                        "id": f"item_{context.turn_id}_{call.call_id}",
                        "turnId": context.turn_id,
                        "threadId": context.thread_id,
                        "role": "tool",
                        "status": "cancelled",
                        "createdAt": _now_iso(),
                        "finishedAt": _now_iso(),
                        "toolName": tool_name,
                        "callId": call.call_id,
                        "toolKind": _classify_tool_kind(tool_name),
                        "output": "User cancelled the operation (policy confirmation).",
                        "isError": True,
                    })
            # CONFIRM without a wired channel (headless/CLI): fall through to
            # normal execution — the safety approval layer still backstops
            # dangerous commands.

        # AI-initiated user confirmation (issue #646): ask_user_confirm_card
        # is a blocking human-in-the-loop tool. When the user-input channel is
        # wired (KUN runtime), route through await_user_input so the desktop
        # renders an inline confirm card and the turn pauses for the choice.
        if (
            tool_name == ASK_USER_CONFIRM_TOOL
            and context.await_user_input is not None
        ):
            return await self._execute_user_confirm(call, context, args)

        # Execute
        try:
            # Session isolation: inject the session key for file/exec tools so
            # per-session workspace isolation engages on the KUN runtime (the
            # legacy orchestrator does the same via _execute_in_sandbox).
            # thread_id → session_key mapping when registered (gateway flows),
            # otherwise the thread id itself is the session key.
            extra: dict[str, Any] = {}
            if tool_name in _SESSION_KEY_TOOLS:
                from miqi.kun_runtime.migration_adapter import thread_id_to_session_key

                session_key = thread_id_to_session_key(context.thread_id) or context.thread_id
                if session_key:
                    extra["_session_key"] = session_key
            if tool_name.startswith("mcp_"):
                # MCP 工具（issue #927）：注入会话上下文供 slurm 计费事件
                # 使用（MCPToolWrapper 会 pop 掉，不传给 MCP 服务端）。
                from miqi.kun_runtime.migration_adapter import (
                    thread_id_to_session_key,
                )

                extra["_session_key"] = (
                    thread_id_to_session_key(context.thread_id)
                    or context.thread_id
                )
                extra["_turn_id"] = context.turn_id
                extra["_tool_call_id"] = call.call_id
            # User-mentioned output dirs (issue #821): pass the turn's
            # auto-sensed roots to file tools so the user's explicitly
            # requested output location (e.g. Desktop/test_result) works
            # without static tools.extra_roots config.
            if tool_name in _USER_ROOTS_TOOLS and context.user_mentioned_roots:
                extra["_user_roots"] = list(context.user_mentioned_roots)
            # Reasoning mode (issue #680): hand the fast-mode search strategy
            # to web_search so it can fan out (parallel queries + fetches).
            if tool_name == "web_search" and context.search_strategy is not None:
                extra["_search_strategy"] = context.search_strategy
                extra["_mode"] = context.mode or ""
            result = await self._registry.execute(tool_name, args, **extra)
            is_error = isinstance(result, str) and result.startswith("Error")
        except asyncio.TimeoutError:
            result = f"Tool '{tool_name}' timed out"
            is_error = True
        except Exception as exc:
            logger.exception(f"Tool '{tool_name}' execution failed")
            result = f"Error executing {tool_name}: {exc}"
            is_error = True

        return ToolHostResult(item={
            "kind": "tool_result",
            "id": f"item_{context.turn_id}_{call.call_id}",
            "turnId": context.turn_id,
            "threadId": context.thread_id,
            "role": "tool",
            "status": "failed" if is_error else "completed",
            "createdAt": _now_iso(),
            "finishedAt": _now_iso(),
            "toolName": tool_name,
            "callId": call.call_id,
            "toolKind": _classify_tool_kind(tool_name),
            "output": result,
            "isError": is_error,
        })

    async def _execute_user_confirm(
        self,
        call: ToolCallLike,
        context: ToolHostContext,
        args: dict[str, Any],
    ) -> ToolHostResult:
        """Execute the blocking ask_user_confirm_card tool via the user-input gate.

        The turn pauses until the user picks a choice, times out, or the turn
        is cancelled. Returns the structured decision as a tool result so the
        model can continue / abort / re-plan.
        """
        from miqi.agent.tools.ask_user_confirm import AskUserConfirmCardTool

        try:
            payload = AskUserConfirmCardTool.normalize_args(args)
            gate_result = await context.await_user_input({
                "threadId": context.thread_id,
                "turnId": context.turn_id,
                "toolName": call.tool_name,
                **payload,
            })
            result = AskUserConfirmCardTool.build_result(gate_result)
            is_error = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("ask_user_confirm_card failed")
            result = f"Error: 用户确认失败：{exc}"
            is_error = True

        return ToolHostResult(item={
            "kind": "tool_result",
            "id": f"item_{context.turn_id}_{call.call_id}",
            "turnId": context.turn_id,
            "threadId": context.thread_id,
            "role": "tool",
            "status": "failed" if is_error else "completed",
            "createdAt": _now_iso(),
            "finishedAt": _now_iso(),
            "toolName": call.tool_name,
            "callId": call.call_id,
            "toolKind": _classify_tool_kind(call.tool_name),
            "output": result,
            "isError": is_error,
        })

    def should_parallelize(self, tool_calls: list[ToolCallLike], approval_policy: str = "auto") -> bool:
        """Decide whether a batch of tool calls can run concurrently.

        Rules: read/list/find/grep tools are parallel-safe (max 3 at once);
        mutating tools always run sequentially.  Approval policies that
        require per-call confirmation also force sequential execution.
        """
        if len(tool_calls) < 2:
            return False
        if approval_policy in ("untrusted", "never"):
            return False
        # Check MiQi registry-level parallelization first
        tc_dicts = [
            {"id": c.call_id, "name": c.tool_name, "arguments": c.arguments}
            for c in tool_calls
        ]
        return self._registry.should_parallelize(tc_dicts)

    def is_parallel_safe(self, call: ToolCallLike) -> bool:
        """Return True if *call* can run in parallel with other read-only tools."""
        if call.tool_name in _NEVER_PARALLEL_NAMES:
            return False
        return call.tool_name in _PARALLEL_SAFE_NAMES

    def max_parallel(self) -> int:
        return _MAX_PARALLEL_TOOL_CALLS

    def clear_read_tracker(self, thread_id: str | None = None) -> None:
        """Clear the read-file tracker (used after compaction to reset stale state)."""
        if self._read_tracker is not None:
            if thread_id is None:
                self._read_tracker.clear()
            else:
                self._read_tracker.pop(thread_id, None)


# ═══════════════════════════════════════════════════════════════════════════════
# FakeToolHost — for testing without real tool execution
# ═══════════════════════════════════════════════════════════════════════════════


class FakeToolHost:
    """A test-double tool host with configurable responses."""

    def __init__(
        self,
        tools: list[dict[str, Any]] | None = None,
        results: dict[str, str] | None = None,
        error_tools: set[str] | None = None,
    ):
        self._tools = tools or []
        self._results = results or {}
        self._error_tools = error_tools or set()
        self._calls: list[tuple[ToolCallLike, ToolHostContext]] = []

    @property
    def calls(self) -> list[tuple[ToolCallLike, ToolHostContext]]:
        return list(self._calls)

    async def list_tools(self, context: ToolHostContext | None = None) -> list[dict[str, Any]]:
        return [dict(t) for t in self._tools]

    async def execute(
        self,
        call: ToolCallLike,
        context: ToolHostContext,
        on_progress: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None = None,
    ) -> ToolHostResult:
        self._calls.append((call, context))
        is_error = call.tool_name in self._error_tools
        output = self._results.get(call.tool_name, f"Result of {call.tool_name}({call.arguments})")
        return ToolHostResult(item={
            "kind": "tool_result",
            "id": f"item_{context.turn_id}_{call.call_id}",
            "turnId": context.turn_id,
            "threadId": context.thread_id,
            "role": "tool",
            "status": "failed" if is_error else "completed",
            "createdAt": _now_iso(),
            "finishedAt": _now_iso(),
            "toolName": call.tool_name,
            "callId": call.call_id,
            "toolKind": _classify_tool_kind(call.tool_name),
            "output": output if not is_error else f"Error: {output}",
            "isError": is_error,
        })

    def should_parallelize(self, tool_calls: list[ToolCallLike], approval_policy: str = "auto") -> bool:
        if len(tool_calls) < 2:
            return False
        if approval_policy in ("untrusted", "never"):
            return False
        return all(
            c.tool_name in _PARALLEL_SAFE_NAMES and c.tool_name not in _NEVER_PARALLEL_NAMES
            for c in tool_calls
        )

    def is_parallel_safe(self, call: ToolCallLike) -> bool:
        return call.tool_name in _PARALLEL_SAFE_NAMES

    def max_parallel(self) -> int:
        return _MAX_PARALLEL_TOOL_CALLS


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _classify_tool_kind(name: str) -> str:
    """Classify a tool name as tool_call, command_execution, or file_change."""
    if name in ("bash", "exec", "shell"):
        return "command_execution"
    if name in (
        "write", "edit", "edit_diff", "apply_patch", "delete", "move",
        "write_file", "edit_file",
        "create_docx", "create_pptx", "create_xlsx",
        "edit_docx", "append_xlsx",
        "docx_write", "pptx_write", "xlsx_write",
    ):
        return "file_change"
    return "tool_call"


def _requires_approval(name: str, approval_policy: str = "auto") -> bool:
    """Return whether the KUN tool host should ask its approval gate."""
    if approval_policy in ("never", "none", "disabled"):
        return False
    if approval_policy in ("untrusted", "suggest", "on_request", "always"):
        return True
    return name not in _PARALLEL_SAFE_NAMES


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
