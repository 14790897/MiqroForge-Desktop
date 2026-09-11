"""Client-scoped workbench process runtime.

Owns the process registry, spawns non-PTY processes with
asyncio.create_subprocess_exec, and manages the full lifecycle:
stdout/stderr streaming, output caps, timeout, stdin, kill,
and cleanup on client disconnect or server stop.

Phase 43: no shell=True, no PTY support. PTY/resizePty return
UNSUPPORTED_FEATURE at the handler layer.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger

# ── Defaults ──────────────────────────────────────────────────────────────

# Default timeout for command/exec and process/spawn when timeoutMs is
# omitted.  5 minutes prevents runaway processes without requiring every
# caller to remember to set a timeout.
DEFAULT_TIMEOUT_MS: int = 300_000  # 5 minutes

# Default per-stream output cap (1 MiB).  Prevents memory exhaustion from
# verbose or infinite-output processes.  Applied independently to stdout
# and stderr.
DEFAULT_OUTPUT_BYTES_CAP: int = 1_048_576  # 1 MiB = 1024 * 1024


# ── Environment variable blocklist ───────────────────────────────────────


# Environment variable prefixes that are blocked for security.
# These can be used to inject code or alter process behaviour in
# dangerous ways when spawning subprocesses.
#
# Applied to BOTH user-supplied env (validated in handlers) AND
# inherited environment (sanitized here before subprocess creation).
BLOCKED_ENV_PREFIXES: tuple[str, ...] = (
    # Dynamic linker / loader injection
    "LD_",              # Linux dynamic linker (LD_PRELOAD, LD_LIBRARY_PATH)
    "DYLD_",            # macOS dynamic linker injection
    # Language runtime injection
    "NODE_OPTIONS",     # Node.js options injection
    "NODE_PATH",        # Node.js module path injection
    "PYTHONSTARTUP",    # Python startup script
    "PYTHONPATH",       # Python import path injection
    "PYTHONHOME",       # Python home override
    "PYTHONOPTIONS",    # Python options injection
    "PYTHONINSPECT",    # Python interactive inspect after script
    "PIP_",             # pip env vars (PIP_REQUIRE_VIRTUALENV bypass, etc.)
    "PERL5LIB",         # Perl library path injection
    "PERL5OPT",         # Perl options injection
    "RUBYOPT",          # Ruby options injection
    "RUBYLIB",          # Ruby library path injection
    "GEM_PATH",         # Ruby gem path injection
    # JVM injection
    "JAVA_TOOL_OPTIONS",# JVM tool options injection
    "_JAVA_OPTIONS",    # JVM options (undocumented but widely supported)
    "JDK_JAVA_OPTIONS", # JDK-specific JVM options
    # Build tool injection
    "MAVEN_OPTS",       # Maven JVM options injection
    "GRADLE_OPTS",      # Gradle JVM options injection
    "SBT_OPTS",         # SBT JVM options injection
    # Shell injection
    "BASH_ENV",         # Bash startup script
    "BASH_FUNC_",       # Bash function export
    "BASHOPTS",         # Bash options
    "SHELLOPTS",        # Shell options
    "ENV",              # POSIX sh startup file
    "IFS",              # Shell word splitting manipulation
    # System / libc injection
    "GCONV_PATH",       # glibc charset conversion module injection
    "GLIBC_TUNABLES",   # glibc tunables injection
    "TMPDIR",           # temp dir redirect (TOCTOU attack surface)
    # Git injection (config files can trigger command execution)
    "GIT_CONFIG_",      # Git config injection
    "GIT_DIR",          # Git directory override
    "GIT_WORK_TREE",    # Git work tree override
    "GIT_INDEX_FILE",   # Git index file override
)


def _sanitize_env_blocklist(env: dict[str, str]) -> dict[str, str]:
    """Remove blocked environment variables from a dict (mutates in place)."""
    for key in list(env.keys()):
        for prefix in BLOCKED_ENV_PREFIXES:
            if key.upper().startswith(prefix.upper()):
                env.pop(key, None)
                break
    return env


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class OutputChunk:
    """A chunk of stdout or stderr data emitted during streaming."""
    stream: str          # "stdout" | "stderr"
    data: bytes
    cap_reached: bool = False


@dataclass
class ProcessExit:
    """Result of a completed (or killed/timed-out) process."""
    handle_id: str
    exit_code: int
    stdout: str
    stderr: str
    stdout_cap_reached: bool = False
    stderr_cap_reached: bool = False
    duration_ms: int = 0
    termination_reason: str | None = None  # "exited" | "timeout" | "killed" | ...


@dataclass
class WorkbenchProcessSnapshot:
    """Public read-only snapshot of a process (live or completed).

    Built from a ``WorkbenchProcessHandle`` and optional ``ProcessExit``.
    Used by workbench/process/list, read, and history.
    """
    client_id: str
    handle_id: str
    kind: str               # "commandExec" | "process"
    command: list[str]
    cwd: str
    running: bool
    exit_code: int | None
    started_at: float
    ended_at: float | None
    duration_ms: int
    stdin_enabled: bool
    stdout_bytes: int
    stderr_bytes: int
    stdout_cap_reached: bool
    stderr_cap_reached: bool
    termination_reason: str | None  # "exited" | "timeout" | "killed" | ...
    client_visible: bool = True


@dataclass
class WorkbenchProcessHandle:
    """Live handle for a running workbench process."""
    client_id: str
    handle_id: str
    kind: str          # "commandExec" | "process"
    process: asyncio.subprocess.Process
    cwd: Path
    command: list[str]
    stdin_enabled: bool = False
    output_cap: int | None = None
    stdout_cap_reached: bool = False
    stderr_cap_reached: bool = False
    client_visible: bool = True
    termination_reason: str | None = None
    _history_recorded: bool = False
    _stdout_buffer: bytearray = field(default_factory=bytearray)
    _stderr_buffer: bytearray = field(default_factory=bytearray)
    _started_at: float = field(default_factory=time.monotonic)

    @property
    def stdout_str(self) -> str:
        return bytes(self._stdout_buffer).decode("utf-8", errors="replace")

    @property
    def stderr_str(self) -> str:
        return bytes(self._stderr_buffer).decode("utf-8", errors="replace")


# Callback: async (handle_id, OutputChunk) -> None
OnChunkCallback = Callable[[str, OutputChunk], Any]


# ── Errors ───────────────────────────────────────────────────────────────


class WorkbenchProcessError(Exception):
    """Base error for workbench process operations."""
    def __init__(self, message: str, *, code: str = "INTERNAL"):
        super().__init__(message)
        self.code = code


class HandleNotFoundError(WorkbenchProcessError):
    def __init__(self, handle_id: str):
        super().__init__(
            f"Process handle not found: {handle_id}",
            code="NOT_FOUND",
        )


class HandleActiveError(WorkbenchProcessError):
    def __init__(self, handle_id: str):
        super().__init__(
            f"Process handle already active: {handle_id}",
            code="INVALID_REQUEST",
        )


class ClientMismatchError(WorkbenchProcessError):
    def __init__(self, handle_id: str):
        super().__init__(
            f"Process handle belongs to another client: {handle_id}",
            code="NOT_FOUND",
        )


class StdinNotAvailableError(WorkbenchProcessError):
    def __init__(self, handle_id: str):
        super().__init__(
            f"Process stdin is not available: {handle_id}",
            code="INVALID_REQUEST",
        )


# ── Runtime ──────────────────────────────────────────────────────────────


class WorkbenchProcessRuntime:
    """Client-scoped process registry and execution engine.

    All processes are keyed by (client_id, handle_id).  Cross-client
    access is rejected.  The runtime is typically stored in
    registry.bridge_context["workbench_process_runtime"] for handler
    access.
    """

    def __init__(self, *, workspace: Path, history_max: int = 200):
        self.workspace = workspace
        self._handles: dict[tuple[str, str], WorkbenchProcessHandle] = {}
        self._lock = asyncio.Lock()
        # Bounded per-client completed-process history
        self._history: dict[str, list[dict[str, object]]] = {}
        self._history_max = history_max

    # ── handle lookup ─────────────────────────────────────────────────

    def get_handle(
        self, client_id: str, handle_id: str,
    ) -> WorkbenchProcessHandle | None:
        return self._handles.get((client_id, handle_id))

    def _require_handle(
        self, client_id: str, handle_id: str,
        *, require_client_visible: bool = False,
    ) -> WorkbenchProcessHandle:
        handle = self._handles.get((client_id, handle_id))
        if handle is None:
            raise HandleNotFoundError(handle_id)
        if handle.client_id != client_id:
            raise ClientMismatchError(handle_id)
        if require_client_visible and not handle.client_visible:
            raise HandleNotFoundError(handle_id)
        return handle

    def _check_duplicate(self, client_id: str, handle_id: str) -> None:
        if (client_id, handle_id) in self._handles:
            raise HandleActiveError(handle_id)

    # ── snapshot / history ───────────────────────────────────────────────

    @staticmethod
    def _build_snapshot(
        handle: WorkbenchProcessHandle,
        *,
        exit_result: ProcessExit | None = None,
    ) -> dict[str, object]:
        """Build a public snapshot dict from a handle and optional exit result."""
        now = time.monotonic()
        if exit_result is not None:
            running = False
            exit_code: int | None = exit_result.exit_code
            ended_at: float | None = now
            duration_ms = exit_result.duration_ms
            termination_reason = exit_result.termination_reason
        else:
            running = handle.process.returncode is None
            exit_code = handle.process.returncode
            ended_at = None if running else now
            duration_ms = int((now - handle._started_at) * 1000)
            termination_reason = handle.termination_reason

        return {
            "clientId": handle.client_id,
            "handleId": handle.handle_id,
            "kind": handle.kind,
            "command": list(handle.command),
            "cwd": str(handle.cwd),
            "running": running,
            "exitCode": exit_code,
            "startedAt": handle._started_at,
            "endedAt": ended_at,
            "durationMs": duration_ms,
            "stdinEnabled": handle.stdin_enabled,
            "stdoutBytes": len(handle._stdout_buffer),
            "stderrBytes": len(handle._stderr_buffer),
            "stdoutCapReached": handle.stdout_cap_reached,
            "stderrCapReached": handle.stderr_cap_reached,
            "terminationReason": termination_reason,
            "clientVisible": handle.client_visible,
        }

    def _record_history(self, client_id: str, snapshot: dict[str, object]) -> None:
        """Append a completed process snapshot to the client's bounded history."""
        if client_id not in self._history:
            self._history[client_id] = []
        history = self._history[client_id]
        history.append(snapshot)
        # Enforce max bound
        while len(history) > self._history_max:
            history.pop(0)

    # ── query: list / read / history ─────────────────────────────────────

    def list_live(
        self, client_id: str, *, kind: str | None = None,
    ) -> list[dict[str, object]]:
        """List live processes for a client, optionally filtered by kind.

        Only returns client-visible handles.  Internal (non-client-visible)
        handles are excluded.
        """
        result: list[dict[str, object]] = []
        for (cid, _), handle in self._handles.items():
            if cid != client_id:
                continue
            if not handle.client_visible:
                continue
            if kind is not None and handle.kind != kind:
                continue
            result.append(self._build_snapshot(handle))
        return result

    def read_live(
        self, client_id: str, handle_id: str, *, include_output: bool = False,
    ) -> dict[str, object]:
        """Read a single live process snapshot for the calling client.

        Returns ``NOT_FOUND``-shaped error dict if the handle doesn't exist
        or belongs to another client.
        """
        handle = self._handles.get((client_id, handle_id))
        if handle is None or handle.client_id != client_id:
            raise HandleNotFoundError(handle_id)
        snapshot = self._build_snapshot(handle)
        if include_output:
            snapshot["stdout"] = handle.stdout_str
            snapshot["stderr"] = handle.stderr_str
        return snapshot

    def history(
        self, client_id: str, *, kind: str | None = None, limit: int = 50,
    ) -> dict[str, object]:
        """Return bounded completed-process history for the calling client.

        Returns ``{"processes": [...], "truncated": bool}``.
        """
        entries = self._history.get(client_id, [])
        if kind is not None:
            entries = [e for e in entries if e.get("kind") == kind]
        if limit < 1:
            limit = 1
        if limit > 200:
            limit = 200
        truncated = len(entries) > limit
        return {
            "processes": list(entries[-limit:]),
            "truncated": truncated,
        }

    # ── cleanup hooks for history recording ───────────────────────────

    async def spawn(
        self,
        *,
        client_id: str,
        handle_id: str,
        kind: str,
        command: list[str],
        cwd: Path,
        env: dict[str, str | None] | None = None,
        stdin_enabled: bool = False,
        output_cap: int | None = None,
        timeout_ms: int | None = None,
        on_chunk: OnChunkCallback | None = None,
        client_visible: bool = True,
    ) -> ProcessExit:
        """Spawn a process and wait for it to exit.

        Used by ``command/exec``.  Returns a ``ProcessExit`` when the
        process completes (or is killed / timed out).
        """
        async with self._lock:
            self._check_duplicate(client_id, handle_id)

            # Build environment — start from inherited env, strip
            # blocked keys, then apply user overrides.
            # string values override inherited keys; None values
            # remove inherited keys.
            process_env = _sanitize_env_blocklist(os.environ.copy())
            if env is not None:
                for key, value in env.items():
                    if value is not None:
                        process_env[key] = value
                    else:
                        process_env.pop(key, None)

            try:
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(cwd),
                    env=process_env,
                    stdin=asyncio.subprocess.PIPE if stdin_enabled else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                raise WorkbenchProcessError(
                    f"Command not found: {command[0]}",
                    code="INVALID_REQUEST",
                )
            except PermissionError:
                raise WorkbenchProcessError(
                    f"Permission denied: {command[0]}",
                    code="INVALID_REQUEST",
                )

            handle = WorkbenchProcessHandle(
                client_id=client_id,
                handle_id=handle_id,
                kind=kind,
                process=proc,
                cwd=cwd,
                command=list(command),
                stdin_enabled=stdin_enabled,
                output_cap=output_cap,
                client_visible=client_visible,
            )
            self._handles[(client_id, handle_id)] = handle

        # Run readers + timeout outside the lock
        try:
            exit_result = await self._run_process(
                handle=handle,
                timeout_ms=timeout_ms,
                on_chunk=on_chunk,
            )
        finally:
            async with self._lock:
                self._handles.pop((client_id, handle_id), None)

        # Record to history (client-visible processes, once only)
        if handle.client_visible and not handle._history_recorded:
            handle._history_recorded = True
            snapshot = self._build_snapshot(handle, exit_result=exit_result)
            self._record_history(client_id, snapshot)

        return exit_result

    # ── spawn_background (fire-and-forget) ─────────────────────────────

    async def spawn_background(
        self,
        *,
        client_id: str,
        handle_id: str,
        kind: str,
        command: list[str],
        cwd: Path,
        env: dict[str, str | None] | None = None,
        stdin_enabled: bool = False,
        output_cap: int | None = None,
        timeout_ms: int | None = None,
        on_chunk: OnChunkCallback | None = None,
        on_exit: Callable[[ProcessExit], Any] | None = None,
        client_visible: bool = True,
    ) -> WorkbenchProcessHandle:
        """Spawn a process in the background and return immediately.

        Used by ``process/spawn``.  Output is delivered via *on_chunk*
        and exit via *on_exit*.  The handle remains registered until
        the process exits.
        """
        async with self._lock:
            self._check_duplicate(client_id, handle_id)

            # Build environment — start from inherited env, strip
            # blocked keys, then apply user overrides.
            # string values override inherited keys; None values
            # remove inherited keys.
            process_env = _sanitize_env_blocklist(os.environ.copy())
            if env is not None:
                for key, value in env.items():
                    if value is not None:
                        process_env[key] = value
                    else:
                        process_env.pop(key, None)

            try:
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(cwd),
                    env=process_env,
                    stdin=asyncio.subprocess.PIPE if stdin_enabled else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                raise WorkbenchProcessError(
                    f"Command not found: {command[0]}",
                    code="INVALID_REQUEST",
                )
            except PermissionError:
                raise WorkbenchProcessError(
                    f"Permission denied: {command[0]}",
                    code="INVALID_REQUEST",
                )

            handle = WorkbenchProcessHandle(
                client_id=client_id,
                handle_id=handle_id,
                kind=kind,
                process=proc,
                cwd=cwd,
                command=list(command),
                stdin_enabled=stdin_enabled,
                output_cap=output_cap,
                client_visible=client_visible,
            )
            self._handles[(client_id, handle_id)] = handle

        async def _background_runner():
            try:
                exit_result = await self._run_process(
                    handle=handle,
                    timeout_ms=timeout_ms,
                    on_chunk=on_chunk,
                )
                # Record to history (client-visible processes, once only)
                if handle.client_visible and not handle._history_recorded:
                    handle._history_recorded = True
                    snapshot = self._build_snapshot(handle, exit_result=exit_result)
                    self._record_history(client_id, snapshot)
                if on_exit is not None:
                    try:
                        await on_exit(exit_result)
                    except Exception:
                        logger.exception(
                            "WorkbenchProcessRuntime: on_exit callback failed "
                            "for handle {}", handle_id,
                        )
            finally:
                async with self._lock:
                    self._handles.pop((client_id, handle_id), None)

        # Fire and forget — the task cleans itself up
        asyncio.create_task(_background_runner(), name=f"wpr-bg:{handle_id}")

        return handle

    # ── internal: run process until exit ───────────────────────────────

    async def _run_process(
        self,
        *,
        handle: WorkbenchProcessHandle,
        timeout_ms: int | None,
        on_chunk: OnChunkCallback | None,
    ) -> ProcessExit:
        proc = handle.process
        stdout_task: asyncio.Task | None = None
        stderr_task: asyncio.Task | None = None
        timeout_task: asyncio.Task | None = None
        cancelled = False

        async def _read_stream(
            stream_name: str,
            stream: asyncio.StreamReader | None,
            buffer: bytearray,
        ) -> bool:
            """Read a stream until EOF. Returns True if cap was reached.

            When the accumulated output reaches *output_cap* mid-chunk,
            the last accepted bytes are emitted with ``capReached=True``
            so the streaming client sees the transition.  Subsequent
            bytes are drained silently — no duplicate capReached events.
            """
            if stream is None:
                return False
            cap = handle.output_cap
            cap_reached = False
            try:
                while True:
                    chunk = await stream.read(4096)
                    if not chunk:
                        break
                    if cap_reached:
                        # Already at cap — drain remaining silently
                        continue
                    if cap is not None and len(buffer) + len(chunk) > cap:
                        # This chunk would exceed the cap — accept up to
                        # the cap boundary and emit with capReached=true.
                        remaining = cap - len(buffer)
                        if remaining > 0:
                            accepted = chunk[:remaining]
                            buffer.extend(accepted)
                            cap_reached = True
                            if on_chunk is not None:
                                try:
                                    await on_chunk(handle.handle_id, OutputChunk(
                                        stream=stream_name,
                                        data=accepted,
                                        cap_reached=True,
                                    ))
                                except Exception:
                                    logger.exception(
                                        "WorkbenchProcessRuntime: on_chunk failed "
                                        "for handle {}", handle.handle_id,
                                    )
                        else:
                            # Buffer already exactly at cap — emit an
                            # empty capReached delta so the client sees
                            # exactly one capReached=true notification.
                            cap_reached = True
                            if on_chunk is not None:
                                try:
                                    await on_chunk(handle.handle_id, OutputChunk(
                                        stream=stream_name,
                                        data=b"",
                                        cap_reached=True,
                                    ))
                                except Exception:
                                    logger.exception(
                                        "WorkbenchProcessRuntime: on_chunk failed "
                                        "for handle {}", handle.handle_id,
                                    )
                        continue
                    # Chunk fits entirely — emit normally
                    buffer.extend(chunk)
                    if on_chunk is not None:
                        try:
                            await on_chunk(handle.handle_id, OutputChunk(
                                stream=stream_name,
                                data=chunk,
                                cap_reached=False,
                            ))
                        except Exception:
                            logger.exception(
                                "WorkbenchProcessRuntime: on_chunk failed "
                                "for handle {}", handle.handle_id,
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "WorkbenchProcessRuntime: error reading {} for handle {}",
                    stream_name, handle.handle_id,
                )
            return cap_reached

        try:
            # Start stream readers
            stdout_task = asyncio.create_task(
                _read_stream("stdout", proc.stdout, handle._stdout_buffer),
                name=f"wpr-stdout:{handle.handle_id}",
            )
            stderr_task = asyncio.create_task(
                _read_stream("stderr", proc.stderr, handle._stderr_buffer),
                name=f"wpr-stderr:{handle.handle_id}",
            )

            # Set up timeout
            if timeout_ms is not None and timeout_ms > 0:
                async def _timeout_killer():
                    await asyncio.sleep(timeout_ms / 1000.0)
                    if proc.returncode is None:
                        logger.warning(
                            "WorkbenchProcessRuntime: timeout {}ms reached for {}, killing",
                            timeout_ms, handle.handle_id,
                        )
                        handle.termination_reason = "timeout"
                        await self._kill_process(proc)

                timeout_task = asyncio.create_task(
                    _timeout_killer(),
                    name=f"wpr-timeout:{handle.handle_id}",
                )

            # Wait for process exit
            await proc.wait()

        except asyncio.CancelledError:
            cancelled = True
            if handle.termination_reason is None:
                handle.termination_reason = "killed"
            await self._kill_process(proc)
        finally:
            # Cancel timeout if still pending
            if timeout_task is not None and not timeout_task.done():
                timeout_task.cancel()
                try:
                    await timeout_task
                except asyncio.CancelledError:
                    pass

            # Wait for stream readers to finish
            if stdout_task is not None:
                if not stdout_task.done():
                    stdout_task.cancel()
                try:
                    cap_stdout = await stdout_task
                except asyncio.CancelledError:
                    cap_stdout = False
                except Exception:
                    cap_stdout = False
                else:
                    handle.stdout_cap_reached = cap_stdout

            if stderr_task is not None:
                if not stderr_task.done():
                    stderr_task.cancel()
                try:
                    cap_stderr = await stderr_task
                except asyncio.CancelledError:
                    cap_stderr = False
                except Exception:
                    cap_stderr = False
                else:
                    handle.stderr_cap_reached = cap_stderr

        exit_code = proc.returncode if proc.returncode is not None else -1
        if cancelled:
            exit_code = -1

        # Default termination reason
        if handle.termination_reason is None:
            handle.termination_reason = "exited"

        duration_ms = int((time.monotonic() - handle._started_at) * 1000)

        return ProcessExit(
            handle_id=handle.handle_id,
            exit_code=exit_code,
            stdout=handle.stdout_str,
            stderr=handle.stderr_str,
            stdout_cap_reached=handle.stdout_cap_reached,
            stderr_cap_reached=handle.stderr_cap_reached,
            duration_ms=duration_ms,
            termination_reason=handle.termination_reason,
        )

    async def _kill_process(self, proc: asyncio.subprocess.Process) -> None:
        """Terminate then kill a process, ensuring it's dead."""
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
                return
            except asyncio.TimeoutError:
                pass
        except ProcessLookupError:
            return
        except Exception:
            pass

        # Force kill
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        except Exception:
            logger.exception(
                "WorkbenchProcessRuntime: error killing process pid={}",
                proc.pid,
            )

    # ── stdin ──────────────────────────────────────────────────────────

    async def write_stdin(
        self, client_id: str, handle_id: str, data: bytes,
        *, require_client_visible: bool = False,
    ) -> None:
        """Write bytes to the process stdin and drain."""
        handle = self._require_handle(
            client_id, handle_id, require_client_visible=require_client_visible,
        )
        if not handle.stdin_enabled or handle.process.stdin is None:
            raise StdinNotAvailableError(handle_id)
        try:
            handle.process.stdin.write(data)
            await handle.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            raise WorkbenchProcessError(
                f"Cannot write to process stdin: {e}",
                code="INVALID_REQUEST",
            )

    async def close_stdin(
        self, client_id: str, handle_id: str,
        *, require_client_visible: bool = False,
    ) -> None:
        """Close the process stdin pipe."""
        handle = self._require_handle(
            client_id, handle_id, require_client_visible=require_client_visible,
        )
        if handle.process.stdin is not None:
            handle.process.stdin.close()
            try:
                await handle.process.stdin.wait_closed()
            except Exception:
                pass

    # ── kill ───────────────────────────────────────────────────────────

    async def kill(
        self, client_id: str, handle_id: str,
        *, require_client_visible: bool = False,
    ) -> ProcessExit:
        """Kill a specific process by handle.

        Returns a ProcessExit with the final output state.  Raises
        HandleNotFoundError if no such handle exists.
        """
        handle = self._require_handle(
            client_id, handle_id, require_client_visible=require_client_visible,
        )
        handle.termination_reason = "killed"
        await self._kill_process(handle.process)
        # Wait briefly for readers to flush
        await asyncio.sleep(0.05)
        duration_ms = int((time.monotonic() - handle._started_at) * 1000)
        exit_result = ProcessExit(
            handle_id=handle.handle_id,
            exit_code=handle.process.returncode if handle.process.returncode is not None else -1,
            stdout=handle.stdout_str,
            stderr=handle.stderr_str,
            stdout_cap_reached=handle.stdout_cap_reached,
            stderr_cap_reached=handle.stderr_cap_reached,
            duration_ms=duration_ms,
            termination_reason=handle.termination_reason,
        )
        # Record to history (once only)
        if handle.client_visible and not handle._history_recorded:
            handle._history_recorded = True
            snapshot = self._build_snapshot(handle, exit_result=exit_result)
            self._record_history(client_id, snapshot)
        return exit_result

    # ── cleanup ────────────────────────────────────────────────────────

    async def kill_client(self, client_id: str) -> None:
        """Kill all active processes owned by *client_id*."""
        async with self._lock:
            handles = [
                h for (cid, _), h in self._handles.items()
                if cid == client_id
            ]
        for handle in handles:
            handle.termination_reason = "client_disconnected"
            try:
                await self._kill_process(handle.process)
            except Exception:
                pass
        # Note: handles are NOT popped here — the background runners
        # or spawn() finally blocks will pop them and record history.
        async with self._lock:
            keys_to_remove = [
                k for k in self._handles if k[0] == client_id
            ]
            for k in keys_to_remove:
                self._handles.pop(k, None)

    async def stop_all(self) -> None:
        """Kill every active process. Called on AppServer shutdown."""
        async with self._lock:
            handles = list(self._handles.values())
        for handle in handles:
            handle.termination_reason = "server_shutdown"
            try:
                await self._kill_process(handle.process)
            except Exception:
                pass
        async with self._lock:
            self._handles.clear()
