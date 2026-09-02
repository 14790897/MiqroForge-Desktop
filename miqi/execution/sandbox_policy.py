"""Sandbox policy resolution engine.

Determines which sandbox type and permissions to use for a tool execution.
Supports escalating from strict to weaker isolation on denial.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from miqi.protocol.permissions import (
    FileSystemAccessMode,
    FileSystemPathRule,
    FileSystemSandboxPolicy,
    NetworkSandboxPolicy,
)


def _office_target_path(tool_name: str, arguments: Any) -> str:
    path = (
        arguments.get("path")
        or arguments.get("file_path")
        or arguments.get("filename", "")
    )
    if not path:
        return ""
    suffix_by_tool = {
        "create_docx": ".docx",
        "docx_write": ".docx",
        "edit_docx": ".docx",
        "create_xlsx": ".xlsx",
        "xlsx_write": ".xlsx",
        "append_xlsx": ".xlsx",
        "create_pptx": ".pptx",
        "pptx_write": ".pptx",
    }
    suffix = suffix_by_tool.get(tool_name)
    if suffix is None:
        return str(path)
    path_str = str(path)
    path_lower = path_str.lower()
    slash_idx = max(path_str.rfind("/"), path_str.rfind("\\"))
    dot_idx = path_str.rfind(".")
    if dot_idx > slash_idx and path_lower[dot_idx:] == suffix:
        return str(path)
    if dot_idx > slash_idx:
        return path_str[:dot_idx] + suffix
    return path_str + suffix


class SandboxType(str, Enum):
    """Available sandbox isolation levels."""
    NONE = "none"          # No sandbox — direct execution
    BWRAP = "bwrap"        # Linux bubblewrap (strongest isolation)
    LANDLOCK = "landlock"  # Linux Landlock LSM (lighter than bwrap)
    RESTRICTED = "restricted"  # Process-level restrictions only


class SandboxDeniedError(Exception):
    """Raised when the selected sandbox denies the execution."""
    pass


@dataclass
class SandboxSelection:
    """Resolved sandbox configuration for a tool execution."""
    sandbox_type: SandboxType
    filesystem_policy: FileSystemSandboxPolicy
    network_policy: NetworkSandboxPolicy
    env_passthrough: list[str] = field(default_factory=list)
    timeout_ms: int = 30_000
    reason: str = ""


class SandboxPolicyEngine:
    """Resolves sandbox policy for tool executions.

    Base selection:
      exec → bwrap (strongest) when available, else LANDLOCK when a real
             adapter exists, else NONE — no sandbox available means direct
             host execution without restrictions (the user runs without
             isolation by choice).
    Escalation strategy (on denial):
      Attempt 0 → bwrap (strongest)
      Attempt 1 → landlock (medium) — only if landlock_supported AND landlock_available
      Attempt 2 → restricted (weakest)
      Fallback → NONE for read-only tools only; exec NEVER falls back to NONE.
    """

    # MiQi currently has NO real Landlock adapter.
    # Even if the host kernel supports Landlock LSM, there is no integration
    # code to set up Landlock rulesets.  This flag must remain False until
    # a real Landlock sandbox implementation is added (Phase 33.4+).
    _LANDLOCK_SUPPORTED: bool = False

    # Tools that never need sandboxing (pure read operations)
    NO_SANDBOX_TOOLS: frozenset[str] = frozenset({
        "read_file", "list_dir", "web_search", "web_fetch",
        "paper_search", "paper_get", "session_search",
        "trace_search", "memory", "plan_create", "plan_update",
        "docx_read", "pptx_read", "xlsx_read",
    })

    # Tools that always benefit from strongest sandbox
    STRONG_SANDBOX_TOOLS: frozenset[str] = frozenset({
        "exec",
    })

    # Phase 34: file mutation tools that must never fall back to NONE.
    # Includes future tools (delete_file) so policy is stable even before
    # the agent tool exists.  Office document write tools are included so
    # approval, sandbox policy, and Phase 32 workspace enforcement are
    # aligned.
    FILE_MUTATION_TOOLS: frozenset[str] = frozenset({
        "write_file",
        "edit_file",
        "delete_file",
        "apply_patch",
        "docx_write",
        "pptx_write",
        "xlsx_write",
        "create_docx",
        "create_pptx",
        "create_xlsx",
        "edit_docx",
        "append_xlsx",
    })

    def __init__(
        self,
        bwrap_available: bool | Callable[[], bool] = False,
        landlock_available: bool = False,
        default_timeout_ms: int = 30_000,
        allow_fallback_to_none: bool | Callable[[], bool] = True,
    ):
        # 可传 callable：沙箱管理器初始化是异步后台任务（#875 CI/产品发现——
        # 会话在沙箱就绪前创建时，冻结的 bool 会让该会话的 exec 永远落到
        # NONE（宿主机无隔离直连），即使沙箱随后就绪也拿不到 BWRAP 选择，
        # 静默绕过用户配置的沙箱隔离。callable 在每次 select() 时求值，
        # 一旦沙箱就绪，既有会话也立即获得 BWRAP 保护。
        self._bwrap_available_provider = bwrap_available
        # allow_fallback_to_none 同样支持 callable（#875 第二轮评估 B7 不变量）：
        # 用户显式关闭沙箱 → True（NONE=宿主机模式是显式选择）；
        # 沙箱开启但不可用 → False（绝不静默降级到宿主机执行）。
        self._allow_fallback_provider = allow_fallback_to_none
        # landlock_available reflects host-kernel capability only.
        # landlock_supported reflects whether MiQi has a real adapter.
        # Both must be True for LANDLOCK to ever be selected.
        self.landlock_available = landlock_available
        self.landlock_supported = self._LANDLOCK_SUPPORTED
        self.default_timeout_ms = default_timeout_ms

    @property
    def bwrap_available(self) -> bool:
        """Evaluate the current bwrap availability (bool or live callable)."""
        provider = self._bwrap_available_provider
        if callable(provider):
            try:
                return bool(provider())
            except Exception:
                return False  # fail-closed: treat as unavailable on error
        return bool(provider)

    def _allow_fallback_to_none(self) -> bool:
        """Evaluate whether NONE (host-execution) fallback is permitted."""
        provider = self._allow_fallback_provider
        if callable(provider):
            try:
                return bool(provider())
            except Exception:
                return False  # fail-closed: 求值失败视为不允许降级
        return bool(provider)

    async def select(
        self,
        ctx: Any,
        attempt: int = 0,
    ) -> SandboxSelection:
        """Select the appropriate sandbox for a tool execution."""
        tool_name = ctx.tool_name

        # 1. Read-only tools: no sandbox needed
        if tool_name in self.NO_SANDBOX_TOOLS:
            return SandboxSelection(
                sandbox_type=SandboxType.NONE,
                filesystem_policy=FileSystemSandboxPolicy(
                    default_mode=FileSystemAccessMode.READ,
                ),
                network_policy=NetworkSandboxPolicy.ALLOW_ALL,
                reason="Read-only tool, sandbox not required",
            )

        # 2. Determine base sandbox type
        base_type = self._base_sandbox_type(tool_name)

        # A profile-level network="none" is an explicit hard denial:
        # keep RESTRICTED enforcement (workspace confinement and
        # fail-closed network) even when no sandbox is available.
        if base_type == SandboxType.NONE and tool_name == "exec":
            profile = getattr(ctx, "permission_profile", None)
            if getattr(profile, "network", None) == "none":
                base_type = SandboxType.RESTRICTED

        # #875 第二轮评估（B7 不变量）：沙箱开启但 bwrap 不可用（初始化窗口 /
        # 初始化失败 / 引擎求值失败）→ 绝不静默降级到 NONE（宿主机直连）——
        # NONE 只允许来自用户的显式沙箱关闭（fallback 求值为 True）。
        if (
            base_type == SandboxType.NONE
            and tool_name == "exec"
            and not self._allow_fallback_to_none()
        ):
            raise SandboxDeniedError(
                "沙箱已开启但尚未就绪或不可用——exec 已拒绝，避免无隔离的"
                "宿主机执行。请稍后重试，或在 设置 > 沙箱隔离 中关闭沙箱后"
                "改用显式的宿主机模式。"
            )

        # 3. Escalate on retry (NONE is NOT in the escalation chain —
        #    fallback to NONE is gated by _resolve_fallback() below.)
        escalation = self._escalation_chain(base_type)
        if attempt < len(escalation):
            selected = escalation[attempt]
            reason = f"Selected {selected.value} (attempt {attempt}) for {tool_name}"
        else:
            # All sandbox types exhausted — resolve fallback per tool type
            selected = self._resolve_fallback(tool_name)
            reason = (
                f"Fallback to {selected.value} for {tool_name} — "
                f"all sandbox types exhausted after {attempt} attempts"
            )

        # Phase 33.4: enrich reason with sandbox availability context
        if tool_name == "exec" and selected in (
            SandboxType.RESTRICTED, SandboxType.NONE,
        ):
            parts: list[str] = []
            if not self.bwrap_available:
                parts.append("bwrap unavailable")
            if self.landlock_available and not self.landlock_supported:
                parts.append("landlock_available=True but landlock_supported=False (no Landlock adapter)")
            elif not self.landlock_available:
                parts.append("landlock unavailable")
            if parts:
                suffix = (
                    " — direct host execution without restrictions"
                    if selected == SandboxType.NONE
                    else " — no stronger sandbox available"
                )
                reason += (
                    " (" + ", ".join(parts) + suffix + ")"
                )
        elif (
            selected == SandboxType.BWRAP
            and tool_name == "exec"
            and self.landlock_available
            and not self.landlock_supported
        ):
            # BWRAP available but LANDLOCK was configured yet unsupported —
            # callers should know the escalation chain skips LANDLOCK.
            reason += (
                " (landlock_available=True but landlock_supported=False"
                " — MiQroForge has no Landlock adapter; escalation will skip to RESTRICTED)"
            )

        # 4. Build permissions
        fs_policy = self._filesystem_policy_for_tool(tool_name, ctx)
        net_policy = self._network_policy_for_tool(tool_name, ctx)

        # Phase 33.3: RESTRICTED cannot enforce network isolation via
        # direct host execution, so it fails closed unless the permission
        # profile explicitly sets network_allowed=True.  RESTRICTED is
        # selected for exec only when a stronger sandbox was available
        # but execution was downgraded, or when the profile explicitly
        # denies network (network="none").  network="none" is a hard
        # denial and blocks even over network_allowed=True.
        if selected == SandboxType.RESTRICTED and tool_name == "exec":
            profile = getattr(ctx, "permission_profile", None)
            network_allowed = (
                getattr(profile, "network_allowed", False)
                if profile is not None
                else False
            )
            network_denied = (
                getattr(profile, "network", None) == "none"
                if profile is not None
                else False
            )
            if network_denied or not network_allowed:
                net_policy = NetworkSandboxPolicy.BLOCK_ALL

        return SandboxSelection(
            sandbox_type=selected,
            filesystem_policy=fs_policy,
            network_policy=net_policy,
            env_passthrough=ctx.arguments.get("env_passthrough", []),
            timeout_ms=self.default_timeout_ms,
            reason=reason,
        )

    def _base_sandbox_type(self, tool_name: str) -> SandboxType:
        """Determine the preferred sandbox type for a tool.

        LANDLOCK requires BOTH:
          - landlock_available (host kernel supports Landlock LSM)
          - landlock_supported (MiQi has a real Landlock adapter)
        Currently landlock_supported is always False.
        """
        if tool_name in self.STRONG_SANDBOX_TOOLS:
            if self.bwrap_available:
                return SandboxType.BWRAP
            if self.landlock_available and self.landlock_supported:
                return SandboxType.LANDLOCK
            # No sandbox available: direct host execution without
            # restrictions (the user runs without isolation by choice).
            return SandboxType.NONE

        # File mutation tools: moderate isolation
        if tool_name in self.FILE_MUTATION_TOOLS:
            return SandboxType.RESTRICTED

        return SandboxType.NONE

    @staticmethod
    def _escalation_chain(base: SandboxType) -> list[SandboxType]:
        """Build the escalation chain from base type downward.

        NONE is deliberately excluded — fallback to NONE is handled
        separately in _resolve_fallback() with tool-specific gating.
        """
        chain: list[SandboxType] = [base]
        all_types = [
            SandboxType.BWRAP,
            SandboxType.LANDLOCK,
            SandboxType.RESTRICTED,
        ]
        try:
            start_idx = all_types.index(base)
            for t in all_types[start_idx + 1:]:
                chain.append(t)
        except ValueError:
            pass
        return chain

    def _resolve_fallback(
        self,
        tool_name: str,
    ) -> SandboxType:
        """Resolve what happens when all sandbox types are exhausted.

        Rules:
          - Read-only tools (NO_SANDBOX_TOOLS) always get NONE.
          - Exec NEVER falls back to NONE — fail closed.
          - File mutation tools NEVER fall back to NONE — fail closed.
            allow_fallback_to_none does not affect file mutation tools.
          - Other tools fall back to NONE only if allow_fallback_to_none
            is True.
        """
        if tool_name in self.NO_SANDBOX_TOOLS:
            return SandboxType.NONE

        if tool_name == "exec":
            raise SandboxDeniedError(
                "No sandbox available for exec — "
                "NONE fallback is disabled for exec because it would "
                "run arbitrary commands directly on the host without "
                "any isolation. Configure bwrap_available=True or "
                "set network_allowed=True on the permission profile "
                "to allow RESTRICTED execution."
            )

        if tool_name in self.FILE_MUTATION_TOOLS:
            raise SandboxDeniedError(
                f"No sandbox available for {tool_name} — "
                "NONE fallback is disabled for file mutation tools "
                "because they modify the workspace. "
                "Use the tool's workspace-bound path enforcement "
                "or configure a supported sandbox."
            )

        if self._allow_fallback_to_none():
            return SandboxType.NONE

        raise SandboxDeniedError(
            f"No sandbox available for {tool_name} and "
            "allow_fallback_to_none is False."
        )

    @staticmethod
    def _filesystem_policy_for_tool(
        tool_name: str,
        ctx: Any,
    ) -> FileSystemSandboxPolicy:
        """Build filesystem policy for a tool execution."""
        if tool_name == "exec":
            return FileSystemSandboxPolicy(
                rules=[],
                default_mode=FileSystemAccessMode.READ,
                deny_hidden=False,
            )

        # Phase 34: all file mutation tools get a WRITE rule for the
        # target path.  write_file / edit_file / delete_file use "path";
        # office document write tools use "file_path".
        if tool_name in SandboxPolicyEngine.FILE_MUTATION_TOOLS:
            path = _office_target_path(tool_name, ctx.arguments)
            rules = []
            if path:
                rules.append(FileSystemPathRule(
                    path=path,
                    mode=FileSystemAccessMode.WRITE,
                ))
            return FileSystemSandboxPolicy(
                rules=rules,
                default_mode=FileSystemAccessMode.READ,
            )

        return FileSystemSandboxPolicy(
            default_mode=FileSystemAccessMode.READ,
        )

    @staticmethod
    def _network_policy_for_tool(
        tool_name: str,
        ctx: Any,
    ) -> NetworkSandboxPolicy:
        """Build network policy for a tool execution."""
        if tool_name in frozenset({"web_search", "web_fetch"}):
            return NetworkSandboxPolicy.ALLOW_ALL
        if tool_name == "exec":
            return NetworkSandboxPolicy.ALLOW_ALL
        return NetworkSandboxPolicy.ALLOW_ALL
