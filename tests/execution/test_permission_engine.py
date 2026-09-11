"""Tests for miqi.execution.permission_engine."""
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
