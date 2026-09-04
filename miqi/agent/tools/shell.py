"""Shell execution tool with bwrap sandbox support."""

import asyncio
import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from miqi.agent.tools.base import Tool
from miqi.execution.sandbox_policy import SandboxType
from miqi.protocol.events import (
    ExecCommandBeginEvent,
    ExecCommandEndEvent,
    ExecCommandOutputDeltaEvent,
)
from miqi.protocol.permissions import NetworkSandboxPolicy

# ── Internal result carrier ────────────────────────────────────────────


@dataclass
class _ExecResult:
    """Carries the output of a single command execution plus metadata
    needed to emit an accurate ExecCommandEndEvent."""

    output: str
    exit_code: int = 0
    duration_ms: int = 0
    cancelled: bool = False
    timed_out: bool = False
    timeout_ms: int | None = None
    sandbox_type: str = "none"


class _ExecHeartbeat:
    """Timer-driven progress heartbeat for long-running commands (#810).

    Emits a small :class:`ExecCommandOutputDeltaEvent` at most once per
    *interval* seconds while the command runs, so the bridge chat drain
    idle timeout (600 s) never ends the turn as a TIMEOUT while the
    command is still alive.  Real output chunks reset the throttle via
    :meth:`note_activity` — a chatty command naturally suppresses
    heartbeats; only silent stretches get them.  When the silence
    exceeds *idle_threshold*, the heartbeat text switches to a
    staleness warning (informational only — the execution timeout
    remains the kill backstop, never the idle signal).

    This generalises the install-routing heartbeat pattern
    (CodeRabbit #820) to every exec path.
    """

    def __init__(
        self,
        *,
        event_emitter,
        turn_id: str,
        tool_call_id: str,
        interval: float,
        idle_threshold: float,
        start_time: float,
    ) -> None:
        self._emitter = event_emitter
        self._turn_id = turn_id
        self._tool_call_id = tool_call_id
        self._interval = max(1.0, interval)
        self._idle_threshold = idle_threshold
        self._start = start_time
        self._last_output = time.monotonic()
        self._last_progress = time.monotonic()
        self._task: asyncio.Task | None = None

    def note_activity(self) -> None:
        """Called on real output chunks — resets the silence clock."""
        self._last_output = time.monotonic()

    async def start(self) -> None:
        if self._emitter is None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            # Adaptive sleep: when output is flowing, sleep until the
            # silence has lasted a full interval before waking — the
            # task costs nothing while the command is chatty (no
            # per-interval wakeups, no events).
            now = time.monotonic()
            next_check = max(now, self._last_output) + self._interval
            await asyncio.sleep(max(0.0, next_check - time.monotonic()))
            now = time.monotonic()
            # Throttle: at most one heartbeat per interval.
            if now - self._last_progress < self._interval:
                continue
            # Only silent stretches need a heartbeat — output deltas
            # already keep the drain alive.
            if now - self._last_output < self._interval:
                continue
            self._last_progress = now
            elapsed = int(now - self._start)
            silent = int(now - self._last_output)
            if silent >= self._idle_threshold:
                text = (
                    f"[exec] 命令已无输出 {silent}s，仍在运行"
                    f"（已运行 {elapsed}s）——静默可能属正常（如 pip 下载），"
                    f"将由执行超时兜底……\n"
                )
            else:
                text = f"[exec] 命令仍在运行（已运行 {elapsed}s）……\n"
            try:
                await self._emitter.emit(ExecCommandOutputDeltaEvent(
                    turn_id=self._turn_id,
                    tool_call_id=self._tool_call_id,
                    stream="stdout",
                    delta=text,
                ))
            except Exception:
                # A failed heartbeat must never kill the heartbeat task —
                # otherwise a silent long command loses ALL liveness
                # events mid-run and the bridge drain ends the turn as a
                # TIMEOUT (the exact failure this heartbeat prevents).
                logger.warning("exec heartbeat emit failed", exc_info=True)


# ── System package install routing (#759) ──────────────────────────────

#: Install-family commands eligible for routing to the WSL distro as root.
#: The bwrap sandbox is unprivileged (uid 1000) against read-only system
#: dirs, so apt-get can never install inside it.  The WSL distro the
#: sandbox ro-binds /usr, /lib, ... from IS the persistent root-capable
#: layer: installing there once makes the toolchain visible in every
#: sandbox session.  Only the install/update family matches — remove/purge
#: and any command that merely contains "apt-get" are NOT auto-routed.
#: Leading "yes |" / "sudo" / flag clusters (e.g. "sudo -n", "apt-get -y")
#: are tolerated because the distro run is already root.
_PKG_INSTALL_RE = re.compile(
    r"^(?:yes\s*\|\s*)?(?:sudo\s+)?(?:-{1,2}[^\s]+\s+)*(?:"
    r"apt-get\s+(?:-{1,2}[^\s]+\s+)*(?:update|upgrade|dist-upgrade|full-upgrade|install|reinstall)"
    r"|apt\s+(?:-{1,2}[^\s]+\s+)*(?:update|upgrade|dist-upgrade|full-upgrade|install|reinstall)"
    r"|dnf\s+(?:-{1,2}[^\s]+\s+)*(?:update|upgrade|install|reinstall)"
    r"|yum\s+(?:-{1,2}[^\s]+\s+)*(?:update|upgrade|install|reinstall)"
    r"|zypper\s+(?:-{1,2}[^\s]+\s+)*(?:update|upgrade|install)"
    r"|apk\s+(?:-{1,2}[^\s]+\s+)*(?:update|upgrade|add)"
    r"|pacman\s+-(?:S(?:yu|yy|y|u)?)"
    r")\b",
    re.IGNORECASE,
)

#: Routed installs run with their own generous timeout — texlive-xetex and
#: friends take minutes to fetch, far beyond the exec tool's default 60 s.
_SYSTEM_INSTALL_TIMEOUT = 1200.0  # seconds (20 min)

#: Progress-heartbeat interval for long routed installs.  The chat drain
#: idle timeout in the bridge is 600 s and the install budget is 1200 s,
#: so without periodic events a texlive-scale install would end the turn
#: with a TIMEOUT error while the root install kept running (CodeRabbit
#: #820).  One tiny delta every 30 s keeps the turn alive for the full
#: budget without flooding the frontend with dpkg output.
_INSTALL_PROGRESS_INTERVAL_SECONDS = 30.0

#: Bounded wait for the stream readers after the main process exited.
#: A grandchild that keeps the stdout/stderr pipe open prevents EOF, so
#: awaiting the reader unconditionally would hang the turn forever (the
#: #810 heartbeat would keep the drain alive all the while).  After this
#: timeout the accumulated text is discarded and the turn moves on.
_STREAM_DRAIN_TIMEOUT_SECONDS = 30.0

#: Tolerated prefix of a routed command: "yes |", "sudo", and leading flag
#: clusters ("sudo -n", "sudo --preserve-env").  Everything after the
#: prefix is normalized (see :meth:`ExecTool._normalize_system_install`):
#: only a bare install command with allowlisted flags survives.
_SYSTEM_INSTALL_PREFIX_RE = re.compile(
    r"^(?:yes\s*\|\s*)?(?:sudo\s+)?(?:-{1,2}[^\s]+\s+)*"
)

#: A package token may contain only these characters (package names, arch
#: qualifiers "pkg:amd64", version pins "pkg=1.2", repo qualifiers "pkg/rel").
#: No shell metacharacters.  Tokens starting with "-" are treated as flags
#: (rejected unless allowlisted); path-like tokens ("./x.deb", "~/x", "/x",
#: ...) are rejected separately in _normalize_system_install — a relative
#: .deb would execute the agent's own file as root via the manager's
#: maintainer scripts (review #759 P1).
_PKG_TOKEN_RE = re.compile(r"^[a-zA-Z0-9+._~:=/-]+$")

#: Flags a routed install may carry into the root distro run, per manager.
#: Everything else — apt -o/-c (Dir::Bin::dpkg, APT::Update::Pre-Invoke),
#: dnf --config/--installroot/--pluginconfpath, pacman --config/--hookdir,
#: zypper --root, and any "--flag=value" form — REFUSES the routing: the
#: distro run happens as root, so an option whose VALUE is a path can
#: execute arbitrary files as root via the manager's hooks/plugins/binaries
#: (security review #759 F1).  Values that merely tweak prompts/verbosity/
#: recommends are fine.
_SYSTEM_INSTALL_SAFE_FLAGS: dict[str, frozenset[str]] = {
    "apt-get": frozenset({
        "-y", "--assume-yes", "-n", "--no-install-recommends",
        "--no-install-suggests", "-q", "-qq", "--quiet", "--print-uris",
        "-f", "--fix-broken", "--only-upgrade", "-u",
    }),
    "apt": frozenset({
        "-y", "--assume-yes", "-n", "--no-install-recommends",
        "--no-install-suggests", "-q", "-qq", "--quiet", "--print-uris",
        "-f", "--fix-broken", "--only-upgrade", "-u",
    }),
    "dnf": frozenset({
        "-y", "--assume-yes", "-q", "--quiet", "--print-uris", "--nobest",
        "--skip-broken",
    }),
    "yum": frozenset({"-y", "--assume-yes", "-q", "--quiet", "--skip-broken"}),
    "zypper": frozenset({
        "-y", "-n", "--non-interactive", "-q", "--quiet", "--no-recommends",
    }),
    "apk": frozenset({"--no-cache", "-q", "--quiet", "--no-progress"}),
    "pacman": frozenset({
        "--noconfirm", "--needed", "-q", "--quiet", "--print", "-p",
        "--asdeps", "--asexplicit",
    }),
}

#: Verb sets per manager (mirrors _PKG_INSTALL_RE's install/update family).
#: ``dist-upgrade``/``full-upgrade`` are deliberately absent: they remove
#: packages and rewrite the whole system (kernel, conflicting packages) as
#: root on the REAL distro — a blast radius far beyond "install a toolchain",
#: and it would smuggle remove capability in through the back door (review
#: #759 N1).  They still classify as install-family (see _PKG_INSTALL_RE) so
#: they reach the routing chain and get a specific refusal message instead
#: of falling through to a confusing in-sandbox failure.
_SYSTEM_INSTALL_VERBS: dict[str, tuple[str, ...]] = {
    "apt-get": ("update", "upgrade", "install", "reinstall"),
    "apt": ("update", "upgrade", "install", "reinstall"),
    "dnf": ("update", "upgrade", "install", "reinstall"),
    "yum": ("update", "upgrade", "install", "reinstall"),
    "zypper": ("update", "upgrade", "install"),
    "apk": ("update", "upgrade", "add"),
}

#: Interception message when install commands are attempted but
#: tools.sandbox.allow_system_installs is disabled — explains the real
#: path instead of the generic guard rejection.
_SYSTEM_INSTALL_NOT_ENABLED_MSG = (
    "Error: 系统包安装命令被拦截——沙箱内无 root 权限且系统目录只读，"
    "apt-get 无法在沙箱内安装。\n"
    "请在 设置 > 沙箱隔离 中开启「允许系统包安装」后重试，或在授权确认卡中选择"
    "「允许本次安装」：开启后 sudo apt-get install ... 会自动以 root 在 WSL "
    "发行版中执行，安装一次跨会话持久，装完即可在沙箱内使用。"
)

#: 系统安装授权卡的应用级串行锁（CodeRabbit #875 09-01 review）：跨
#: ExecTool 实例（不同会话/registry）的弹卡必须全局串行——per-instance 锁
#: 只挡同一实例，不同会话并发安装时非可见会话的卡会静默超时而非排队。
#: asyncio.Lock 绑定事件循环，这里按运行中 loop 惰性创建（生产 = bridge
#: 单 loop → 全局一把锁；测试 = per-test loop → 各自新锁）。
_system_install_approval_lock: asyncio.Lock | None = None
_system_install_approval_lock_loop: asyncio.AbstractEventLoop | None = None


def _get_system_install_approval_lock() -> asyncio.Lock:
    """Return the application-wide serialization lock for approval cards.

    The lock is bound to the CURRENT event loop: asyncio.Lock is loop
    bound, so a lock created on another loop cannot be awaited here.  A
    new lock is created when the loop changes.

    ARCHITECTURE ASSUMPTION (#875 review): this is a true application
    global ONLY because the production runtime guarantees a single
    persistent event loop (BridgeRuntimeLoop owns every runtime/registry
    on one loop).  If a future multi-loop runtime (threads, process
    pools) is introduced, this degrades to per-loop serialization and
    two approval cards could reach the foreground concurrently — revisit
    this (e.g. a cross-loop lock) before enabling such a runtime.
    """
    global _system_install_approval_lock, _system_install_approval_lock_loop
    loop = asyncio.get_running_loop()
    if (
        _system_install_approval_lock is None
        or _system_install_approval_lock_loop is not loop
    ):
        _system_install_approval_lock = asyncio.Lock()
        _system_install_approval_lock_loop = loop
    return _system_install_approval_lock

#: Interception message when the approval card WAS shown and the user
#: declined or the card timed out — the NOT_ENABLED message suggests using
#: the card, which the user just rejected, so it must not be reused here
#: (#875 review P3-2).
_SYSTEM_INSTALL_DENIED_MSG = (
    "Error: 系统包安装授权未通过（拒绝或超时），本次安装未执行。\n"
    "如需继续，请重新发起命令后在授权确认卡中选择「允许本次安装」，"
    "或在 设置 > 沙箱隔离 中开启「允许系统包安装」。"
)

#: Interception message when system installs are enabled but the sandbox
#: runs on native Linux (no rootful WSL distro layer to install into).
_SYSTEM_INSTALL_WSL_ONLY_MSG = (
    "Error: 系统包安装仅支持 Windows + WSL 环境（需要 rootful 的 WSL 发行版，"
    "沙箱系统目录从该发行版 ro-bind）。当前沙箱运行在原生 Linux 上，"
    "无法路由系统安装；请改用用户级安装（如 pip install --user、"
    "工具链源码本地构建），或让用户在 Windows + WSL 环境下使用此功能。"
)

#: Interception message when a routed command is not a single, option-safe
#: install command: shell compounds (&&/;/|/redirects/...) and un-allowlisted
#: package-manager options (apt -o/-c, dnf --config/--installroot, pacman
#: --config/--hookdir, zypper --root, ...) are refused — the command would
#: run as root on the REAL distro filesystem.
_SYSTEM_INSTALL_SINGLE_MSG = (
    "Error: 命令被安全护栏拦截（系统安装路由只接受单一安装命令："
    "包管理器 + 操作 + 包名，仅允许 -y/--non-interactive 等安全选项，"
    "不允许复合命令或自定义选项）——安装命令会以 root 在 WSL 发行版中"
    "执行，不允许附加其他 shell 操作。"
)

#: Interception message for apt dist-upgrade/full-upgrade — they remove
#: packages and rewrite the whole distro (kernel, conflicts) as root, a
#: blast radius far beyond installing a toolchain (review #759 N1).
_SYSTEM_INSTALL_DISTUPGRADE_MSG = (
    "Error: 命令被安全护栏拦截——dist-upgrade/full-upgrade 会以 root 在"
    "WSL 发行版中移除/替换系统包（含内核），破坏面远超安装工具链，"
    "不在系统安装路由的允许范围内。允许的操作：update、upgrade、install、"
    "reinstall（以及各包管理器的对应安装家族动词）。"
)

#: Interception message when the routing chain cannot resolve a live
#: bwrap sandbox to attach the install to — previously this silently fell
#: through to the normal path, where a missing sandbox degrades to running
#: the command in Windows cmd ("sudo is not recognized") despite the exec
#: environment telling the agent installs are routed (review #759 N2).
_SYSTEM_INSTALL_NO_SANDBOX_MSG = (
    "Error: 系统包安装命令被拦截——当前没有可用的 bwrap 沙箱"
    "（沙箱创建/启动失败或未激活），无法路由到 WSL 发行版以 root 执行。"
    "安装命令未执行。请先确认沙箱环境正常后重试。"
)

#: Interception message when the policy engine selected a network-blocked
#: execution for this command.  A distro-side install MUST download
#: packages; routing it would fetch as root against the policy's explicit
#: fail-closed choice, so the routing refuses instead (CodeRabbit #820).
#: Currently defensive — the policy engine only emits BLOCK_ALL for
#: RESTRICTED selections, which the routing never overrides — but the
#: routed path must never become the weaker path if that changes.
_SYSTEM_INSTALL_NO_NETWORK_MSG = (
    "Error: 系统包安装命令被拦截——当前沙箱策略禁止网络访问"
    "（NetworkSandboxPolicy.BLOCK_ALL），而安装必须在 WSL 发行版中"
    "联网下载软件包。请在权限配置中允许网络后重试。"
)


class ExecTool(Tool):
    """Tool to execute shell commands, optionally inside a bwrap sandbox."""

    def __init__(
        self,
        timeout: int = 60,
        max_timeout: int = 1800,
        idle_timeout: float = 90.0,
        heartbeat_interval: float = 30.0,
        kill_grace_seconds: float = 5.0,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        env_passthrough: list[str] | None = None,
        approval_callback=None,
        sandbox_manager=None,
        system_install_approver=None,
    ):
        self.timeout = timeout
        self.max_timeout = max_timeout
        self.idle_timeout = idle_timeout
        self.heartbeat_interval = heartbeat_interval
        self.kill_grace_seconds = kill_grace_seconds
        self.working_dir = working_dir
        self.env_passthrough: frozenset[str] = frozenset(env_passthrough or [])
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",  # rm -r, rm -rf, rm -fr
            r"\bdel\s+/[fq]\b",  # del /f, del /q
            r"\brmdir\s+/s\b",  # rmdir /s
            r"(?:^|[;&|]\s*)format\b",  # format (as standalone command only)
            r"\b(mkfs|diskpart)\b",  # disk operations
            r"\bdd\s+if=",  # dd
            r">\s*/dev/sd",  # write to disk
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",  # fork bomb
            r"\bsudo\b",  # privilege escalation
            r"\beval\b",  # code/string evaluation
            r"\bsource\b",  # source external scripts
            r"`[^`\n]{1,500}`",  # backtick command substitution
            r"\$\([^)\n]{1,500}\)",  # $() command substitution
            r"\|\s*(ba|da|z|fi|c)?sh\b",  # pipe to any shell variant
            r"\b(?:curl|wget)\b[^;\n]{0,200}\|\s*python[23]?\b",  # download-and-execute via Python
        ]
        # System-install routing (#759) runs the command as root in the WSL
        # distro, so it re-checks the deny patterns EXCEPT sudo — the
        # routed command legitimately starts with sudo/apt-get.  Any other
        # dangerous pattern (rm -rf, disk ops, pipe-to-shell, ...) refuses
        # the routing instead of letting it run as root.
        self._deny_patterns_without_sudo = [
            p for p in self.deny_patterns if "sudo" not in p
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.approval_callback = approval_callback
        self._sandbox_manager = sandbox_manager
        # #854: 系统包安装授权通道——关闭状态下拦截点弹确认卡而非直接拒绝。
        # 签名: async (command: str) -> "once" | "always" | "deny" |
        # "deny_no_channel"。fail-closed: 无通道/异常/超时一律 deny（外部
        # 审阅 #854；#875 review F3 增加 deny_no_channel 区分"卡从未出现"）。
        self.system_install_approver = system_install_approver

    @property
    def name(self) -> str:
        return "exec"

    @property
    def execution_timeout(self) -> float | None:
        """Outer backstop for ToolRegistry's ``asyncio.wait_for``.

        ExecTool manages its own execution budget internally (per-call
        ``timeout`` arg / configured default, with process-tree kill and
        structured results), so the registry-level wrapper must never
        truncate a long command at its 120 s default.  Returning the max
        budget keeps ``wait_for`` as a pure last-resort guard while the
        real timeout semantics stay inside the tool (#810).

        The backstop must sit AFTER the tool's own cleanup window —
        when ``timeout == max_timeout`` the tool needs
        kill_grace + bounded stream drains (2 × 30 s) to return its
        structured timeout result; an equal outer wait_for would cancel
        the tool mid-cleanup and replace the structured result with a
        bare TimeoutError (#845 review).
        """
        return (
            float(self.max_timeout)
            + self.kill_grace_seconds
            + 2 * _STREAM_DRAIN_TIMEOUT_SECONDS
            + 5.0  # scheduling margin
        )

    def _normalize_timeout(self, raw: Any) -> tuple[int | None, str | None]:
        """Validate a per-call ``timeout`` request (#810).

        Returns ``(timeout_ms, error_message)``.  ``None`` timeout means
        "use the configured default".  Requests above ``max_timeout``
        are REJECTED (never silently clamped) so the model learns the
        ceiling and can split the task instead.
        """
        if raw is None:
            return None, None
        try:
            # Strict integer seconds: fractional floats ("3.7") and numeric
            # strings ("10") are not accepted — the model must learn the
            # exact unit.  bool is an int subclass — True must not slip
            # through as 1s.  Integral floats (3.0) remain accepted.
            if (
                isinstance(raw, bool)
                or isinstance(raw, str)
                or (isinstance(raw, float) and not raw.is_integer())
            ):
                raise ValueError
            requested = int(raw)
        except (TypeError, ValueError):
            return None, f"Error: 参数 timeout 必须是整数秒，收到 {raw!r}。"
        if requested < 1:
            return None, f"Error: 参数 timeout 必须 ≥ 1 秒，收到 {requested}。"
        if requested > self.max_timeout:
            return None, (
                f"Error: 请求的超时时间 {requested} 秒超过上限 "
                f"{self.max_timeout} 秒（{self.max_timeout // 60} 分钟）。"
                "请拆分任务或使用更小的超时。"
            )
        return requested * 1000, None

    @property
    def description(self) -> str:
        from miqi.sandbox.manager import describe_exec_environment

        return (
            "Execute a shell command and return its output. "
            "Use with caution. "
            + describe_exec_environment(self._sandbox_manager, workspace=self.working_dir)
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command",
                },
                "timeout": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": self.max_timeout,
                    "description": (
                        f"执行超时（秒）。默认 {self.timeout} 秒，最长 "
                        f"{self.max_timeout} 秒（{self.max_timeout // 60} 分钟）。"
                        "长任务（如 pip install、LaTeX 编译、PDF 渲染、并发网络检查）"
                        "请显式传入足够的时间，避免任务被截断；超过上限的请求会被拒绝。"
                    ),
                },
            },
            "required": ["command"],
        }

    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()

        # Phase 21: extract runtime-injected event emitter and metadata
        event_emitter = kwargs.pop("_event_emitter", None)
        turn_id = kwargs.pop("_turn_id", "")
        tool_call_id = kwargs.pop("_tool_call_id", "")
        cancel_event = kwargs.pop("_cancel_event", None)

        # Phase 42: extract exec source tag (shell vs userShell)
        exec_source = kwargs.pop("_exec_source", "shell")

        # #810: per-call execution timeout (seconds).  None → configured
        # default; over max_timeout → rejected before the command starts.
        timeout_arg = kwargs.pop("timeout", None)
        requested_timeout_ms, timeout_error = self._normalize_timeout(timeout_arg)

        # Phase 31.8: consume ledger runtime and thread_id injected by
        # ToolOrchestrator for replay-persistent event recording.
        ledger_runtime = kwargs.pop("_ledger_runtime", None)
        thread_id = kwargs.pop("_thread_id", "")

        # Phase 31: consume SandboxSelection injected by ToolOrchestrator.
        _sandbox = kwargs.pop("_sandbox", None)
        _session_key = kwargs.pop("_session_key", None)

        # Resolve sandbox_type for the begin event from the actual selection
        if _sandbox is not None:
            sandbox_type = _sandbox.sandbox_type.value
        elif self._sandbox_manager is not None:
            sandbox_type = "bwrap"
        else:
            sandbox_type = "none"

        # Phase 21: emit exec begin event
        if event_emitter is not None:
            await event_emitter.emit(
                ExecCommandBeginEvent(
                    turn_id=turn_id,
                    tool_call_id=tool_call_id,
                    command=command,
                    cwd=cwd,
                    sandbox_type=sandbox_type,
                    source=exec_source,
                )
            )

        # Phase 31.8: record exec start in ledger for replay
        if ledger_runtime is not None:
            await ledger_runtime.append_item(
                thread_id=thread_id,
                turn_id=turn_id,
                item_type="exec_started",
                payload={
                    "tool_call_id": tool_call_id,
                    "command": command,
                    "cwd": cwd,
                    "sandbox_type": sandbox_type,
                    "source": exec_source,
                },
            )

        # Phase 31.5: exec end event needs a single exit point.
        # _ExecResult carries output + metadata so the end event is accurate.
        async def _run() -> _ExecResult:
            # #810: invalid / over-limit timeout requests are rejected
            # BEFORE routing, approval or subprocess spawn — the model
            # gets a clear error and the command never runs.
            if timeout_error is not None:
                return _ExecResult(output=timeout_error, exit_code=1)

            # Phase 77 (#759): system package install routing.  The bwrap
            # sandbox cannot install system packages (unprivileged uid,
            # read-only /usr /var /etc), so install-family commands are
            # routed to the WSL distro as root when the user enabled
            # tools.sandbox.allow_system_installs — or intercepted with a
            # clear explanation when not.  Returns a result (handled) or
            # None (proceed with normal execution below).
            routed_result = await self._maybe_route_system_install(
                command,
                sandbox_selection=_sandbox,
                session_key=_session_key,
                cancel_event=cancel_event,
                event_emitter=event_emitter,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                requested_timeout_ms=requested_timeout_ms,
            )
            if routed_result is not None:
                return routed_result

            # If desktop approval callback is wired in, use the full
            # approval system.  Otherwise fall back to the static guard.
            if self.approval_callback is not None:
                import functools

                from miqi.agent.command_approval import check_dangerous_command
                loop = asyncio.get_event_loop()
                check_fn = functools.partial(
                    check_dangerous_command,
                    command,
                    approval_callback=self.approval_callback,
                )
                approval_result = await loop.run_in_executor(None, check_fn)
                if not approval_result.get("approved", True):
                    msg = approval_result.get(
                        "message",
                        "Error: 命令被拦截——用户拒绝了审批。",
                    )
                    return _ExecResult(output=msg, exit_code=1)
            else:
                # Guard runs before any sandbox creation — sandbox_active
                # only changes PATH SEMANTICS (sandbox overlays vs host paths).
                guard_error = self._guard_command(
                    command, cwd,
                    sandbox_active=(
                        (
                            _sandbox is not None
                            and getattr(_sandbox, "sandbox_type", None)
                            == SandboxType.BWRAP
                        )
                        or (
                            # Only the legacy no-selection path may fall
                            # back to the manager's active sandbox — a
                            # NONE/RESTRICTED selection executes on the
                            # HOST and must keep host path semantics
                            # (issue #811 review).
                            _sandbox is None
                            and self._sandbox_manager is not None
                            and getattr(
                                self._sandbox_manager, "active_sandbox", None,
                            ) is not None
                        )
                    ),
                )
                if guard_error:
                    return _ExecResult(output=guard_error, exit_code=1)

            # Phase 31.6: if cancel_event is already set before we start,
            # return immediately without spawning a subprocess.
            if cancel_event is not None and cancel_event.is_set():
                return _ExecResult(
                    output="Error: 命令在启动前被取消。",
                    exit_code=-1, cancelled=True,
                )

            # Phase 59 (#607): snapshot the host workspace BEFORE exec so files
            # created/modified by subprocesses (scripts writing via open(),
            # not `>` redirects) can be tracked as write assets afterwards.
            # Without this, router-style pipelines generate deliverables the
            # Task Assets panel never sees — they showed up only as read
            # (process) entries when the agent later inspected them.
            # Snapshot the exec's cwd (not just the global workspace) so
            # artifacts written to a custom workspace are diffed too (#682).
            # Off-loop: os.walk over a large workspace must not stall the
            # bridge event loop (CodeRabbit #682 review).
            before = await asyncio.to_thread(self._snapshot_workspace, cwd)

            # ── common args shared by every execution path ──────────
            exec_kwargs = dict(
                event_emitter=event_emitter,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                cancel_event=cancel_event,
                # #810: per-call timeout (ms) overrides the configured
                # default / sandbox selection default; None keeps them.
                timeout_ms=requested_timeout_ms,
                # Phase 31.8: ledger runtime and thread_id for replay
                ledger_runtime=ledger_runtime,
                thread_id=thread_id,
                # Session key for per-session sandbox isolation
                session_key=_session_key,
            )

            # Phase 31: if ToolOrchestrator injected a SandboxSelection,
            # it is the single source of truth for how this command runs.
            # ExecTool MUST follow it — no independent sandbox decision.
            if _sandbox is not None:
                result = await self._execute_with_sandbox_selection(
                    _sandbox, command, cwd, **exec_kwargs,
                )
            # Legacy path (no orchestrator): session_key preferred, fall back to active sandbox
            elif self._sandbox_manager is not None:
                if _session_key:
                    sandbox = await self._sandbox_manager.get_or_create(_session_key)
                else:
                    sandbox = self._sandbox_manager.active_sandbox
                    if not sandbox or not sandbox.is_running:
                        sandbox = None
                if sandbox and sandbox.is_running:
                    result = await self._execute_in_sandbox(
                        sandbox, command, cwd, **exec_kwargs,
                    )
                else:
                    # Legacy fallback (no sandbox): same host-semantics
                    # re-check as the BWRAP fallback (issue #811 review).
                    fallback_guard = self._guard_host_fallback(command, cwd)
                    if fallback_guard is not None:
                        return fallback_guard
                    # Fall back to direct execution (no sandbox)
                    result = await self._execute_direct(command, cwd, **exec_kwargs)
            else:
                # Fall back to direct execution (no sandbox)
                result = await self._execute_direct(command, cwd, **exec_kwargs)

            # Phase 59 (#607): track subprocess-created files in the host
            # workspace as write assets. Only runs for successful commands —
            # partial/failed output is not a deliverable. Private sandbox
            # copies (non bind-mounted) never reach the host, so their
            # outputs stay out of scope (#507 semantics).
            if result.exit_code == 0:
                await self._track_workspace_changes(before, _session_key, cwd)
            return result

        exec_result = await _run()

        # Phase 31.5: emit exec end event with real metadata.
        if event_emitter is not None:
            await event_emitter.emit(ExecCommandEndEvent(
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                exit_code=exec_result.exit_code,
                duration_ms=exec_result.duration_ms,
                output_size=len(exec_result.output),
            ))

        # Phase 31.8: record exec completion in ledger for replay,
        # including terminal status flags (timeout, cancel, non-zero exit).
        if ledger_runtime is not None:
            await ledger_runtime.append_item(
                thread_id=thread_id,
                turn_id=turn_id,
                item_type="exec_completed",
                payload={
                    "tool_call_id": tool_call_id,
                    "exit_code": exec_result.exit_code,
                    "duration_ms": exec_result.duration_ms,
                    "output_size": len(exec_result.output),
                    "cancelled": exec_result.cancelled,
                    "timed_out": exec_result.timed_out,
                },
            )

        # Phase 47: mirror files created by curl/wget from sandbox to host
        # so they survive sandbox cleanup and appear in Task Assets.
        if _sandbox is not None and exec_result.exit_code == 0:
            try:
                await self._mirror_downloaded_files(
                    command, _sandbox, _session_key,
                )
            except Exception:
                logger.warning("exec: file mirroring failed", exc_info=True)

        return exec_result.output

    async def _execute_in_sandbox(
        self, sandbox, command: str, cwd: str,
        *,
        timeout_ms: int | None = None,
        env_passthrough: list[str] | None = None,
        event_emitter=None,
        turn_id: str = "",
        tool_call_id: str = "",
        cancel_event: asyncio.Event | None = None,
        # Phase 31.8: ledger runtime for replay-persistent event recording
        ledger_runtime=None,
        thread_id: str = "",
        session_key: str | None = None,
    ) -> _ExecResult:
        """Execute a command inside the bwrap sandbox with streaming I/O.

        Phase 33.2: Uses ``sandbox.run_command_streaming()`` for incremental
        stdout/stderr, emits :class:`ExecCommandOutputDeltaEvent`, and
        supports ``cancel_event`` and timeout with process-group kill.

        Follows the same internal task pattern as :meth:`_execute_direct`
        (proc_wait, cancel_wait, stdout_task, stderr_task) so cancel/timeout
        behaviour is consistent across sandboxed and direct execution paths.
        """
        effective_timeout = (timeout_ms / 1000) if timeout_ms else self.timeout
        effective_env_passthrough: frozenset[str]
        if env_passthrough is not None:
            effective_env_passthrough = frozenset(env_passthrough)
        else:
            effective_env_passthrough = self.env_passthrough

        # Phase 31.6: honour cancel_event before starting sandbox work.
        if cancel_event is not None and cancel_event.is_set():
            return _ExecResult(
                output="Error: 命令在沙箱启动前被取消。",
                exit_code=-1, cancelled=True,
            )

        start = time.monotonic()

        # Build sandbox env and cwd
        sandbox_cwd = self._resolve_sandbox_cwd(cwd)
        sandbox_env = sandbox.get_sandbox_env()
        if effective_env_passthrough:
            safe_env = self._build_safe_env(extra_passthrough=list(effective_env_passthrough))
            for k in effective_env_passthrough:
                if k in safe_env and k not in sandbox_env:
                    sandbox_env[k] = safe_env[k]

        # Phase 33.2: use streaming API for incremental I/O + cancel support
        try:
            handle = await sandbox.run_command_streaming(
                command, env=sandbox_env, cwd=sandbox_cwd,
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error("Sandbox execution failed: {} — {}", type(e).__name__, e)
            return _ExecResult(
                output=(
                    f"Error: 沙箱执行失败——{type(e).__name__}：{e}\n"
                    f"Hint: You are running inside a Linux sandbox. Use Linux-style "
                    f"paths (e.g. /home/miqi/workspace/) and Linux commands."
                ),
                exit_code=1,
                duration_ms=duration_ms,
            )

        # ── Launch all internal tasks (same pattern as _execute_direct) ──
        # #810: heartbeat keeps the bridge drain (600 s idle) alive during
        # silent long-running sandboxed commands.
        heartbeat = _ExecHeartbeat(
            event_emitter=event_emitter,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            interval=self.heartbeat_interval,
            idle_threshold=self.idle_timeout,
            start_time=start,
        )
        await heartbeat.start()
        stdout_task: asyncio.Task = asyncio.create_task(
            self._read_stream(
                handle.stdout, "stdout",
                event_emitter=event_emitter,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                ledger_runtime=ledger_runtime,
                thread_id=thread_id,
                on_chunk=heartbeat.note_activity,
            ),
        )
        stderr_task: asyncio.Task = asyncio.create_task(
            self._read_stream(
                handle.stderr, "stderr",
                event_emitter=event_emitter,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                ledger_runtime=ledger_runtime,
                thread_id=thread_id,
                on_chunk=heartbeat.note_activity,
            ),
        )
        proc_wait: asyncio.Task = asyncio.create_task(handle.wait())
        cancel_wait: asyncio.Task | None = None
        stdout_text = ""
        stdout_trunc = False
        stderr_text = ""
        stderr_trunc = False

        cancelled = False
        timed_out = False
        # #810: same semantics as _execute_direct — the finally cleanup
        # must not re-kill when the cancel/timeout branch handled it.
        kill_attempted = False

        try:
            if cancel_event is not None:
                cancel_wait = asyncio.create_task(cancel_event.wait())
                done, _ = await asyncio.wait(
                    [proc_wait, cancel_wait],
                    timeout=effective_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # #810: same-tick completion wins over cancel (see
                # _execute_direct for rationale).
                if cancel_wait in done and proc_wait not in done:
                    cancelled = True
                elif not done:
                    timed_out = True

                # ── Normal completion: cancel_wait was *not* set — clean up ──
                if cancel_wait is not None and not cancel_wait.done():
                    cancel_wait.cancel()
                    try:
                        await cancel_wait
                    except asyncio.CancelledError:
                        pass
            else:
                try:
                    await asyncio.wait_for(proc_wait, timeout=effective_timeout)
                except asyncio.TimeoutError:
                    timed_out = True

            # ── Cancel / timeout: kill process group, then await proc_wait ──
            if cancelled or timed_out:
                # #845 review: same execution/cleanup split as the direct
                # path — snapshot before kill+drain so the timeout result
                # reports real execution time.
                timeout_triggered_ms = int((time.monotonic() - start) * 1000)
                kill_attempted = True
                await handle.kill()
                if not proc_wait.done():
                    try:
                        await proc_wait
                    except Exception:
                        pass

            # ── Wait for stream readers — they see EOF when pipes close ──
            # #845 review: bound the drain like the direct path — a
            # grandchild holding the pipe open would otherwise keep the
            # reader alive forever and hang the turn past its timeout.
            try:
                stdout_text, stdout_trunc = await asyncio.wait_for(
                    stdout_task, timeout=_STREAM_DRAIN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                stdout_task.cancel()
                try:
                    await stdout_task
                except (asyncio.CancelledError, Exception):
                    pass
                stdout_text, stdout_trunc = "", True
            try:
                stderr_text, stderr_trunc = await asyncio.wait_for(
                    stderr_task, timeout=_STREAM_DRAIN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                stderr_task.cancel()
                try:
                    await stderr_task
                except (asyncio.CancelledError, Exception):
                    pass
                stderr_text, stderr_trunc = "", True

        finally:
            # ── Stop the heartbeat — the command is done or dying. ──
            await heartbeat.stop()

            # #810: if the sandbox process is still alive here (outer
            # cancellation such as ToolRegistry's asyncio.wait_for, or an
            # unexpected error), kill it so no orphan survives.
            # NB: check handle.returncode (None = still running), NOT
            # proc_wait.done() — wait_for cancels the inner wait task and
            # a cancelled task reports done() == True while the process
            # is very much alive.
            if not kill_attempted and handle.returncode is None:
                try:
                    await handle.kill()
                except Exception:
                    logger.warning(
                        "exec: failed to kill sandbox process on abnormal exit",
                        exc_info=True,
                    )

            # ── Safety net — NO task survives this method ────────────
            for task in (cancel_wait, proc_wait, stdout_task, stderr_task):
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

            # ── Release sandbox temporary resources (WSL script file) ───────
            try:
                await handle.cleanup()
            except Exception:
                pass

        duration_ms = int((time.monotonic() - start) * 1000)
        exit_code = handle.returncode if handle.returncode is not None else -1

        # Log sandbox command failures
        cmd_summary = command[:200] + "…" if len(command) > 200 else command

        # ── Build result text ─────────────────────────────────────────
        if cancelled:
            logger.info("Sandbox command cancelled after {}ms: {}", duration_ms, cmd_summary)
            return _ExecResult(
                output="Error: 命令已被用户取消。",
                exit_code=exit_code, duration_ms=duration_ms,
                cancelled=True,
            )
        if timed_out:
            logger.error("Sandbox command timed out after {}ms: {}", duration_ms, cmd_summary)
            timeout_meta = {
                "status": "timeout",
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "execution_duration_ms": timeout_triggered_ms,
                "cleanup_duration_ms": max(0, duration_ms - timeout_triggered_ms),
                "timeout_ms": int(effective_timeout * 1000),
                "command": command[:200],
                "process_terminated": True,
                "retryable": True,
            }
            out = (
                f"Error: 命令执行超时（已运行 {timeout_triggered_ms / 1000:.1f}s，"
                f"超时上限 {effective_timeout:.0f}s，进程已终止）\n"
                + json.dumps(timeout_meta, ensure_ascii=False)
            )
            # Include the tail of whatever the command printed before it
            # died — the model can see what it was doing and recover.
            partial = stdout_text
            if stderr_text and stderr_text.strip():
                partial = f"{partial}\nSTDERR:\n{stderr_text}"
            if partial.strip():
                out += "\n\n[超时前的部分输出（末尾 2000 字符）]\n" + partial[-2000:]
            out += (
                "\n建议：1. 增大 timeout 参数后重试；"
                "2. 将任务拆分为更小的步骤；"
                "3. 超过 30 分钟的任务请分批执行。"
            )
            return _ExecResult(
                output=out,
                exit_code=exit_code, duration_ms=duration_ms,
                timed_out=True, timeout_ms=int(effective_timeout * 1000),
            )

        if exit_code != 0:
            cmd_short = command[:100] + "…" if len(command) > 100 else command
            logger.warning("Sandbox command failed exit={} duration={}ms: {}", exit_code, duration_ms, cmd_short)

        trunc_note = ""
        if stdout_trunc or stderr_trunc:
            trunc_note = "\n[output truncated]"

        output_parts: list[str] = []
        if stdout_text:
            output_parts.append(stdout_text)
        if stderr_text and stderr_text.strip():
            output_parts.append(f"STDERR:\n{stderr_text}")
        if exit_code != 0:
            output_parts.append(f"\nExit code: {exit_code}")
        if trunc_note:
            output_parts.append(trunc_note)

        result = "\n".join(output_parts) if output_parts else "(no output)"

        # Truncate aggregated result at a readable limit
        max_len = 10_000
        if len(result) > max_len:
            result = result[:max_len] + (
                f"\n... (truncated, {len(result) - max_len} more chars)"
            )

        return _ExecResult(
            output=result, exit_code=exit_code, duration_ms=duration_ms,
        )

    def _resolve_sandbox_cwd(self, cwd: str) -> str:
        """Map a working directory to its sandbox equivalent.

        Rules:
        - /home/miqi/workspace/... → already a sandbox path, use as-is
        - /mnt/c/...              → already a WSL path, remap to /home/miqi/workspace/...
        - C:\\Users\\...           → Windows path, remap relative to workspace
        - Relative path            → resolve against /home/miqi/workspace
        """
        import re

        # Already a sandbox path
        if cwd.startswith("/home/miqi/"):
            return cwd

        # WSL /mnt/ path — remap to sandbox workspace
        mnt_match = re.match(r"^/mnt/([a-z])/(.+)$", cwd)
        if mnt_match:
            drive = mnt_match.group(1)
            rest = mnt_match.group(2)
            # If workspace matches, compute relative
            if self.working_dir:
                ws_str = str(self.working_dir).replace("\\", "/")
                ws_match = re.match(r"^([A-Za-z]):/(.+)$", ws_str)
                if ws_match and ws_match.group(1).lower() == drive:
                    ws_rest = ws_match.group(2).rstrip("/")
                    if rest.startswith(ws_rest + "/") or rest == ws_rest:
                        rel = rest[len(ws_rest):].lstrip("/")
                        return f"/home/miqi/workspace/{rel}" if rel else "/home/miqi/workspace"
            return cwd  # Can't map, use as-is (may fail but at least visible)

        # Windows absolute path
        win_match = re.match(r"^([A-Za-z]):[/\\](.+)$", cwd)
        if win_match and self.working_dir:
            try:
                rel = Path(cwd).relative_to(self.working_dir)
                return f"/home/miqi/workspace/{rel}"
            except ValueError:
                pass
            # Fallback: compute from drive letter
            drive = win_match.group(1).lower()
            rest = win_match.group(2).replace("\\", "/")
            ws_str = str(self.working_dir).replace("\\", "/")
            ws_match = re.match(r"^([A-Za-z]):/(.+)$", ws_str)
            if ws_match and ws_match.group(1).lower() == drive:
                ws_rest = ws_match.group(2).rstrip("/")
                if rest.startswith(ws_rest + "/") or rest == ws_rest:
                    rel = rest[len(ws_rest):].lstrip("/")
                    return f"/home/miqi/workspace/{rel}" if rel else "/home/miqi/workspace"
            # Not under workspace — map as /mnt/c/...
            return f"/mnt/{drive}/{rest}"

        # Relative path or other — default to workspace root
        return "/home/miqi/workspace"

    async def _execute_with_sandbox_selection(
        self, selection: Any, command: str, cwd: str,
        *,
        timeout_ms: int | None = None,
        event_emitter=None,
        turn_id: str = "",
        tool_call_id: str = "",
        cancel_event: asyncio.Event | None = None,
        ledger_runtime=None,
        thread_id: str = "",
        session_key: str | None = None,
    ) -> _ExecResult:
        """Execute a command according to the ToolOrchestrator's SandboxSelection.

        This is the SINGLE enforcement point for sandbox policy.  The
        ``selection`` object is the output of
        ``SandboxPolicyEngine.select()`` and was injected by
        ``ToolOrchestrator._execute_in_sandbox()``.  ExecTool MUST NOT
        second-guess it or silently fall back to a weaker execution mode.

        Rules (Phase 31):
        - NONE       → direct host execution (orchestrator explicitly allowed it).
        - BWRAP      → must use bwrap sandbox.  Unavailable → fall back to host with warning.
        - LANDLOCK   → unsupported yet.  Fail closed.
        - RESTRICTED → direct execution with cwd/env/timeout enforcement.

        Timeout (#810): a per-call ``timeout`` request (validated against
        ``max_timeout`` upstream) wins over the selection's policy
        default; ``selection.timeout_ms`` is only the fallback.
        """
        st = selection.sandbox_type
        common = dict(
            timeout_ms=timeout_ms if timeout_ms is not None else selection.timeout_ms,
            env_passthrough=list(selection.env_passthrough),
            event_emitter=event_emitter,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            cancel_event=cancel_event,
            # Phase 31.8: pass ledger runtime and thread_id to sub-executors
            ledger_runtime=ledger_runtime,
            thread_id=thread_id,
        )

        # ── NONE: orchestrator explicitly allowed direct execution ──────
        if st == SandboxType.NONE:
            return await self._execute_direct(command, cwd, **common)

        # ── BWRAP: strongest isolation; session_key preferred ──────────
        if st == SandboxType.BWRAP:
            sandbox = None
            if self._sandbox_manager is not None:
                # Prefer session_key for per-session isolation, fall back to active sandbox
                if session_key:
                    sandbox = await self._sandbox_manager.get_or_create(session_key)
                else:
                    sandbox = self._sandbox_manager.active_sandbox
            if sandbox is not None and sandbox.is_running:
                return await self._execute_in_sandbox(
                    sandbox, command, cwd, **common,
                )
            # Sandbox not available — the pre-flight guard ran with
            # SANDBOX path semantics (BWRAP selected) and may have
            # allowed sandbox-internal paths (/home/miqi/**, /tmp) that
            # mean something else on the host.  Re-check with HOST
            # semantics before falling back (issue #811 review).
            fallback_guard = self._guard_host_fallback(command, cwd)
            if fallback_guard is not None:
                return fallback_guard
            # Fall back to direct execution (e.g. during first-time
            # install when bwrap isn't ready yet).  Attach a note so the
            # AI knows it's running without isolation.
            logger.warning(
                "BWRAP sandbox not available for session_key={} — falling back to host execution",
                session_key,
            )
            result = await self._execute_direct(command, cwd, **common)
            result.output = (
                "[sandbox not available — running on host]\n"
                + result.output
            )
            return result

        # ── LANDLOCK: not yet implemented ───────────────────────────────
        if st == SandboxType.LANDLOCK:
            return _ExecResult(
                output=(
                    "Error: MiQroForge 尚未实现 LANDLOCK 沙箱。 "
                    "命令未执行。"
                ),
                exit_code=1,
            )

        # ── RESTRICTED: process-level restrictions ──────────────────────
        if st == SandboxType.RESTRICTED:
            return await self._execute_restricted(
                command, cwd, sandbox_selection=selection, **common,
            )

        return _ExecResult(
            output=f"Error: 未知沙箱类型 '{st}'",
            exit_code=1,
        )

    async def _execute_restricted(
        self, command: str, cwd: str, sandbox_selection: Any,
        *,
        timeout_ms: int | None = None,
        env_passthrough: list[str] | None = None,
        event_emitter=None,
        turn_id: str = "",
        tool_call_id: str = "",
        cancel_event: asyncio.Event | None = None,
        # Phase 31.8: ledger runtime for replay-persistent event recording
        ledger_runtime=None,
        thread_id: str = "",
    ) -> _ExecResult:
        """Execute with RESTRICTED sandbox policy enforcement.

        Phase 33.3 hardened enforcement:
        - cwd MUST be within workspace (always, not config-gated)
        - Command is scanned for file paths outside workspace
        - Network policy: BLOCK_ALL → fail closed (cannot enforce
          network isolation in direct host execution)
        - timeout_ms and env_passthrough from SandboxSelection
        """
        # 1. Resolve workspace — required for RESTRICTED enforcement.
        if not self.working_dir:
            return _ExecResult(
                output=(
                    "Error: RESTRICTED 沙箱需要工作区，但 "
                    "未配置任何工作区。命令未执行。"
                ),
                exit_code=1,
            )
        workspace = Path(self.working_dir).resolve()

        # 2. cwd MUST be within workspace — always enforced for
        #    RESTRICTED, regardless of restrict_to_workspace config.
        try:
            Path(cwd).resolve().relative_to(workspace)
        except ValueError:
            return _ExecResult(
                output=(
                    f"Error: RESTRICTED 沙箱策略要求 cwd "
                    f"必须位于工作区内。cwd={cwd} 超出 "
                    f"workspace={workspace}。命令未执行。"
                ),
                exit_code=1,
            )

        # 3. Scan command for file paths that reference locations
        #    outside the workspace.  Conservative static scan — may
        #    reject commands with path-like string literals.  The
        #    model can adjust its command to use workspace paths.
        unsafe = self._find_paths_outside_workspace(command, cwd, workspace)
        if unsafe:
            return _ExecResult(
                output=(
                    f"Error: RESTRICTED 沙箱策略：命令 "
                    f"引用了工作区外的路径："
                    f"{', '.join(unsafe[:5])}。命令未执行。"
                ),
                exit_code=1,
            )

        # 4. Network policy — BLOCK_ALL means we must fail closed.
        #    Direct host execution cannot enforce network isolation.
        if sandbox_selection.network_policy == NetworkSandboxPolicy.BLOCK_ALL:
            return _ExecResult(
                output=(
                    "Error: RESTRICTED 沙箱无法强制网络 "
                    "隔离（直接主机执行）。如需在 RESTRICTED 下允许网络访问，"
                    "请在权限配置中设置 network_allowed=True，"
                    "或使用 BWRAP 沙箱获得完整隔离。命令未执行。"
                ),
                exit_code=1,
            )

        # 5. Proceed with direct host execution — timeout and
        #    env_passthrough from SandboxSelection, unless a per-call
        #    timeout request overrode it (#810).
        return await self._execute_direct(
            command, cwd,
            timeout_ms=timeout_ms if timeout_ms is not None else sandbox_selection.timeout_ms,
            env_passthrough=list(sandbox_selection.env_passthrough),
            event_emitter=event_emitter,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            cancel_event=cancel_event,
            ledger_runtime=ledger_runtime,
            thread_id=thread_id,
        )

    # ── Path scanning (Phase 33.3 RESTRICTED enforcement) ─────────────

    @staticmethod
    def _find_paths_outside_workspace(
        command: str, cwd: str, workspace: Path,
    ) -> list[str]:
        """Find file paths in *command* that resolve outside *workspace*.

        Conservative static scan — does NOT parse shell syntax.
        Detects:
        - Windows absolute paths (C:\\..., D:\\...)
        - POSIX absolute paths (/etc/..., /tmp/..., /mnt/c/...)
        - Explicit traversal patterns (../, ..\\)
        - Redirect targets (> path, >> path, < path)
        - Shell variable expansion ($VAR, ${VAR}) — always unsafe
        - Tilde expansion (~/path, ~user/path) — expanded then checked

        Paths that appear inside string literals (e.g.
        ``python -c "open('/etc/passwd')"``) ARE detected — this is a
        real file access, even if wrapped in code.

        Returns a list of unsafe path strings (empty if all safe).
        """
        cwd_path = Path(cwd).resolve()
        ws = workspace.resolve()
        candidates: set[str] = set()

        # ── 1. Windows absolute paths: C:\\..., D:\\..., etc. ──────
        for m in re.finditer(
            r'\b([A-Za-z]:[\\/][^\s\"\'|&;<>`$()[\]]*)', command,
        ):
            candidates.add(m.group(1).rstrip('.,;:'))

        # ── 2. POSIX absolute paths (/usr/..., /etc/..., /mnt/...) ─
        #    Exclude paths after :// (URLs).
        for m in re.finditer(
            r'(?<!:/)(/[^\s\"\'|&;<>`$()[\]]{2,})', command,
        ):
            candidates.add(m.group(1).rstrip('.,;:'))

        # ── 3. Explicit traversal: ../file or ..\\file ─────────────
        for m in re.finditer(
            r'\.\.[\\/][^\s\"\'|&;<>`$()[\]]*', command,
        ):
            candidates.add(m.group(0).rstrip('.,;:'))

        # ── 4. Redirect targets: > path, >> path, 2> path, < path ─
        for m in re.finditer(
            r'[12]?[><]+\s*([^\s|&;<>]+)', command,
        ):
            target = m.group(1).strip('\'"')
            if target:
                candidates.add(target)

        # ── 5. Shell variable expansion: $VAR/path, ${VAR}/path ────
        #    Statically unresolvable — always treated as unsafe when
        #    followed by a path separator (indicating file access).
        for m in re.finditer(
            r'\$[a-zA-Z_][a-zA-Z0-9_]*(?:/[^\s\"\'|&;<>`$()[\]]+)?',
            command,
        ):
            candidates.add(m.group(0).rstrip('.,;:'))
        for m in re.finditer(
            r'\$\{[a-zA-Z_][a-zA-Z0-9_]*\}(?:/[^\s\"\'|&;<>`$()[\]]+)?',
            command,
        ):
            candidates.add(m.group(0).rstrip('.,;:'))

        # ── 6. Tilde expansion: ~/path, ~user/path ─────────────────
        for m in re.finditer(
            r'~[a-zA-Z0-9_-]*(?:/[^\s\"\'|&;<>`$()[\]]+)?', command,
        ):
            candidates.add(m.group(0).rstrip('.,;:'))

        # ── Resolve and check each candidate ───────────────────────
        unsafe: list[str] = []
        for path_str in sorted(candidates):
            # Skip empty, pure whitespace, or shell operators
            if not path_str or not path_str.strip():
                continue
            if path_str in ('|', '||', '&&', '&', ';', '2>', '1>'):
                continue
            try:
                # Phase 33.3-REVIEW: expand tilde (~/path, ~user/path)
                # before resolving.  Shell variable references ($VAR,
                # ${VAR}) are NEVER expanded — their runtime value is
                # unknowable statically, so they are always unsafe.
                expanded = path_str
                if path_str.startswith('~'):
                    expanded = os.path.expanduser(path_str)
                    # If expanduser returned the original unchanged,
                    # the tilde couldn't be resolved → unsafe.
                    if expanded == path_str:
                        unsafe.append(path_str)
                        continue

                # Reject any path containing an unresolved $ — shell
                # variable expansion is unknowable statically.
                if '$' in expanded:
                    unsafe.append(path_str)
                    continue

                p = Path(expanded)
                if not p.is_absolute():
                    p = cwd_path / expanded
                resolved = p.resolve()
                try:
                    resolved.relative_to(ws)
                except ValueError:
                    unsafe.append(path_str)
            except (ValueError, OSError):
                # Can't resolve — conservative: treat as unsafe.
                unsafe.append(path_str)

        return unsafe

    # ── Streaming helpers ────────────────────────────────────────────

    @staticmethod
    async def _read_stream(
        stream: asyncio.StreamReader | None,
        stream_name: str,
        *,
        event_emitter,
        turn_id: str,
        tool_call_id: str,
        max_chars: int = 50_000,
        # Phase 31.8: ledger runtime for replay-persistent delta recording
        ledger_runtime=None,
        thread_id: str = "",
        # #810: called on every real chunk — lets the exec heartbeat
        # reset its silence clock (chatty commands suppress heartbeats).
        on_chunk=None,
    ) -> tuple[str, bool, bool]:
        """Read *stream* incrementally, emit delta events, accumulate text.

        Returns ``(accumulated_text, was_truncated)``.

        Once ``max_chars`` is reached the accumulated text stops growing
        (truncated=True), but the pipe keeps being drained and the
        chunks discarded — otherwise the child process blocks forever on
        a full pipe buffer: alive, wedged, and (with the long #810
        budgets) burning the whole execution budget while the heartbeat
        keeps reporting "still running".  Output activity keeps
        resetting the heartbeat silence clock even in the discard phase.
        """
        if stream is None:
            return "", False

        chunks: list[str] = []
        total = 0
        truncated = False
        while True:
            try:
                chunk = await stream.read(4096)
            except Exception:
                break
            if not chunk:
                break
            if truncated:
                # Over the cap: drain and discard, keep EOF progressing.
                if on_chunk is not None:
                    on_chunk()
                continue
            text = chunk.decode("utf-8", errors="replace")
            remaining = max_chars - total
            if remaining <= 0:
                truncated = True
                if on_chunk is not None:
                    on_chunk()
                continue
            if len(text) > remaining:
                text = text[:remaining]
                truncated = True
            chunks.append(text)
            total += len(text)
            if on_chunk is not None:
                on_chunk()
            if event_emitter is not None:
                await event_emitter.emit(ExecCommandOutputDeltaEvent(
                    turn_id=turn_id,
                    tool_call_id=tool_call_id,
                    stream=stream_name,
                    delta=text,
                ))
            # Phase 31.8: record exec output delta in ledger for replay
            if ledger_runtime is not None:
                await ledger_runtime.append_item(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    item_type="exec_output_delta",
                    content=text,
                    payload={
                        "tool_call_id": tool_call_id,
                        "stream": stream_name,
                    },
                )
            # NOTE: do NOT break on truncated here — the next loop iteration
            # hits the `if truncated:` discard branch above and keeps
            # draining the pipe through EOF.  Breaking would leave the
            # remaining pipe data unread, letting a still-writing child
            # block on a full buffer and wedge the process wait (#845
            # review, CodeRabbit).
        return "".join(chunks), truncated

    async def _kill_process(
        self, process: asyncio.subprocess.Process, grace_seconds: float = 5.0,
        pgid: int | None = None,
    ) -> None:
        """Terminate, then kill *process* and its whole process tree.

        #810: timeout/cancel must mean "the command is truly stopped" —
        a caller that retries after a timeout must never collide with a
        still-running sibling (two pip installs, two xelatex on the same
        .aux).  On Windows the tree is killed via ``taskkill /T``;
        on POSIX the process group (spawned with ``start_new_session``)
        is signalled, so grandchildren die too.
        """
        if os.name == "nt":
            # Host execution on Windows now runs through bash.exe (Git Bash)
            # or cmd.exe — killing only the wrapper leaves grandchildren
            # (e.g. a long-running find) alive.  taskkill /T kills the tree.
            killer = None
            try:
                try:
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    await killer.wait()
                except asyncio.CancelledError:
                    # External cancellation landed mid-cleanup: absorb it
                    # long enough for taskkill to finish (shield), then
                    # re-raise — the caller's finally must not re-spawn a
                    # second taskkill against an already-dead pid.
                    if killer is not None:
                        await asyncio.shield(killer.wait())
                    raise
            except Exception:
                logger.warning("taskkill failed for pid {}; falling back to terminate", process.pid)
            # Fallback even when taskkill itself failed (EDR/perm): the
            # wrapper process must still be terminated and reaped with a
            # bounded wait — an unbounded wait here would hang the turn.
            try:
                process.terminate()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(process.wait(), timeout=grace_seconds)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=grace_seconds)
                except (asyncio.TimeoutError, ProcessLookupError):
                    pass
            return
        else:
            # POSIX: signal the whole process group (created via
            # start_new_session=True at spawn).  Fall back to the
            # single-process signal if the group is gone.
            # The PGID is captured at spawn time and passed in — after
            # the leader exits its PID is reaped and os.getpgid(pid)
            # raises ProcessLookupError, which would silently skip the
            # SIGKILL sweep of surviving grandchildren (#845 review).
            if pgid is None and os.name != "nt":
                try:
                    pgid = os.getpgid(process.pid)
                except (ProcessLookupError, PermissionError):
                    pgid = None
            try:
                if pgid is not None:
                    os.killpg(pgid, signal.SIGTERM)
                terminate_done = pgid is not None
            except (ProcessLookupError, PermissionError):
                terminate_done = False
            if not terminate_done:
                try:
                    process.terminate()
                except ProcessLookupError:
                    return
            try:
                await asyncio.wait_for(process.wait(), timeout=grace_seconds)
            except asyncio.TimeoutError:
                try:
                    if pgid is not None:
                        os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=grace_seconds)
                except (asyncio.TimeoutError, ProcessLookupError):
                    pass
            else:
                # Leader exited with SIGTERM, but SIGTERM-immune
                # grandchildren may still be alive in the group — sweep
                # once more with SIGKILL.  An empty group fails fast.
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

    # ── Direct execution (Phase 31.5 streaming + 31.6 cancel/timeout) ──

    async def _execute_direct(
        self, command: str, cwd: str,
        *,
        timeout_ms: int | None = None,
        env_passthrough: list[str] | None = None,
        event_emitter=None,
        turn_id: str = "",
        tool_call_id: str = "",
        cancel_event: asyncio.Event | None = None,
        # Phase 31.8: ledger runtime for replay-persistent exec event recording
        ledger_runtime=None,
        thread_id: str = "",
        session_key: str | None = None,
    ) -> _ExecResult:
        """Execute a command directly on the host (no sandbox).

        Phase 31.5: stdout/stderr are read incrementally and each chunk
        is emitted as an ``ExecCommandOutputDeltaEvent``.  The final text
        is still accumulated for the tool result.

        Phase 31.6: *cancel_event* is raced against process completion.
        On cancel or timeout the subprocess is terminate-d then kill-ed.
        ``duration_ms`` measures real wall-clock time.

        Phase 31.6+ (resource cleanup): Every internal asyncio.Task
        (proc_wait, cancel_wait, stdout_task, stderr_task) is guaranteed
        to be completed — via natural completion, explicit await after
        kill, or cancel+await — before this method returns.  The finally
        block is a safety net that cancels and awaits any task that
        wasn't handled by the primary paths.
        """
        effective_timeout = (timeout_ms / 1000) if timeout_ms else self.timeout
        start = time.monotonic()

        try:
            _kwargs: dict = {}
            if os.name == "nt":
                _kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                # POSIX: new session so _kill_process can signal the whole
                # process group on timeout/cancel (#810) — grandchildren
                # (pip workers, xelatex children) die with the parent.
                _kwargs["start_new_session"] = True
            bash: str | None = None
            if os.name == "nt":
                from miqi.sandbox.manager import find_git_bash

                bash = find_git_bash()
            if bash is not None:
                # Git Bash on Windows: the AI issues bash-style commands
                # (; chains, ls/find/grep, /c/ paths).  Run through
                # bash -c instead of cmd.exe so those actually work.
                # create_subprocess_exec (not shell) keeps cmd.exe out of
                # the picture entirely — no double-quoting surprises.
                process = await asyncio.create_subprocess_exec(
                    bash, "-c", command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=self._build_safe_env(extra_passthrough=env_passthrough),
                    **_kwargs,
                )
            else:
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=self._build_safe_env(extra_passthrough=env_passthrough),
                    **_kwargs,
                )
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            return _ExecResult(
                output=f"Error executing command: {str(e)}",
                exit_code=1,
                duration_ms=duration_ms,
            )

        # #845 review: capture the PGID immediately after spawn — once
        # the leader is reaped, os.getpgid(pid) raises ProcessLookupError
        # and the SIGKILL sweep of surviving grandchildren would silently
        # no-op.  Pass the saved PGID into _kill_process instead.
        _pgid = None
        if os.name != "nt":
            try:
                _pgid = os.getpgid(process.pid)
            except (ProcessLookupError, PermissionError):
                # 命令可能已在 spawn 后瞬间退出并被 reap(如 echo)
                # —— 此时没有进程组可杀,交给单进程路径兜底。
                _pgid = None

        # ── Launch all internal tasks ─────────────────────────────────
        # #810: heartbeat keeps the bridge drain (600 s idle) and the
        # frontend watchdog alive during silent long-running commands.
        heartbeat = _ExecHeartbeat(
            event_emitter=event_emitter,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            interval=self.heartbeat_interval,
            idle_threshold=self.idle_timeout,
            start_time=start,
        )
        await heartbeat.start()
        stdout_task: asyncio.Task = asyncio.create_task(
            self._read_stream(
                process.stdout, "stdout",
                event_emitter=event_emitter,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                ledger_runtime=ledger_runtime,
                thread_id=thread_id,
                on_chunk=heartbeat.note_activity,
            ),
        )
        stderr_task: asyncio.Task = asyncio.create_task(
            self._read_stream(
                process.stderr, "stderr",
                event_emitter=event_emitter,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                ledger_runtime=ledger_runtime,
                thread_id=thread_id,
                on_chunk=heartbeat.note_activity,
            ),
        )
        proc_wait: asyncio.Task = asyncio.create_task(process.wait())
        cancel_wait: asyncio.Task | None = None
        stdout_text = ""
        stdout_trunc = False
        stderr_text = ""
        stderr_trunc = False

        cancelled = False
        timed_out = False
        # #810: set before any kill attempt so the finally cleanup never
        # re-kills a process the kill path already handled (external
        # cancellation inside _kill_process still completes the tree
        # kill — see _kill_process) — avoids a duplicate taskkill.
        kill_attempted = False

        try:
            if cancel_event is not None:
                cancel_wait = asyncio.create_task(cancel_event.wait())
                done, _ = await asyncio.wait(
                    [proc_wait, cancel_wait],
                    timeout=effective_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # #810: when cancel and completion land in the same tick,
                # the command actually finished — report success, not a
                # spurious "user cancelled" (which would make the model
                # retry and duplicate side effects).
                if cancel_wait in done and proc_wait not in done:
                    cancelled = True
                elif not done:
                    timed_out = True

                # ── Normal completion: cancel_wait was *not* set — clean up ──
                if cancel_wait is not None and not cancel_wait.done():
                    cancel_wait.cancel()
                    try:
                        await cancel_wait
                    except asyncio.CancelledError:
                        pass
            else:
                try:
                    await asyncio.wait_for(proc_wait, timeout=effective_timeout)
                except asyncio.TimeoutError:
                    timed_out = True

            # ── Cancel / timeout: kill process tree, then await proc_wait ──
            if cancelled or timed_out:
                # #845 review: snapshot the execution duration at the
                # moment the budget ran out — the wall-clock duration_ms
                # below includes kill_grace + stream drains, which would
                # otherwise make "已运行 35s，超时上限 1s" appear.
                timeout_triggered_ms = int((time.monotonic() - start) * 1000)
                kill_attempted = True
                await self._kill_process(process, grace_seconds=self.kill_grace_seconds, pgid=_pgid)
                # After kill the process has exited — proc_wait should be
                # done (or nearly done).  Explicitly await to guarantee
                # no pending task remains.
                if not proc_wait.done():
                    try:
                        await proc_wait
                    except Exception:
                        pass

            # ── Wait for stream readers — they see EOF when pipes close ──
            # Normal path: readers complete naturally.
            # Cancel/timeout path: after process is dead, pipes close and
            # readers see EOF (or are cancelled in the safety net below).
            # A grandchild holding the pipe open keeps the reader from
            # ever seeing EOF — bound the wait so the turn cannot hang
            # forever with the heartbeat keeping the drain alive.
            try:
                stdout_text, stdout_trunc = await asyncio.wait_for(
                    stdout_task, timeout=_STREAM_DRAIN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                stdout_task.cancel()
                try:
                    await stdout_task
                except (asyncio.CancelledError, Exception):
                    pass
                stdout_text, stdout_trunc = "", True
            try:
                stderr_text, stderr_trunc = await asyncio.wait_for(
                    stderr_task, timeout=_STREAM_DRAIN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                stderr_task.cancel()
                try:
                    await stderr_task
                except (asyncio.CancelledError, Exception):
                    pass
                stderr_text, stderr_trunc = "", True

        finally:
            # ── Stop the heartbeat — the command is done or dying. ──
            await heartbeat.stop()

            # #810: if the process is still alive here (outer cancellation
            # such as ToolRegistry's asyncio.wait_for, or an unexpected
            # error), kill the whole tree so no orphan survives — the
            # "timeout means truly stopped" contract must hold on every
            # exit path, not just the timed_out branch.
            # NB: check process.returncode (None = still running), NOT
            # proc_wait.done() — asyncio.wait_for cancels the inner
            # proc_wait task on timeout, and a cancelled task reports
            # done() == True while the process is very much alive.
            # kill_attempted skips the re-kill when the timed_out/cancel
            # branch already ran _kill_process (or was mid-way through
            # it — _kill_process absorbs the cancellation and finishes).
            if not kill_attempted and process.returncode is None:
                try:
                    await self._kill_process(process, grace_seconds=self.kill_grace_seconds, pgid=_pgid)
                except Exception:
                    logger.warning(
                        "exec: failed to kill process on abnormal exit", exc_info=True,
                    )

            # ── Safety net — NO task survives this method ────────────
            for task in (cancel_wait, proc_wait, stdout_task, stderr_task):
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

        duration_ms = int((time.monotonic() - start) * 1000)
        exit_code = process.returncode if process.returncode is not None else -1

        # Log direct command failures
        cmd_summary = command[:200] + "…" if len(command) > 200 else command

        # ── Build result text ─────────────────────────────────────────
        if cancelled:
            logger.info("Direct command cancelled after {}ms: {}", duration_ms, cmd_summary)
            return _ExecResult(
                output="Error: 命令已被用户取消。",
                exit_code=exit_code, duration_ms=duration_ms,
                cancelled=True,
            )
        if timed_out:
            logger.error("Direct command timed out after {}ms: {}", duration_ms, cmd_summary)
            timeout_meta = {
                "status": "timeout",
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "execution_duration_ms": timeout_triggered_ms,
                "cleanup_duration_ms": max(0, duration_ms - timeout_triggered_ms),
                "timeout_ms": int(effective_timeout * 1000),
                "command": command[:200],
                "process_terminated": True,
                "retryable": True,
            }
            out = (
                f"Error: 命令执行超时（已运行 {duration_ms / 1000:.1f}s，"
                f"超时上限 {effective_timeout:.0f}s，进程已终止）\n"
                + json.dumps(timeout_meta, ensure_ascii=False)
            )
            # Include the tail of whatever the command printed before it
            # died — the model can see what it was doing and recover.
            partial = stdout_text
            if stderr_text and stderr_text.strip():
                partial = f"{partial}\nSTDERR:\n{stderr_text}"
            if partial.strip():
                out += "\n\n[超时前的部分输出（末尾 2000 字符）]\n" + partial[-2000:]
            out += (
                "\n建议：1. 增大 timeout 参数后重试；"
                "2. 将任务拆分为更小的步骤；"
                "3. 超过 30 分钟的任务请分批执行。"
            )
            return _ExecResult(
                output=out,
                exit_code=exit_code, duration_ms=duration_ms,
                timed_out=True, timeout_ms=int(effective_timeout * 1000),
            )

        if exit_code != 0:
            cmd_short = command[:100] + "…" if len(command) > 100 else command
            logger.warning("Direct command failed exit={} duration={}ms: {}", exit_code, duration_ms, cmd_short)

        trunc_note = ""
        if stdout_trunc or stderr_trunc:
            trunc_note = "\n[output truncated]"

        output_parts: list[str] = []
        if stdout_text:
            output_parts.append(stdout_text)
        if stderr_text and stderr_text.strip():
            output_parts.append(f"STDERR:\n{stderr_text}")
        if exit_code != 0:
            output_parts.append(f"\nExit code: {exit_code}")
        if trunc_note:
            output_parts.append(trunc_note)

        result = "\n".join(output_parts) if output_parts else "(no output)"

        # Truncate aggregated result at a readable limit
        max_len = 10_000
        if len(result) > max_len:
            result = result[:max_len] + (
                f"\n... (truncated, {len(result) - max_len} more chars)"
            )

        return _ExecResult(
            output=result, exit_code=exit_code, duration_ms=duration_ms,
        )

    def _build_safe_env(
        self, *, extra_passthrough: list[str] | None = None,
    ) -> dict[str, str]:
        """Return a sanitised copy of os.environ with credential variables removed.

        MCP servers inject secrets (API keys, tokens, passwords) into the
        process environment.  Without this filter, any shell subprocess spawned
        by the agent would inherit those secrets, leaking them to executed
        commands (e.g. ``exec("env")``).

        Variables listed in ``self.env_passthrough`` (and optionally
        ``extra_passthrough`` from a SandboxSelection) are explicitly exempted
        from the filter.  This lets operators selectively allow scripts run via
        the exec tool to access specific credentials (e.g. ``OPENAI_API_KEY``)
        without opening the door to every secret in the environment.

        Note: this filter does NOT apply to MCP server processes — those are
        started by the MCP SDK (StdioServerParameters) and always inherit the
        parent environment unchanged.
        """
        _sensitive = re.compile(
            r"(api[_-]?key|secret|token|password|passwd)", re.IGNORECASE
        )
        _sensitive_prefixes = (
            "OPENAI_", "ANTHROPIC_", "FEISHU_", "DINGTALK_",
            "TELEGRAM_", "SLACK_", "DISCORD_", "QQ_", "GROQ_",
            "AZURE_", "AWS_", "GOOGLE_", "GITHUB_", "BRAVE_", "OLLAMA_",
        )
        passthrough = set(self.env_passthrough)
        if extra_passthrough:
            passthrough.update(extra_passthrough)
        return {
            k: v for k, v in os.environ.items()
            if k in passthrough
            or (not _sensitive.search(k) and not k.startswith(_sensitive_prefixes))
        }

    # ── System package install routing (#759) ──────────────────────────

    @staticmethod
    def _is_system_install_command(command: str) -> bool:
        """True when the command is an install/update-family package command.

        Matches the anchored install-family regex (tolerating leading
        ``yes |`` / ``sudo`` / short-flag prefixes).  Commands that merely
        contain "apt-get" elsewhere (e.g. ``echo apt-get install``) are not
        matches — only commands STARTING with the package manager.
        """
        return bool(_PKG_INSTALL_RE.match(command.strip()))

    @staticmethod
    def _inject_noninteractive_flags(command: str) -> str:
        """Inject non-interactive flags when absent so the root distro run
        never hangs on a TTY prompt: -y for apt/apt-get/dnf/yum,
        --non-interactive for zypper (-y is not a zypper option),
        --noconfirm for pacman.  The verb must be the first token (sudo /
        leading flags are already stripped by the caller).
        """
        cmd = command
        if re.match(r"^(apt-get|apt|dnf|yum)\s+\S+", cmd, re.IGNORECASE) and not re.search(
            r"(^|\s)-y(\s|$)", cmd
        ):
            cmd = re.sub(
                r"^(apt-get|apt|dnf|yum)(\s+\S+)",
                lambda mo: f"{mo.group(1)}{mo.group(2)} -y",
                cmd,
                count=1,
                flags=re.IGNORECASE,
            )
        elif re.match(r"^zypper\s+\S+", cmd, re.IGNORECASE) and not re.search(
            r"(^|\s)-n(\s|$)|--non-interactive", cmd
        ):
            # --non-interactive is a GLOBAL zypper option and must precede
            # the command (zypper --non-interactive install ...), per SUSE
            # docs; placed after the verb it may be ignored (CodeRabbit #820).
            cmd = re.sub(
                r"^(zypper)(\s+\S+)",
                r"\1 --non-interactive\2",
                cmd,
                count=1,
                flags=re.IGNORECASE,
            )
        elif re.match(r"^pacman\s+-\S+", cmd, re.IGNORECASE) and "--noconfirm" not in cmd:
            cmd = re.sub(
                r"^(pacman\s+-\S+)",
                r"\1 --noconfirm",
                cmd,
                count=1,
                flags=re.IGNORECASE,
            )
        return cmd

    @staticmethod
    def _normalize_system_install(command: str) -> str | None:
        """Reduce a routed install command to its safe canonical form.

        Returns the canonical ``manager verb [flags] [packages...]`` string
        (safe to embed in ``bash -c``), or ``None`` when the command is not
        a single option-safe install command.

        The tolerated prefix (``yes |`` / ``sudo`` / leading flags) is
        stripped, then every remaining token is walked:

        * the verb must be an install/update-family verb for the manager
          (pacman's ``-S`` family is its own verb, exactly once);
        * a flag must be in :data:`_SYSTEM_INSTALL_SAFE_FLAGS` for that
          manager — apt ``-o``/``-c``, dnf ``--config``/``--installroot``,
          pacman ``--config``/``--hookdir``, zypper ``--root`` and any
          ``--flag=value`` form are REFUSED: their value is a path the
          manager executes as root (hooks/plugins/binaries), so an
          allowlisted package manager option is the only thing that may
          reach the distro run (security review #759 F1);
        * a package token must match :data:`_PKG_TOKEN_RE` — package specs
          only (names, ``pkg:arch``, ``pkg=ver``), no shell metacharacters
          and nothing starting with ``-`` (treated as a flag); ANY token
          containing ``/`` is REFUSED — a relative multi-segment token
          (``pkgs/evil.deb``) would make the distro's root apt execute
          maintainer scripts from a workspace file the agent wrote
          (arbitrary root code execution, review #759 P1 + CodeRabbit
          #820; the ``pkg/rel`` repo-qualifier form is dropped with it —
          it cannot be reliably distinguished from a path), and
          ``/etc/passwd``-style tokens would trip the desktop approval
          system's ``/etc/`` patterns for nothing (review #759 N5).
          Package file suffixes (``.deb``/``.rpm``/...) are refused even
          without a slash.  ``~`` survives only after ``=`` (version pin
          ``pkg=1.0~rc1``); the absence of metacharacters is what makes
          the rebuilt command injection-safe inside ``bash -c``.
        """
        text = command.strip()
        prefix = _SYSTEM_INSTALL_PREFIX_RE.match(text)
        rest = text[prefix.end():].strip()
        if not rest:
            return None
        head = rest.split(None, 1)
        manager = head[0].lower()
        safe = _SYSTEM_INSTALL_SAFE_FLAGS.get(manager)
        if safe is None:
            return None
        verbs = _SYSTEM_INSTALL_VERBS.get(manager)
        tokens = head[1].split() if len(head) > 1 else []

        verb_seen = False
        normalized: list[str] = [manager]
        for tok in tokens:
            if tok.startswith("-"):
                if manager == "pacman" and re.match(r"^-S(?:yu|yy|y|u)?$", tok):
                    if verb_seen:
                        return None  # only one -S family operation
                    verb_seen = True
                    normalized.append(tok)
                    continue
                if tok not in safe:
                    return None
                normalized.append(tok)
                continue
            if not _PKG_TOKEN_RE.match(tok):
                return None  # shell metacharacters
            if (
                "/" in tok
                or (tok.startswith("~") and "=" not in tok)
                or tok.lower().endswith(
                    (".deb", ".rpm", ".apk",
                     ".pkg.tar.zst", ".pkg.tar.xz", ".pkg.tar.gz", ".pkg.tar")
                )
            ):
                # Path-like tokens are refused outright.  ANY slash is
                # refused — not just leading ./ ../ — because a relative
                # multi-segment token ("pkgs/evil.deb", "sessions/k/…/evil.deb")
                # would let the agent's own workspace file reach the
                # distro's ROOT apt, which executes .deb maintainer scripts
                # (arbitrary root code execution, same channel as the -o/-c
                # option injection and review #759 P1).  This also drops
                # the pkg/rel repo-qualifier form — it cannot be reliably
                # distinguished from a path and is rarely needed.  Package
                # file suffixes (.deb/.rpm/...) are refused even without a
                # slash.  "~" is only legal after "=" (version pin
                # "pkg=1.0~rc1"); "~x" / "~/x" are shell expansions.
                # (review #759 P1 + CodeRabbit #820)
                return None
            if manager != "pacman" and not verb_seen:
                if tok not in verbs:
                    return None
                verb_seen = True
            normalized.append(tok)
        if not verb_seen:
            return None
        return " ".join(normalized)

    def _guard_system_install_command(self, command: str) -> str | None:
        """Safety guard for commands that would run as root in the WSL distro.

        Layers, in order:
        1. The same deny patterns as :meth:`_guard_command`, minus the sudo
           pattern (the routed command legitimately starts with sudo).  Any
           OTHER dangerous pattern (rm -rf, disk ops, pipe-to-shell, command
           substitution, ...) refuses the routing outright.
        2. ``allow_patterns`` (if configured) applies here too — an explicit
           allowlist must match the command, same as the normal guard.
        3. Single-command normalization: the tolerated prefix (yes | / sudo
           / leading flags) is stripped, then the command must reduce to a
           bare ``manager verb packages`` command with allowlisted flags
           only.  Shell compounds and any option whose value could redirect
           execution (apt -o/-c, dnf --config/--installroot, pacman
           --config/--hookdir, ...) are refused (see
           :meth:`_normalize_system_install`).

        A refusal exits with an error — the command does NOT fall through to
        sandbox execution, because it was headed for a root run and the
        sandbox would silently fail anyway (no root, read-only /usr).
        """
        lower = command.strip().lower()
        for pattern in self._deny_patterns_without_sudo:
            if re.search(pattern, lower):
                return "Error: 命令被安全护栏拦截（系统安装路由拒绝了危险模式）"
        if self.allow_patterns and not any(
            re.search(p, lower) for p in self.allow_patterns
        ):
            return "Error: 命令被安全护栏拦截（不在白名单中）"
        # dist-upgrade/full-upgrade classify as install-family (so they reach
        # this guard instead of a confusing in-sandbox failure) but remove
        # packages and rewrite the whole distro as root — refused here with a
        # specific message (review #759 N1).
        stripped = command.strip()
        prefix = _SYSTEM_INSTALL_PREFIX_RE.match(stripped)
        if prefix:
            stripped = stripped[prefix.end():]
        if re.search(r"\b(?:dist-upgrade|full-upgrade)\b", stripped):
            return _SYSTEM_INSTALL_DISTUPGRADE_MSG
        if self._normalize_system_install(command) is None:
            return _SYSTEM_INSTALL_SINGLE_MSG
        return None

    async def _resolve_sandbox(self, session_key: str | None):
        """Get the live sandbox for a session (creates it on demand)."""
        if self._sandbox_manager is None:
            return None
        if session_key:
            return await self._sandbox_manager.get_or_create(session_key)
        return self._sandbox_manager.active_sandbox

    async def _request_system_install_approval(self, command: str) -> tuple[str, bool, bool]:
        """#854: 系统包安装授权确认卡 → (decision, persist_failed, runtime_failed)。

        decision ∈ {"once", "always", "deny", "deny_no_channel"}；
        persist_failed 仅在 decision=="always" 且 config 持久化失败时为
        True（外部审阅 #854："允许并记住"保存失败必须对用户可见）；
        runtime_failed 仅在 decision=="always" 且 config 已持久化但
        runtime 切换失败时为 True（#875 review P4：重启后生效）。

        - fail-closed：无 approver 通道 / 异常 / 超时一律 deny（或
          deny_no_channel），绝不放行
        - 并发串行：同一时刻只有一张系统安装授权卡进入前台（外部审阅 #854）
        - "允许本次"（once）是调用级授权——不修改任何全局状态
        """
        if self.system_install_approver is None:
            return ("deny_no_channel", False, False)
        # 统一 120s 墙钟上限（CodeRabbit #875 09-01 Minor）：从调用开始计时，
        # 覆盖应用级锁等待 + approver（含 gate 排队）全程——排队的请求不会
        # 先无界等锁、锁到手后再拿第二个独立 120s（最坏 240s）。
        # 锁释放由 async with 保证（取消也释放）；approver 内部的独立超时
        # 已移除，单一 deadline 在 shell 层。
        async def _approve_under_lock() -> tuple[str, bool, bool]:
            async with _get_system_install_approval_lock():
                return await self.system_install_approver(command)

        try:
            decision = await asyncio.wait_for(_approve_under_lock(), timeout=120)
        except TimeoutError:
            logger.warning(
                "system install approval timed out (120s incl. lock wait) — deny"
            )
            return ("deny", False, False)
        except Exception as exc:  # noqa: BLE001 - fail-closed on any error
            # loguru: {} interpolation, NOT logging-style %s (#875 review)
            logger.warning("system install approval failed ({}) — deny", exc)
            return ("deny", False, False)
        # 畸形元组（错误长度）也必须 fail-closed 而非抛穿（#875 review F8）
        persist_failed = False
        runtime_failed = False
        if isinstance(decision, tuple):
            if len(decision) not in (2, 3):
                logger.warning(
                    "system install approval returned malformed tuple {!r} — deny",
                    decision,
                )
                return ("deny", False, False)
            # #875 review (5th): unpack BEFORE overwriting `decision` —
            # the previous code re-bound decision to the string first, so
            # `len(decision) == 3` compared the string length and the
            # runtime_failed flag was never read.
            if len(decision) == 2:
                decision, persist_failed = decision
            else:
                decision, persist_failed, runtime_failed = decision
            persist_failed = bool(persist_failed)
            runtime_failed = bool(runtime_failed)
        if decision not in ("once", "always", "deny", "deny_no_channel"):
            logger.warning("system install approval returned unknown decision {!r} — deny", decision)
            return ("deny", False, False)
        return (decision, persist_failed, runtime_failed)

    async def _maybe_route_system_install(
        self,
        command: str,
        *,
        sandbox_selection,
        session_key: str | None,
        cancel_event: asyncio.Event | None = None,
        event_emitter=None,
        turn_id: str = "",
        tool_call_id: str = "",
        # #810: the model's per-call timeout request; routed installs must
        # respect it (capped by the install budget), not silently run the
        # fixed 1200 s budget regardless of what the model was granted.
        requested_timeout_ms: int | None = None,
    ) -> _ExecResult | None:
        """Route system package installs to the WSL distro as root (#759).

        Returns an :class:`_ExecResult` when the command was handled
        (routed to the distro, or intercepted with guidance), None when
        normal execution should proceed.

        Decision chain:
        1. Only when a bwrap sandbox context is active (never overrides an
           orchestrator's explicit NONE/RESTRICTED selection).
        2. Only for install-family commands (see :meth:`_is_system_install_command`).
        3. Network policy: a selection that blocks network access
           (BLOCK_ALL) refuses the routing — a distro-side install must
           download packages, and it must not fetch as root against the
           policy's fail-closed choice (CodeRabbit #820).  Currently
           defensive (the policy engine only emits BLOCK_ALL for
           RESTRICTED selections, which step 1 already excludes) but the
           routed path must never become the weaker path.
        4. A disabled sandbox manager (user chose direct host exec) → the
           routing never participates, not even to intercept (review #759
           O2).
        5. Guard + normalize run BEFORE any card or approval — a command
           that fails the deny-pattern re-check or single-command
           normalization is refused outright; only commands that will
           actually execute reach the approval card, and the card shows the
           NORMALIZED command, i.e. exactly what runs as root (#875 review
           P3-1/P3-3).
        6. ``allow_system_installs`` off → the approval card (once/always/
           deny) intercepts BEFORE any sandbox resolution: no sandbox is
           created for it.  The deny branch either points at the settings
           page (card never shown — CLI/no desktop channel) or states the
           refusal plainly (card denied or timed out, #875 review P3-2).
        7. Desktop approval (when ``approval_callback`` is wired) runs here
           too — routed commands do NOT bypass the approval system; the
           user can decline, which intercepts before any sandbox is
           resolved or any root command is spawned.
        8. A live bwrap sandbox must resolve — when none is available the
           command is intercepted with a clear message instead of falling
           through to the normal path's Windows-cmd degradation (review
           #759 N2).
        9. WSL-only — native Linux sandboxes get a WSL-only message.
        10. Cancel check, then the normalized command is executed as root in
            the WSL distro.
        """
        if self._sandbox_manager is None:
            return None
        if not self._is_system_install_command(command):
            return None
        if (
            sandbox_selection is not None
            and sandbox_selection.sandbox_type != SandboxType.BWRAP
        ):
            return None
        if (
            sandbox_selection is not None
            and sandbox_selection.network_policy == NetworkSandboxPolicy.BLOCK_ALL
        ):
            # Fail closed: the selected policy forbids network access, and
            # a distro-side install must download packages — fetching as
            # root against that choice would make the routed path weaker
            # than the restricted path it replaced (CodeRabbit #820).
            return _ExecResult(output=_SYSTEM_INSTALL_NO_NETWORK_MSG, exit_code=1)

        # O2: sandbox disabled (enabled=False) means direct host exec was
        # chosen — neither routing nor intercepting installs is wanted; the
        # command goes through the normal path like any other.
        if not getattr(self._sandbox_manager, "enabled", False):
            return None

        # Guard + normalize BEFORE any card or approval (#875 review P3-1):
        # a command that the guard would refuse (un-allowlisted flags, deny
        # patterns, dist-upgrade, shell compounds) or that cannot be
        # normalized must never reach the user's approval card — the user
        # would approve something that then gets refused anyway.  The card
        # therefore shows the NORMALIZED command, i.e. exactly what will
        # execute (display == execution, #875 review P3-3).
        guard_error = self._guard_system_install_command(command)
        if guard_error:
            return _ExecResult(output=guard_error, exit_code=1)

        normalized = self._normalize_system_install(command)
        if normalized is None:
            return _ExecResult(output=_SYSTEM_INSTALL_SINGLE_MSG, exit_code=1)

        # 显示=执行（#875 review F6）：把非交互 flag（-y/--non-interactive/
        # --noconfirm）注入归一化命令——卡片显示的就是最终以 root 执行的
        # 命令（_inject_noninteractive_flags 幂等，执行时再次调用无副作用）。
        normalized = self._inject_noninteractive_flags(normalized)

        persist_failed = False
        runtime_failed = False  # #875 review P4: 弹卡分支可能置位

        # O1: check the allow toggle before touching the sandbox.  When it
        # is off the command is not dead on arrival — an approval card is
        # shown instead of hard-rejecting, so a non-developer user can
        # grant the install without editing config.json (#854).
        if not getattr(self._sandbox_manager, "allow_system_installs", False):
            # 弹系统安装授权卡（once/always/deny），替代直接拒绝：
            # "允许本次"是调用级授权，不修改全局开关（外部审阅 #854）；
            # "允许并记住"由 approver 内部走统一入口持久化后放行。
            # 无桌面通道（deny_no_channel）时指向设置页（#875 review F3）。
            decision, persist_failed, runtime_failed = (
                await self._request_system_install_approval(normalized)
            )
            if decision not in ("once", "always"):
                # 卡已弹但用户拒绝/超时 → 明确告知；无桌面通道（卡从未出现）
                # → 指向设置页（#875 review P3-2/F3——approver 恒非 None，
                # 以 deny_no_channel 决策区分，而非 approver 是否为 None）。
                if decision == "deny_no_channel":
                    return _ExecResult(output=_SYSTEM_INSTALL_NOT_ENABLED_MSG, exit_code=1)
                return _ExecResult(output=_SYSTEM_INSTALL_DENIED_MSG, exit_code=1)
            # runtime_failed (#875 review)：config 已保存但 runtime 未生效——
            # 提示交给 _execute_system_install 输出（与 persist_failed 同路径），
            # 不在此处中断执行流程。

        # Phase 77 (#759) + review F2: routed commands must not bypass the
        # approval system.  Same call the normal path uses; commands that
        # hit DANGEROUS_PATTERNS (rm -rf, redirects into /etc/, ...) go
        # through the user's approval callback just like any other command.
        if self.approval_callback is not None:
            import functools

            from miqi.agent.command_approval import check_dangerous_command
            loop = asyncio.get_event_loop()
            check_fn = functools.partial(
                check_dangerous_command,
                command,
                approval_callback=self.approval_callback,
            )
            approval_result = await loop.run_in_executor(None, check_fn)
            if not approval_result.get("approved", True):
                msg = approval_result.get(
                    "message",
                    "Error: 命令被拦截——用户拒绝了审批。",
                )
                return _ExecResult(output=msg, exit_code=1)

        sandbox = await self._resolve_sandbox(session_key)
        if sandbox is None or not getattr(sandbox, "is_running", False):
            # No live sandbox to attach the install to.  Previously this
            # fell through to the normal path, which degrades to Windows
            # cmd when no sandbox is available — "sudo is not recognized"
            # despite the environment claiming installs are routed (review
            # #759 N2).  Intercept with a clear message instead.
            return _ExecResult(output=_SYSTEM_INSTALL_NO_SANDBOX_MSG, exit_code=1)

        if not getattr(sandbox, "supports_system_installs", False):
            return _ExecResult(output=_SYSTEM_INSTALL_WSL_ONLY_MSG, exit_code=1)

        if cancel_event is not None and cancel_event.is_set():
            return _ExecResult(
                output="Error: 命令在启动前被取消。",
                exit_code=-1, cancelled=True,
            )

        return await self._execute_system_install(
            sandbox, normalized,
            event_emitter=event_emitter, turn_id=turn_id,
            tool_call_id=tool_call_id,
            requested_timeout_ms=requested_timeout_ms,
            # #875 review F4/P4：persist_failed / runtime_failed 显式传递
            # （提示只属于本次安装），不再用实例标志——实例标志在早期返回路径
            # （拒绝/无沙箱/WSL-only）会残留，导致后续安装误报。
            persist_failed=persist_failed,
            runtime_failed=runtime_failed,
        )

    async def _execute_system_install(
        self, sandbox, command: str,
        *,
        event_emitter=None,
        turn_id: str = "",
        tool_call_id: str = "",
        # #810: per-call timeout request — the install runs with
        # min(requested, _SYSTEM_INSTALL_TIMEOUT) instead of the fixed
        # 1200 s budget alone.
        requested_timeout_ms: int | None = None,
        # #875 review F4: "允许并记住"持久化失败提示（显式传递，不用实例标志）
        persist_failed: bool = False,
        # #875 review P4: config 已保存但 runtime 未立即生效（重启后生效）
        runtime_failed: bool = False,
    ) -> _ExecResult:
        """Run a normalized install command as root in the WSL distro (#759).

        *command* is the canonical form produced by
        :meth:`_normalize_system_install` (already prefix-stripped, only
        allowlisted flags and package tokens) — it is safe to embed in
        ``bash -c``.  The non-interactive flag (-y / --non-interactive /
        --noconfirm) is injected (idempotently — the routing already
        injected it so the card displays the final executed command, #875
        review F6) so the root run never hangs on a TTY prompt.

        The result is buffered (no streaming) with a dedicated long
        timeout; texlive-scale installs can emit tens of MB of progress
        text (dpkg per-package lines, ``\\r`` progress bars pass through
        verbatim), so the accumulated output is tail-bounded by the sandbox
        layer and the agent sees nothing until the run finishes.  During
        the run a progress delta is emitted roughly every
        :data:`_INSTALL_PROGRESS_INTERVAL_SECONDS` — output-triggered
        through the distro run's chunk callback AND timer-driven by a
        background heartbeat task, so even a completely silent install
        (slow downloads, quiet apt phases) keeps the bridge's chat drain
        idle timeout (600 s) from ending the turn as a TIMEOUT while the
        root install continues in the distro (CodeRabbit #820).  A
        cancelled install can leave the distro-side run finishing in the
        background — acceptable, since the install is idempotent and
        lands in the persistent distro either way.
        """
        install_cmd = self._inject_noninteractive_flags(command)

        # #810: the routed install honours the SAME timeout model as a
        # plain exec — the configured default (self.timeout) when the
        # model omits the per-call arg, the requested value otherwise,
        # both capped by the generous install budget.  A silent
        # exec("pip install …") must not run 20 minutes while a plain
        # command is killed at 60 s (#845 review).
        if requested_timeout_ms is not None:
            install_timeout = min(
                _SYSTEM_INSTALL_TIMEOUT, requested_timeout_ms / 1000,
            )
        else:
            install_timeout = min(_SYSTEM_INSTALL_TIMEOUT, float(self.timeout))

        start = time.monotonic()
        last_progress = 0.0

        async def _emit_progress(stream_name: str) -> None:
            """Throttled heartbeat: one tiny delta per interval keeps the
            turn alive without flooding the frontend with dpkg output."""
            nonlocal last_progress
            if event_emitter is None:
                return
            now = time.monotonic()
            if now - last_progress < _INSTALL_PROGRESS_INTERVAL_SECONDS:
                return
            last_progress = now
            await event_emitter.emit(ExecCommandOutputDeltaEvent(
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                stream=stream_name,
                delta=(
                    f"[system install] 安装进行中（已运行 "
                    f"{int(now - start)}s）……\n"
                ),
            ))

        async def _progress(text: str, stream_name: str) -> None:
            """Output-triggered heartbeat: reused for the output path so
            both sources share one throttle."""
            await _emit_progress(stream_name)

        async def _heartbeat() -> None:
            """Timer-driven fallback: emits a progress delta even when the
            distro writes nothing for a long stretch (slow downloads,
            silent apt phases) — output callbacks alone would let the
            bridge's 600 s drain idle timeout end the turn while the root
            install keeps running (CodeRabbit #820)."""
            while True:
                await asyncio.sleep(_INSTALL_PROGRESS_INTERVAL_SECONDS)
                await _emit_progress("stdout")

        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            rc, out, err = await sandbox.run_in_distro_root(
                install_cmd, timeout=install_timeout, on_output=_progress,
            )
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        duration_ms = int((time.monotonic() - start) * 1000)

        if rc != 0:
            logger.warning(
                "System install failed exit={} duration={}ms: {}",
                rc, duration_ms, command[:200],
            )

        if rc == 0:
            output_parts = [
                "[system install] 已以 root 在 WSL 发行版中执行（非沙箱内）；"
                "安装跨会话持久，装完即可在沙箱内使用。"
            ]
        else:
            # Review F4: never claim success on failure — the agent must
            # not misread a failed install as completed.
            output_parts = [
                f"[system install] 失败（exit {rc}）——命令以 root 在 WSL "
                "发行版中执行，未安装成功，沙箱内暂不可用。"
            ]
        if out.strip():
            output_parts.append(out.rstrip())
        if err.strip():
            output_parts.append(f"STDERR:\n{err.rstrip()}")
        if rc != 0:
            output_parts.append(f"\nExit code: {rc}")
        # 外部审阅 #854 疑点 1 → B + #875 review P2/F4：持久化失败必须可见，
        # 且与安装命令成败无关（rc==0 才提示会漏掉安装失败的情况）——用户
        # 点了「允许并记住」，却可能在重启后丢失授权，必须无条件告知。
        if persist_failed:
            output_parts.append(
                "\n[提示] 本次安装已放行，但授权保存失败，「允许系统包安装」"
                "未开启，重启后需要重新授权。"
            )
        if runtime_failed:
            # #875 review P4: config 持久化成功但 runtime 未生效——用户点了
            # 「允许并记住」却看到当前会话仍未开启，必须无条件告知。
            output_parts.append(
                "\n[提示] 「允许并记住」已保存到配置，但当前运行时未能立即生效——"
                "本次安装已放行，重启后系统包安装将自动以 root 执行。"
            )

        return _ExecResult(
            output="\n".join(output_parts),
            exit_code=rc,
            duration_ms=duration_ms,
            # Review F7: the telemetry context is the bwrap sandbox whose
            # distro this install lands in, not "none".
            sandbox_type="bwrap",
        )

    def _guard_command(
        self, command: str, cwd: str, *, sandbox_active: bool = False,
    ) -> str | None:
        """Path-aware capability guard for exec commands (issue #811).

        Replaces the blanket deny-pattern scan for destructive file
        operations with the capability engine (``miqi.agent.command_guard``):
        the command is split into subcommands (``&&`` / ``||`` / ``;`` /
        ``&`` chains) and each is checked independently — rm/cp/mv/find/
        inline-script operations and redirect targets are allowed only when
        every affected path resolves inside the session scope (canonical
        ``..``/symlink checks, UNCERTAIN → deny), and refusals carry a
        structured reason + safe alternative instead of the old
        one-line "检测到危险模式".  ``sudo`` gets a dedicated
        PRIVILEGE_ESCALATION_UNAVAILABLE answer.

        Non-file-op subcommands keep the legacy deny-pattern scan;
        ``restrict_to_workspace`` string checks still apply to them.
        ``allow_patterns`` (when configured) still applies to the whole
        command.
        """
        from miqi.agent.command_guard import (
            FILE_OP_PATTERN_EXCLUSIONS,
            evaluate_command,
        )

        verdict = evaluate_command(
            command, self._guard_runtime_paths(cwd, sandbox_active),
        )
        if not verdict.allowed:
            return verdict.message

        if self.allow_patterns:
            lower = command.strip().lower()
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: 命令被安全护栏拦截（不在白名单中）"

        # Legacy deny-pattern scan, per subcommand.  Capability-checked
        # (file-op) subcommands skip only the rm/del/rmdir blanket patterns
        # the engine subsumes — everything else (fork bomb, dd, command
        # substitution, pipe-to-shell, ...) still applies everywhere.
        for idx, sub in enumerate(verdict.subcommands):
            sub_lower = sub.lower()
            if idx in verdict.handled:
                patterns = [
                    p for p in self.deny_patterns
                    if p not in FILE_OP_PATTERN_EXCLUSIONS
                ]
            else:
                patterns = self.deny_patterns
            for pattern in patterns:
                if re.search(pattern, sub_lower):
                    return "Error: 命令被安全护栏拦截（检测到危险模式）"

        if self.restrict_to_workspace:
            legacy = " ".join(
                sub for idx, sub in enumerate(verdict.subcommands)
                if idx not in verdict.handled
            )
            if legacy:
                guard_error = self._legacy_restrict_check(legacy, cwd)
                if guard_error:
                    return guard_error
        return None

    def _guard_host_fallback(
        self, command: str, cwd: str,
    ) -> _ExecResult | None:
        """Re-check the guard with HOST path semantics before a
        host-fallback execution (issue #811 review).

        When a BWRAP-selected exec falls back to the host (no live
        sandbox), the pre-flight guard ran with sandbox path semantics
        and may have allowed sandbox-internal paths (``/home/miqi/**``,
        ``/tmp``) that are REAL host paths here.  Returns a refusal
        result, or None to proceed.  Skipped when an approval callback
        is wired: the pre-flight guard never ran on that path, and
        adding a new refusal layer would change the approval flow.
        """
        if self.approval_callback is not None:
            return None
        guard_error = self._guard_command(command, cwd, sandbox_active=False)
        if guard_error:
            return _ExecResult(output=guard_error, exit_code=1)
        return None

    def _legacy_restrict_check(self, cmd: str, cwd: str) -> str | None:
        """Legacy restrict_to_workspace string checks (non-file-op only)."""
        if "..\\" in cmd or "../" in cmd:
            return "Error: 命令被安全护栏拦截（检测到路径穿越）"

        cwd_path = Path(cwd).resolve()

        win_paths = re.findall(r"[A-Za-z]:\\[^\\\"']+", cmd)
        # Only match absolute paths — avoid false positives on relative
        # paths like ".venv/bin/python" where "/bin/python" would be
        # incorrectly extracted by the old pattern.
        posix_paths = re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", cmd)

        for raw in win_paths + posix_paths:
            try:
                p = Path(raw.strip()).resolve()
            except Exception:
                continue
            if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                return "Error: 命令被安全护栏拦截（路径超出工作目录）"

        return None

    def _guard_runtime_paths(self, cwd: str, sandbox_active: bool):
        """Build the RuntimePaths context for the capability engine.

        Resolves the host workspace root and the session files dir
        (``<workspace>/sessions/<key>/files``) so the engine can apply
        the Level 0/1/2 path hierarchy from issue #811.
        """
        from miqi.agent.command_guard import RuntimePaths

        ws: Path | None = None
        try:
            from miqi.runtime.file_handlers import _get_workspace_path

            ws = Path(_get_workspace_path()).resolve()
        except Exception:
            ws = None

        session_dir: str | None = None
        if ws is not None:
            # Derive from the SAME per-call cwd used for host_cwd —
            # self.working_dir may describe a different session when the
            # caller passes an explicit working_dir (issue #811 review).
            sessions_root = (ws / "sessions").resolve()
            for candidate in (cwd, self.working_dir):
                if not candidate:
                    continue
                try:
                    wd = Path(candidate).resolve()
                    wd.relative_to(sessions_root)
                    session_dir = str(wd)
                    break
                except ValueError:
                    continue

        miqi_home: str | None = None
        try:
            from miqi.paths import get_miqi_home

            miqi_home = str(Path(get_miqi_home()).resolve())
        except Exception:
            miqi_home = None

        return RuntimePaths(
            host_cwd=str(Path(cwd).resolve()),
            host_workspace=str(ws) if ws is not None else None,
            session_files_dir=session_dir,
            sandbox_active=sandbox_active,
            sandbox_cwd=self._resolve_sandbox_cwd(cwd) if sandbox_active else "",
            miqi_home=miqi_home,
            host_home=str(Path.home()) if hasattr(Path, "home") else None,
        )

    async def _mirror_downloaded_files(
        self, command: str, sandbox_selection, session_key: str | None,
    ) -> None:
        """After a successful exec, mirror files created by curl/wget from the
        sandbox workspace to the host workspace so they survive sandbox cleanup
        and appear in the Task Assets panel."""
        import re as _re
        from pathlib import Path

        # Need both the real sandbox instance and the workspace path
        if self._sandbox_manager is None or not session_key:
            return
        sandbox = await self._sandbox_manager.get_or_create(session_key)
        if sandbox is None:
            return
        try:
            from miqi.runtime.file_handlers import _get_workspace_path
            workspace = _get_workspace_path()
        except Exception:
            return

        # Parse the command for output filenames
        filename = None
        # wget -O <file> / --output-document=<file>  (takes explicit path argument)
        # Note: wget -o / --output-file= writes to log file, not the download — intentionally skipped.
        if 'wget' in command:
            m = _re.search(r'(?:^|\s)-O\s+(\S+)', command)
            if m:
                filename = m.group(1).strip('\'"')
            elif '--output-document=' in command:
                m = _re.search(r'--output-document=(\S+)', command)
                if m:
                    filename = m.group(1).strip('\'"')
        # curl -o <file> / --output <file>  (takes explicit path argument)
        elif 'curl' in command:
            m = _re.search(r'(?:^|\s)-o\s+(\S+)', command)
            if m:
                filename = m.group(1).strip('\'"')
            elif '--output' in command:
                m = _re.search(r'--output\s+(\S+)', command)
                if m:
                    filename = m.group(1).strip('\'"')
            else:
                # curl -O / --remote-name  (boolean flag — derive from URL basename)
                # Match O in flag clusters: -O, -LO, -fsSLO, etc. Must start with dash.
                m = _re.search(r'(?:^|\s)-[a-zA-Z]*O(?:\s+|$)', command)
                if m:
                    # Extract the last download URL from the command and take its basename
                    urls = [t for t in command.split() if t.startswith('http://') or t.startswith('https://')]
                    if urls:
                        from urllib.parse import urlparse
                        parsed = urlparse(urls[-1])
                        name = parsed.path.rstrip('/').split('/')[-1] or 'downloaded_file'
                        filename = name

        # shell redirect > or >>
        if not filename:
            m = _re.search(r'(?:^|\s)>{1,2}\s*(\S+)', command)
            if m:
                filename = m.group(1).strip('\'"')

        if not filename:
            return

        # Resolve the sandbox workspace path
        from miqi.agent.tools.filesystem import (
            _get_session_workspace,
            _persist_tracked_file,
            _resolve_sandbox_path,
            _sandbox_to_host_path,
        )

        session_ws = _get_session_workspace(workspace, sandbox)
        sandbox_path = _resolve_sandbox_path(filename, session_ws, sandbox)
        host_path = _sandbox_to_host_path(sandbox_path, workspace, sandbox)

        # Security: verify both paths remain under session workspace before writing.
        # An attacker could craft a command like "curl -o ../../../etc/passwd" to
        # escape the session directory. Use path-relative containment check.
        try:
            canonical_host = Path(host_path).resolve()
            canonical_ws = Path(workspace).resolve()
            # Allow exact match (workspace itself) or paths under workspace.
            # Reject sibling directories like /workspace-evil/ that prefix-match.
            if canonical_host != canonical_ws and canonical_ws not in canonical_host.parents:
                # Expected for non-workspace paths (e.g. /tmp package
                # installs); the mirror is only for workspace artifacts.
                logger.debug("exec [mirror] rejected: path escapes workspace: {}", host_path)
                return
        except Exception as exc:
            logger.warning("exec [mirror] path resolution failed: {}", exc)
            return

        # Check if the file exists inside the sandbox
        try:
            from miqi.agent.tools.filesystem import _sandbox_file_exists
            exists = await _sandbox_file_exists(sandbox, sandbox_path)
        except Exception:
            exists = False

        if not exists:
            return

        # Mirror to host
        try:
            from miqi.agent.tools.filesystem import _sandbox_read_file
            content = await _sandbox_read_file(sandbox, sandbox_path)
            target = Path(host_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
            logger.info("exec [mirror]: {} → {}", sandbox_path, host_path)
        except Exception as exc:
            logger.warning("exec [mirror] failed for {}: {}", sandbox_path, exc)
            return

        _persist_tracked_file(workspace, host_path, op="write", session_key=session_key)


    # ── Phase 59: subprocess artifact tracking (#607) ───────────────────────

    # Directories never treated as AI artifacts: VCS/deps/caches plus the
    # app's own session/runtime state. `sessions/`, `.miqi-runtime/` and
    # `logs/` are written by the app itself DURING an exec (ledger,
    # tracked_files.json, sandbox logs) — tracking them would
    # self-reference the panel with every command.
    _WORKSPACE_SNAPSHOT_EXCLUDES = frozenset({
        ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
        "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
        ".nox", "sessions", ".miqi-runtime", "logs",
    })
    _WORKSPACE_SNAPSHOT_MAX_FILES = 5000

    def _snapshot_workspace(
        self, root: str | Path | None = None,
    ) -> dict[str, tuple[int, int]] | None:
        """Snapshot workspace files as {abs_path: (mtime_ns, size)}.

        Used to detect files a subprocess created/modified during an exec.
        ``root`` defaults to the global workspace path; pass the exec's cwd
        (custom workspace / sessions/<key>/files) so subprocess artifacts
        written OUTSIDE the global workspace are still diffed (#682 review).
        Returns None when tracking is DISABLED (workspace missing or
        oversized) — an empty dict is a legitimate EMPTY workspace, which
        must still be diffed (every file after the exec is then new).
        """
        try:
            if root is None:
                from miqi.runtime.file_handlers import _get_workspace_path

                root = Path(_get_workspace_path())
            else:
                root = Path(root)
        except Exception:
            return None
        if not root.is_dir():
            return None
        snap: dict[str, tuple[int, int]] = {}
        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames if d not in self._WORKSPACE_SNAPSHOT_EXCLUDES
            ]
            for fn in filenames:
                count += 1
                if count > self._WORKSPACE_SNAPSHOT_MAX_FILES:
                    return None
                p = Path(dirpath) / fn
                try:
                    st = p.stat()
                except OSError:
                    continue
                snap[str(p)] = (st.st_mtime_ns, st.st_size)
        return snap

    async def _track_workspace_changes(
        self, before: dict[str, tuple[int, int]] | None, session_key: str | None,
        root: str | Path | None = None,
    ) -> None:
        """Persist files the subprocess created/modified as write tracked files."""
        if before is None:
            return
        # Snapshot + diff + persist run off the event loop: os.walk over a
        # large workspace is O(files) and each persist cycle is a full
        # tracked_files.json read+rewrite (CodeRabbit #682 review).
        after = await asyncio.to_thread(self._snapshot_workspace, root)
        if after is None:
            return
        changed = [
            path_str
            for path_str, meta in after.items()
            if before.get(path_str) is None or before[path_str] != meta
        ]
        if not changed:
            return
        try:
            await asyncio.to_thread(
                self._persist_changed_batch, changed, session_key,
            )
        except Exception:
            logger.debug("exec [track] batch persist failed", exc_info=True)

    def _persist_changed_batch(
        self, changed: list[str], session_key: str | None,
    ) -> None:
        """Synchronous batch persist of exec-created files (off-loop)."""
        if not session_key:
            return
        try:
            from pathlib import Path

            from miqi.runtime.file_handlers import _get_workspace_path
            workspace = Path(_get_workspace_path())
        except Exception:
            return
        try:
            from miqi.session.manager import SessionManager
            sm = SessionManager(workspace)
            # Strip the client_id prefix (same rule as _persist_tracked_file).
            if ":" in session_key:
                parts = session_key.split(":", 1)
                if len(parts) == 2 and parts[0] != "desktop":
                    session_key = parts[1]
            sm.save_tracked_files_batch(
                session_key, [(p, "write") for p in changed],
            )
        except Exception as exc:
            logger.debug("exec [track] batch persist failed: {}", exc)
