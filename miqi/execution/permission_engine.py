"""Permission policy decision engine.

Consults (in order):
1. Config-based deny rules (checked first — explicit blocks always win)
2. Read-only tools → auto-allow (unless blocked by deny pattern)
3. Session-scoped allowlist
4. Permanent whitelist
5. Shell safety check (metacharacter-aware)
6. File write approval
7. Default: deny-by-default (APPROVAL_REQUIRED)
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from miqi.execution.exec_policy import PolicyVerdict

# Shell metacharacters that indicate command chaining or injection
_SHELL_METACHAR_PATTERN = re.compile(r"[;&|`$(){}\[\]<>!\n\r]")


def _office_target_path(tool_name: str, arguments: dict[str, Any]) -> str:
    path = (
        arguments.get("path", "")
        or arguments.get("file_path", "")
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


def _format_manual_hint(tool_name: str, arguments: dict) -> str:
    """Format a human-readable hint for manual mode approval dialogs."""
    if tool_name in ("write_file", "edit_file", "apply_patch"):
        path = str(arguments.get("path") or arguments.get("file_path") or "")
        return f"修改文件: {path}" if path else "修改文件"
    if tool_name == "exec":
        cmd = str(arguments.get("command", ""))
        return f"执行命令: {cmd[:80]}" if cmd else "执行命令"
    if tool_name in ("delete", "move"):
        path = str(arguments.get("path") or "")
        return f"{'删除' if tool_name == 'delete' else '移动'}: {path}" if path else tool_name
    if tool_name in ("web_search", "web_fetch"):
        query = str(arguments.get("query") or arguments.get("url") or "")
        return f"网络请求: {query[:60]}" if query else "网络请求"
    return tool_name


class PermissionVerdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


@dataclass
class PermissionDecision:
    verdict: PermissionVerdict
    category: str = ""
    description: str = ""
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    allow_permanent: bool = False


class PermissionEngine:
    """Central permission decision engine.

    Deny-by-default architecture: unknown tools require approval.
    Explicit deny patterns take precedence over everything else.
    """

    # Read-only tools: auto-allow (but deny patterns still checked first).
    # Network-backed tools route through the "network" approval category so
    # bypassNetworkApproval has a real permission path.
    READ_ONLY_TOOLS: frozenset[str] = frozenset({
        "read_file", "list_dir",
        "session_search", "trace_search",
        "docx_read", "pptx_read", "xlsx_read",
        # #1104: 只写会话台账的归类标记，不动用户文件 → 免确认
        "declare_result_files",
    })

    NETWORK_TOOLS: frozenset[str] = frozenset({
        "web_search", "web_fetch",
        "paper_search", "paper_get", "paper_download",
    })

    FILE_WRITE_TOOLS: frozenset[str] = frozenset({
        "write_file", "edit_file", "delete_file", "apply_patch",
        "docx_write", "pptx_write", "xlsx_write",
        "create_docx", "create_pptx", "create_xlsx",
        "edit_docx", "append_xlsx",
    })

    TOOL_CONFIRMATION_TOOLS: frozenset[str] = frozenset({
        "memory",
        "message",
        "skill_manage",
        "plan_create",
        "plan_update",
        "spawn",
        "task_begin",
        "task_end",
        "cron",
        # SURE 项目核查工具(MCP server 'sure' 的 5 个工具,#1256 D5-A)。
        # 此前落 unknown-tool 默认分支:每次调用都弹审批、无可「记住」选项、
        # 文案为 "Unknown tool: …"。加入后仍弹审批但可「永久允许」。
        # 注意:成员匹配为精确匹配(tool_name in ...),前缀规则不生效;
        # 名字由 mcp_<server>_<tool> 命名规则生成(miqi/agent/tools/mcp.py)。
        "mcp_sure_sure_check",
        "mcp_sure_sure_get_report",
        "mcp_sure_sure_get_repair",
        "mcp_sure_sure_recheck",
        "mcp_sure_sure_status",
    })

    # SURE 项目核查工具（MCP server 'sure'，命名 mcp_sure_<tool>）。
    # 这三个工具带 project 参数：省略时 SURE 核查的是它**自己的启动目录**而不是
    # 用户项目（PoC 已复现）。路径运行时保护 v1 = 拒绝制——缺失/非绝对路径直接
    # DENY 并提示改传绝对路径（#1256 阶段 1 子项）。前提：MCP server 名为 'sure'
    # 的标准配置；服务器改名后工具名变为 mcp_<server>_<tool>，本保护不随之生效
    # （与 D5-A 审批名单同一前提）。
    SURE_PROJECT_TOOLS: frozenset[str] = frozenset({
        "mcp_sure_sure_check",
        "mcp_sure_sure_recheck",
        "mcp_sure_sure_get_repair",
    })

    # Safe shell commands: auto-allow (metacharacter-free commands only)
    # Trailing spaces allow exact match; the matcher strips input before
    # comparing, so bare "ls" matches "ls " and "pwd" matches "pwd ".
    SAFE_COMMAND_PREFIXES: tuple[str, ...] = (
        "ls ", "cat ", "head ", "tail ", "wc ", "grep ",
        "find ", "which ", "pwd ", "echo ", "date ", "whoami ",
        "git status", "git log", "git diff", "git branch",
        "python --version", "node --version",
        "cargo --version", "npm --version", "pip list",
        "poetry --version", "uv --version", "dir ", "type ",
    )

    def __init__(
        self,
        permanent_allowlist: set[str] | None = None,
        deny_patterns: set[str] | None = None,
        session_allowlist: set[str] | None = None,
        approval_bypass: Any | None = None,
    ):
        self.permanent_allowlist = permanent_allowlist or set()
        self.deny_patterns = deny_patterns or set()
        # Phase 31.6: session-scoped allowlist (cleared when session ends)
        self.session_allowlist = session_allowlist or set()
        self.approval_bypass = approval_bypass

    async def check(self, ctx: Any) -> PermissionDecision:
        """Check whether a tool call is permitted.

        Args:
            ctx: ToolExecutionContext with tool_name, arguments.

        Returns:
            PermissionDecision with the verdict.
        """
        tool_name = ctx.tool_name
        profile = getattr(ctx, "permission_profile", None)

        # 1. Deny list check FIRST — explicit blocks always win
        for pattern in self.deny_patterns:
            if pattern in tool_name or pattern in str(ctx.arguments):
                return PermissionDecision(
                    verdict=PermissionVerdict.DENY,
                    reason=f"Matches deny pattern: {pattern}",
                )

        # 1.5 SURE 路径运行时保护（#1256）：带 project 的核查工具必须显式传
        # **绝对路径**——省略时 SURE 会核查它自己的启动目录，而不是用户项目
        # （PoC 已复现）。这是结构性错误、不是可授权的偏好，因此位于任何放行
        # 名单 / 审批绕过之前：`*:*` 通配放行或 plan mode 的 bypass 都不能让它
        # 通过；拒绝理由直接把修正方法写给模型（它可自纠后重试）。
        if tool_name in self.SURE_PROJECT_TOOLS:
            project = ctx.arguments.get("project")
            if not isinstance(project, str) or not project.strip():
                return PermissionDecision(
                    verdict=PermissionVerdict.DENY,
                    reason=(
                        "缺少 project 参数。SURE 核查工具必须显式传项目绝对路径——"
                        "省略时 SURE 会核查它自己的启动目录，而不是用户的项目。"
                        "请把 project 设为该项目的绝对路径后重试。"
                    ),
                )
            if not Path(project).is_absolute():
                # 用 Path(...).is_absolute() 而非 os.path.isabs:后者在 Python<3.13
                # 的 Windows 上对盘符相对路径(如 `\repo`)返回 True——它相对的是
                # "当前盘符",不是用户项目;Path 要求完全限定(带盘符的根),
                # 与"必须是绝对路径"的守卫语义一致(CodeRabbit #1266)。
                return PermissionDecision(
                    verdict=PermissionVerdict.DENY,
                    reason=(
                        f"project 必须是绝对路径（收到：{project!r}）。"
                        "请传该项目在工作区中的绝对路径后重试。"
                    ),
                )

        # 1a. 确认卡本身是「用户决策入口」——它必须在审批层之前跑起来，
        # 否则 manual 的 force_approval / 未知工具的审批会先把它挡住，
        # 用户永远看不到那张卡（卡就是确认动作，再套一层审批即死锁）。
        # 放在 deny list 之后：显式封禁仍然优先。
        if tool_name == "ask_user_confirm_card":
            return PermissionDecision(verdict=PermissionVerdict.ALLOW)

        # 1b. Execution policy: bypass skips all checks.
        # IMPORTANT: safety relies on the caller filtering tool availability
        # BEFORE setting this flag (e.g. plan mode removes write/exec tools
        # via PLAN_BLOCKED_TOOLS before setting bypass_approval=True).
        # The deny-list check above still wins; this only skips the
        # category-based approval flow for tools that passed filtering.
        if getattr(ctx, "bypass_approval", False):
            return PermissionDecision(
                verdict=PermissionVerdict.ALLOW,
                category="run",
                reason="Bypassed by execution policy (bypass mode)",
                allow_permanent=False,
            )

        # 1c. Execution policy: manual forces approval for everything
        if getattr(ctx, "force_approval", False):
            detail = _format_manual_hint(tool_name, ctx.arguments)
            return PermissionDecision(
                verdict=PermissionVerdict.APPROVAL_REQUIRED,
                category="run",
                reason="Approval required by execution policy (manual mode)",
                allow_permanent=False,
                description=f"手动模式 · {detail}",
            )

        # 3. Session-scoped allowlist (keyed by tool + arguments)
        cmd_key = self._make_key(ctx)
        if cmd_key in self.session_allowlist:
            return PermissionDecision(verdict=PermissionVerdict.ALLOW)

        # 4. Permanent allowlist (keyed by tool + arguments)
        #    Supports wildcard: "*:*" bypasses all approvals
        if "*:*" in self.permanent_allowlist:
            return PermissionDecision(verdict=PermissionVerdict.ALLOW)
        if cmd_key in self.permanent_allowlist:
            return PermissionDecision(verdict=PermissionVerdict.ALLOW)

        # 4b. Global permanent allowlist (cross-session, persisted to disk)
        try:
            from miqi.agent.command_approval import get_permanent_allowlist as _get_gpa
            gpa = _get_gpa()
            if "*:*" in gpa:
                return PermissionDecision(verdict=PermissionVerdict.ALLOW)
            if cmd_key and cmd_key in gpa:
                return PermissionDecision(verdict=PermissionVerdict.ALLOW)
        except Exception:
            pass

        # 4c. Read-only tools: auto-allow (unless blocked above)
        if tool_name in self.READ_ONLY_TOOLS:
            return PermissionDecision(verdict=PermissionVerdict.ALLOW)

        # 5. Exec tool branch
        if tool_name == "exec":
            cmd = str(ctx.arguments.get("command", ""))

            # Declarative ExecPolicy takes precedence when present
            if profile is not None and getattr(profile, "exec_policy", None) is not None:
                policy_decision = profile.exec_policy.evaluate_command(cmd)
                if policy_decision.verdict == PolicyVerdict.DENY:
                    return PermissionDecision(
                        verdict=PermissionVerdict.DENY,
                        reason=f"Denied by exec policy: {policy_decision.source}",
                    )
                if policy_decision.verdict == PolicyVerdict.ALLOW:
                    # Policy allows override the legacy safe-prefix whitelist, but
                    # shell metacharacters still force approval to prevent injection.
                    if _SHELL_METACHAR_PATTERN.search(cmd.strip()):
                        return self._apply_approval_policy(
                            PermissionDecision(
                                verdict=PermissionVerdict.APPROVAL_REQUIRED,
                                category="exec",
                                description=f"Policy allowed but command contains shell metacharacters: {cmd[:100]}",
                                details={"command": cmd},
                                allow_permanent=True,
                            ),
                            profile,
                        )
                    return PermissionDecision(
                        verdict=PermissionVerdict.ALLOW,
                        reason=f"Allowed by exec policy: {policy_decision.source}",
                    )
                # PROMPT → require approval
                return self._apply_approval_policy(
                    PermissionDecision(
                        verdict=PermissionVerdict.APPROVAL_REQUIRED,
                        category="exec",
                        description=f"Run: {cmd[:100]}",
                        details={"command": cmd},
                        allow_permanent=True,
                    ),
                    profile,
                )

            # Fall-through: legacy profile prefix rules + safe command checks
            if profile is not None:
                parts = cmd.split()
                # Deny rules checked first — explicit blocks always win
                for prefix in getattr(profile, "exec_deny_prefixes", []):
                    if parts[:len(prefix)] == prefix:
                        return PermissionDecision(
                            verdict=PermissionVerdict.DENY,
                            reason=f"Denied by permission profile prefix: {' '.join(prefix)}",
                        )
                # Allow rules — still require metacharacter safety
                for prefix in getattr(profile, "exec_allow_prefixes", []):
                    if parts[:len(prefix)] == prefix:
                        if not self._is_safe_command(cmd):
                            return self._apply_approval_policy(
                                PermissionDecision(
                                    verdict=PermissionVerdict.APPROVAL_REQUIRED,
                                    category="exec",
                                    description=f"Allowed prefix but command contains shell metacharacters: {cmd[:100]}",
                                    details={"command": cmd},
                                ),
                                profile,
                            )
                        return PermissionDecision(
                            verdict=PermissionVerdict.ALLOW,
                            reason=f"Allowed by permission profile prefix: {' '.join(prefix)}",
                        )

            # 6. Shell commands: metacharacter-aware safety check
            if self._is_safe_command(cmd):
                return PermissionDecision(verdict=PermissionVerdict.ALLOW)
            return self._apply_approval_policy(
                PermissionDecision(
                    verdict=PermissionVerdict.APPROVAL_REQUIRED,
                    category="exec",
                    description=f"Run: {cmd[:100]}",
                    details={"command": cmd},
                    allow_permanent=True,
                ),
                profile,
            )

        # 7. File writes: require approval unless whitelisted.
        # Phase 31.7: includes office document write tools so they are
        # explicitly categorized (not falling through to "unknown_tool")
        # and support permanent allowlisting.
        if tool_name in self.FILE_WRITE_TOOLS:
            path = _office_target_path(tool_name, ctx.arguments)
            return self._apply_approval_policy(
                PermissionDecision(
                    verdict=PermissionVerdict.APPROVAL_REQUIRED,
                    category="file_write",
                    description=f"{tool_name}: {path}",
                    details={"path": path, "operation": tool_name},
                    allow_permanent=True,
                ),
                profile,
            )

        # 8. Network-backed tools require approval unless bypassed.
        if tool_name in self.NETWORK_TOOLS:
            target = self._network_target(ctx.arguments)
            return self._apply_approval_policy(
                PermissionDecision(
                    verdict=PermissionVerdict.APPROVAL_REQUIRED,
                    category="network",
                    description=f"{tool_name}: {target}"[:200],
                    details={
                        "tool_name": tool_name,
                        "target": target,
                    },
                    allow_permanent=True,
                ),
                profile,
            )

        # 9. Known stateful/non-read-only tools require generic confirmation.
        if tool_name in self.TOOL_CONFIRMATION_TOOLS:
            return self._apply_approval_policy(
                PermissionDecision(
                    verdict=PermissionVerdict.APPROVAL_REQUIRED,
                    category="tool_confirmation",
                    description=f"{tool_name}: {self._tool_target(ctx.arguments)}"[:200],
                    details={
                        "tool_name": tool_name,
                        "arguments": ctx.arguments,
                    },
                    allow_permanent=True,
                ),
                profile,
            )

        # 10. Default: deny-by-default - unknown tools require approval.
        return self._apply_approval_policy(
            PermissionDecision(
                verdict=PermissionVerdict.APPROVAL_REQUIRED,
                category="tool_confirmation",
                description=f"Unknown tool: {tool_name}",
                details={"tool_name": tool_name},
            ),
            profile,
        )

    def _is_safe_command(self, cmd: str) -> bool:
        """Check if a shell command is safe to auto-approve.

        Rejects any command containing shell metacharacters
        (;, &, |, `, $, etc.) even if the prefix matches.
        """
        cmd_stripped = cmd.strip()
        if _SHELL_METACHAR_PATTERN.search(cmd_stripped):
            return False
        cmd_lower = cmd_stripped.lower()
        return any(cmd_lower.startswith(p) for p in self.SAFE_COMMAND_PREFIXES)

    def _apply_approval_policy(
        self,
        decision: PermissionDecision,
        profile: Any | None,
        *,
        failed: bool = False,
    ) -> PermissionDecision:
        """Potentially auto-approve a decision based on the active policy."""
        if decision.verdict != PermissionVerdict.APPROVAL_REQUIRED:
            return decision
        policy = getattr(profile, "approval_policy", None)
        if policy is None:
            if self._bypasses_approval(decision.category):
                return self._bypassed_decision(decision)
            return decision
        if self._bypasses_approval(decision.category):
            return self._bypassed_decision(decision)
        if not policy.requires_prompt(category=decision.category, failed=failed):
            return PermissionDecision(
                verdict=PermissionVerdict.ALLOW,
                category=decision.category,
                reason=f"Auto-approved by policy ({policy.mode.value})",
                description=decision.description,
                details=decision.details,
                allow_permanent=decision.allow_permanent,
            )
        return decision

    def _bypasses_approval(self, category: str) -> bool:
        bypass = self.approval_bypass
        if bypass is None:
            return False
        bypasses_category = getattr(bypass, "bypasses_category", None)
        if callable(bypasses_category):
            return bool(bypasses_category(category))
        if getattr(bypass, "bypass_all", False):
            return True
        if category == "exec":
            return bool(getattr(bypass, "bypass_command_approval", False))
        if category == "file_write":
            return bool(getattr(bypass, "bypass_file_write_approval", False))
        if category == "network":
            return bool(getattr(bypass, "bypass_network_approval", False))
        return bool(getattr(bypass, "bypass_tool_confirmation", False))

    @staticmethod
    def _bypassed_decision(decision: PermissionDecision) -> PermissionDecision:
        return PermissionDecision(
            verdict=PermissionVerdict.ALLOW,
            category=decision.category,
            reason="Auto-approved by approval bypass",
            description=decision.description,
            details=decision.details,
            allow_permanent=decision.allow_permanent,
        )

    @staticmethod
    def _network_target(arguments: dict[str, Any]) -> str:
        for key in ("url", "query", "paper_id", "doi", "title"):
            value = arguments.get(key)
            if value:
                return str(value)
        return str(arguments)[:120]

    @staticmethod
    def _tool_target(arguments: dict[str, Any]) -> str:
        for key in ("action", "content", "title", "name"):
            value = arguments.get(key)
            if value:
                return str(value)
        return str(arguments)[:120]

    @staticmethod
    def _make_key(ctx: Any) -> str:
        """Create a stable key for permanent allowlisting."""
        tool = ctx.tool_name
        if tool == "exec":
            return f"exec:{ctx.arguments.get('command', '')}"
        if tool in PermissionEngine.FILE_WRITE_TOOLS:
            return f"{tool}:{_office_target_path(tool, ctx.arguments)}"
        return PermissionEngine.key_for(tool, ctx.arguments)

    @staticmethod
    def key_for(tool_name: str, arguments: Any) -> str:
        """tool + 参数 的稳定键（「本次会话允许 / 永久允许」的记录端与匹配端共用）。

        不能用内置 ``hash()``：它对 str 每进程加盐（PYTHONHASHSEED 随机），
        dict 顺序一变键也变——持久化的「永久允许」在重启后/不同参数顺序下
        永远匹配不上（任何记录都成为空承诺，见 #1256 D5-A 验收）。
        改用 canonical JSON（sort_keys）的 sha256 前 16 位十六进制。
        """
        try:
            canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001 — 极端不可序列化参数退回 repr，键仍稳定
            canonical = repr(arguments)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return f"{tool_name}:{digest}"
