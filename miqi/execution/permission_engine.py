"""Permission policy decision engine.

Consults (in order):
1. Config-based deny rules (checked first — explicit blocks always win)
2. Interactive user-input tools → their own inline card, never a second approval dialog
3. Execution-policy bypass/manual handling
4. Read-only tools → auto-allow (unless blocked by deny pattern)
5. Session/permanent allowlists
6. Shell safety / file / network approval
7. Default: deny-by-default (APPROVAL_REQUIRED)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from miqi.execution.approval_policy import ApprovalPolicy
from miqi.execution.exec_policy import PolicyVerdict


_SHELL_METACHAR_PATTERN = re.compile(r"[;&|`$(){}\[\]<>!\n\r]")


def _office_target_path(tool_name: str, arguments: dict[str, Any]) -> str:
    path = arguments.get("path", "") or arguments.get("file_path", "") or arguments.get("filename", "")
    if not path:
        return ""
    suffix_by_tool = {
        "create_docx": ".docx", "docx_write": ".docx", "edit_docx": ".docx",
        "create_xlsx": ".xlsx", "xlsx_write": ".xlsx", "append_xlsx": ".xlsx",
        "create_pptx": ".pptx", "pptx_write": ".pptx",
    }
    suffix = suffix_by_tool.get(tool_name)
    if suffix is None:
        return str(path)
    path_str = str(path)
    path_lower = path_str.lower()
    slash_idx = max(path_str.rfind("/"), path_str.rfind("\\"))
    dot_idx = path_str.rfind(".")
    if dot_idx > slash_idx and path_lower[dot_idx:] == suffix:
        return path_str
    if dot_idx > slash_idx:
        return path_str[:dot_idx] + suffix
    return path_str + suffix


def _format_manual_hint(tool_name: str, arguments: dict) -> str:
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
    """Central permission decision engine with deny-by-default semantics."""

    INTERACTIVE_CONFIRM_TOOLS: frozenset[str] = frozenset({
        "ask_user_confirm_card",
        "ask_user_plan_confirm",
        "request_action_confirmation",
    })

    READ_ONLY_TOOLS: frozenset[str] = frozenset({
        "read_file", "list_dir", "session_search", "trace_search",
        "docx_read", "pptx_read", "xlsx_read", "todo_write",
    })

    NETWORK_TOOLS: frozenset[str] = frozenset({
        "web_search", "web_fetch", "paper_search", "paper_get", "paper_download",
    })

    FILE_WRITE_TOOLS: frozenset[str] = frozenset({
        "write_file", "edit_file", "delete_file", "apply_patch",
        "docx_write", "pptx_write", "xlsx_write", "create_docx", "create_pptx",
        "create_xlsx", "edit_docx", "append_xlsx",
    })

    TOOL_CONFIRMATION_TOOLS: frozenset[str] = frozenset({
        "memory", "message", "skill_manage", "plan_create", "plan_update", "spawn",
        "task_begin", "task_end", "cron",
    })

    SAFE_COMMAND_PREFIXES: tuple[str, ...] = (
        "ls ", "cat ", "head ", "tail ", "wc ", "grep ", "find ", "which ", "pwd ",
        "echo ", "date ", "whoami ", "git status", "git log", "git diff", "git branch",
        "python --version", "node --version", "cargo --version", "npm --version", "pip list",
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
        self.session_allowlist = session_allowlist or set()
        self.approval_bypass = approval_bypass

    async def check(self, ctx: Any) -> PermissionDecision:
        tool_name = ctx.tool_name
        profile = getattr(ctx, "permission_profile", None)

        # Explicit deny always wins.
        for pattern in self.deny_patterns:
            if pattern in tool_name or pattern in str(ctx.arguments):
                return PermissionDecision(verdict=PermissionVerdict.DENY, reason=f"Matches deny pattern: {pattern}")

        # Interactive tools own their user interaction. This must precede manual
        # force_approval, otherwise the user sees an approval dialog and then a card.
        if tool_name in self.INTERACTIVE_CONFIRM_TOOLS:
            return PermissionDecision(
                verdict=PermissionVerdict.ALLOW,
                reason="interactive confirmation tool",
                category="user_input",
            )

        if getattr(ctx, "bypass_approval", False):
            return PermissionDecision(
                verdict=PermissionVerdict.ALLOW,
                category="run",
                reason="Bypassed by execution policy (bypass mode)",
                allow_permanent=False,
            )

        if getattr(ctx, "force_approval", False):
            detail = _format_manual_hint(tool_name, ctx.arguments)
            return PermissionDecision(
                verdict=PermissionVerdict.APPROVAL_REQUIRED,
                category="run",
                reason="Approval required by execution policy (manual mode)",
                allow_permanent=False,
                description=f"手动模式 · {detail}",
            )

        cmd_key = self._make_key(ctx)
        if cmd_key in self.session_allowlist:
            return PermissionDecision(verdict=PermissionVerdict.ALLOW)
        if "*:*" in self.permanent_allowlist or cmd_key in self.permanent_allowlist:
            return PermissionDecision(verdict=PermissionVerdict.ALLOW)

        try:
            from miqi.agent.command_approval import get_permanent_allowlist as _get_gpa
            gpa = _get_gpa()
            if "*:*" in gpa or (cmd_key and cmd_key in gpa):
                return PermissionDecision(verdict=PermissionVerdict.ALLOW)
        except Exception:
            pass

        if tool_name in self.READ_ONLY_TOOLS:
            return PermissionDecision(verdict=PermissionVerdict.ALLOW)

        if tool_name == "exec":
            cmd = str(ctx.arguments.get("command", ""))
            if profile is not None and getattr(profile, "exec_policy", None) is not None:
                policy_decision = profile.exec_policy.evaluate_command(cmd)
                if policy_decision.verdict == PolicyVerdict.DENY:
                    return PermissionDecision(verdict=PermissionVerdict.DENY, reason=f"Denied by exec policy: {policy_decision.source}")
                if policy_decision.verdict == PolicyVerdict.ALLOW:
                    if _SHELL_METACHAR_PATTERN.search(cmd.strip()):
                        return self._apply_approval_policy(PermissionDecision(
                            verdict=PermissionVerdict.APPROVAL_REQUIRED,
                            category="exec",
                            description=f"Policy allowed but command contains shell metacharacters: {cmd[:100]}",
                            details={"command": cmd},
                            allow_permanent=True,
                        ), profile)
                    return PermissionDecision(verdict=PermissionVerdict.ALLOW, reason=f"Allowed by exec policy: {policy_decision.source}")
                return self._apply_approval_policy(PermissionDecision(
                    verdict=PermissionVerdict.APPROVAL_REQUIRED,
                    category="exec", description=f"Run: {cmd[:100]}",
                    details={"command": cmd}, allow_permanent=True,
                ), profile)
            if profile is not None:
                parts = cmd.split()
                for prefix in getattr(profile, "exec_deny_prefixes", []):
                    if parts[:len(prefix)] == prefix:
                        return PermissionDecision(verdict=PermissionVerdict.DENY, reason=f"Denied by permission profile prefix: {' '.join(prefix)}")
                for prefix in getattr(profile, "exec_allow_prefixes", []):
                    if parts[:len(prefix)] == prefix:
                        if not self._is_safe_command(cmd):
                            return self._apply_approval_policy(PermissionDecision(
                                verdict=PermissionVerdict.APPROVAL_REQUIRED,
                                category="exec", description=f"Allowed prefix but command contains shell metacharacters: {cmd[:100]}",
                                details={"command": cmd},
                            ), profile)
                        return PermissionDecision(verdict=PermissionVerdict.ALLOW, reason=f"Allowed by permission profile prefix: {' '.join(prefix)}")
            if self._is_safe_command(cmd):
                return PermissionDecision(verdict=PermissionVerdict.ALLOW)
            return self._apply_approval_policy(PermissionDecision(
                verdict=PermissionVerdict.APPROVAL_REQUIRED, category="exec",
                description=f"Run: {cmd[:100]}", details={"command": cmd}, allow_permanent=True,
            ), profile)

        if tool_name in self.FILE_WRITE_TOOLS:
            path = _office_target_path(tool_name, ctx.arguments)
            return self._apply_approval_policy(PermissionDecision(
                verdict=PermissionVerdict.APPROVAL_REQUIRED, category="file_write",
                description=f"{tool_name}: {path}", details={"path": path, "operation": tool_name},
                allow_permanent=True,
            ), profile)

        if tool_name in self.NETWORK_TOOLS:
            target = self._network_target(ctx.arguments)
            return self._apply_approval_policy(PermissionDecision(
                verdict=PermissionVerdict.APPROVAL_REQUIRED, category="network",
                description=f"{tool_name}: {target}"[:200],
                details={"tool_name": tool_name, "target": target}, allow_permanent=True,
            ), profile)

        if tool_name in self.TOOL_CONFIRMATION_TOOLS:
            return self._apply_approval_policy(PermissionDecision(
                verdict=PermissionVerdict.APPROVAL_REQUIRED, category="tool_confirmation",
                description=f"{tool_name}: {self._tool_target(ctx.arguments)}"[:200],
                details={"tool_name": tool_name, "arguments": ctx.arguments}, allow_permanent=True,
            ), profile)

        return self._apply_approval_policy(PermissionDecision(
            verdict=PermissionVerdict.APPROVAL_REQUIRED, category="tool_confirmation",
            description=f"Unknown tool: {tool_name}", details={"tool_name": tool_name},
        ), profile)

    def _is_safe_command(self, cmd: str) -> bool:
        cmd_stripped = cmd.strip()
        if _SHELL_METACHAR_PATTERN.search(cmd_stripped):
            return False
        return any(cmd_stripped.lower().startswith(prefix) for prefix in self.SAFE_COMMAND_PREFIXES)

    def _apply_approval_policy(self, decision: PermissionDecision, profile: Any | None, *, failed: bool = False) -> PermissionDecision:
        if decision.verdict != PermissionVerdict.APPROVAL_REQUIRED:
            return decision
        policy = getattr(profile, "approval_policy", None)
        if policy is None:
            return self._bypassed_decision(decision) if self._bypasses_approval(decision.category) else decision
        if self._bypasses_approval(decision.category):
            return self._bypassed_decision(decision)
        if not policy.requires_prompt(category=decision.category, failed=failed):
            return PermissionDecision(
                verdict=PermissionVerdict.ALLOW, category=decision.category,
                reason=f"Auto-approved by policy ({policy.mode.value})",
                description=decision.description, details=decision.details,
                allow_permanent=decision.allow_permanent,
            )
        return decision

    def _bypasses_approval(self, category: str) -> bool:
        bypass = self.approval_bypass
        if bypass is None:
            return False
        fn = getattr(bypass, "bypasses_category", None)
        if callable(fn):
            return bool(fn(category))
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
            verdict=PermissionVerdict.ALLOW, category=decision.category,
            reason="Auto-approved by approval bypass", description=decision.description,
            details=decision.details, allow_permanent=decision.allow_permanent,
        )

    @staticmethod
    def _network_target(arguments: dict[str, Any]) -> str:
        for key in ("url", "query", "paper_id", "doi", "title"):
            if (value := arguments.get(key)):
                return str(value)
        return str(arguments)[:120]

    @staticmethod
    def _tool_target(arguments: dict[str, Any]) -> str:
        for key in ("action", "content", "title", "name"):
            if (value := arguments.get(key)):
                return str(value)
        return str(arguments)[:120]

    @staticmethod
    def _make_key(ctx: Any) -> str:
        tool = ctx.tool_name
        if tool == "exec":
            return f"exec:{ctx.arguments.get('command', '')}"
        if tool in PermissionEngine.FILE_WRITE_TOOLS:
            return f"{tool}:{_office_target_path(tool, ctx.arguments)}"
        return f"{tool}:{hash(str(ctx.arguments))}"
