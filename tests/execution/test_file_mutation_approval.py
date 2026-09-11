"""Phase 31.7: File Mutation / Apply Patch Approval Alignment audit tests.

Proves that every file mutation tool path goes through:
  ToolOrchestrator → PermissionEngine → approval/permission decision

No direct file mutation may bypass the approval pipeline.
AppServer files.* control-plane API must not be reachable as an agent tool.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.config.schema import ApprovalBypassConfig
from miqi.execution.hook_runtime import HookOutcome
from miqi.execution.orchestrator import (
    ToolExecutionContext,
    ToolOrchestrator,
)
from miqi.execution.permission_engine import (
    PermissionDecision,
    PermissionEngine,
    PermissionVerdict,
)
from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

# ── Helpers ────────────────────────────────────────────────────────────────


class FakeConfig:
    """Minimal config for create_runtime_tool_registry."""
    agents = None
    tools = None
    _session_key = ""


def make_ctx(**kwargs):
    return ToolExecutionContext(
        tool_name=kwargs.get("tool_name", "write_file"),
        tool_call_id=kwargs.get("tool_call_id", "call_001"),
        arguments=kwargs.get("arguments", {"path": "test.txt"}),
        turn_id=kwargs.get("turn_id", "turn_001"),
        thread_id=kwargs.get("thread_id", "thread_abc"),
        agent_type=kwargs.get("agent_type", "main"),
    )


# ── PermissionEngine: file mutation tools require approval ─────────────────


_FILE_WRITE_NAMES = ["write_file", "edit_file", "delete_file"]


@pytest.mark.parametrize("tool_name", _FILE_WRITE_NAMES)
@pytest.mark.asyncio
async def test_file_write_tools_require_approval(tool_name):
    """Every agent file mutation tool must trigger APPROVAL_REQUIRED."""
    engine = PermissionEngine()

    class FakeCtx:
        pass

    ctx = FakeCtx()
    ctx.tool_name = tool_name
    ctx.arguments = {"path": "/tmp/test.txt"}

    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED, (
        f"{tool_name} should require approval"
    )
    assert decision.category == "file_write"
    assert decision.allow_permanent is True


_OFFICE_WRITE_NAMES = [
    "docx_write", "pptx_write", "xlsx_write",
    "create_docx", "create_pptx", "create_xlsx",
    "edit_docx", "append_xlsx",
]


@pytest.mark.parametrize("tool_name", _OFFICE_WRITE_NAMES)
@pytest.mark.asyncio
async def test_office_doc_write_tools_require_approval(tool_name):
    """Phase 31.7: office document write tools must be explicitly
    categorized as file_write, not fall through to unknown_tool."""
    engine = PermissionEngine()

    class FakeCtx:
        pass

    ctx = FakeCtx()
    ctx.tool_name = tool_name
    ctx.arguments = {"file_path": "/tmp/output.docx"}

    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED, (
        f"{tool_name} should require approval"
    )
    assert decision.category == "file_write", (
        f"{tool_name} should be category=file_write, got {decision.category!r}"
    )
    assert decision.allow_permanent is True, (
        f"{tool_name} should support allow_permanent"
    )


@pytest.mark.asyncio
async def test_create_docx_permission_key_uses_final_suffix():
    """Approval allowlist keys should match the final file path create_docx writes."""
    engine = PermissionEngine()

    class FakeCtx:
        pass

    ctx = FakeCtx()
    ctx.tool_name = "create_docx"
    ctx.arguments = {"filename": "/tmp/report"}

    decision = await engine.check(ctx)
    assert decision.details["path"] == "/tmp/report.docx"
    assert engine._make_key(ctx) == "create_docx:/tmp/report.docx"


# ── PermissionEngine: read-only file tools auto-allow ──────────────────────


_READ_ONLY_FILE_NAMES = [
    "read_file", "list_dir",
    "docx_read", "pptx_read", "xlsx_read",
]


@pytest.mark.parametrize("tool_name", _READ_ONLY_FILE_NAMES)
@pytest.mark.asyncio
async def test_read_only_file_tools_auto_allow(tool_name):
    """Read-only file tools must be auto-allowed unless a deny pattern matches."""
    engine = PermissionEngine()

    class FakeCtx:
        pass

    ctx = FakeCtx()
    ctx.tool_name = tool_name
    ctx.arguments = {"path": "test.txt", "file_path": "test.txt"}

    decision = await engine.check(ctx)
    assert decision.verdict == PermissionVerdict.ALLOW, (
        f"Read-only tool {tool_name} should auto-allow"
    )


# ── ToolRegistry audit: no unauthorized bypass paths ───────────────────────


def test_agent_tool_registry_contains_file_mutation_tools(tmp_path):
    """Agent ToolRegistry must contain the expected set of file mutation tools."""
    registry = create_runtime_tool_registry(
        config=FakeConfig(),
        workspace=tmp_path,
    )
    names = set(registry.tool_names)

    # Mutation tools: must be present so the model can use them
    assert "write_file" in names
    assert "edit_file" in names

    # Read tools
    assert "read_file" in names
    assert "list_dir" in names

    # Office tools
    assert "create_docx" in names
    assert "create_pptx" in names
    assert "create_xlsx" in names
    assert "edit_docx" in names
    assert "append_xlsx" in names
    assert "docx_write" in names
    assert "pptx_write" in names
    assert "xlsx_write" in names


def test_agent_tool_registry_does_not_contain_appserver_files_methods(tmp_path):
    """Phase 31.7: AppServer files.* control-plane API must NOT be reachable
    as agent tools.  The agent ToolRegistry and the AppServer method namespace
    are separate — this test proves there is no accidental collision."""
    registry = create_runtime_tool_registry(
        config=FakeConfig(),
        workspace=tmp_path,
    )
    names = set(registry.tool_names)

    # AppServer files.* methods — these are control-plane RPCs, NOT agent tools
    appserver_methods = {
        "files.tree", "files.read", "files.write", "files.delete",
        "files.diff", "files.revert", "files.accept",
    }
    for method in appserver_methods:
        assert method not in names, (
            f"AppServer control-plane method {method!r} must NOT be "
            f"registered as an agent tool — this would create a bypass path"
        )


def test_delete_file_tool_does_not_exist(tmp_path):
    """The delete_file agent tool is referenced in policy but has no
    implementation.  If it existed, it would need to be audited.
    This test asserts it does not exist — so there is no bypass risk."""
    registry = create_runtime_tool_registry(
        config=FakeConfig(),
        workspace=tmp_path,
    )
    names = set(registry.tool_names)
    assert "delete_file" not in names, (
        "delete_file tool does not exist — if you are adding it, ensure "
        "it goes through ToolOrchestrator → PermissionEngine for approval"
    )


# ── Orchestrator integration: approval blocks file mutation ────────────────


@pytest.fixture
def mock_orch_components():
    """Mocked orchestrator dependencies for file-mutation approval tests."""
    pe = MagicMock()
    pe.check = AsyncMock()
    se = MagicMock()
    se.select = AsyncMock()
    hr = MagicMock()
    hr.run = AsyncMock()
    hr.run_with_outcome = AsyncMock(return_value=HookOutcome.continue_())
    tr = MagicMock()
    ev = MagicMock()
    ev.emit = AsyncMock()
    return {
        "permission_engine": pe,
        "sandbox_engine": se,
        "hook_runtime": hr,
        "tool_registry": tr,
        "event_emitter": ev,
    }


@pytest.fixture
def orch(mock_orch_components):
    return ToolOrchestrator(
        permission_engine=mock_orch_components["permission_engine"],
        sandbox_engine=mock_orch_components["sandbox_engine"],
        hook_runtime=mock_orch_components["hook_runtime"],
        tool_registry=mock_orch_components["tool_registry"],
        event_emitter=mock_orch_components["event_emitter"],
    )


@pytest.mark.asyncio
async def test_write_file_deny_prevents_mutation(orch, mock_orch_components):
    """When user denies write_file, the tool MUST NOT execute."""
    mock_orch_components["permission_engine"].check.return_value = PermissionDecision(
        verdict=PermissionVerdict.APPROVAL_REQUIRED,
        category="file_write",
        description="write_file: /tmp/secret.txt",
        details={"path": "/tmp/secret.txt", "operation": "write_file"},
        allow_permanent=True,
    )
    mock_orch_components["sandbox_engine"].select.return_value = MagicMock(
        sandbox_type="none",
        filesystem_policy=MagicMock(),
        network_policy="allow_all",
    )
    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="should-not-write")
    mock_orch_components["tool_registry"].get.return_value = tool_mock

    ctx = make_ctx(
        tool_name="write_file",
        arguments={"path": "/tmp/secret.txt", "content": "evil"},
    )
    task = asyncio.create_task(orch.execute(ctx))
    await asyncio.sleep(0.05)

    approval_id = f"{ctx.turn_id}:{ctx.tool_call_id}"
    orch.resolve_approval(approval_id, "deny")

    result = await task
    assert "denied" in result.result.lower(), (
        f"Expected denial, got: {result.result!r}"
    )
    tool_mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_write_file_allow_performs_mutation(orch, mock_orch_components):
    """When user allows write_file, the tool MUST execute."""
    mock_orch_components["permission_engine"].check.return_value = PermissionDecision(
        verdict=PermissionVerdict.APPROVAL_REQUIRED,
        category="file_write",
        description="write_file: /tmp/safe.txt",
        details={"path": "/tmp/safe.txt", "operation": "write_file"},
        allow_permanent=True,
    )
    mock_orch_components["sandbox_engine"].select.return_value = MagicMock(
        sandbox_type="none",
        filesystem_policy=MagicMock(),
        network_policy="allow_all",
    )
    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="Successfully wrote 5 bytes")
    mock_orch_components["tool_registry"].get.return_value = tool_mock

    ctx = make_ctx(
        tool_name="write_file",
        arguments={"path": "/tmp/safe.txt", "content": "hello"},
    )
    task = asyncio.create_task(orch.execute(ctx))
    await asyncio.sleep(0.05)

    approval_id = f"{ctx.turn_id}:{ctx.tool_call_id}"
    orch.resolve_approval(approval_id, "once")

    result = await task
    assert "Successfully wrote" in result.result
    tool_mock.execute.assert_called_once()


@pytest.mark.asyncio
async def test_file_write_bypass_executes_without_approval_request(mock_orch_components):
    """File-write bypass must skip the approval prompt at orchestrator level."""
    permission_engine = PermissionEngine(
        approval_bypass=ApprovalBypassConfig(bypass_file_write_approval=True),
    )
    mock_orch_components["sandbox_engine"].select.return_value = MagicMock(
        sandbox_type="none",
        filesystem_policy=MagicMock(),
        network_policy="allow_all",
    )
    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="Successfully wrote 5 bytes")
    mock_orch_components["tool_registry"].get.return_value = tool_mock
    orch = ToolOrchestrator(
        permission_engine=permission_engine,
        sandbox_engine=mock_orch_components["sandbox_engine"],
        hook_runtime=mock_orch_components["hook_runtime"],
        tool_registry=mock_orch_components["tool_registry"],
        event_emitter=mock_orch_components["event_emitter"],
    )

    ctx = make_ctx(
        tool_name="write_file",
        arguments={"path": "/tmp/bypass.txt", "content": "hello"},
    )
    result = await orch.execute(ctx)

    assert result.permission_decision is not None
    assert result.permission_decision.verdict == PermissionVerdict.ALLOW
    assert result.permission_decision.reason == "Auto-approved by approval bypass"
    assert "Successfully wrote" in result.result
    tool_mock.execute.assert_called_once()
    emitted = [
        call.args[0].__class__.__name__
        for call in mock_orch_components["event_emitter"].emit.await_args_list
    ]
    assert "ApprovalRequestedEvent" not in emitted


@pytest.mark.asyncio
async def test_command_bypass_executes_metachar_command_without_approval_request(
    mock_orch_components,
):
    """Command bypass must skip approval for shell metacharacter commands."""
    permission_engine = PermissionEngine(
        approval_bypass=ApprovalBypassConfig(bypass_command_approval=True),
    )
    mock_orch_components["sandbox_engine"].select.return_value = MagicMock(
        sandbox_type="none",
        filesystem_policy=MagicMock(),
        network_policy="allow_all",
    )
    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="uid=1000")
    mock_orch_components["tool_registry"].get.return_value = tool_mock
    orch = ToolOrchestrator(
        permission_engine=permission_engine,
        sandbox_engine=mock_orch_components["sandbox_engine"],
        hook_runtime=mock_orch_components["hook_runtime"],
        tool_registry=mock_orch_components["tool_registry"],
        event_emitter=mock_orch_components["event_emitter"],
    )

    ctx = make_ctx(
        tool_name="exec",
        arguments={"command": "pwd && id"},
    )
    result = await orch.execute(ctx)

    assert result.permission_decision is not None
    assert result.permission_decision.verdict == PermissionVerdict.ALLOW
    assert result.permission_decision.reason == "Auto-approved by approval bypass"
    assert result.result == "uid=1000"
    tool_mock.execute.assert_called_once()
    emitted = [
        call.args[0].__class__.__name__
        for call in mock_orch_components["event_emitter"].emit.await_args_list
    ]
    assert "ApprovalRequestedEvent" not in emitted


@pytest.mark.asyncio
async def test_network_bypass_executes_web_tool_without_approval_request(
    mock_orch_components,
):
    """Network bypass must skip approval for web_fetch/web_search tools."""
    permission_engine = PermissionEngine(
        approval_bypass=ApprovalBypassConfig(bypass_network_approval=True),
    )
    mock_orch_components["sandbox_engine"].select.return_value = MagicMock(
        sandbox_type="none",
        filesystem_policy=MagicMock(),
        network_policy="allow_all",
    )
    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value='{"ok": true}')
    mock_orch_components["tool_registry"].get.return_value = tool_mock
    orch = ToolOrchestrator(
        permission_engine=permission_engine,
        sandbox_engine=mock_orch_components["sandbox_engine"],
        hook_runtime=mock_orch_components["hook_runtime"],
        tool_registry=mock_orch_components["tool_registry"],
        event_emitter=mock_orch_components["event_emitter"],
    )

    ctx = make_ctx(
        tool_name="web_fetch",
        arguments={"url": "https://www.iana.org/domains/reserved"},
    )
    result = await orch.execute(ctx)

    assert result.permission_decision is not None
    assert result.permission_decision.verdict == PermissionVerdict.ALLOW
    assert result.permission_decision.category == "network"
    assert result.permission_decision.reason == "Auto-approved by approval bypass"
    assert result.result == '{"ok": true}'
    tool_mock.execute.assert_called_once()
    emitted = [
        call.args[0].__class__.__name__
        for call in mock_orch_components["event_emitter"].emit.await_args_list
    ]
    assert "ApprovalRequestedEvent" not in emitted


@pytest.mark.asyncio
async def test_tool_confirmation_bypass_executes_message_without_approval_request(
    mock_orch_components,
):
    """Tool-confirmation bypass must work for a real registered tool name."""
    permission_engine = PermissionEngine(
        approval_bypass=ApprovalBypassConfig(bypass_tool_confirmation=True),
    )
    mock_orch_components["sandbox_engine"].select.return_value = MagicMock(
        sandbox_type="none",
        filesystem_policy=MagicMock(),
        network_policy="allow_all",
    )
    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="Message sent")
    mock_orch_components["tool_registry"].get.return_value = tool_mock
    orch = ToolOrchestrator(
        permission_engine=permission_engine,
        sandbox_engine=mock_orch_components["sandbox_engine"],
        hook_runtime=mock_orch_components["hook_runtime"],
        tool_registry=mock_orch_components["tool_registry"],
        event_emitter=mock_orch_components["event_emitter"],
    )

    ctx = make_ctx(
        tool_name="message",
        arguments={"content": "bypass tool confirmation test"},
    )
    result = await orch.execute(ctx)

    assert result.permission_decision is not None
    assert result.permission_decision.verdict == PermissionVerdict.ALLOW
    assert result.permission_decision.category == "tool_confirmation"
    assert result.permission_decision.reason == "Auto-approved by approval bypass"
    assert result.result == "Message sent"
    tool_mock.execute.assert_called_once()
    emitted = [
        call.args[0].__class__.__name__
        for call in mock_orch_components["event_emitter"].emit.await_args_list
    ]
    assert "ApprovalRequestedEvent" not in emitted


@pytest.mark.asyncio
async def test_edit_file_timeout_prevents_mutation(orch, mock_orch_components):
    """When approval times out for edit_file, the tool MUST NOT execute."""
    mock_orch_components["permission_engine"].check.return_value = PermissionDecision(
        verdict=PermissionVerdict.APPROVAL_REQUIRED,
        category="file_write",
        description="edit_file: /tmp/config.py",
        details={"path": "/tmp/config.py", "operation": "edit_file"},
    )
    orch.approval_timeout_ms = 50
    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="should-not-run")
    mock_orch_components["tool_registry"].get.return_value = tool_mock

    ctx = make_ctx(
        tool_name="edit_file",
        arguments={"path": "/tmp/config.py", "old_text": "x", "new_text": "y"},
    )
    result = await orch.execute(ctx)

    assert "denied" in result.result.lower() or "timeout" in result.result.lower()
    tool_mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_edit_file_abort_prevents_mutation(orch, mock_orch_components):
    """When turn is aborted, pending edit_file approval must deny and
    the tool MUST NOT execute."""
    mock_orch_components["permission_engine"].check.return_value = PermissionDecision(
        verdict=PermissionVerdict.APPROVAL_REQUIRED,
        category="file_write",
        description="edit_file: /tmp/config.py",
        details={"path": "/tmp/config.py", "operation": "edit_file"},
    )
    mock_orch_components["sandbox_engine"].select.return_value = MagicMock(
        sandbox_type="none",
        filesystem_policy=MagicMock(),
        network_policy="allow_all",
    )
    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="should-not-run")
    mock_orch_components["tool_registry"].get.return_value = tool_mock

    ctx = make_ctx(
        tool_name="edit_file",
        tool_call_id="call-abort",
        turn_id="turn-abort",
        thread_id="thread-abort-fm",
        arguments={"path": "/tmp/config.py", "old_text": "x", "new_text": "y"},
    )
    task = asyncio.create_task(orch.execute(ctx))
    await asyncio.sleep(0.05)

    cancelled = await orch.cancel_approvals_for_thread("thread-abort-fm")
    assert cancelled >= 1

    result = await task
    assert "denied" in result.result.lower() or "aborted" in result.result.lower()
    tool_mock.execute.assert_not_called()


@pytest.mark.asyncio
async def test_permanent_allow_scoped_to_specific_path(orch, mock_orch_components):
    """Phase 31.7: 'always' approval for write_file must add only the
    specific path to the permanent allowlist — not a blanket wildcard."""
    mock_orch_components["permission_engine"].check.return_value = PermissionDecision(
        verdict=PermissionVerdict.APPROVAL_REQUIRED,
        category="file_write",
        description="write_file: /tmp/safe.txt",
        details={"path": "/tmp/safe.txt", "operation": "write_file"},
        allow_permanent=True,
    )
    mock_orch_components["sandbox_engine"].select.return_value = MagicMock(
        sandbox_type="none",
        filesystem_policy=MagicMock(),
        network_policy="allow_all",
    )
    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="ok")
    mock_orch_components["tool_registry"].get.return_value = tool_mock

    ctx = make_ctx(
        tool_name="write_file",
        tool_call_id="call-always",
        turn_id="turn-always",
        arguments={"path": "/tmp/safe.txt", "content": "hello"},
    )

    task = asyncio.create_task(orch.execute(ctx))
    await asyncio.sleep(0.05)

    # Give orchestrator a real set to write into
    orch.permissions.permanent_allowlist = set()

    approval_id = f"{ctx.turn_id}:{ctx.tool_call_id}"
    orch.resolve_approval(approval_id, "always")
    await task

    allowlist = orch.permissions.permanent_allowlist
    # The specific path should be allowlisted
    assert "write_file:/tmp/safe.txt" in allowlist, (
        f"Expected 'write_file:/tmp/safe.txt' in allowlist, got: {allowlist}"
    )
    # A different path should NOT be allowlisted
    assert "write_file:/tmp/evil.txt" not in allowlist, (
        "Permanent allow must be scoped to the approved path only"
    )
    # Wildcard-like paths must not appear
    for entry in allowlist:
        assert "*" not in entry, (
            f"Allowlist entry {entry!r} contains wildcard — scope too broad"
        )


@pytest.mark.asyncio
async def test_write_file_deny_by_policy_blocks_execution(orch, mock_orch_components):
    """When PermissionEngine returns DENY for write_file, the tool
    must not execute and the result must indicate denial."""
    mock_orch_components["permission_engine"].check.return_value = PermissionDecision(
        verdict=PermissionVerdict.DENY,
        reason="Blocked by deny pattern: /etc",
    )
    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="should-not-run")
    mock_orch_components["tool_registry"].get.return_value = tool_mock

    ctx = make_ctx(
        tool_name="write_file",
        arguments={"path": "/etc/passwd", "content": "malicious"},
    )
    result = await orch.execute(ctx)

    assert "权限被拒绝" in result.result
    tool_mock.execute.assert_not_called()


# ── Snapshot test: real WriteFileTool goes through orchestrator ────────────

@pytest.mark.asyncio
async def test_real_write_file_tool_goes_through_tool_orchestrator(tmp_path):
    """End-to-end: when PermissionEngine approves write_file, the real
    WriteFileTool writes to disk via the full orchestrator pipeline."""
    from miqi.agent.tools.filesystem import WriteFileTool
    from miqi.agent.tools.registry import ToolRegistry
    from miqi.execution.sandbox_policy import (
        SandboxPolicyEngine,
    )

    test_file = tmp_path / "test_output.txt"
    content = "Hello from Phase 31.7"

    # Set up real PermissionEngine with permanent allowlist
    perm_engine = PermissionEngine(
        permanent_allowlist={f"write_file:{test_file}"},
    )

    # Set up real ToolRegistry with the actual WriteFileTool
    registry = ToolRegistry()
    registry.register(WriteFileTool(workspace=tmp_path))

    # Minimal sandbox engine (all direct-exec since no sandbox available)
    sandbox_engine = SandboxPolicyEngine(
        bwrap_available=False,
        landlock_available=False,
        allow_fallback_to_none=True,
    )

    # Mock hook runtime and event emitter
    hook_runtime = MagicMock()
    hook_runtime.run = AsyncMock()
    hook_runtime.run_with_outcome = AsyncMock(return_value=HookOutcome.continue_())
    event_emitter = MagicMock()
    event_emitter.emit = AsyncMock()

    orchestrator = ToolOrchestrator(
        permission_engine=perm_engine,
        sandbox_engine=sandbox_engine,
        hook_runtime=hook_runtime,
        tool_registry=registry,
        event_emitter=event_emitter,
    )

    ctx = make_ctx(
        tool_name="write_file",
        arguments={"path": str(test_file), "content": content},
    )
    result = await orchestrator.execute(ctx)

    # Assert file was created
    assert test_file.exists(), "write_file did not create the file"
    assert test_file.read_text() == content, "write_file wrote wrong content"
    assert "Successfully wrote" in result.result


@pytest.mark.asyncio
async def test_real_edit_file_tool_goes_through_tool_orchestrator(tmp_path):
    """End-to-end: when PermissionEngine approves edit_file, the real
    EditFileTool edits a file via the full orchestrator pipeline."""
    from miqi.agent.tools.filesystem import EditFileTool
    from miqi.agent.tools.registry import ToolRegistry
    from miqi.execution.sandbox_policy import (
        SandboxPolicyEngine,
    )

    test_file = tmp_path / "config.py"
    original = "debug = False\n"
    test_file.write_text(original)

    # Pre-allow this specific edit
    perm_engine = PermissionEngine(
        permanent_allowlist={f"edit_file:{test_file}"},
    )

    registry = ToolRegistry()
    registry.register(EditFileTool(workspace=tmp_path))

    sandbox_engine = SandboxPolicyEngine(
        bwrap_available=False,
        landlock_available=False,
        allow_fallback_to_none=True,
    )

    hook_runtime = MagicMock()
    hook_runtime.run = AsyncMock()
    hook_runtime.run_with_outcome = AsyncMock(return_value=HookOutcome.continue_())
    event_emitter = MagicMock()
    event_emitter.emit = AsyncMock()

    orchestrator = ToolOrchestrator(
        permission_engine=perm_engine,
        sandbox_engine=sandbox_engine,
        hook_runtime=hook_runtime,
        tool_registry=registry,
        event_emitter=event_emitter,
    )

    ctx = make_ctx(
        tool_name="edit_file",
        arguments={
            "path": str(test_file),
            "old_text": "debug = False",
            "new_text": "debug = True",
        },
    )
    result = await orchestrator.execute(ctx)

    # Assert file was edited
    updated = test_file.read_text()
    assert "debug = True" in updated, f"edit_file did not apply edit: {updated!r}"
    assert "Successfully edited" in result.result


@pytest.mark.asyncio
async def test_write_file_deny_by_orchestrator_does_not_mutate_disk(tmp_path):
    """When orchestrator denies write_file (via deny pattern), the file
    on disk must remain unchanged."""
    from miqi.agent.tools.filesystem import WriteFileTool
    from miqi.agent.tools.registry import ToolRegistry
    from miqi.execution.sandbox_policy import SandboxPolicyEngine

    test_file = tmp_path / "protected.txt"
    test_file.write_text("original content")

    # Deny pattern blocks this path
    perm_engine = PermissionEngine(
        deny_patterns={"protected"},
    )

    registry = ToolRegistry()
    registry.register(WriteFileTool(workspace=tmp_path))

    sandbox_engine = SandboxPolicyEngine(
        bwrap_available=False,
        landlock_available=False,
        allow_fallback_to_none=True,
    )

    hook_runtime = MagicMock()
    hook_runtime.run = AsyncMock()
    hook_runtime.run_with_outcome = AsyncMock(return_value=HookOutcome.continue_())
    event_emitter = MagicMock()
    event_emitter.emit = AsyncMock()

    orchestrator = ToolOrchestrator(
        permission_engine=perm_engine,
        sandbox_engine=sandbox_engine,
        hook_runtime=hook_runtime,
        tool_registry=registry,
        event_emitter=event_emitter,
    )

    ctx = make_ctx(
        tool_name="write_file",
        arguments={"path": str(test_file), "content": "malicious overwrite"},
    )
    result = await orchestrator.execute(ctx)

    # File must NOT have been modified
    assert test_file.read_text() == "original content", (
        "write_file mutated disk despite deny-by-policy"
    )
    assert "权限被拒绝" in result.result


# ── AppServer files.* namespace isolation ──────────────────────────────────


def test_appserver_files_methods_not_in_runtime_tool_registry_after_full_setup(
    tmp_path,
):
    """Even with full optional dependencies, no AppServer files.* method
    name should appear in the agent tool registry."""
    from unittest.mock import MagicMock

    registry = create_runtime_tool_registry(
        config=FakeConfig(),
        workspace=tmp_path,
        memory_store=MagicMock(),
        trace_store=MagicMock(),
        session_manager=MagicMock(),
        bus=MagicMock(),
        subagent_manager=MagicMock(),
        cron_service=MagicMock(),
        plan_tracker=MagicMock(),
    )

    names = set(registry.tool_names)

    appserver_files_methods = [
        "files.tree", "files.read", "files.write", "files.delete",
        "files.diff", "files.revert", "files.accept",
    ]
    for method in appserver_files_methods:
        assert method not in names, (
            f"AppServer {method} must not be an agent tool"
        )

    # Also: no tool name starts with "files."
    for name in names:
        assert not name.startswith("files."), (
            f"Tool {name!r} starts with 'files.' — potential namespace collision"
        )


# ── apply_patch must not bypass approval ───────────────────────────────────


@pytest.mark.asyncio
async def test_apply_patch_tool_requires_approval(tmp_path):
    """apply_patch must go through ToolOrchestrator → PermissionEngine."""
    from miqi.execution.permission_engine import PermissionEngine, PermissionVerdict

    registry = create_runtime_tool_registry(
        config=FakeConfig(),
        workspace=tmp_path,
    )
    names = set(registry.tool_names)
    assert "apply_patch" in names, "apply_patch tool must be registered"

    class _Ctx:
        tool_name = "apply_patch"
        arguments = {"patch": "--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-a\n+b\n"}

    decision = await PermissionEngine().check(_Ctx())
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED
    assert decision.category == "file_write"
