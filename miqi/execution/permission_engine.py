"""Permission policy decision engine.

Consults (in order):
1. Config-based deny rules (checked first — explicit blocks always win)
2. Interactive user-input tools → their own inline card, never a second approval dialog
3. Action Guard → high-risk actions always confirm; bypass_approval never skips it
   (deferred to step 5 for manual-only turns, where every call already asks)
4. Execution-policy bypass (bypass_approval) → skip the category-based approval flow
5. Execution-policy manual mode (force_approval) → every call asks
6. Read-only tools → auto-allow (unless blocked by deny pattern)
7. Session/permanent allowlists
8. Shell safety / file / network approval
9. Default: deny-by-default (APPROVAL_REQUIRED)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from miqi.execution.exec_policy import PolicyVerdict

# Shell metacharacters that indicate command chaining or injection
_SHELL_METACHAR_PATTERN = re.compile(r"[;&|`$(){}\[\]<>!\n\r]")

# Action Guard 确认缓存上界：防长会话无界增长；超限清空重建，最坏退化为多弹卡
# （安全方向——只可能多问，不可能少问）。
_MAX_ACTION_GUARD_CONFIRMED = 512


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


# P2-b（#1071 评审）：Action Guard 卡面原本只有 tool_name，用户看不到「对什么执行」
# 就等于闭眼确认。这里按通用 key-name 取安全相关字段（不枚举具体工具），拼进 message。
# 取不到任何字段时返回 ""，调用方退回原文案——本函数不影响任何判定逻辑。
_SAFETY_ARG_KEYS: tuple[str, ...] = (
    "path", "file_path", "filepath", "filename", "file_name", "file", "files",
    "destination", "dest", "dst", "source", "src", "target", "url", "uri",
    "command", "cmd", "size", "size_bytes", "bytes", "recursive", "dir", "directory",
)
_MAX_SAFETY_ARG_VALUE = 80
_MAX_SAFETY_ARG_SUMMARY = 200


def _safety_arg_summary(arguments: Any) -> str:
    if not isinstance(arguments, dict) or not arguments:
        return ""
    lowered: dict[str, Any] = {}
    for key, value in arguments.items():
        lowered.setdefault(str(key).lower(), value)
    parts: list[str] = []
    for key in _SAFETY_ARG_KEYS:
        value = lowered.get(key)
        if value is None or value == "" or value is False:
            continue
        if isinstance(value, (dict, list, tuple, set)):
            # 复合值只留个形状（截断），避免把整个 payload 刷到卡面
            value = str(value)[:_MAX_SAFETY_ARG_VALUE]
        else:
            value = str(value)[:_MAX_SAFETY_ARG_VALUE]
        parts.append(f"{key}={value}")
    return "；".join(parts)[:_MAX_SAFETY_ARG_SUMMARY]


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
        # #1104: 只写会话台账的归类标记，不动用户文件 → 免确认
        "declare_result_files",
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
        action_guard_resolver: Any | None = None,
    ):
        self.permanent_allowlist = permanent_allowlist or set()
        self.deny_patterns = deny_patterns or set()
        self.session_allowlist = session_allowlist or set()
        self.approval_bypass = approval_bypass
        # Action Guard（外部复核 9-11）：高危外部副作用（risk>=10：上传/支付/
        # 破坏性删除/外发消息/spawn）在真实派发前强制确认——resolver 即
        # user_input_gate 弹卡通道（无则为 headless，走 APPROVAL_REQUIRED）。
        self.action_guard_resolver = action_guard_resolver
        # 会话级去重（授权模型，产品拍板 2026-09-16）：
        # **确认范围＝同一 thread 内同一工具（thread + tool_name）**；本 thread 内
        # 后续同类动作不再逐一询问——键只有 thread+tool，不看参数。
        # 卡片 payload 里的「（确认后本对话内同类动作将不再逐一询问）」即本模型的
        # 用户侧表述，两者必须同时改。
        #
        # 概念区分：`_action_guard_confirmed`（本决策，安全层兜底）≠
        # `session_allowlist` / `permanent_allowlist`（用户显式「允许并记住」，
        # 按 `_make_key` 键控——参数变了 key 就变）。别把两套机制混在一起改。
        #
        # 曾评估「安全参数摘要（thread+tool+args digest）」方案，因摩擦未采纳——
        # 见 docs/dev-notes/action-guard-confirmation-scope.md。
        self._action_guard_confirmed: set[str] = set()

    async def _action_guard(self, ctx: Any) -> "PermissionDecision | None":
        """fail-closed：should_confirm_action 命中的动作未经用户确认不得执行。

        授权模型：确认范围＝同一 thread 内同一工具（thread + tool_name）；本 thread
        内后续同类动作不再逐一询问。判定仍逐次看参数（should_confirm_action），
        只是确认缓存按 thread+tool 计。

        不依赖模型自觉先调 request_action_confirmation——在真实执行边界兜底。
        """
        arguments = getattr(ctx, "arguments", None)
        try:
            from miqi.execution.task_policy import should_confirm_action

            if not isinstance(arguments, dict):
                # 畸形参数（非 dict）：参数级判定（敏感路径、破坏性删除）做不了。
                # 旧写法会让下面的 _is_sensitive_path 抛 AttributeError，冒泡进
                # except 被吞成「非高危」→ 静默放行。无法判定就按高危处理。
                return self._guard_indeterminate(ctx, "参数不是对象")
            if not should_confirm_action(ctx.tool_name, arguments):
                return None
        except Exception:  # noqa: BLE001
            # 「判定为非高危」与「判定不可用」不是一回事：无法证明该动作无害时
            # 不得放行——本函数的 docstring 声明的是 fail-closed，这里必须做到。
            # 主路径上畸形参数会先被 orchestrator 的 schema 校验挡掉，判定表本身
            # 不可用则属于安装损坏，因此这个分支的爆炸半径只在真正的故障态。
            return self._guard_indeterminate(ctx, "判定不可用")
        key = f"{getattr(ctx, 'thread_id', '')}:{ctx.tool_name}"
        if key in self._action_guard_confirmed:
            return None
        # 模型侧 ActionCard 已在本 turn 对同类动作取得用户确认 → 不重复弹卡。
        # family 级（而非 turn 级）：确认一次 upload 不会顺带放行 spawn/删目录。
        try:
            from miqi.execution.task_policy import action_family

            _fam = action_family(ctx.tool_name)
        except Exception:  # noqa: BLE001
            _fam = None
        if _fam and _fam in (getattr(ctx, "action_confirmed_families", None) or frozenset()):
            return None
        if self.action_guard_resolver is None:
            # headless/CLI：无弹卡通道——不静默放行，交给常规审批流显式要求。
            return PermissionDecision(
                verdict=PermissionVerdict.APPROVAL_REQUIRED,
                category="run",
                reason="危险动作需要确认（Action Guard）",
                description=f"危险动作确认 · {ctx.tool_name}",
                allow_permanent=False,
            )
        # P2-b（#1071 评审）：附上安全相关参数，避免用户「闭眼确认」；取不到则退回原口径。
        _hint = _safety_arg_summary(getattr(ctx, "arguments", None))
        try:
            result = await self.action_guard_resolver(
                {
                    "title": "危险动作确认",
                    "message": (
                        f"模型请求执行高危动作：{ctx.tool_name}"
                        + (f"（{_hint}）" if _hint else "")
                        + "。确认后才真正执行。（确认后本对话内同类动作将不再逐一询问）"
                    ),
                    "choices": [
                        {"id": "confirm", "label": "允许执行", "role": "confirm"},
                        {"id": "cancel", "label": "拒绝", "role": "cancel"},
                    ],
                    "allow_remember_choice": False,
                    "thread_id": getattr(ctx, "thread_id", ""),
                    "turn_id": getattr(ctx, "turn_id", ""),
                    "tool_name": ctx.tool_name,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return PermissionDecision(
                verdict=PermissionVerdict.DENY,
                reason=f"行动确认通道失败（fail-closed）：{exc}",
            )
        answers = result.get("answers") if isinstance(result, dict) else None
        if (
            isinstance(result, dict)
            and result.get("status") == "submitted"
            and isinstance(answers, dict)
            and answers.get("choice_id") == "confirm"
        ):
            if len(self._action_guard_confirmed) >= _MAX_ACTION_GUARD_CONFIRMED:
                self._action_guard_confirmed.clear()
            self._action_guard_confirmed.add(key)
            return None
        return PermissionDecision(
            verdict=PermissionVerdict.DENY,
            reason="用户未确认危险动作（Action Guard）",
        )

    @staticmethod
    def _guard_indeterminate(ctx: Any, why: str) -> PermissionDecision:
        """判定不可用/无法判定时的 fail-closed 决策：不放行，交常规审批准入。

        无弹卡通道时由 orchestrator 兜底（APPROVAL_REQUIRED → 无应答通道即
        deny_no_channel），方向安全——只可能多问，不可能少问。
        """
        return PermissionDecision(
            verdict=PermissionVerdict.APPROVAL_REQUIRED,
            category="run",
            reason=f"危险动作判定不可用（Action Guard fail-closed）：{why}",
            description=f"危险动作确认 · {ctx.tool_name}",
            allow_permanent=False,
        )

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

        # Action Guard（外部复核 9-11，fail-closed）：高危外部副作用在真实派发前
        # 强制确认——模型不先调 request_action_confirmation 也无法绕过。
        #
        # #1102：必须排在 bypass_approval **之前**。auto 模式由执行策略**自动**置位
        # bypass_approval（用户在选择器上授权的是「普通动作免确认」，不是「高危动作
        # 免兜底」——见 docs/design-646-v2-plan-card.md「auto ≠ root」），bypass 若
        # 短路在前，guard 在 auto / plan 下永不执行。
        #
        # 手动模式（force_approval）例外：那里每个动作本来就要确认，guard 的专用卡
        # 不再叠加——两张卡对同一个动作没有增量安全性，而 guard 卡面「同类动作不再
        # 逐一询问」的会话缓存语义在 manual 下并不成立（force 会再次拦下），叠加反而
        # 让卡面文案失真。
        # 例外只对「纯手动」（force 且非 bypass）成立：两标志同时置位时不该让 bypass
        # 借道 force 跳过 guard——那正是 #1102 要堵的语义。bypass 仍优先于 force，
        # 故普通动作在该组合下照旧由 bypass 放行。
        _manual_only = getattr(ctx, "force_approval", False) and not getattr(
            ctx, "bypass_approval", False
        )
        if not _manual_only:
            guard_decision = await self._action_guard(ctx)
            if guard_decision is not None:
                return guard_decision

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
