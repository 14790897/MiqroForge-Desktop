"""Task and action policy for #646.

The product boundary is intentionally small:

1. Plan is a task-level collaboration decision, not a per-write permission
   prompt. Explicitly simple tasks should execute directly in Edit mode.
2. Action confirmation is reserved for high-impact external/destructive
   actions and is independent from Plan.
3. Unknown tools are treated conservatively for task classification.
"""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path


class TaskIntentRisk(IntEnum):
    READ_ONLY = 0
    MODIFY_LOCAL = 2
    EXECUTE = 5
    EXTERNAL_EFFECT = 10


TOOL_RISK: dict[str, int] = {
    # read = 0
    "web_search": 0,
    "web_fetch": 0,
    "paper_search": 0,
    "paper_get": 0,
    "read_file": 0,
    "list_dir": 0,
    "search_files": 0,
    "grep": 0,
    "memory_search": 0,
    "session_search": 0,
    "trace_search": 0,
    # write = 2
    "write_file": 2,
    "edit_file": 2,
    "apply_patch": 2,
    "create_doc": 2,
    "append_file": 2,
    "docx_write": 2,
    "pptx_write": 2,
    "xlsx_write": 2,
    "create_docx": 2,
    "create_pptx": 2,
    "create_xlsx": 2,
    "edit_docx": 2,
    "append_xlsx": 2,
    # exec = 5
    "exec": 5,
    "run_script": 5,
    "python": 5,
    # external = 10
    "upload": 10,
    "upload_run": 10,
    "qraft_upload": 10,
    "delete_file": 10,
    "delete_dir": 10,
    "remove_file": 10,
    "rm": 10,
    "payment": 10,
    "send_message": 10,
    "spawn": 10,
}


def tool_risk(tool_name: str) -> int:
    """Return a conservative task risk for an explicitly classified tool.

    Unknown tools are non-zero so a newly introduced mutating tool cannot be
    silently treated as read-only by Plan detection. Action confirmation still
    requires an explicit high-risk classification.
    """
    return TOOL_RISK.get(tool_name, 2)


PHASE_READ = "READ"
PHASE_WRITE = "WRITE"
PHASE_EXEC = "EXEC"
PHASE_EXTERNAL = "EXTERNAL"

TOOL_PHASE: dict[str, str] = {
    # READ
    "web_search": PHASE_READ,
    "web_fetch": PHASE_READ,
    "paper_search": PHASE_READ,
    "paper_get": PHASE_READ,
    "read_file": PHASE_READ,
    "list_dir": PHASE_READ,
    "search_files": PHASE_READ,
    "grep": PHASE_READ,
    "memory_search": PHASE_READ,
    "session_search": PHASE_READ,
    "trace_search": PHASE_READ,
    # WRITE
    "write_file": PHASE_WRITE,
    "edit_file": PHASE_WRITE,
    "apply_patch": PHASE_WRITE,
    "create_doc": PHASE_WRITE,
    "append_file": PHASE_WRITE,
    "docx_write": PHASE_WRITE,
    "pptx_write": PHASE_WRITE,
    "xlsx_write": PHASE_WRITE,
    "create_docx": PHASE_WRITE,
    "create_pptx": PHASE_WRITE,
    "create_xlsx": PHASE_WRITE,
    "edit_docx": PHASE_WRITE,
    "append_xlsx": PHASE_WRITE,
    # EXEC
    "exec": PHASE_EXEC,
    "run_script": PHASE_EXEC,
    "python": PHASE_EXEC,
    # EXTERNAL
    "upload": PHASE_EXTERNAL,
    "upload_run": PHASE_EXTERNAL,
    "qraft_upload": PHASE_EXTERNAL,
    "delete_file": PHASE_EXTERNAL,
    "delete_dir": PHASE_EXTERNAL,
    "remove_file": PHASE_EXTERNAL,
    "rm": PHASE_EXTERNAL,
    "payment": PHASE_EXTERNAL,
    "send_message": PHASE_EXTERNAL,
    "spawn": PHASE_EXTERNAL,
}


def phase_for_tool(tool_name: str) -> str | None:
    return TOOL_PHASE.get(tool_name)


def is_mutation_tool(tool_name: str) -> bool:
    phase = phase_for_tool(tool_name)
    return phase in (PHASE_WRITE, PHASE_EXEC, PHASE_EXTERNAL)


TOOL_DESCRIPTION: dict[str, str] = {
    "web_search": "搜集资料",
    "web_fetch": "读取网页内容",
    "paper_search": "搜集论文",
    "paper_get": "阅读论文",
    "read_file": "读取文件",
    "list_dir": "查看目录",
    "search_files": "查找文件",
    "grep": "搜索文件内容",
    "memory_search": "检索记忆",
    "session_search": "检索历史会话",
    "trace_search": "检索执行记录",
    "write_file": "创建文档",
    "edit_file": "修改文件",
    "apply_patch": "修改代码",
    "create_doc": "生成文档",
    "append_file": "追加写入",
    "docx_write": "创建 Word 文档",
    "pptx_write": "创建演示文稿",
    "xlsx_write": "创建表格",
    "create_docx": "创建 Word 文档",
    "create_pptx": "创建演示文稿",
    "create_xlsx": "创建表格",
    "edit_docx": "修改 Word 文档",
    "append_xlsx": "追加表格内容",
    "exec": "运行命令",
    "run_script": "运行脚本",
    "python": "执行 Python",
    "delete_file": "删除文件",
    "delete_dir": "删除目录",
    "remove_file": "删除文件",
    "rm": "删除文件",
    "upload": "上传结果到外部平台",
    "upload_run": "上传运行结果",
    "qraft_upload": "上传到 Qraft",
    "payment": "支付/产生费用",
    "send_message": "发送外部消息",
    "spawn": "启动外部进程",
}


def describe_tool(tool_name: str) -> str:
    return TOOL_DESCRIPTION.get(tool_name, "执行操作")


ACTION_CONFIRM_THRESHOLD = 10


def action_risk_score(tool_names: list[str]) -> int:
    if not tool_names:
        return 0
    return max(tool_risk(t) for t in tool_names)


def should_confirm_action(tool_name: str, arguments: dict | None = None) -> bool:
    risk = tool_risk(tool_name)
    if risk < ACTION_CONFIRM_THRESHOLD:
        return False
    if _is_sensitive_path(arguments or {}):
        return True
    if tool_name in ("delete_file", "delete_dir", "remove_file", "rm"):
        if tool_name == "delete_dir":
            return True
        return _is_destructive_delete(arguments or {})
    return True


def _is_sensitive_path(args: dict) -> bool:
    path = str(args.get("path") or args.get("file_path") or args.get("target") or "")
    if not path:
        return False
    try:
        parts = Path(path).expanduser().parts
    except Exception:
        return False
    lowered = {part.lower() for part in parts}
    return ".git" in lowered or ".ssh" in lowered


def _is_destructive_delete(args: dict) -> bool:
    path = str(args.get("path") or args.get("file_path") or "")
    if args.get("recursive") or args.get("rec"):
        return True
    if any(ch in path for ch in "*?["):
        return True
    if args.get("dir") or args.get("directory"):
        return True
    if path in ("/", "~", str(args.get("workspace", "")) or "", ".", ".."):
        return True
    if path.endswith(("/", "\\")):
        return True
    return False


COMPLEXITY_THRESHOLD = 4


def complexity_score(
    *,
    n_tool_calls: int,
    uses_skill: bool = False,
    produces_artifact: bool = False,
    phase_history: list[str] | None = None,
) -> int:
    """Calculate task complexity as a weak signal, not a permission gate."""
    score = 0
    if n_tool_calls >= 6:
        score += 2
    elif n_tool_calls >= 3:
        score += 1
    if phase_history and len({p for p in phase_history if p}) >= 2:
        score += 2
    if produces_artifact:
        score += 2
    if uses_skill:
        score += 3
    return score


def should_plan_confirm(
    tool_calls: list[str],
    *,
    mode: str = "edit",
    uses_skill: bool = False,
    produces_artifact: bool | None = None,
    phase_history: list[str] | None = None,
) -> bool:
    """Decide whether a task deserves a single collaboration Plan step.

    Explicitly simple Edit-mode operations stay direct. A local write by itself
    is not a reason to stop the user. Multi-step, cross-phase, skill-driven or
    otherwise genuinely complex work can still trigger the plan gate.
    """
    if mode in ("plan", "auto"):
        return False
    if produces_artifact is None:
        produces_artifact = any(tool_risk(t) >= 2 for t in tool_calls)
    return complexity_score(
        n_tool_calls=len(tool_calls),
        uses_skill=uses_skill,
        produces_artifact=produces_artifact,
        phase_history=phase_history,
    ) >= COMPLEXITY_THRESHOLD


def should_show_timeline(
    tool_calls: list[str],
    *,
    produces_artifact: bool | None = None,
    phase_history: list[str] | None = None,
) -> bool:
    """Show Timeline only for work substantial enough to benefit from it."""
    if produces_artifact is None:
        produces_artifact = any(tool_risk(t) >= 2 for t in tool_calls)
    return complexity_score(
        n_tool_calls=len(tool_calls),
        produces_artifact=produces_artifact,
        phase_history=phase_history,
    ) >= COMPLEXITY_THRESHOLD


def plan_card_steps(tool_calls: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Turn tool calls into short user-facing behavior labels."""
    seen: set[str] = set()
    steps: list[dict[str, str]] = []
    for name, _arg_hint in tool_calls:
        if not name:
            continue
        label = describe_tool(str(name))
        if not label or label in seen:
            continue
        seen.add(label)
        steps.append({"name": label, "tools": []})
    return steps


# 权限类别细分（外部复核 9-11）：EXTERNAL phase 不全是"上传"——删除/支付/
# 外发消息/进程 spawn 需要各自语义，避免 PlanCard 权限摘要与实际副作用不符。
_EXTERNAL_PERMISSION_BY_TOOL: dict[str, str] = {
    "upload": "external_upload",
    "upload_run": "external_upload",
    "qraft_upload": "external_upload",
    "delete_file": "external_delete",
    "delete_dir": "external_delete",
    "remove_file": "external_delete",
    "rm": "external_delete",
    "payment": "payment",
    "send_message": "external_message",
    "spawn": "process_spawn",
}


def permissions_for_tools(tool_calls: list[str]) -> list[str]:
    """Convert tool capabilities into user-facing permission categories."""
    perms: list[str] = []
    for tool_name in tool_calls:
        phase = phase_for_tool(tool_name)
        if phase == PHASE_EXTERNAL:
            perm = _EXTERNAL_PERMISSION_BY_TOOL.get(tool_name, "external_other")
            if perm not in perms:
                perms.append(perm)
        elif phase == PHASE_EXEC and "exec" not in perms:
            perms.append("exec")
        elif phase == PHASE_WRITE and "workspace_write" not in perms:
            perms.append("workspace_write")
        elif phase == PHASE_READ and tool_name.startswith(("web_", "paper_")) and "network_read" not in perms:
            perms.append("network_read")
    return perms
