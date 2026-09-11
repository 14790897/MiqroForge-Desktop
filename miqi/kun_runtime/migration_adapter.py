"""Bridge between MiQi legacy session model and KUN thread model.

Provides:
- ``session_key → threadId`` mapping for coexistence
- ``GatewayKunRuntime`` — gateway/CLI-compatible wrapper around KunRuntime
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Simple in-memory bidirectional mapping.
# In production this could be backed by a JSON file or SQLite metadata table.
_SESSION_TO_THREAD: dict[str, str] = {}
_THREAD_TO_SESSION: dict[str, str] = {}


def session_key_to_thread_id(session_key: str) -> str:
    """Map a MiQi session key (``channel:chat_id``) to a KUN thread ID.

    If no mapping exists yet, a new thread ID is generated.
    Generates deterministic thread IDs based on session key for idempotency.
    """
    if session_key in _SESSION_TO_THREAD:
        return _SESSION_TO_THREAD[session_key]
    # Deterministic: use a stable hash so restarts produce the same mapping
    thread_id = _make_thread_id(session_key)
    _SESSION_TO_THREAD[session_key] = thread_id
    _THREAD_TO_SESSION[thread_id] = session_key
    return thread_id


def thread_id_to_session_key(thread_id: str) -> str | None:
    """Reverse mapping: return the MiQi session key for a KUN thread ID."""
    return _THREAD_TO_SESSION.get(thread_id)


def register_mapping(session_key: str, thread_id: str) -> None:
    """Explicitly register a mapping (e.g. when loading from persistence)."""
    _SESSION_TO_THREAD[session_key] = thread_id
    _THREAD_TO_SESSION[thread_id] = session_key


def clear_mapping(session_key: str) -> None:
    """Remove a mapping (e.g. when a thread is deleted)."""
    thread_id = _SESSION_TO_THREAD.pop(session_key, None)
    if thread_id:
        _THREAD_TO_SESSION.pop(thread_id, None)


def _make_thread_id(session_key: str) -> str:
    """Generate a stable, URL-safe thread ID from a session key."""
    import hashlib
    h = hashlib.sha256(session_key.encode()).hexdigest()
    return f"thread_{h[:16]}"


# ═══════════════════════════════════════════════════════════════════════════════
# GatewayKunRuntime — KunRuntime wrapper for gateway/CLI
# ═══════════════════════════════════════════════════════════════════════════════


class GatewayKunRuntime:
    """Wraps KunRuntime for use by gateway_cmd, agent_cmd, and bridge.

    Translates between the legacy ``process_direct()`` interface and
    KUN's thread/turn/event pipeline.
    """

    def __init__(
        self,
        data_dir: str | Path,
        workspace: Path,
        provider: Any,
        tool_registry: Any,
        model: str,
        agent_name: str = "miqi",
        mcp_servers: dict[str, Any] | None = None,
    ):
        import threading

        from miqi.kun_runtime.runtime import KunRuntime, RuntimeOptions

        self._workspace = workspace
        self._agent_name = agent_name
        self._mcp_servers = mcp_servers or {}

        self._runtime = KunRuntime(RuntimeOptions(
            data_dir=Path(data_dir),
            workspace=str(workspace),
            model=model,
        ))
        self._runtime.set_provider(provider)
        self._runtime.set_tool_registry(tool_registry)

        self._running = False
        self._cron: Any = None
        self._mcp_connected = False
        # Compatibility with legacy AgentLoop's abort mechanism (used by bridge)
        self._abort_event = threading.Event()

    @property
    def bus(self) -> Any:
        """Compatibility property — gateway code may reference loop.bus."""
        return None  # KUN runtime doesn't use MessageBus

    @property
    def channels_config(self) -> Any:
        """Compatibility property."""
        return None

    async def _ensure_mcp(self) -> None:
        """Connect MCP servers if configured (delegates to tool registry)."""
        if self._mcp_connected or not self._mcp_servers:
            return
        from miqi.agent.tools.mcp import connect_mcp_servers
        try:
            import asyncio
            registry = self._runtime.tool_host._registry
            self._mcp_keep_alive = asyncio.Event()
            self._mcp_tasks = await connect_mcp_servers(
                self._mcp_servers, registry, self._mcp_keep_alive,
                # #975：下载类工具需要 base_workspace 落盘（桌面/网关同源语义）。
                workspace=self._workspace,
            )
            self._mcp_connected = True
        except Exception:
            pass  # MCP connection failure is non-fatal for KunRuntime path

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Any = None,
    ) -> str:
        """Process a message using KUN runtime. Compatible with legacy ``process_direct``."""
        await self._ensure_mcp()

        thread_id = session_key_to_thread_id(session_key)

        # Ensure thread exists
        thread = await self._runtime.threads.get(thread_id)
        if thread is None:
            thread = await self._runtime.threads.create(
                workspace=str(self._workspace),
                model=self._runtime._options.model,
                title=session_key,
            )
            thread_id = thread["id"]
            register_mapping(session_key, thread_id)

        # Start turn
        result = await self._runtime.turns.start_turn(thread_id, content)
        turn_id = result["turnId"]

        # Run turn
        status = await self._runtime.run_turn(thread_id, turn_id)

        # Extract assistant text from items
        items = await self._runtime.session_store.load_items(thread_id)
        assistant_text = ""
        for item in reversed(items):
            if item.get("turnId") == turn_id and item.get("kind") == "assistant_text":
                assistant_text = str(item.get("text", ""))
                break

        if not assistant_text and status == "failed":
            assistant_text = "⚠️ 任务处理遇到错误，请查看运行日志。"

        return assistant_text

    async def run(self) -> None:
        """Start KUN runtime loop (compatibility stub — KUN is turn-driven)."""
        self._running = True

    def stop(self) -> None:
        """Stop the KUN runtime."""
        self._running = False

    async def close_mcp(self) -> None:
        """Close MCP connections (no-op for now)."""
        pass
