"""
MiQi bridge server — stdin/stdout JSON-line protocol.

Protocol:
  Request:  {"id": "<uuid>", "method": "<name>", "params": {...}}
  Response: {"id": "<uuid>", "result": {...}}
  Error:    {"id": "<uuid>", "error": "<message>"}
  Event:    {"id": "<uuid>", "type": "<event_type>", "data": {...}}

Events (type field) are sent during chat for streaming progress:
  - "progress": tool-call hint or progress milestone
  - "final": chat complete with full response content
  - "error": chat encountered an error

All JSON is written to stdout one line per message. Logs go to stderr.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from miqi.bridge.loopback_compat import install_loopback_safe_socketpair
from miqi.runtime.workspace_logging import _redact_message, append_workspace_log

# Windows loopback can be selectively filtered by security software (WFP
# residue), which makes asyncio's socketpair-based self-pipe hang forever and
# the bridge never reaches "ready". Install the guarded fallback before any
# asyncio event loop is created.
install_loopback_safe_socketpair()

# Force UTF-8 on Windows (default is GBK/cp936 which cannot encode emoji)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')
if hasattr(sys.stdin, 'reconfigure'):
    sys.stdin.reconfigure(encoding='utf-8')

# Get raw binary stdout for _send so we bypass any remaining text-layer encoding
_stdout_buffer = sys.stdout.buffer if hasattr(sys.stdout, 'buffer') else None

_stdout_lock = threading.Lock()
_file_logging_sinks: dict[Path, int] = {}

# Client prefix used to namespace session keys in sandbox metadata lookups.
_CLIENT_PREFIX = "miqi-desktop:"


def _log(msg: str, level: str = "INFO") -> None:
    """Unified log function — writes to stderr with [miqi-bridge] prefix.

    Also routes through loguru so that all loguru-based modules (sandbox,
    agent loop, etc.) appear in the same stream.
    """
    try:
        print(f"[miqi-bridge] {msg}", file=sys.stderr, flush=True)
    except OSError:
        # stderr may be closed during shutdown on Windows / PyInstaller
        pass


def _init_logging() -> None:
    """Configure loguru to output alongside the bridge's _log() format.

    Replaces loguru's default handler with one that uses the [miqi-bridge]
    prefix and writes to stderr, so all module-level logger.info/error calls
    are visible in the same terminal stream.
    """
    from loguru import logger  # pylint: disable=import-error,import-outside-toplevel

    # Remove the default handler (sink #0) which uses loguru's verbose format
    logger.remove()

    # Add a clean handler that matches _log()'s style
    logger.add(
        sys.stderr,
        format="<level>[miqi-bridge] {name}:{function}:{line} | {message}</level>",
        level="INFO",
        colorize=True,
    )


def _send(data: dict[str, Any]) -> None:
    """Write one atomic JSON line to stdout as UTF-8 bytes (thread-safe)."""
    line = (json.dumps(data, ensure_ascii=False) + "\n").encode('utf-8')
    with _stdout_lock:
        if _stdout_buffer is not None:
            _stdout_buffer.write(line)
            _stdout_buffer.flush()
        else:
            sys.stdout.write(line.decode('utf-8'))
            sys.stdout.flush()


def _result(req_id: str, result: Any = None) -> None:
    _send({"id": req_id, "result": result if result is not None else {}})


def _error(req_id: str, message: str) -> None:
    _send({"id": req_id, "error": message})


def _event(req_id: str, event_type: str, data: Any) -> None:
    _send({"id": req_id, "type": event_type, "data": data})


def _terminal_event(req_id: str, event_type: str, data: Any) -> bool:
    """Send a terminal event (final/error/aborted) for req_id.

    Returns True if this is the first terminal event for this request.
    Returns False (and drops the event) if a terminal event was already sent
    — this prevents duplicate terminal states from racing abort vs completion.
    """
    if not _state.mark_terminated(req_id):
        _log(f"Dropping duplicate terminal event {event_type} for {req_id}")
        return False
    _event(req_id, event_type, data)
    return True


# ---------------------------------------------------------------------------
# Bridge state
# ---------------------------------------------------------------------------

class BridgeState:
    """Holds cached config, abort state, and shared sandbox manager."""

    def __init__(self) -> None:
        self.config = None  # lazy-loaded
        # #789: snapshot of the config at process start — never overwritten by
        # saves.  Used to compute PENDING restart-requiring state (tier-C
        # fields whose current value differs from what the process runs with).
        self.config_at_startup = None
        self._lock = threading.Lock()
        self._terminated: set[str] = set()
        self._pending_approvals: dict[str, threading.Event] = {}
        self._approval_decisions: dict[str, str] = {}
        self._approval_meta: dict[str, dict] = {}
        self._sandbox_manager: Any = None  # shared SandboxManager across agents
        self._agent_control: Any = None  # Phase 2 shared AgentControl
        self._orchestrator: Any = None  # Phase 3 shared ToolOrchestrator
        self._plan_tracker: Any = None  # Phase 9 shared PlanTracker
        self._plugin_manager: Any = None  # Phase 4 shared PluginManager
        self._mcp_servers: dict = {}  # MCP servers registered by plugins
        self._runtime_sessions: dict[str, Any] = {}  # Phase 11 RuntimeSession cache

    def load_config(self):
        from miqi.config.loader import load_config

        self.config = load_config()
        # First load in the process is the startup snapshot (#789).  Deep
        # copy: handlers mutate self.config in place (e.g. mcp_upsert /
        # mcp_delete), and a shared nested object would corrupt the
        # pending-restart baseline (2026-09-01 review).
        if self.config_at_startup is None:
            self.config_at_startup = self.config.model_copy(deep=True)
        return self.config

    async def get_runtime_session(self, session_key: str, *, caller_id: str = "", approval_callback=None):
        """Get or create a RuntimeSession for the given session key.

        Sessions are cached and reused across bridge requests. This is the
        Phase 11 foundation — actual chat routing through RuntimeSession
        will happen in Phase 14.

        Session keys are namespaced per caller to prevent cross-user
        session access when multiple frontends share a bridge process.
        caller_id will become REQUIRED in Phase 14 (no default).
        """
        import warnings

        if not caller_id:
            warnings.warn(
                "get_runtime_session called without caller_id — "
                "sessions are NOT isolated. caller_id will be required "
                "in Phase 14.",
                FutureWarning,
                stacklevel=2,
            )

        from miqi.providers.factory import make_provider
        from miqi.runtime.session import RuntimeSession

        # Namespace sessions by caller to prevent cross-user access
        ns_key = f"{caller_id}:{session_key}" if caller_id else session_key

        runtime = self._runtime_sessions.get(ns_key)
        if runtime is not None:
            return runtime

        config = self.load_config()
        provider = make_provider(config)
        runtime = RuntimeSession.create(
            config=config,
            provider=provider,
            session_id=ns_key,
            workspace=config.workspace_path,
        )
        await runtime.start()
        self._runtime_sessions[ns_key] = runtime
        return runtime

    def _ensure_sandbox_manager(self):
        """Lazy-init the shared SandboxManager from config."""
        if self._sandbox_manager is not None:
            return
        config = self.load_config()
        sb_cfg = getattr(config.tools, "sandbox", None)
        if sb_cfg is None or not getattr(sb_cfg, "enabled", True):
            self._sandbox_manager = "disabled"
            return
        from miqi.sandbox.manager import SandboxManager

        def _session_workspace(key: str, *, client_id: str | None = None) -> str | None:
            try:
                from miqi.session.manager import SessionManager
                sm = SessionManager(config.workspace_path)
                # The sandbox key is namespaced `client_id:session_key` (e.g.
                # `miqi-desktop:desktop:xxx`); the session metadata is stored
                # under the bare session_key (`desktop:xxx`).  The resolver
                # may be called without client_id, so strip the known client
                # prefix `miqi-desktop:` when present, and otherwise keep the
                # key intact (a raw key like `desktop:xxx` must not be split).
                bare_key = key
                if key.startswith(_CLIENT_PREFIX):
                    bare_key = key[len(_CLIENT_PREFIX):]
                session = sm.get_or_create(bare_key, client_id=client_id)
                return session.metadata.get("workspace")
            except Exception:
                return None

        self._sandbox_manager = SandboxManager(
            workspace=config.workspace_path,
            share_net=getattr(sb_cfg, "share_net", True),
            # Start with enabled=False so tools run locally during
            # background install.  _init_sandbox_manager() in loop.py
            # auto-enables after initialize() succeeds and persists
            # the change to config without requiring a restart.
            enabled=False,
            max_sandboxes=getattr(sb_cfg, "max_sandboxes", 10),
            auto_cleanup=getattr(sb_cfg, "auto_cleanup", True),
            auto_install_deps=getattr(sb_cfg, "auto_install_deps", True),
            allow_system_installs=getattr(sb_cfg, "allow_system_installs", False),
            wsl_distro=getattr(sb_cfg, "wsl_distro", ""),
            wsl_base_dir=getattr(sb_cfg, "wsl_base_dir", "/tmp/miqi-sandboxes"),
            session_workspace_resolver=_session_workspace,
        )

    def destroy_sandbox(self, session_key: str, *, client_id: str | None = None) -> bool:
        """Destroy the sandbox for a session (called on delete/archive).

        Phase 30: client_id is used to compute the client-scoped sandbox key.
        When client_id is None, falls back to raw session_key (legacy path).

        Safe to call from both legacy (no running loop) and persistent-loop
        contexts.  On a persistent loop the async work is delegated to a
        short-lived thread so we never call ``run_until_complete`` on an
        already-running loop.
        """
        if self._sandbox_manager is None or self._sandbox_manager == "disabled":
            return False

        async def _destroy() -> bool:
            try:
                return await self._sandbox_manager.destroy(
                    session_key, client_id=client_id,
                )
            except Exception as exc:
                _log(f"destroy_sandbox error: {exc}")
                return False

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No loop running — legacy path, safe to call asyncio.run()
            return asyncio.run(_destroy())

        # Persistent loop running — delegate to a separate thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _destroy()).result(timeout=60)

    async def destroy_sandbox_async(self, session_key: str, *, client_id: str | None = None) -> bool:
        """Destroy the sandbox for async AppServer handlers without blocking."""
        if self._sandbox_manager is None or self._sandbox_manager == "disabled":
            return False
        try:
            return await self._sandbox_manager.destroy(session_key, client_id=client_id)
        except Exception as exc:
            _log(f"destroy_sandbox error: {exc}")
            return False

    def abort_active(self) -> dict:
        """Resolve all pending approvals so blocked daemon threads can exit.

        After Phase 14, turn abort is handled by RuntimeSession.submit(AbortTurn(...)).
        This method remains as a safety net to unblock approval-waiting threads
        (the user-facing abort button also calls RuntimeSession, not this method).
        """
        pending_ids = self.list_pending_approval_ids()
        for aid in pending_ids:
            self.resolve_approval(aid, "deny")
        return {"aborted": len(pending_ids) > 0}

    def mark_terminated(self, req_id: str) -> bool:
        """Atomically check-and-mark a request as terminated.

        Returns True if this call is the first to mark the request,
        False if it was already terminated by a concurrent path (e.g. abort
        raced natural completion).
        """
        with self._lock:
            if req_id in self._terminated:
                return False
            self._terminated.add(req_id)
            return True

    def register_approval(self, approval_id: str, meta: dict | None = None) -> threading.Event:
        """Create and store an event for a pending approval. Returns the event."""
        evt = threading.Event()
        with self._lock:
            self._pending_approvals[approval_id] = evt
            if meta:
                meta["created_at"] = time.time()
                self._approval_meta[approval_id] = meta
        return evt

    def resolve_approval(self, approval_id: str, decision: str) -> bool:
        """Set the decision and unblock the waiting callback. Returns True if found."""
        with self._lock:
            evt = self._pending_approvals.pop(approval_id, None)
            self._approval_meta.pop(approval_id, None)
            if evt is None:
                return False
            self._approval_decisions[approval_id] = decision
        evt.set()
        return True

    def get_approval_decision(self, approval_id: str) -> str:
        """Retrieve and remove the stored decision. Returns 'deny' if not found."""
        with self._lock:
            return self._approval_decisions.pop(approval_id, "deny")

    def list_pending_approval_ids(self) -> list[str]:
        with self._lock:
            return list(self._pending_approvals.keys())

    def list_pending_approvals(self) -> list[dict]:
        """Return pending approvals with metadata for display."""
        import time as _time
        now = _time.time()
        result: list[dict] = []
        with self._lock:
            for aid in list(self._pending_approvals.keys()):
                meta = self._approval_meta.get(aid, {})
                result.append({
                    "approval_id": aid,
                    "command": meta.get("command", ""),
                    "description": meta.get("description", ""),
                    "allow_permanent": meta.get("allow_permanent", True),
                    "created_at": meta.get("created_at", now),
                    "age_seconds": now - meta.get("created_at", now),
                })
        return result


_state = BridgeState()


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

_SECRET_FIELDS = {"apiKey", "api_key", "token", "secret", "password", "appSecret"}


def _redact_secrets(obj: Any, parent_key: str = "") -> None:
    """Redact secret values in-place."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _SECRET_FIELDS or any(s in k.lower() for s in ("secret", "token", "password", "api_key", "apikey")):
                if isinstance(v, str) and v:
                    obj[k] = v[:4] + "****" if len(v) > 4 else "****"
            elif isinstance(v, (dict, list)):
                _redact_secrets(v, k)
    elif isinstance(obj, list):
        for item in obj:
            _redact_secrets(item, parent_key)


def handle_status(req_id: str, params: dict) -> None:
    from miqi.paths import get_config_path
    config_exists = get_config_path()
    _result(req_id, {
        "status": "ok",
        "configured": config_exists.exists(),
        "python_version": sys.version,
    })


# Phase 27.3: handle_chat_send, _run_chat_send_via_runtime, handle_chat_abort,


def handle_plan_get(req_id: str, params: dict) -> None:
    """Get current plan for a thread."""
    plan_id = params.get("plan_id", "") or params.get("thread_id", "")
    tracker = getattr(_state, '_plan_tracker', None)
    if tracker is not None and plan_id:
        plan = tracker.get(plan_id)
        if plan is not None:
            _result(req_id, {"plan": tracker.to_dict(plan)})
            return
    _result(req_id, {"plan": None})


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

_METHODS = {
    "status": handle_status,
    # All other methods (chat, sessions, config, providers, channels,
    # approvals, cron, memory, experience, skills, mcp, python, plugins,
    # permissions, agent) are now AppServer methods — see
    # miqi/runtime/app_server.py:register_command_handlers()
    "plan.get": handle_plan_get,
}


def _dispatch(req_id: str, method: str, params: dict) -> None:
    handler = _METHODS.get(method)
    if handler is None:
        _error(req_id, f"Unknown method: {method}")
        return
    handler(req_id, params)


# ── Phase 26: AppServer-backed dispatch ──────────────────────────────────

_app_server: Any = None  # lazily created AppServer


def _ensure_app_server() -> Any:
    """Return the AppServer instance created by BridgeRuntimeLoop.

    Phase 27.2: AppServer is now owned by BridgeRuntimeLoop
    (miqi/bridge/loop.py). This function returns the global
    reference for backward compatibility with tests.
    """
    global _app_server
    return _app_server


# Phase 27.2: _register_app_server_methods removed. Handler registration
# now happens in BridgeRuntimeLoop._init_app_server() in miqi/bridge/loop.py.
# Phase 27.1: _dispatch_via_appserver removed. All dispatch now goes through
# BridgeRuntimeLoop._drain_loop() which uses the persistent event loop.


def _add_file_logging(workspace: Path) -> None:
    """Add an idempotent redacting loguru file sink."""
    from loguru import logger

    from miqi.runtime.workspace_logging import cleanup_old_logs, get_log_dir

    workspace = workspace.resolve()
    if workspace in _file_logging_sinks:
        return

    log_dir = get_log_dir(workspace)

    def _redacting_sink(message: Any) -> None:
        record = message.record
        timestamp = record["time"].strftime("%Y-%m-%dT%H:%M:%SZ")
        date_str = record["time"].strftime("%Y-%m-%d")
        log_path = log_dir / f"bridge-{date_str}.log"
        line = (
            f"[{timestamp}] [{record['level'].name}] [bridge] "
            f"{record['name']}:{record['function']}:{record['line']} | "
            f"{_redact_message(record['message'])}\n"
        )
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        cleanup_old_logs(workspace)

    _file_logging_sinks[workspace] = logger.add(_redacting_sink, level="DEBUG")


def _ensure_workspace_init() -> None:
    """Create workspace directories and template files if they don't exist."""
    try:
        from importlib.resources import files as pkg_files

        from miqi.utils.helpers import get_workspace_path

        workspace = get_workspace_path()
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "memory").mkdir(exist_ok=True)
        (workspace / "skills").mkdir(exist_ok=True)

        # Add persistent file logging (daily rotation, 7-day retention)
        _add_file_logging(workspace)

        templates_dir = pkg_files("miqi") / "templates"
        for item in templates_dir.iterdir():
            if not item.name.endswith(".md"):
                continue
            dest = workspace / item.name
            if not dest.exists():
                dest.write_text(item.read_text(encoding="utf-8"), encoding="utf-8")

        memory_template = templates_dir / "memory" / "MEMORY.md"
        memory_file = workspace / "memory" / "MEMORY.md"
        if not memory_file.exists():
            memory_file.write_text(memory_template.read_text(encoding="utf-8"), encoding="utf-8")

        append_workspace_log(workspace, "Bridge workspace initialized", source="bridge")
        _log("Workspace ready")
    except Exception as exc:
        _log(f"Workspace init warning (non-fatal): {exc}")
        # Only attempt workspace logging if workspace was successfully resolved.
        # Wrap in its own try/except so a logging I/O failure cannot escape
        # this non-fatal handler.
        if "workspace" in locals():
            try:
                append_workspace_log(
                    workspace,
                    f"Workspace init warning: {exc}",
                    level="WARNING",
                    source="bridge",
                )
            except Exception:
                pass  # logging failure — already reported via _log above


# Global bridge state — accessible from atexit/signal handlers
_bridge_state: BridgeState | None = None


def _graceful_shutdown() -> None:
    """Attempt to destroy all sandboxes on shutdown.

    Called via atexit or signal handler. Uses a sync wrapper around
    the async destroy_all() since we can't await in these contexts.
    """
    global _bridge_state
    if _bridge_state is None:
        return

    sandbox_mgr = getattr(_bridge_state, "_sandbox_manager", None)
    if sandbox_mgr is None or sandbox_mgr == "disabled":
        return

    # Try async destroy — run in a new event loop if needed
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Phase 27.6: persistent loop running — schedule cleanup.
            # We must await the future so that destroy_all() actually
            # runs before _clear_state_file() deletes the state file
            # and _bridge_state is set to None (#329).
            try:
                loop.run_until_complete(
                    asyncio.ensure_future(sandbox_mgr.destroy_all())
                )
            except Exception as destroy_exc:
                _log(
                    "Sandbox destroy_all failed during shutdown "
                    f"(non-fatal): {destroy_exc}"
                )
        else:
            # No running loop (signal/atexit after loop closed) — create one
            asyncio.run(sandbox_mgr.destroy_all())
    except Exception as exc:
        _log(f"Sandbox cleanup on shutdown failed (non-fatal): {exc}")
    finally:
        # Always clear the state file to prevent stale entries.
        # Only do this after destroy_all() has completed (#329).
        try:
            sandbox_mgr._clear_state_file()
        except Exception:
            pass
        _bridge_state = None


def _clear_sandbox_state_file_fast() -> None:
    """#959: 看门狗退出路径的状态文件快清理（不做 destroy_all —— 那正是
    优雅关停的挂起向量）。防下一次启动读到 stale 条目。"""
    global _bridge_state
    if _bridge_state is None:
        return
    sandbox_mgr = getattr(_bridge_state, "_sandbox_manager", None)
    if sandbox_mgr is None or sandbox_mgr == "disabled":
        return
    try:
        sandbox_mgr._clear_state_file()
    except Exception:
        pass


def main() -> None:
    global _bridge_state

    # Fast self-check mode: report Python/deps availability and exit without
    # starting the bridge. Electron's python.check used to spawnSync this
    # binary with --check; without this early exit the bridge started fully
    # and then sat waiting on stdin until the sync call timed out (#603).
    if "--check" in sys.argv:
        # JSON-only path: do NOT call _init_logging() — it imports loguru,
        # which would mask a missing-loguru dependency from the report.
        try:
            import importlib

            issues = []
            if sys.version_info < (3, 11):
                issues.append(
                    f"Python {sys.version_info.major}.{sys.version_info.minor} is too old (need >= 3.11)",
                )
            for mod in ("pydantic", "httpx", "loguru"):
                try:
                    importlib.import_module(mod)
                except ImportError:
                    issues.append(f"Missing dependency: {mod}")
            info = {
                "ok": len(issues) == 0,
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "issues": issues,
            }
            print(json.dumps(info), flush=True)
        except Exception as exc:
            print(json.dumps({"ok": False, "issues": [f"check failed: {exc}"]}), flush=True)
        return

    _init_logging()
    _log("Bridge server starting")
    _ensure_workspace_init()

    # Persist approval history so records survive bridge restarts
    try:
        from miqi.agent.command_approval import init_history_file
        from miqi.config.loader import get_data_dir

        init_history_file(get_data_dir() / "approval_history.jsonl")
    except Exception as exc:
        _log(f"Approval history init warning (non-fatal): {exc}")

    # Initialize bridge state — reuse the global _state instance
    _bridge_state = _state

    # Register graceful shutdown: destroy sandboxes & clear state file
    atexit.register(_graceful_shutdown)

    # Also handle SIGTERM (e.g. from Electron's process.kill() or Hot Reload)
    try:
        signal.signal(signal.SIGTERM, lambda *_: _graceful_shutdown())
    except (OSError, ValueError):
        # signal.signal can fail if not in main thread or not supported
        pass

    # Phase 27.1: use BridgeRuntimeLoop with persistent asyncio event loop
    # instead of per-request asyncio.run(). Legacy handlers continue to
    # work via the _dispatch fallback path.
    from miqi.bridge.loop import BridgeRuntimeLoop

    # #959: parent-death watchdog — Electron 被硬杀（E2E 15s 竞速超时/崩溃）
    # 时 before-quit → BridgeManager.stop() 不会执行，桥若无此看门狗会成为
    # 孤儿污染后续运行（mcps.list 挂起）。看门狗发现启动链死亡后硬退出
    # （os._exit），故意绕过会挂起在 WSL teardown 的 _graceful_shutdown；
    # 退出前只做状态文件快清理 + Windows 整树回收 MCP/exec 孙进程。
    def _on_parent_death() -> None:
        _clear_sandbox_state_file_fast()
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(os.getpid())],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass

    from miqi.bridge.parent_watchdog import start_parent_watchdog

    start_parent_watchdog(on_death=_on_parent_death)

    bridge = BridgeRuntimeLoop(
        send_func=_send,
        dispatch_legacy_func=_dispatch,
        bridge_state=_state,
        dev_mode=False,
    )
    bridge.start()

    # stdin closed — graceful exit
    _graceful_shutdown()
    _log("Bridge server stopped")


if __name__ == "__main__":
    main()
