"""Tool registry for dynamic tool management.

Supports both sequential and concurrent execution of tool calls.
Concurrent execution logic ported from Hermes Agent's run_agent.py:
  - _PARALLEL_SAFE_TOOLS: read-only tools that can always run in parallel
  - _PATH_SCOPED_TOOLS: file tools that can safely run in parallel when
    their target paths do not overlap
  - _NEVER_PARALLEL_TOOLS: tools that must always run sequentially
  - Path overlap detection prevents concurrent writes to the same file

.. admonition:: Production-path audit (Phase 63)

   ``ToolRegistry.execute()`` and ``ToolRegistry.execute_concurrent()`` are
   **never** called in production.  All runtime tool invocations flow through
   ``ToolOrchestrator.execute()`` → ``_execute_in_sandbox()`` (see
   ``miqi/runtime/services.py:157-158`` and ``miqi/runtime/tool_runtime.py``).
   The ``ToolRegistry`` instance is created by ``create_runtime_tool_registry()``
   and passed into the orchestrator as its ``tools`` dependency — it is used
   only for schema definitions and as a lookup table, never for direct execution.

   The only remaining callers of ``ToolRegistry.execute()`` /
   ``execute_concurrent()`` are in test files (``test_file_mutation_approval.py``,
   ``test_phase31_acceptance.py``, ``test_phase32_office_path_enforcement.py``,
   ``test_tool_validation.py``).  These tests construct ad-hoc registries and
   bypass the orchestrator — they are not representative of production flows.

   If you add a new production call site that calls ``ToolRegistry.execute()``
   or ``execute_concurrent()`` directly, you are bypassing:
   - Permission checks
   - Sandbox policy enforcement
   - Approval lifecycle
   - Hook pipeline
   - Replay ledger mirroring
   - Structured error sanitization (``_sanitize_exc_for_ui()``)

   **Do not do that.**  Wire through ``ToolOrchestrator.execute()`` instead.
"""
from __future__ import annotations

import asyncio
import json
import traceback
from typing import Any

from loguru import logger

from miqi.agent.tools.base import Tool

# Default timeout for individual tool execution (seconds)
DEFAULT_TOOL_TIMEOUT = 120

# ── Concurrency classification (matches Hermes) ────────────────────────────
# Tools safe to run fully in parallel (read-only, no side effects)
_PARALLEL_SAFE_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "list_dir",
    "web_search",
    "web_fetch",
    "paper_search",
    "paper_get",
    "session_search",   # read-only FTS5 search
    "trace_search",     # read-only embedding/FTS5 search
    "skill_manage",     # list/view are read-only; create/patch/archive have side effects but are safe
})

# File tools with path arguments — safe in parallel when paths don't overlap
_PATH_SCOPED_TOOLS: frozenset[str] = frozenset({
    "write_file",
    "edit_file",
    "read_file",
    "create_docx",
    "create_pptx",
    "create_xlsx",
    "edit_docx",
    "append_xlsx",
    "memory",  # writes to workspace/memory/MEMORY.md or USER.md
    "task_begin",  # writes to workspace/traces/
    "task_end",    # writes to workspace/traces/
})

# Tools that must NEVER run in parallel (ordering/state matters)
_NEVER_PARALLEL_TOOLS: frozenset[str] = frozenset({
    "exec",
    "message",
    "spawn",
    "cron",
    "ask_user_confirm_card",
})


def _extract_path_arg(name: str, arguments: dict[str, Any]) -> str | None:
    """Extract the primary path argument from a tool call (if any)."""
    for key in ("path", "file_path", "filename"):
        if key in arguments:
            return str(arguments[key])
    return None


def _paths_overlap(path_a: str | None, path_b: str | None) -> bool:
    """Return True if two paths conflict (same path or one is prefix of the other)."""
    if path_a is None or path_b is None:
        return False
    a = path_a.rstrip("/")
    b = path_b.rstrip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and sequential or concurrent execution of tools.
    """

    def __init__(self, tool_timeout: float = DEFAULT_TOOL_TIMEOUT):
        self._tools: dict[str, Tool] = {}
        self.tool_timeout = tool_timeout

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any], **extra) -> str:
        """
        Execute a tool by name with given parameters.

        .. warning::
           This is an **internal** API.  Production runtime tool calls MUST
           go through ``ToolRuntime`` → ``ToolOrchestrator``, not directly
           through ``ToolRegistry.execute()``.  Direct calls bypass
           permission checks, sandbox policy, approval lifecycle, hooks,
           and ledger/replay mirroring.

           The only internal caller is ``execute_concurrent()``, which is
           itself deprecated (no production call sites as of Phase 32).

        Args:
            name: Tool name.
            params: Tool parameters.
            **extra: Extra keyword arguments forwarded to tool.execute()
                     (e.g. ``_on_progress`` for MCP progress callbacks).

        Returns:
            Tool execution result as string.

        Raises:
            KeyError: If tool not found.
        """
        hint = "\n\n[Analyze the error above and try a different approach.]"

        # #984 layer 3 (R4): ``_user_roots`` is a harness-owned channel
        # (ToolOrchestrator strips the model's copy before injecting its
        # own).  ``params`` on this path IS model-authored — on any caller
        # that skips the orchestrator (e.g. the legacy SubagentManager) a
        # model-supplied ``_user_roots`` would become both a sandbox bind
        # source and the guard's write scope.  Only ``**extra`` may carry
        # it.
        params = {k: v for k, v in params.items() if k != "_user_roots"}

        tool = self._tools.get(name)
        if not tool:
            return f"Error: 未找到工具 '{name}'。可用工具：{', '.join(self.tool_names)}"

        try:
            errors = tool.validate_params(params)
            if errors:
                return f"Error: 工具 '{name}' 参数无效：" + "; ".join(errors) + hint
            # Per-tool timeout takes priority (e.g. MCPToolWrapper exposes its
            # own configured toolTimeout); fall back to the registry-level default.
            timeout = tool.execution_timeout if tool.execution_timeout is not None else self.tool_timeout
            result = await asyncio.wait_for(
                tool.execute(**params, **extra),
                timeout=timeout,
            )
            if isinstance(result, str) and result.startswith("Error"):
                return result + hint
            return result
        except asyncio.TimeoutError:
            return f"Error: 工具 '{name}' 在 {timeout} 秒后超时" + hint
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(
                "Tool '{}' raised {}: {}\n{}",
                name, type(e).__name__, e, tb,
            )
            return f"Error executing {name}: {type(e).__name__}: {e}" + hint

    # ── Concurrent execution ───────────────────────────────────────────────

    def should_parallelize(
        self,
        tool_calls: list[dict[str, Any]],
    ) -> bool:
        """Decide whether a batch of tool calls can be executed concurrently.

        Rules (matching Hermes _should_parallelize_tool_batch logic):
        1. Batch must have ≥ 2 calls.
        2. No call may be in _NEVER_PARALLEL_TOOLS.
        3. At least one call must be in _PARALLEL_SAFE_TOOLS OR all calls
           are in _PATH_SCOPED_TOOLS without path overlaps.
        """
        if len(tool_calls) < 2:
            return False

        names = [tc.get("name", "") for tc in tool_calls]

        # Any never-parallel tool → sequential
        if any(n in _NEVER_PARALLEL_TOOLS for n in names):
            return False

        # If all calls are read-only safe → always parallel
        if all(n in _PARALLEL_SAFE_TOOLS for n in names):
            return True

        # If all calls are path-scoped file tools → parallel only when no path overlaps
        if all(n in _PATH_SCOPED_TOOLS for n in names):
            paths = [_extract_path_arg(tc["name"], tc.get("arguments", {})) for tc in tool_calls]
            for i, pa in enumerate(paths):
                for j, pb in enumerate(paths):
                    if i != j and _paths_overlap(pa, pb):
                        return False
            return True

        # Mixed bag — require at least one parallel-safe tool to greenlight the batch
        return any(n in _PARALLEL_SAFE_TOOLS for n in names)

    async def execute_concurrent(
        self,
        tool_calls: list[dict[str, Any]],
        default_kwargs: dict[str, Any] | None = None,
    ) -> list[tuple[str, str]]:
        """Execute multiple tool calls concurrently using asyncio.gather.

        .. deprecated::
           This method has **no production call sites** (Phase 32 audit).
           All concurrent tool dispatch goes through ``ToolRuntime.execute_many()``
           → ``ToolOrchestrator.execute()`` so that permission checks,
           sandbox policy, approval lifecycle, hooks, and ledger/replay
           mirroring are uniformly applied.

           This method is retained for:
           - Legacy test code that constructs ad-hoc ``ToolRegistry`` instances.
           - Documentation examples (docs/backend/tools.md).
           - Emergency debugging / REPL exploration.

           Do NOT add new production call sites in ``miqi/runtime``,
           ``miqi/bridge``, ``miqi/cli``, ``miqi/tui``, ``miqi/channels``,
           or ``miqi/cron``.

        Args:
            tool_calls: List of dicts with keys 'id', 'name', 'arguments'.
            default_kwargs: Extra kwargs forwarded to every tool execution.

        Returns:
            List of (tool_call_id, result_str) preserving input order.
        """
        extra = default_kwargs or {}

        async def _one(tc: dict[str, Any]) -> tuple[str, str]:
            tc_id = tc.get("id", "")
            name = tc.get("name", "")
            args = tc.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    args = {}
            result = await self.execute(name, args, **extra)
            return tc_id, result

        results = await asyncio.gather(*[_one(tc) for tc in tool_calls])
        return list(results)

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
