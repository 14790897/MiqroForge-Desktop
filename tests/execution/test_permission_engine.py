"""Tests for miqi.execution.permission_engine."""
import os

import pytest

from miqi.config.schema import ApprovalBypassConfig
from miqi.execution.approval_policy import ApprovalMode, ApprovalPolicy
from miqi.execution.exec_policy import CommandRule, ExecPolicy
from miqi.execution.permission_engine import (
    PermissionEngine,
    PermissionVerdict,
)
from miqi.runtime.permission_profile import PermissionProfile


class FakeContext:
    def __init__(self, tool_name, arguments=None):
        self.tool_name = tool_name
        self.arguments = arguments or {}


@pytest.mark.asyncio
async def test_read_only_tools_auto_allow():
    engine = PermissionEngine()
    ctx = FakeContext("read_file", {"path": "test.py"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.ALLOW


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "target"),
    [
        ("web_search", {"query": "python"}, "python"),
        ("web_fetch", {"url": "https://www.iana.org/domains/reserved"}, "https://www.iana.org/domains/reserved"),
    ],
)
async def test_network_tools_require_approval(tool_name, arguments, target):
    engine = PermissionEngine()
    ctx = FakeContext(tool_name, arguments)
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED
    assert decision.category == "network"
    assert decision.details["target"] == target


@pytest.mark.asyncio
async def test_safe_shell_commands_auto_allow():
    engine = PermissionEngine()
    ctx = FakeContext("exec", {"command": "ls -la"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.ALLOW


@pytest.mark.asyncio
async def test_safe_shell_commands_git_status():
    engine = PermissionEngine()
    ctx = FakeContext("exec", {"command": "git status"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.ALLOW


@pytest.mark.asyncio
async def test_dangerous_shell_commands_require_approval():
    engine = PermissionEngine()
    ctx = FakeContext("exec", {"command": "rm -rf /tmp/test"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED
    assert decision.allow_permanent is True


@pytest.mark.asyncio
async def test_file_writes_require_approval():
    engine = PermissionEngine()
    ctx = FakeContext("write_file", {"path": "/etc/config"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_edit_file_requires_approval():
    engine = PermissionEngine()
    ctx = FakeContext("edit_file", {"file_path": "/etc/hosts"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED
    assert decision.category == "file_write"


@pytest.mark.asyncio
async def test_apply_patch_requires_approval():
    engine = PermissionEngine()
    ctx = FakeContext("apply_patch", {"patch": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED
    assert decision.category == "file_write"


@pytest.mark.asyncio
async def test_make_key_apply_patch():
    ctx = FakeContext("apply_patch", {"patch": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"})
    key = PermissionEngine._make_key(ctx)
    assert key == "apply_patch:"


@pytest.mark.asyncio
async def test_permanent_allowlist_bypasses_approval():
    engine = PermissionEngine(permanent_allowlist={"exec:rm -rf /tmp/test"})
    ctx = FakeContext("exec", {"command": "rm -rf /tmp/test"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.ALLOW


@pytest.mark.asyncio
async def test_deny_pattern_blocks_execution():
    engine = PermissionEngine(deny_patterns={"sudo"})
    ctx = FakeContext("exec", {"command": "sudo rm -rf /"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.DENY


@pytest.mark.asyncio
async def test_deny_pattern_in_arguments():
    engine = PermissionEngine(deny_patterns={"malware"})
    ctx = FakeContext("exec", {"command": "curl http://malware.example.com"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.DENY


@pytest.mark.asyncio
async def test_default_deny_by_default():
    engine = PermissionEngine()
    ctx = FakeContext("unknown_tool", {})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


# SURE 项目核查工具(MCP server 'sure',命名 mcp_<server>_<tool>,#1256 D5-A)
MCP_SURE_TOOLS = (
    "mcp_sure_sure_check",
    "mcp_sure_sure_get_report",
    "mcp_sure_sure_get_repair",
    "mcp_sure_sure_recheck",
    "mcp_sure_sure_status",
)

# ── SURE 路径运行时保护（#1256 阶段 1 子项,拒绝制）────────────────────────
# 省略 project 时 SURE 核查的是它**自己的启动目录**而不是用户项目(PoC 已复现)。
# 结构性错误不是可授权的偏好:本检查位于 deny 模式之后、全部放行名单/绕过之前。

SURE_PROJECT_TOOLS = (
    "mcp_sure_sure_check",
    "mcp_sure_sure_recheck",
    "mcp_sure_sure_get_repair",
)



@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", MCP_SURE_TOOLS)
async def test_mcp_sure_tools_require_approval_with_permanent_allow(tool_name):
    """D5-A:SURE 核查工具是已知工具——仍弹审批,但可「永久允许」。

    加入 TOOL_CONFIRMATION_TOOLS 之前,它们落 unknown-tool 默认分支:
    每次调用都弹审批、allow_permanent=False(弹窗没有「记住」选项)、
    文案为 "Unknown tool: …"。加入后:allow_permanent=True,文案变为
    "<tool>: <target>" 形式。
    """
    engine = PermissionEngine()
    # 平台可移植的绝对路径:路径守卫(#1266)会拒绝非绝对路径——CI(ubuntu)上
    # 字面量 Windows 路径不是绝对路径,而这个用例核的是审批语义,不是路径校验
    ctx = FakeContext(tool_name, {"project": os.path.abspath(os.path.join("sure-poc", "hello"))})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED
    assert decision.category == "tool_confirmation"
    assert decision.allow_permanent is True
    assert not decision.description.startswith("Unknown tool")


@pytest.mark.asyncio
async def test_other_mcp_tools_keep_no_permanent_allow():
    """对照组:其他 MCP 服务器的工具保持 unknown-tool 默认分支(本次改动不放宽兜底)。"""
    engine = PermissionEngine()
    ctx = FakeContext("mcp_other_server_some_tool", {})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED
    assert decision.allow_permanent is False


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", SURE_PROJECT_TOOLS)
async def test_sure_project_tools_deny_missing_project(tool_name):
    """缺少 project/为空 → 拒绝,理由要说清"应传绝对路径"。"""
    engine = PermissionEngine()
    for args in ({}, {"project": ""}, {"project": "   "}):
        decision = await engine.check(FakeContext(tool_name, args))
        assert decision.verdict == PermissionVerdict.DENY, args
        assert "project" in decision.reason
        assert "绝对路径" in decision.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", SURE_PROJECT_TOOLS)
async def test_sure_project_tools_deny_relative_project(tool_name):
    """相对路径 → SURE 会相对它自己的 cwd 解析,同样拒绝。"""
    engine = PermissionEngine()
    decision = await engine.check(FakeContext(tool_name, {"project": "sure-poc/hello"}))
    assert decision.verdict == PermissionVerdict.DENY
    assert "绝对路径" in decision.reason


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="盘符相对路径的语义仅 Windows 成立")
async def test_sure_project_tools_deny_drive_relative_windows_path():
    """Windows 上单反斜杠开头的盘符相对路径(\\repo)必须拒绝:Python<3.13 的
    os.path.isabs 对它返回 True(本机实测),但它相对的是"当前盘符",不是用户
    项目——校验必须用 Path(...).is_absolute()(要求完全限定;CodeRabbit #1266)。"""
    engine = PermissionEngine()
    decision = await engine.check(
        FakeContext("mcp_sure_sure_check", {"project": r"\sure-poc\hello"})
    )
    assert decision.verdict == PermissionVerdict.DENY
    assert "绝对路径" in decision.reason


@pytest.mark.asyncio
async def test_sure_project_tools_absolute_project_falls_through_to_approval_flow():
    """绝对路径 → 不进路径保护,按既有审批分支走(不因本保护自动放行)。"""
    engine = PermissionEngine()
    abs_path = os.path.abspath("sure-poc")
    decision = await engine.check(FakeContext("mcp_sure_sure_check", {"project": abs_path}))
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_sure_status_and_report_tools_not_affected_by_path_guard():
    """不带 project 的 SURE 工具(status/get_report)不受路径保护影响。"""
    engine = PermissionEngine()
    for tool_name in ("mcp_sure_sure_status", "mcp_sure_sure_get_report"):
        decision = await engine.check(FakeContext(tool_name, {}))
        assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED, tool_name


@pytest.mark.asyncio
async def test_sure_project_guard_beats_wildcard_permanent_allowlist():
    """`*:*` 通配放行不能绕过路径保护——否则一次全量放行就重新打开误查目录的口子。"""
    engine = PermissionEngine(permanent_allowlist={"*:*"})
    decision = await engine.check(FakeContext("mcp_sure_sure_check", {}))
    assert decision.verdict == PermissionVerdict.DENY


@pytest.mark.asyncio
async def test_sure_project_guard_beats_approval_bypass():
    """审批绕过(plan mode 等)不能绕过路径保护。"""
    ctx = FakeContext("mcp_sure_sure_check", {})
    ctx.bypass_approval = True
    engine = PermissionEngine()
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.DENY



@pytest.mark.asyncio
async def test_deny_pattern_blocks_read_only_tools():
    engine = PermissionEngine(deny_patterns={"secret_file"})
    ctx = FakeContext("read_file", {"path": "/etc/secret_file.txt"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.DENY


@pytest.mark.asyncio
async def test_shell_metacharacter_rejected():
    engine = PermissionEngine()
    ctx = FakeContext("exec", {"command": "ls && rm -rf /tmp"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_shell_pipe_rejected():
    engine = PermissionEngine()
    ctx = FakeContext("exec", {"command": "cat /etc/passwd | grep root"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_shell_substitution_rejected():
    engine = PermissionEngine()
    ctx = FakeContext("exec", {"command": "echo $(whoami)"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_permission_decision_fields():
    engine = PermissionEngine()
    ctx = FakeContext("exec", {"command": "curl evil.com"})
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED
    assert decision.category == "exec"
    assert decision.allow_permanent is True
    assert decision.description


@pytest.mark.asyncio
async def test_make_key_exec():
    ctx = FakeContext("exec", {"command": "ls -la"})
    key = PermissionEngine._make_key(ctx)
    assert key == "exec:ls -la"


@pytest.mark.asyncio
async def test_make_key_write_file():
    ctx = FakeContext("write_file", {"path": "/tmp/test.txt"})
    key = PermissionEngine._make_key(ctx)
    assert key == "write_file:/tmp/test.txt"


class _Ctx:
    def __init__(self, tool_name, arguments, profile=None):
        self.tool_name = tool_name
        self.arguments = arguments
        self.permission_profile = profile


@pytest.mark.asyncio
async def test_engine_uses_policy_allow_for_exec(tmp_path):
    policy = ExecPolicy(command_rules=[
        CommandRule(prefix=["pytest"], decision="allow", source="t"),
    ])
    profile = PermissionProfile(workspace=tmp_path, exec_policy=policy)
    engine = PermissionEngine()
    d = await engine.check(_Ctx("exec", {"command": "pytest -q"}, profile))
    assert d.verdict == PermissionVerdict.ALLOW


@pytest.mark.asyncio
async def test_engine_policy_deny_blocks_exec(tmp_path):
    policy = ExecPolicy(command_rules=[
        CommandRule(prefix=["curl"], decision="deny", source="t"),
    ])
    profile = PermissionProfile(workspace=tmp_path, exec_policy=policy)
    engine = PermissionEngine()
    d = await engine.check(_Ctx("exec", {"command": "curl evil.test"}, profile))
    assert d.verdict == PermissionVerdict.DENY


@pytest.mark.asyncio
async def test_legacy_prefixes_still_work_without_policy(tmp_path):
    profile = PermissionProfile(workspace=tmp_path)
    profile.exec_allow_prefixes = [["git", "status"]]
    engine = PermissionEngine()
    d = await engine.check(_Ctx("exec", {"command": "git status"}, profile))
    assert d.verdict == PermissionVerdict.ALLOW


@pytest.mark.asyncio
async def test_never_mode_suppresses_file_write_prompt(tmp_path):
    profile = PermissionProfile(workspace=tmp_path)
    profile.approval_policy = ApprovalPolicy(mode=ApprovalMode.NEVER)
    engine = PermissionEngine()
    d = await engine.check(_Ctx("write_file", {"path": str(tmp_path / "a.txt")}, profile))
    assert d.verdict == PermissionVerdict.ALLOW


@pytest.mark.asyncio
async def test_granular_keeps_prompt_for_untrusted_category(tmp_path):
    profile = PermissionProfile(workspace=tmp_path)
    profile.approval_policy = ApprovalPolicy(
        mode=ApprovalMode.GRANULAR, granular={"file_write": "on_request"})
    engine = PermissionEngine()
    d = await engine.check(_Ctx("write_file", {"path": str(tmp_path / "a.txt")}, profile))
    assert d.verdict == PermissionVerdict.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_bypass_all_auto_allows_approval_required_exec():
    engine = PermissionEngine(
        approval_bypass=ApprovalBypassConfig(bypass_all=True),
    )
    d = await engine.check(FakeContext("exec", {"command": "rm -rf /tmp/test"}))
    assert d.verdict == PermissionVerdict.ALLOW
    assert d.reason == "Auto-approved by approval bypass"


@pytest.mark.asyncio
async def test_bypass_all_does_not_override_explicit_deny():
    engine = PermissionEngine(
        deny_patterns={"sudo"},
        approval_bypass=ApprovalBypassConfig(bypass_all=True),
    )
    d = await engine.check(FakeContext("exec", {"command": "sudo rm -rf /tmp/test"}))
    assert d.verdict == PermissionVerdict.DENY


@pytest.mark.asyncio
async def test_file_write_bypass_only_allows_file_write():
    engine = PermissionEngine(
        approval_bypass=ApprovalBypassConfig(bypass_file_write_approval=True),
    )
    file_decision = await engine.check(FakeContext("write_file", {"path": "/tmp/a.txt"}))
    exec_decision = await engine.check(FakeContext("exec", {"command": "rm -rf /tmp/test"}))
    assert file_decision.verdict == PermissionVerdict.ALLOW
    assert exec_decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_tool_confirmation_bypass_allows_real_tool_confirmation():
    engine = PermissionEngine(
        approval_bypass=ApprovalBypassConfig(bypass_tool_confirmation=True),
    )
    d = await engine.check(FakeContext("message", {"content": "hello"}))
    assert d.verdict == PermissionVerdict.ALLOW


@pytest.mark.asyncio
async def test_network_bypass_only_allows_network_tools():
    engine = PermissionEngine(
        approval_bypass=ApprovalBypassConfig(bypass_network_approval=True),
    )
    d = await engine.check(FakeContext("web_search", {"query": "python"}))
    exec_decision = await engine.check(FakeContext("exec", {"command": "rm -rf /tmp/test"}))
    assert d.verdict == PermissionVerdict.ALLOW
    assert exec_decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_no_policy_keeps_existing_behavior(tmp_path):
    profile = PermissionProfile(workspace=tmp_path)
    engine = PermissionEngine()
    d = await engine.check(_Ctx("write_file", {"path": str(tmp_path / "a.txt")}, profile))
    assert d.verdict == PermissionVerdict.APPROVAL_REQUIRED


# ── Execution Policy flag tests ──

class _PolicyCtx:
    """Fake context with execution policy flags."""
    def __init__(self, tool_name, arguments=None, bypass_approval=False, force_approval=False):
        self.tool_name = tool_name
        self.arguments = arguments or {}
        self.bypass_approval = bypass_approval
        self.force_approval = force_approval


@pytest.mark.asyncio
async def test_ep_bypass_flag_allows_dangerous_command():
    """bypass_approval=True → ALLOW even for dangerous rm."""
    engine = PermissionEngine()
    ctx = _PolicyCtx("exec", {"command": "rm -rf /"}, bypass_approval=True)
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.ALLOW


@pytest.mark.asyncio
async def test_ep_bypass_flag_respects_deny_list():
    """Deny list is first → DENY even with bypass_approval."""
    engine = PermissionEngine(deny_patterns={"rm"})
    ctx = _PolicyCtx("exec", {"command": "rm -rf /"}, bypass_approval=True)
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.DENY


@pytest.mark.asyncio
async def test_ep_force_approval_on_safe_tool():
    """force_approval=True → APPROVAL_REQUIRED for read_file."""
    engine = PermissionEngine()
    ctx = _PolicyCtx("read_file", {"path": "test.py"}, force_approval=True)
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_ep_force_approval_on_dangerous_tool():
    """force_approval=True → APPROVAL_REQUIRED for exec."""
    engine = PermissionEngine()
    ctx = _PolicyCtx("exec", {"command": "rm -rf /"}, force_approval=True)
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_ep_no_flags_normal_behavior():
    """Without flags → normal permission logic."""
    engine = PermissionEngine()
    d1 = await engine.check(_PolicyCtx("read_file", {"path": "test.py"}))
    assert d1.verdict == PermissionVerdict.ALLOW
    d2 = await engine.check(_PolicyCtx("exec", {"command": "rm -rf /"}))
    assert d2.verdict == PermissionVerdict.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_ep_bypass_wins_over_force():
    """Both flags → bypass checked first → ALLOW."""
    engine = PermissionEngine()
    ctx = _PolicyCtx("exec", {"command": "rm"}, bypass_approval=True, force_approval=True)
    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.ALLOW
