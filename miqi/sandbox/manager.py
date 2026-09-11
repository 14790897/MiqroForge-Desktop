"""Sandbox manager — creates/switches/destroys per-session bwrap sandboxes.

Integrates with MiQi's SessionManager to automatically:
1. Create a new sandbox when a session starts
2. Switch sandbox context when the active conversation changes
3. Clean up sandboxes when sessions are archived/deleted

Supports Windows + WSL: automatically detects WSL and routes bwrap
commands through wsl.exe when running on Windows.

State persistence (落盘):
- A JSON state file (sandbox_state.json) tracks all active sandboxes
- On sandbox create/destroy, the file is atomically updated
- On bridge startup, stale sandboxes from previous runs are cleaned up
- On graceful shutdown, all sandboxes are destroyed and state cleared

Usage:
    manager = SandboxManager(workspace=Path("~/.miqi/workspace"))
    await manager.initialize()

    # When a session activates:
    sandbox = await manager.get_or_create("feishu:oc_123")

    # When switching conversations:
    await manager.activate("feishu:oc_123")
    current = manager.active_sandbox  # BwrapSandbox for oc_123

    # When a session is archived/deleted:
    await manager.destroy("feishu:oc_123")
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from miqi.sandbox.bwrap import BwrapSandbox, BwrapSandboxError, _create_subprocess_exec, _is_windows


def sandbox_is_active(sandbox_manager: Any) -> bool:
    """Whether the WSL/bwrap sandbox is enabled AND initialized right now.

    Mirrors the ``bwrap_available`` computation in RuntimeServices, but
    reads the LIVE manager attributes — the policy engine's snapshot at
    build time can be stale after a toggle change mid-session.
    """
    return bool(
        sandbox_manager is not None
        and sandbox_manager != "disabled"
        and getattr(sandbox_manager, "enabled", False)
        and getattr(sandbox_manager, "_initialized", False)
    )


_git_bash_checked = False
_git_bash_path: str | None = None

_host_python_checked = False
_host_python_path: str | None = None


def _find_host_python() -> str | None:
    """Find a real Python interpreter on the host, skipping store stubs.

    Packaged (frozen) builds only — there sys.executable is the bridge
    binary itself, not an interpreter. On Windows the PATH scan must skip
    %LOCALAPPDATA%\\Microsoft\\WindowsApps\\python.exe: that is a store
    stub which opens the Microsoft Store and hangs instead of running.
    Result is cached because the per-turn environment description calls
    this on every turn.
    """
    global _host_python_checked, _host_python_path
    if _host_python_checked:
        return _host_python_path
    _host_python_checked = True
    if os.name == "nt":
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if not entry:
                continue
            cand = os.path.join(entry, "python.exe")
            if not os.path.isfile(cand):
                continue
            if "windowsapps" in cand.lower():
                continue
            _host_python_path = cand
            break
    else:
        _host_python_path = shutil.which("python3") or shutil.which("python")
    return _host_python_path


def _is_cygwin_bash(path: str) -> bool:
    r"""True when *path* is a Cygwin bash.exe (e.g. C:\cygwin64\bin\bash.exe).

    Cygwin uses /cygdrive/c/... paths, NOT the /c/... convention the
    environment description promises — selecting it would mislead the AI.
    """
    p = str(path).lower().replace("\\", "/")
    return any(part == "cygwin" or part == "cygwin64" for part in p.split("/"))


def _is_windows_system_bash(path: str) -> bool:
    """True when *path* is the WSL entrypoint (C:\\Windows\\System32\\bash.exe).

    shutil.which("bash") can resolve to System32\\bash.exe on machines with
    WSL enabled — running that would execute commands inside a Linux distro
    while the prompt claims Git Bash with /c/ path mappings.

    String-based split (not Path.parts) so the check is independent of the
    host platform — Path.parts does not treat backslashes as separators on
    POSIX and would let the WSL entrypoint through on Linux runners.
    """
    p = str(path).lower().replace("\\", "/")
    parts = [x for x in p.split("/") if x]
    return (
        len(parts) >= 2
        and parts[1] == "windows"
        and len(parts[0]) >= 2
        and parts[0][1] == ":"
    )


_GIT_BASH_COMMON_LOCATIONS = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
)


def find_git_bash() -> str | None:
    """Locate Git Bash (bash.exe) on Windows; None when not installed.

    Without the WSL sandbox, exec runs bash-style commands through Git
    Bash when available so the AI's bash habits (; chains, ls/find/grep)
    keep working on Windows.  Result is cached per process.

    Known Git-for-Windows install locations take precedence over PATH:
    ``shutil.which("bash")`` can resolve to C:\\Windows\\System32\\bash.exe
    (the WSL entrypoint), which would silently run commands in a Linux
    distro — that candidate is rejected.
    """
    global _git_bash_checked, _git_bash_path
    if _git_bash_checked:
        return _git_bash_path
    _git_bash_checked = True
    # Debug/acceptance override: force the Windows cmd fallback path
    # even when Git Bash is installed (e.g. MIQI_FORCE_CMD_EXEC=1).
    if getattr(os, "environ", {}).get("MIQI_FORCE_CMD_EXEC") == "1":
        _git_bash_path = None
        return None

    for base in _GIT_BASH_COMMON_LOCATIONS:
        if os.path.exists(base):
            _git_bash_path = base
            return base
    local = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe")
    if os.path.exists(local):
        _git_bash_path = local
        return local
    from_path = shutil.which("bash")
    if (
        from_path is not None
        and not _is_windows_system_bash(from_path)
        and not _is_cygwin_bash(from_path)
    ):
        _git_bash_path = from_path
        return from_path
    return None


def windows_path_to_msys(path: str | Path) -> str:
    """Convert a Windows path to its MSYS/Git Bash form (C:\\x → /c/x)."""
    p = str(path).replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        return "/" + p[0].lower() + p[2:]
    return p


def windows_path_to_mnt(path: str | Path) -> str:
    """Convert a Windows path to its WSL form (C:\\x → /mnt/c/x)."""
    p = str(path).replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        return "/mnt/" + p[0].lower() + p[2:]
    return p


def _skills_dirs_note(workspace: str | Path | None, style: str) -> str:
    """One sentence telling the AI how to locate skills.

    The prompt must NOT disclose machine-specific absolute paths for the
    builtin skills — the app may live anywhere (dev checkout, installed
    package, extracted archive).  skill_manage(action='view') resolves the
    runtime location itself and appends the scripts directory, so point the
    AI there; the workspace skills dir is the only config-derived path worth
    mentioning (and it is per-user runtime data, not a hard-coded path).
    """
    ws_skills = str(Path(workspace) / "skills") if workspace is not None else ""

    if style == "msys":
        ws_skills = windows_path_to_msys(ws_skills) if ws_skills else ""
    elif style == "mnt":
        ws_skills = windows_path_to_mnt(ws_skills) if ws_skills else ""

    parts = []
    if ws_skills:
        parts.append(f"工作区技能目录 {ws_skills}")
    parts.append(
        "技能内容与内置技能的脚本目录请用 skill_manage(action='view', name=<技能名>) 获取"
        "（返回内容末尾附脚本目录路径），无需搜索或猜测绝对路径"
    )
    return "技能定位：" + "、".join(parts) + "。"


def _host_python_note(exe: str, style: str) -> str:
    if style == "msys":
        exe = windows_path_to_msys(exe)
    return (
        f"推荐 Python 解释器：{exe}。"
        "运行 python 脚本请直接用这个完整路径，避免 PATH 上其他 "
        "python 启动卡顿（如商店占位程序）。"
    )


def _python_note(style: str) -> str:
    """One sentence disclosing the bridge's REAL python interpreter.

    Host-exec paths only — inside the sandbox the host interpreter cannot
    run (no WSL interop), see _sandbox_python_note instead.  The AI
    otherwise resolves `python` via PATH and can hit the WindowsApps
    store stub (hangs ~forever) or a stale interpreter — give it the full
    path in the exec environment's path style
    (msys = /c/..., native = as-is).
    """
    if getattr(sys, "frozen", False):
        # Packaged (PyInstaller) build: sys.executable is miqi-bridge.exe
        # itself, NOT a python interpreter — recommending it would make
        # the AI launch a second bridge instead of running the script.
        # The embedded runtime is private to the bridge, so fall back to a
        # real host interpreter when one exists.
        host_py = _find_host_python()
        if host_py:
            return _host_python_note(host_py, style)
        return (
            "本机未检测到可用的独立 Python（打包版自带的运行时仅供 bridge "
            "内部使用，不能用来执行脚本）。运行 Python 脚本请让用户安装 "
            "Python 3.11+，或启用沙箱后在沙箱内使用 python3。"
        )
    exe = str(getattr(sys, "executable", ""))
    if not exe:
        return ""
    return _host_python_note(exe, style)


def _sandbox_python_note(sandbox_manager: Any) -> str:
    """Tell the AI how to run Python INSIDE the bwrap sandbox.

    The sandbox ro-binds the distro's /usr, so python3 is always present
    (the WSL readiness probe only passes with python3 + pip available).
    The host interpreter disclosure (_python_note) is wrong here: bwrap's
    namespace isolation removes the WSL interop bridge, so Windows .exe
    files under /mnt/c — including the bridge's own venv python — can
    never start, and recommending one makes the AI retry a dead path
    (#822).
    """
    if _is_windows():
        interop = (
            "沙箱内无 WSL interop：/mnt/c/... 下的 Windows 程序（含 Windows 侧 "
            "python.exe）无法启动，不要尝试运行。"
        )
    else:
        interop = ""
    install = (
        "Python 依赖用 python3 -m pip install --user <包名> 安装（写入沙箱 HOME，"
        "沙箱销毁后不保留）；pip 报 externally-managed 时改用 "
        "python3 -m venv ~/.venv && ~/.venv/bin/pip install <包名>。"
    )
    if getattr(sandbox_manager, "allow_system_installs", False):
        install += (
            "需要长期可用的依赖可直接 sudo apt-get install python3-<包名>"
            "（随发行版持久化，装完沙箱内立即可用）。"
        )
    return (
        f"沙箱内请使用 python3（发行版自带，只读挂载始终可用）。{interop}{install}"
    )


def describe_exec_environment(
    sandbox_manager: Any,
    workspace: str | Path | None = None,
) -> str:
    """Human-readable description of where/how exec commands run, for AI prompts.

    Used by the exec tool description and the per-turn session context so
    the AI is told the ACTUAL environment instead of a hard-coded sandbox
    story: with the sandbox active exec runs inside WSL with /home/miqi
    paths; without it exec runs directly on the host — through Git Bash
    on Windows when available, otherwise Windows cmd.
    """
    if sandbox_is_active(sandbox_manager):
        parts = [
            (
                "exec 在 WSL 沙箱中运行——默认工作区下沙箱 /home/miqi/workspace "
                "与文件工具目录不同（沙箱为独立目录，看不到文件工具写入的文件），"
                "自定义工作区下二者相同；exec 中访问文件请用主机路径（如 /mnt/c/...），"
                "或改用文件工具。"
            )
        ]
        # Phase 77 (#759): system package install routing.  Tell the AI the
        # ACTUAL way to obtain system toolchains (LaTeX, compilers, ...):
        # inside the sandbox they are unprivileged + system dirs are
        # read-only, so apt-get can never work there; when enabled, install
        # commands are routed to the WSL distro as root and persist.
        if getattr(sandbox_manager, "allow_system_installs", False):
            parts.append(
                "系统包安装已开启：需要系统工具链（如 LaTeX/xelatex、编译器）时，"
                "直接运行 sudo apt-get install -y <包名>（或 apt/dnf/pacman 等），"
                "该命令会以 root 在 WSL 发行版中执行（仅 Windows + WSL 生效），"
                "安装一次跨会话持久，装完即可在沙箱内使用。"
            )
        else:
            parts.append(
                "沙箱内无法安装系统包（tools.sandbox.allow_system_installs 未开启）："
                "sudo/apt-get install 会被拦截；如需 LaTeX 等系统工具链，"
                "请让用户在配置中开启该选项。"
            )
        return (
            " ".join(parts)
            + _skills_dirs_note(workspace, "mnt")
            + _sandbox_python_note(sandbox_manager)
        )
    if os.name == "nt":
        if find_git_bash() is not None:
            mapping = ""
            if workspace is not None:
                mapping = (
                    f" 工作区 {workspace} 在 Git Bash 中为 "
                    f"{windows_path_to_msys(workspace)}。"
                )
            return (
                "exec 通过 Git Bash（bash.exe）在 Windows 本机执行（当前未启用沙箱），"
                "与文件工具使用同一工作目录；支持 bash 语法与常用命令"
                "（ls/find/grep/sed 等），用 && 或 ; 连接多条命令；"
                f"Windows 路径在 Git Bash 中映射为 /c/... 形式（如 C:\\Users\\x 对应 /c/Users/x）。{mapping}"
                "文件工具（read_file/write_file/list_dir）仍使用 Windows 路径。"
                + _skills_dirs_note(workspace, "msys") + _python_note("msys")
            )
        return (
            "exec 直接在 Windows cmd 中运行（当前未启用沙箱），"
            "与文件工具使用同一工作目录，请使用 Windows 路径（如 C:\\Users\\...）。"
            "cmd 语法注意：用 && 连接多条命令（不支持 ; 分隔），"
            "ls/find/grep/sed 不可用（用 dir / where / findstr），"
            "或使用 powershell -Command \"...\"。"
            "本机未检测到 Git Bash，请只用 cmd 或 powershell 语法；"
            "不要直接运行 bash/wsl 命令：PATH 上的 bash 可能是 "
            "System32\\bash.exe（子系统的入口桩），若子系统未启用会报 "
            "「EXECUTABLE NOT FOUND / 子系统未安装」。"
            + _skills_dirs_note(workspace, "native") + _python_note("native")
        )
    return (
        "exec 直接在本机 shell（bash）中运行（当前未启用沙箱），"
        "与文件工具使用同一工作目录，使用标准 Linux/macOS 命令与路径。"
        + _skills_dirs_note(workspace, "native")
    )


class SandboxManager:
    """Manages per-session bwrap sandboxes.

    On Windows, automatically detects WSL and runs bwrap inside WSL.

    State is persisted to disk so that:
    - Crashed/killed bridge instances don't leave orphaned WSL directories
    - On restart, stale sandboxes are automatically cleaned up
    """

    def __init__(
        self,
        workspace: Path,
        sandbox_base_dir: Path | None = None,
        share_net: bool = True,
        enabled: bool = True,
        max_sandboxes: int = 10,
        auto_cleanup: bool = True,
        wsl_distro: str = "",
        wsl_base_dir: str = "/tmp/miqi-sandboxes",
        sandbox_distro_name: str = "AIShadowSandbox",
        auto_install_deps: bool = True,
        allow_system_installs: bool = False,
        session_workspace_resolver: Any = None,
    ):
        self.workspace = workspace
        self.sandbox_base_dir = sandbox_base_dir or workspace / "sandboxes"
        self.share_net = share_net
        self.enabled = enabled
        self.max_sandboxes = max_sandboxes
        self.auto_cleanup = auto_cleanup
        self.wsl_distro = wsl_distro
        self.wsl_base_dir = wsl_base_dir
        self.sandbox_distro_name = sandbox_distro_name
        self.auto_install_deps = auto_install_deps
        # #759: when enabled, package install commands (apt-get/apt/dnf/...)
        # are routed to the WSL distro as root so system toolchains install
        # once and persist across sessions (visible in every sandbox via the
        # distro's ro-bind system dirs).
        self.allow_system_installs = allow_system_installs

        self._sandboxes: dict[str, BwrapSandbox] = {}
        self._active_key: str | None = None
        # threading.Lock for cross-thread safety.  The desktop bridge spawns
        # a new thread (with its own asyncio event loop) for every chat
        # request, so an asyncio.Lock (bound to one loop) would raise
        # "Lock object ... is bound to a different event loop" when accessed
        # from another thread's loop.  A threading.Lock has no loop affinity.
        self._lock = threading.Lock()
        # Keys currently being created (prevents duplicate concurrent creation)
        self._creating: set[str] = set()
        self._initialized = False

        # Callable(session_key: str) -> Path | None for per-session workspace lookup
        self._session_workspace_resolver = session_workspace_resolver

        # ── State persistence ─────────────────────────────────────────
        self._state_file = self._resolve_state_file()

    # ── State file path ───────────────────────────────────────────────

    @staticmethod
    def _resolve_state_file() -> Path:
        """Resolve the state file path next to the config directory."""
        try:
            from miqi.config.loader import get_data_dir
            data_dir = get_data_dir()
        except Exception:
            from miqi.paths import get_miqi_home
            data_dir = get_miqi_home()
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "sandbox_state.json"

    # ── State persistence ─────────────────────────────────────────────

    def _save_state(self) -> None:
        """Atomically write current sandbox state to disk.

        Each entry records enough information to clean up the sandbox
        directory even without an in-memory BwrapSandbox instance.
        """
        entries = []
        for key, sandbox in self._sandboxes.items():
            entries.append({
                "session_key": key,
                "wsl_base_dir": sandbox.wsl_base_dir,
                "linux_base_dir": sandbox._linux_base_dir,
                "sandbox_home": sandbox.sandbox_home,
                "sandbox_workspace": sandbox.sandbox_workspace,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

        payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sandboxes": entries,
        }

        try:
            # Atomic write: write to temp file, then rename
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._state_file.parent),
                prefix=".sandbox_state_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                # On Windows, need to remove target first before rename
                if self._state_file.exists():
                    self._state_file.unlink()
                os.rename(tmp_path, str(self._state_file))
            except Exception:
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            logger.debug("Sandbox state saved: {} entries", len(entries))
        except Exception as exc:
            logger.warning("Failed to save sandbox state: {}", exc)

    @staticmethod
    def _validate_state(data: Any) -> bool:
        """Validate the loaded sandbox state schema.

        Requires a top-level dict, a ``sandboxes`` list, and a non-empty
        ``linux_base_dir`` string on every entry.  A syntactically-valid file
        with the wrong shape (e.g. ``[]``) is treated as damaged so callers
        fall back to orphan recovery instead of crashing or skipping cleanup.
        """
        if not isinstance(data, dict):
            return False
        entries = data.get("sandboxes")
        if not isinstance(entries, list):
            return False
        for entry in entries:
            if not isinstance(entry, dict):
                return False
            base = entry.get("linux_base_dir")
            if not isinstance(base, str) or not base:
                return False
        return True

    def _load_state(self) -> tuple[dict[str, Any] | None, bool]:
        """Read the persisted sandbox state from disk.

        Returns a ``(state, damaged)`` tuple:
        - ``state``: the parsed JSON payload, or None if no state exists.
        - ``damaged``: True when a state file exists but could not be parsed
          (corrupt / truncated / schema-invalid).  Callers should rebuild from
          a filesystem scan instead of trusting the file (#472).
        """
        if not self._state_file.exists():
            return None, False
        try:
            with open(self._state_file, encoding="utf-8") as f:
                data = json.load(f)
            if not SandboxManager._validate_state(data):
                logger.warning("Sandbox state file has invalid schema")
                return None, True
            return data, False
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read sandbox state: {}", exc)
            return None, True

    async def _cleanup_orphan_scan(self) -> tuple[int, bool]:
        """Scan the sandbox base dir for leftover sandbox directories.

        Used when the state file is missing or corrupt: instead of trusting
        the (possibly lost) registration, enumerate whatever is under the
        sandbox base dir in Linux/WSL and remove every entry.  This closes
        the "orphan directory lost from state" leak (#472).

        Returns ``(cleaned, completed)`` — ``completed`` is False when the
        scan itself could not run (WSL unavailable, timeout) or when any
        removal failed, so callers know not to drop the damaged state file.
        """
        if _is_windows():
            distro = self.wsl_distro
            if not distro:
                distro = await BwrapSandbox._detect_wsl_distro() or ""
            if not distro:
                logger.warning("No WSL distro available for orphan scan")
                return 0, False
            proc = await _create_subprocess_exec(
                "wsl.exe", "-d", distro, "--",
                "find", self.wsl_base_dir, "-mindepth", "1", "-maxdepth", "1", "-type", "d",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            proc = await _create_subprocess_exec(
                "find", str(self.sandbox_base_dir), "-mindepth", "1", "-maxdepth", "1", "-type", "d",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=20.0)
        except asyncio.TimeoutError:
            logger.warning("Sandbox orphan scan timed out")
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            return 0, False

        dirs = [line.strip() for line in out.decode("utf-8", errors="replace").splitlines() if line.strip()]
        cleaned = 0
        completed = True
        for linux_dir in dirs:
            try:
                if await BwrapSandbox.cleanup_dir(
                    linux_dir, self.wsl_distro,
                    expected_root=self._sandbox_root(),
                ):
                    cleaned += 1
                    logger.info("Orphan scan cleaned sandbox: {}", linux_dir)
                else:
                    completed = False
                    logger.warning("Orphan scan failed to remove: {}", linux_dir)
            except Exception as exc:
                completed = False
                logger.warning("Orphan scan error on {}: {}", linux_dir, exc)
        if cleaned:
            logger.info("Sandbox orphan scan complete: {} removed", cleaned)
        return cleaned, completed

    def _sandbox_root(self) -> str:
        """The expected sandbox root for cleanup validation (native or WSL)."""
        if _is_windows():
            return self.wsl_base_dir
        return str(self.sandbox_base_dir)

    async def cleanup_stale(self) -> int:
        """Clean up sandbox directories left from a previous bridge run.

        Called on bridge startup. Reads the state file, removes all listed
        sandbox directories in WSL/Linux, and clears the state file.

        Returns the number of stale sandboxes cleaned up.
        """
        state, damaged = self._load_state()
        if damaged:
            # Corrupt state file — the registered entries are unrecoverable.
            # Rebuild from a filesystem scan so orphaned directories are not
            # permanently lost.  Only drop the corrupt file when the scan
            # actually completed; otherwise keep it so the next startup
            # retries instead of silently forgetting the orphans (#472).
            logger.warning(
                "Sandbox state file corrupt — falling back to directory scan"
            )
            cleaned, completed = await self._cleanup_orphan_scan()
            if completed:
                try:
                    if self._state_file.exists():
                        self._state_file.unlink()
                except OSError as exc:
                    logger.warning("Failed to remove corrupt state file: {}", exc)
            else:
                logger.warning(
                    "Orphan scan incomplete — keeping corrupt state file for retry"
                )
            return cleaned

        if state is None:
            return 0

        entries = state.get("sandboxes", [])
        if not entries:
            # Empty state file — nothing to clean
            self._clear_state_file()
            return 0

        cleaned = 0
        failures = 0
        for entry in entries:
            linux_base_dir = entry.get("linux_base_dir")
            if not linux_base_dir:
                continue

            # Use BwrapSandbox's static cleanup helper
            try:
                if await BwrapSandbox.cleanup_dir(
                    linux_base_dir,
                    wsl_distro=self.wsl_distro,
                    expected_root=self._sandbox_root(),
                ):
                    cleaned += 1
                    logger.info(
                        "Cleaned up stale sandbox: {} ({})",
                        entry.get("session_key", "?"), linux_base_dir,
                    )
                else:
                    failures += 1
                    logger.warning(
                        "Failed to clean stale sandbox {} ({})",
                        entry.get("session_key", "?"), linux_base_dir,
                    )
            except Exception as exc:
                failures += 1
                logger.warning(
                    "Failed to clean stale sandbox {}: {}",
                    entry.get("session_key", "?"), exc,
                )

        if failures == 0:
            # All listed sandboxes handled — safe to drop the state file.
            self._clear_state_file()
        else:
            # Keep the state file so failed directories are retried on the
            # next startup instead of being silently forgotten (#472).
            logger.warning(
                "{} stale sandbox(s) failed cleanup — state kept for retry", failures
            )
        logger.info("Stale sandbox cleanup complete: {} removed", cleaned)
        return cleaned

    def _clear_state_file(self) -> None:
        """Remove or truncate the state file."""
        try:
            if self._state_file.exists():
                self._state_file.unlink()
        except OSError as exc:
            logger.warning("Failed to clear sandbox state file: {}", exc)

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def initialize(self) -> bool:
        """Check if bwrap is available and initialize the manager.

        Also cleans up any stale sandboxes from a previous bridge run.

        Returns True if sandboxing is available, False otherwise.
        """
        if self._initialized:
            return self.enabled

        if not self.enabled:
            logger.info("Sandbox disabled by configuration")
            self._initialized = True
            return False

        available = await BwrapSandbox.is_available(
            wsl_distro=self.wsl_distro,
            auto_install_deps=self.auto_install_deps,
        )
        if not available:
            logger.warning(
                "bwrap not found — sandbox isolation is NOT available. "
                "Install bubblewrap: apt install bubblewrap"
            )
            self._initialized = True
            self.enabled = False
            return False

        self._initialized = True

        # Clean up sandboxes left from a previous bridge run
        stale_count = await self.cleanup_stale()
        if stale_count > 0:
            logger.info(
                "Cleaned up {} stale sandbox(es) from previous run",
                stale_count,
            )

        logger.info("Sandbox manager initialized (bwrap available)")
        return True

    # ── Sandbox CRUD ───────────────────────────────────────────────────

    def _sandbox_key(self, session_key: str, *, client_id: str | None = None) -> str:
        """Compute the internal sandbox dict key.

        Phase 30: When client_id is provided, uses client-scoped namespace:
            sandbox_key = f"{client_id}:{session_key}"
        This prevents two clients with the same session_key from sharing
        a sandbox. When client_id is None (legacy/single-tenant path),
        uses raw session_key for backward compatibility.
        """
        if client_id is not None:
            return f"{client_id}:{session_key}"
        return session_key

    async def get_or_create(
        self, session_key: str, *, client_id: str | None = None, workspace: Path | None = None,
    ) -> BwrapSandbox | None:
        """Get an existing sandbox for the session, or create a new one.

        client_id (Phase 30): Required for multi-tenant isolation.
        workspace: Override the manager's default workspace for this sandbox.
        When provided, the internal sandbox key is namespaced as
        ``client_id:session_key`` to prevent cross-client sandbox sharing.

        Returns None if sandboxing is not available or disabled.
        Thread-safe: uses threading.Lock for dict access, releases it during
        the slow sandbox.start() call so other threads are not blocked.
        """
        if not self.enabled:
            return None

        # Allow lazy initialization: if initialize() hasn't completed yet
        # (it runs in background after the bridge ready signal), check
        # availability on demand.  The _ensure_wsl_deps auto-install has
        # its own cache so repeated calls are cheap after the first.
        if not self._initialized:
            if not await BwrapSandbox.is_available(
                wsl_distro=self.wsl_distro,
                auto_install_deps=self.auto_install_deps,
            ):
                return None
            # Do NOT set _initialized here — let the background
            # initialize() task handle that along with stale cleanup.

        sandbox_key = self._sandbox_key(session_key, client_id=client_id)

        with self._lock:
            if sandbox_key in self._sandboxes:
                sandbox = self._sandboxes[sandbox_key]
                if sandbox.is_running:
                    return sandbox
                # Sandbox was stopped, remove and recreate
                del self._sandboxes[sandbox_key]

            # Prevent concurrent creation of the same sandbox (Issue #221)
            created_here = False
            if sandbox_key in self._creating:
                logger.info(
                    "Sandbox {} is already being created — using local fallback",
                    sandbox_key,
                )
                return None
            else:
                self._creating.add(sandbox_key)
                created_here = True

            need_evict = len(self._sandboxes) >= self.max_sandboxes

        # All paths after this point must clean up self._creating
        try:
            # Evict outside the lock: _evict_oldest() acquires self._lock internally
            if need_evict:
                await self._evict_oldest()

            # Re-check after eviction (another thread may have created this sandbox)
            with self._lock:
                if sandbox_key in self._sandboxes:
                    sandbox = self._sandboxes[sandbox_key]
                    if sandbox.is_running:
                        return sandbox

            sandbox = BwrapSandbox(
                session_key=sandbox_key,
                workspace=(
                    workspace if workspace is not None
                    else self._resolve_session_workspace(session_key, client_id=client_id) or self.workspace
                ),
                sandbox_base_dir=self.sandbox_base_dir if not self.wsl_distro else None,
                share_net=self.share_net,
                wsl_distro=self.wsl_distro,
                wsl_base_dir=self.wsl_base_dir,
                sandbox_distro_name=self.sandbox_distro_name,
                auto_install_deps=self.auto_install_deps,
            )

            try:
                await sandbox.start()
                with self._lock:
                    self._sandboxes[sandbox_key] = sandbox
                    self._save_state()
                logger.info(
                    "Created sandbox for session: {} (client={})",
                    session_key, client_id,
                )
                return sandbox
            except BwrapSandboxError as exc:
                logger.error(
                    "Failed to create sandbox for {} (client={}): {}",
                    session_key, client_id, exc,
                )
                return None
        finally:
            if created_here:
                with self._lock:
                    self._creating.discard(sandbox_key)

    async def activate(
        self, session_key: str, *, client_id: str | None = None,
    ) -> BwrapSandbox | None:
        """Set the active sandbox for the given session.

        This is called when the user switches to a different conversation.
        Returns the activated sandbox, or None if not available.
        """
        sandbox_key = self._sandbox_key(session_key, client_id=client_id)
        sandbox = await self.get_or_create(session_key, client_id=client_id)
        with self._lock:
            self._active_key = sandbox_key
        return sandbox

    async def destroy(
        self, session_key: str, *, client_id: str | None = None,
    ) -> bool:
        """Stop and remove a sandbox for the given session."""
        sandbox_key = self._sandbox_key(session_key, client_id=client_id)
        with self._lock:
            sandbox = self._sandboxes.pop(sandbox_key, None)
            if sandbox is None:
                return False

        await sandbox.stop()
        self._save_state()

        if self._active_key == sandbox_key:
            self._active_key = None

        logger.info(
            "Destroyed sandbox for session: {} (client={})",
            session_key, client_id,
        )
        return True

    async def destroy_all(self) -> int:
        """Stop and remove all sandboxes. Returns count destroyed."""
        with self._lock:
            sandboxes = list(self._sandboxes.items())
            self._sandboxes.clear()
            self._active_key = None

        count = 0
        for key, sandbox in sandboxes:
            await sandbox.stop()
            count += 1

        # Save empty state after destroying all
        self._save_state()
        return count

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def active_sandbox(self) -> BwrapSandbox | None:
        """Get the currently active sandbox."""
        if self._active_key and self._active_key in self._sandboxes:
            return self._sandboxes[self._active_key]
        return None

    @property
    def active_key(self) -> str | None:
        return self._active_key

    @property
    def sandbox_count(self) -> int:
        return len(self._sandboxes)

    def get_sandbox(
        self, session_key: str, *, client_id: str | None = None,
    ) -> BwrapSandbox | None:
        """Get a sandbox by session key without creating one."""
        sandbox_key = self._sandbox_key(session_key, client_id=client_id)
        return self._sandboxes.get(sandbox_key)

    def list_sandboxes(self) -> list[dict[str, Any]]:
        """List all active sandboxes with their status."""
        result = []
        for key, sandbox in self._sandboxes.items():
            result.append({
                "session_key": key,
                "is_active": key == self._active_key,
                "is_running": sandbox.is_running,
                "workspace": sandbox.workspace_path,
                "distro": getattr(sandbox, "_detected_distro", ""),
            })
        return result

    # ── Internal ───────────────────────────────────────────────────────

    def _pick_eviction_candidate(self) -> str | None:
        """Pick a sandbox key to evict (FIFO, prefer non-active). Must hold _lock."""
        if not self._sandboxes:
            return None
        for key in list(self._sandboxes.keys()):
            if key != self._active_key:
                return key
        # All are active? Pick the first one
        return next(iter(self._sandboxes), None)

    async def _evict_key(self, key: str) -> None:
        """Evict a specific sandbox by key. Called outside the lock."""
        with self._lock:
            sandbox = self._sandboxes.pop(key, None)
        if sandbox is None:
            return
        await sandbox.stop()
        self._save_state()
        if self._active_key == key:
            self._active_key = None
        logger.info("Evicted sandbox for session: {}", key)

    async def _evict_oldest(self) -> None:
        """Evict the oldest (FIFO) sandbox. Legacy helper."""
        key = None
        with self._lock:
            key = self._pick_eviction_candidate()
        if key:
            await self._evict_key(key)

    def _resolve_session_workspace(
        self, session_key: str, *, client_id: str | None = None
    ) -> Path | None:
        """Look up the workspace for a session from metadata, if available.

        Passes client_id through to the resolver so multi-tenant ownership
        is enforced when reading session metadata (Phase 30 isolation).
        """
        if self._session_workspace_resolver is None:
            return None
        try:
            ws = self._session_workspace_resolver(session_key, client_id=client_id)
            if ws and isinstance(ws, str):
                return Path(ws)
            return ws if isinstance(ws, Path) else None
        except Exception:
            return None
