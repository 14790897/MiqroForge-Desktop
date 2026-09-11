"""Tests for miqi.execution.sandbox_policy."""

import pytest

from miqi.execution.sandbox_policy import (
    SandboxDeniedError,
    SandboxPolicyEngine,
    SandboxType,
)
from miqi.protocol.permissions import (
    FileSystemAccessMode,
)


class FakeContext:
    def __init__(self, tool_name, arguments=None):
        self.tool_name = tool_name
        self.arguments = arguments or {}


@pytest.mark.asyncio
async def test_read_only_tools_no_sandbox():
    engine = SandboxPolicyEngine()
    ctx = FakeContext("read_file", {"path": "test.py"})
    selection = await engine.select(ctx)
    assert selection.sandbox_type == SandboxType.NONE
    assert selection.reason


@pytest.mark.asyncio
async def test_exec_tool_prefers_bwrap():
    engine = SandboxPolicyEngine(bwrap_available=True)
    ctx = FakeContext("exec", {"command": "npm test"})
    selection = await engine.select(ctx)
    assert selection.sandbox_type == SandboxType.BWRAP


# ═══════════════════════════════════════════════════════════════════════════
# Phase 33.4: LANDLOCK hardening — landlock_available=True does NOT select
# LANDLOCK because landlock_supported=False (no real Landlock adapter yet).
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_landlock_available_does_not_select_landlock_when_unsupported():
    """Phase 33.4: landlock_available=True but landlock_supported=False
    → engine skips LANDLOCK, selects NONE (no sandbox) instead."""
    engine = SandboxPolicyEngine(bwrap_available=False, landlock_available=True)
    ctx = FakeContext("exec", {"command": "npm test"})
    selection = await engine.select(ctx)
    # LANDLOCK should NOT be selected because landlock_supported=False
    assert selection.sandbox_type == SandboxType.NONE
    # Reason must explain why LANDLOCK was skipped
    assert "landlock_available" in selection.reason.lower()
    assert "landlock_supported" in selection.reason.lower()
    assert "no Landlock adapter" in selection.reason


@pytest.mark.asyncio
async def test_landlock_available_and_supported_can_select_landlock():
    """If both landlock_available AND landlock_supported are True,
    LANDLOCK should be selectable (future-proof test)."""
    engine = SandboxPolicyEngine(bwrap_available=False, landlock_available=True)
    # Simulate a future where the adapter exists
    engine.landlock_supported = True
    ctx = FakeContext("exec", {"command": "npm test"})
    selection = await engine.select(ctx)
    assert selection.sandbox_type == SandboxType.LANDLOCK


@pytest.mark.asyncio
async def test_exec_tool_without_sandbox_selects_none():
    """Without bwrap or landlock, exec selects NONE — direct host
    execution without restrictions (sandbox off by user choice)."""
    engine = SandboxPolicyEngine(bwrap_available=False, landlock_available=False)
    ctx = FakeContext("exec", {"command": "npm test"})
    selection = await engine.select(ctx)
    assert selection.sandbox_type == SandboxType.NONE


# ═══════════════════════════════════════════════════════════════════════════
# Phase 33.4: fallback hardening — exec NEVER falls back to NONE
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_escalation_on_retry_bwrap_available():
    """With bwrap available, escalation chain is [BWRAP, LANDLOCK, RESTRICTED].
    NONE is NOT in the chain.  Attempt 3 (beyond chain) raises for exec."""
    engine = SandboxPolicyEngine(bwrap_available=True)
    ctx = FakeContext("exec", {"command": "npm test"})
    # Attempt 0 → bwrap
    s0 = await engine.select(ctx, attempt=0)
    assert s0.sandbox_type == SandboxType.BWRAP
    # Attempt 1 → landlock (escalated)
    s1 = await engine.select(ctx, attempt=1)
    assert s1.sandbox_type == SandboxType.LANDLOCK
    # Attempt 2 → restricted
    s2 = await engine.select(ctx, attempt=2)
    assert s2.sandbox_type == SandboxType.RESTRICTED
    # Attempt 3 → beyond chain → exec NEVER falls to NONE → raises
    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(ctx, attempt=3)
    assert "NONE fallback is disabled for exec" in str(exc_info.value)


@pytest.mark.asyncio
async def test_escalation_past_chain_raises_for_exec():
    """Exec escalation exhausted → always raises, even with
    allow_fallback_to_none=True."""
    engine = SandboxPolicyEngine(
        bwrap_available=True,
        allow_fallback_to_none=True,
    )
    ctx = FakeContext("exec", {"command": "npm test"})
    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(ctx, attempt=4)
    assert "NONE fallback is disabled for exec" in str(exc_info.value)


@pytest.mark.asyncio
async def test_allow_fallback_to_none_false_for_exec():
    """allow_fallback_to_none=False with exec failure → clear error message."""
    engine = SandboxPolicyEngine(
        bwrap_available=True,
        allow_fallback_to_none=False,
    )
    ctx = FakeContext("exec", {"command": "npm test"})
    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(ctx, attempt=4)
    assert "NONE fallback is disabled for exec" in str(exc_info.value)


@pytest.mark.asyncio
async def test_read_only_tools_still_none_after_exhaustion():
    """read-only tools always get NONE, even with allow_fallback_to_none=False
    and attempt beyond chain."""
    engine = SandboxPolicyEngine(allow_fallback_to_none=False)
    ctx = FakeContext("read_file", {"path": "test.py"})
    # read-only tools skip escalation entirely
    selection = await engine.select(ctx, attempt=0)
    assert selection.sandbox_type == SandboxType.NONE
    # Even with absurd attempt number
    selection = await engine.select(ctx, attempt=99)
    assert selection.sandbox_type == SandboxType.NONE


@pytest.mark.asyncio
async def test_write_file_fallback_to_none_blocked():
    """Phase 34: write_file NEVER falls back to NONE, even when
    allow_fallback_to_none=True."""
    engine = SandboxPolicyEngine(allow_fallback_to_none=True)
    ctx = FakeContext("write_file", {"path": "/tmp/test.txt"})
    # Attempt 0 → RESTRICTED
    s0 = await engine.select(ctx, attempt=0)
    assert s0.sandbox_type == SandboxType.RESTRICTED
    # Attempt 1 → beyond chain → MUST raise, NOT return NONE
    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(ctx, attempt=1)
    assert "NONE fallback is disabled for file mutation" in str(exc_info.value)


@pytest.mark.asyncio
async def test_write_file_fallback_blocked_when_disallowed():
    """write_file fallback to NONE blocked when allow_fallback_to_none=False."""
    engine = SandboxPolicyEngine(allow_fallback_to_none=False)
    ctx = FakeContext("write_file", {"path": "/tmp/test.txt"})
    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(ctx, attempt=1)
    assert "NONE fallback is disabled for file mutation" in str(exc_info.value)


@pytest.mark.asyncio
async def test_exec_with_landlock_no_fallback_to_none():
    """When bwrap unavailable and landlock unsupported, exec gets NONE
    (direct host execution). Exhaustion beyond NONE must still raise —
    the no-sandbox base selection does not weaken fail-closed retries."""
    engine = SandboxPolicyEngine(
        bwrap_available=False,
        landlock_available=False,
        allow_fallback_to_none=True,
    )
    ctx = FakeContext("exec", {"command": "npm test"})
    # Attempt 0 → NONE
    s0 = await engine.select(ctx, attempt=0)
    assert s0.sandbox_type == SandboxType.NONE
    # Attempt 1 → beyond chain → MUST raise for exec
    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(ctx, attempt=1)
    assert "NONE fallback is disabled for exec" in str(exc_info.value)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 33.4: reason string enrichment
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_reason_includes_landlock_unsupported_info():
    """Reason string must mention landlock unsupported when configured
    but no adapter exists."""
    engine = SandboxPolicyEngine(bwrap_available=False, landlock_available=True)
    ctx = FakeContext("exec", {"command": "npm test"})
    selection = await engine.select(ctx)
    assert selection.sandbox_type == SandboxType.NONE
    assert "landlock_supported=False" in selection.reason
    assert "no Landlock adapter" in selection.reason


@pytest.mark.asyncio
async def test_reason_includes_no_stronger_sandbox_info():
    """Reason for NONE exec explains why no sandbox is available and that
    execution is unrestricted."""
    engine = SandboxPolicyEngine(bwrap_available=False, landlock_available=False)
    ctx = FakeContext("exec", {"command": "npm test"})
    selection = await engine.select(ctx)
    assert selection.sandbox_type == SandboxType.NONE
    assert "bwrap unavailable" in selection.reason
    assert "direct host execution without restrictions" in selection.reason


@pytest.mark.asyncio
async def test_reason_for_read_only_tools():
    """Read-only tools have clear reason string."""
    engine = SandboxPolicyEngine()
    ctx = FakeContext("read_file", {"path": "test.py"})
    selection = await engine.select(ctx)
    assert "sandbox not required" in selection.reason


# ═══════════════════════════════════════════════════════════════════════════
# Original tests preserved (updated for Phase 33.4 semantics)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_write_file_uses_restricted():
    engine = SandboxPolicyEngine()
    ctx = FakeContext("write_file", {"path": "/tmp/test.txt"})
    selection = await engine.select(ctx)
    assert selection.sandbox_type == SandboxType.RESTRICTED


@pytest.mark.asyncio
async def test_filesystem_policy_for_exec():
    engine = SandboxPolicyEngine()
    ctx = FakeContext("exec", {"command": "ls"})
    selection = await engine.select(ctx)
    assert selection.filesystem_policy.default_mode == FileSystemAccessMode.READ


@pytest.mark.asyncio
async def test_filesystem_policy_for_write():
    engine = SandboxPolicyEngine()
    ctx = FakeContext("write_file", {"path": "/tmp/out.txt"})
    selection = await engine.select(ctx)
    # Should have a write rule for the target path
    assert len(selection.filesystem_policy.rules) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Phase 34: File mutation sandbox policy hardening
# ═══════════════════════════════════════════════════════════════════════════


_FILE_MUTATION_TOOL_NAMES = [
    "write_file", "edit_file", "delete_file",
    "docx_write", "pptx_write", "xlsx_write",
    "create_docx", "create_pptx", "create_xlsx",
    "edit_docx", "append_xlsx",
]


@pytest.mark.parametrize("tool_name", _FILE_MUTATION_TOOL_NAMES)
@pytest.mark.asyncio
async def test_file_mutation_tool_never_falls_back_to_none(tool_name):
    """Phase 34: every file mutation tool must NEVER fall back to NONE,
    even when allow_fallback_to_none=True."""
    engine = SandboxPolicyEngine(allow_fallback_to_none=True)
    ctx = FakeContext(tool_name, {"path": "/tmp/test.txt", "file_path": "/tmp/test.txt"})
    # Attempt 0 → RESTRICTED
    s0 = await engine.select(ctx, attempt=0)
    assert s0.sandbox_type == SandboxType.RESTRICTED
    # Attempt 1 → beyond chain → MUST raise
    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(ctx, attempt=1)
    assert "NONE fallback is disabled for file mutation" in str(exc_info.value)


@pytest.mark.parametrize("tool_name", _FILE_MUTATION_TOOL_NAMES)
@pytest.mark.asyncio
async def test_file_mutation_fallback_blocked_allow_fallback_false(tool_name):
    """Phase 34: allow_fallback_to_none=False → raises for all file mutation tools."""
    engine = SandboxPolicyEngine(allow_fallback_to_none=False)
    ctx = FakeContext(tool_name, {"path": "/tmp/test.txt", "file_path": "/tmp/test.txt"})
    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(ctx, attempt=99)
    assert "NONE fallback is disabled for file mutation" in str(exc_info.value)


@pytest.mark.parametrize("tool_name", _FILE_MUTATION_TOOL_NAMES)
@pytest.mark.asyncio
async def test_file_mutation_tool_base_is_restricted(tool_name):
    """Phase 34: every file mutation tool's base sandbox type is RESTRICTED."""
    engine = SandboxPolicyEngine()
    ctx = FakeContext(tool_name, {"path": "/tmp/test.txt", "file_path": "/tmp/test.txt"})
    selection = await engine.select(ctx, attempt=0)
    assert selection.sandbox_type == SandboxType.RESTRICTED


_READ_ONLY_TOOLS = ["read_file", "list_dir", "docx_read", "pptx_read", "xlsx_read"]


@pytest.mark.parametrize("tool_name", _READ_ONLY_TOOLS)
@pytest.mark.asyncio
async def test_read_only_tools_still_return_none(tool_name):
    """Phase 34: read-only tools are unaffected — they still get NONE."""
    engine = SandboxPolicyEngine()
    ctx = FakeContext(tool_name, {"path": "test.txt", "file_path": "test.txt"})
    selection = await engine.select(ctx, attempt=0)
    assert selection.sandbox_type == SandboxType.NONE


@pytest.mark.asyncio
async def test_filesystem_policy_for_office_write_includes_write_rule():
    """Phase 34: _filesystem_policy_for_tool() must include a WRITE rule
    for office document write tools, using file_path."""
    from miqi.protocol.permissions import FileSystemAccessMode

    engine = SandboxPolicyEngine()
    ctx = FakeContext("docx_write", {"file_path": "/tmp/report.docx"})
    selection = await engine.select(ctx)
    assert len(selection.filesystem_policy.rules) >= 1
    rule = selection.filesystem_policy.rules[0]
    assert rule.path == "/tmp/report.docx"
    assert rule.mode == FileSystemAccessMode.WRITE


@pytest.mark.asyncio
async def test_filesystem_policy_for_pptx_write_includes_write_rule():
    """Phase 34: pptx_write gets a WRITE rule for file_path."""
    from miqi.protocol.permissions import FileSystemAccessMode

    engine = SandboxPolicyEngine()
    ctx = FakeContext("pptx_write", {"file_path": "/tmp/slides.pptx"})
    selection = await engine.select(ctx)
    assert len(selection.filesystem_policy.rules) >= 1
    rule = selection.filesystem_policy.rules[0]
    assert rule.path == "/tmp/slides.pptx"
    assert rule.mode == FileSystemAccessMode.WRITE


@pytest.mark.asyncio
async def test_filesystem_policy_for_xlsx_write_includes_write_rule():
    """Phase 34: xlsx_write gets a WRITE rule for file_path."""
    from miqi.protocol.permissions import FileSystemAccessMode

    engine = SandboxPolicyEngine()
    ctx = FakeContext("xlsx_write", {"file_path": "/tmp/data.xlsx"})
    selection = await engine.select(ctx)
    assert len(selection.filesystem_policy.rules) >= 1
    rule = selection.filesystem_policy.rules[0]
    assert rule.path == "/tmp/data.xlsx"
    assert rule.mode == FileSystemAccessMode.WRITE


@pytest.mark.asyncio
async def test_filesystem_policy_for_create_docx_uses_final_suffix():
    """create_docx auto-adds .docx, so the sandbox must allow the final path."""
    from miqi.protocol.permissions import FileSystemAccessMode

    engine = SandboxPolicyEngine()
    ctx = FakeContext("create_docx", {"filename": "/tmp/report"})
    selection = await engine.select(ctx)
    assert len(selection.filesystem_policy.rules) >= 1
    rule = selection.filesystem_policy.rules[0]
    assert rule.path == "/tmp/report.docx"
    assert rule.mode == FileSystemAccessMode.WRITE


@pytest.mark.asyncio
async def test_sandbox_denied_error_for_file_mutation_is_actionable():
    """Phase 34: SandboxDeniedError for file mutation must list actionable info."""
    engine = SandboxPolicyEngine(allow_fallback_to_none=True)
    ctx = FakeContext("write_file", {"path": "/tmp/test.txt"})
    with pytest.raises(SandboxDeniedError) as exc_info:
        await engine.select(ctx, attempt=99)
    msg = str(exc_info.value)
    assert "workspace" in msg.lower() or "sandbox" in msg.lower()


def test_file_mutation_tools_matches_policy_set():
    """Phase 34: orchestrator and policy engine must agree on the set
    of file mutation tools."""
    from miqi.execution.sandbox_policy import SandboxPolicyEngine

    # Reconstruct the orchestrator's local set
    orch_set = frozenset({
        "write_file", "edit_file", "delete_file", "apply_patch",
        "docx_write", "pptx_write", "xlsx_write",
        "create_docx", "create_pptx", "create_xlsx",
        "edit_docx", "append_xlsx",
    })
    assert orch_set == SandboxPolicyEngine.FILE_MUTATION_TOOLS, (
        "Orchestrator's _FILE_MUTATION_TOOLS must match "
        "SandboxPolicyEngine.FILE_MUTATION_TOOLS"
    )
