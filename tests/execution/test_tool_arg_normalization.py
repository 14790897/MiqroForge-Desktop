"""Tests for tool arg normalization and sanitized debug logging (Phase 56).

Validates:
1. _normalize_tool_args maps alias → canonical without overwriting
2. _sanitize_args_for_log redacts secrets, truncates long strings, skips _prefixed
3. Orchestrator emits tool/error event on tool execution failure
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

# ── _normalize_tool_args ──────────────────────────────────────────────

def test_normalize_file_path_to_path():
    from miqi.execution.orchestrator import _normalize_tool_args
    result = _normalize_tool_args("read_file", {"file_path": "/tmp/a.txt"})
    assert result == {"path": "/tmp/a.txt"}


def test_normalize_cmd_to_command():
    from miqi.execution.orchestrator import _normalize_tool_args
    result = _normalize_tool_args("exec", {"cmd": "echo hello"})
    assert result == {"command": "echo hello"}


def test_normalize_filename_to_path():
    from miqi.execution.orchestrator import _normalize_tool_args
    result = _normalize_tool_args("read_file", {"filename": "config.json"})
    assert result == {"path": "config.json"}


def test_normalize_does_not_overwrite_existing_canonical():
    """When both alias and canonical exist, canonical wins and alias is dropped."""
    from miqi.execution.orchestrator import _normalize_tool_args
    result = _normalize_tool_args(
        "read_file", {"file_path": "/wrong.txt", "path": "/correct.txt"},
    )
    # canonical value preserved, alias removed
    assert result["path"] == "/correct.txt"
    assert "file_path" not in result


def test_normalize_preserves_unrelated_args():
    from miqi.execution.orchestrator import _normalize_tool_args
    result = _normalize_tool_args(
        "write_file",
        {"file_path": "/out.txt", "content": "hello", "mode": "w"},
    )
    assert result == {"path": "/out.txt", "content": "hello", "mode": "w"}


def test_normalize_noop_for_already_canonical():
    from miqi.execution.orchestrator import _normalize_tool_args
    result = _normalize_tool_args("exec", {"command": "ls", "working_dir": "/tmp"})
    assert result == {"command": "ls", "working_dir": "/tmp"}


# ── schema-aware normalization (issue #805) ───────────────────────────

class _FakeTool:
    """Minimal Tool-like object exposing a JSON-schema ``parameters`` property."""

    def __init__(self, properties: dict):
        self._properties = properties

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": self._properties,
            "required": list(self._properties),
        }


def test_normalize_rewrites_file_path_when_tool_schema_has_path():
    """read_file-style tools (canonical ``path``) still get file_path → path."""
    from miqi.execution.orchestrator import _normalize_tool_args
    tool = _FakeTool({"path": {"type": "string"}})
    result = _normalize_tool_args("read_file", {"file_path": "/tmp/a.txt"}, tool)
    assert result == {"path": "/tmp/a.txt"}


def test_normalize_keeps_file_path_for_pdf_read_style_tool():
    """issue #805: tools whose schema declares file_path must keep it unchanged."""
    from miqi.execution.orchestrator import _normalize_tool_args
    tool = _FakeTool({
        "file_path": {"type": "string"},
        "force_ocr": {"type": "boolean"},
    })
    result = _normalize_tool_args(
        "pdf_read", {"file_path": "uploads/report.pdf", "force_ocr": False}, tool,
    )
    assert result == {"file_path": "uploads/report.pdf", "force_ocr": False}
    assert "path" not in result


def test_normalize_maps_filename_to_file_path_for_file_path_style_tool():
    """filename alias maps to schema-declared file_path (CodeRabbit #840).

    PdfReadTool.execute() reads only ``file_path`` — retaining ``filename``
    would still produce the missing-file_path error.
    """
    from miqi.execution.orchestrator import _normalize_tool_args
    tool = _FakeTool({"file_path": {"type": "string"}})
    result = _normalize_tool_args("pdf_read", {"filename": "out.pdf"}, tool)
    assert result == {"file_path": "out.pdf"}
    assert "filename" not in result


def test_normalize_drops_filename_when_file_path_already_present():
    """When both file_path and filename arrive for a file_path tool, filename is dropped."""
    from miqi.execution.orchestrator import _normalize_tool_args
    tool = _FakeTool({"file_path": {"type": "string"}})
    result = _normalize_tool_args(
        "pdf_read", {"filename": "wrong.pdf", "file_path": "correct.pdf"}, tool,
    )
    assert result == {"file_path": "correct.pdf"}
    assert "filename" not in result


def test_normalize_drops_alias_when_schema_has_path_and_both_given():
    """When the tool declares ``path`` and both alias+canonical arrive, alias is dropped."""
    from miqi.execution.orchestrator import _normalize_tool_args
    tool = _FakeTool({"path": {"type": "string"}})
    result = _normalize_tool_args(
        "write_file",
        {"file_path": "/wrong.txt", "path": "/correct.txt", "content": "hi"},
        tool,
    )
    assert result == {"path": "/correct.txt", "content": "hi"}
    assert "file_path" not in result


def test_normalize_keeps_file_path_for_real_pdf_read_tool():
    """Regression for issue #805 with the real PdfReadTool schema."""
    from miqi.documents.pdf_read_tool import PdfReadTool
    from miqi.execution.orchestrator import _normalize_tool_args
    tool = PdfReadTool()
    result = _normalize_tool_args(
        "pdf_read", {"file_path": "uploads/report.pdf"}, tool,
    )
    assert result == {"file_path": "uploads/report.pdf"}


# ── _sanitize_args_for_log ────────────────────────────────────────────

def test_sanitize_redacts_api_key():
    from miqi.execution.orchestrator import _sanitize_args_for_log
    result = _sanitize_args_for_log({"api_key": "sk-secret-123456"})
    assert result == {"api_key": "[REDACTED]"}


def test_sanitize_redacts_token():
    from miqi.execution.orchestrator import _sanitize_args_for_log
    result = _sanitize_args_for_log({"token": "ghp_abcdef"})
    assert result == {"token": "[REDACTED]"}


def test_sanitize_redacts_password():
    from miqi.execution.orchestrator import _sanitize_args_for_log
    result = _sanitize_args_for_log({"password": "s3cret!"})
    assert result == {"password": "[REDACTED]"}


def test_sanitize_skips_internal_prefixed_args():
    from miqi.execution.orchestrator import _sanitize_args_for_log
    result = _sanitize_args_for_log({
        "command": "echo hi",
        "_sandbox": "restricted",
        "_event_emitter": "obj",
    })
    assert "_sandbox" not in result
    assert "_event_emitter" not in result
    assert result == {"command": "echo hi"}


def test_sanitize_truncates_long_strings():
    from miqi.execution.orchestrator import _sanitize_args_for_log
    long_text = "x" * 500
    result = _sanitize_args_for_log({"content": long_text})
    # 200 chars + '…' = 201
    assert len(result["content"]) == 201
    assert result["content"].endswith('…')


def test_sanitize_preserves_short_values():
    from miqi.execution.orchestrator import _sanitize_args_for_log
    result = _sanitize_args_for_log({"command": "echo hi", "path": "/tmp/a.txt"})
    assert result == {"command": "echo hi", "path": "/tmp/a.txt"}


def test_sanitize_redacts_secret():
    from miqi.execution.orchestrator import _sanitize_args_for_log
    result = _sanitize_args_for_log({"my_secret": "value"})
    assert result == {"my_secret": "[REDACTED]"}


# ── Orchestrator tool/error event on execution failure ─────────────────

@pytest.mark.asyncio
async def test_orchestrator_emits_tool_error_on_execution_exception():
    from miqi.execution.orchestrator import (
        ToolExecutionContext,
        ToolOrchestrator,
    )
    from miqi.execution.permission_engine import (
        PermissionDecision,
        PermissionEngine,
        PermissionVerdict,
    )
    from miqi.execution.sandbox_policy import (
        SandboxPolicyEngine,
        SandboxSelection,
        SandboxType,
    )
    from miqi.protocol.permissions import (
        FileSystemSandboxPolicy,
        NetworkSandboxPolicy,
    )

    # ── Setup mocks ──
    permission_engine = MagicMock(spec=PermissionEngine)
    permission_engine.check = AsyncMock(return_value=PermissionDecision(
        verdict=PermissionVerdict.ALLOW, reason="",
    ))

    sandbox_engine = MagicMock(spec=SandboxPolicyEngine)
    sandbox_engine.select = AsyncMock(return_value=SandboxSelection(
        sandbox_type=SandboxType.NONE,
        filesystem_policy=FileSystemSandboxPolicy(),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        env_passthrough=[],
        reason="no sandbox",
    ))

    events = MagicMock()
    events.emit = AsyncMock()

    # hook_runtime with run_with_outcome as awaitable AsyncMock
    from miqi.execution.hook_runtime import HookOutcome
    hooks = MagicMock()
    hooks.run_with_outcome = AsyncMock(return_value=HookOutcome(action="continue"))

    # Tool that raises unconditionally
    broken_tool = MagicMock()
    broken_tool.name = "broken_tool"
    broken_tool.execute = AsyncMock(side_effect=RuntimeError("simulated failure"))

    tool_registry = MagicMock()
    tool_registry.get.return_value = broken_tool

    orchestrator = ToolOrchestrator(
        permission_engine=permission_engine,
        sandbox_engine=sandbox_engine,
        hook_runtime=hooks,
        tool_registry=tool_registry,
        event_emitter=events,
        session_id="test",
    )

    ctx = ToolExecutionContext(
        tool_name="broken_tool",
        tool_call_id="call-1",
        arguments={},
        turn_id="turn-1",
        thread_id="thread-1",
        agent_type="main",
        cancel_event=None,
        permission_profile=None,
    )

    result_ctx = await orchestrator.execute(ctx)

    # orchestrator.execute() returns the ToolExecutionContext with .result set
    result_str = result_ctx.result
    assert result_str is not None
    assert "工具执行失败 broken_tool" in result_str
    assert "RuntimeError" in result_str
    assert "simulated failure" in result_str

    # Check tool/error event was emitted as proper ToolErrorEvent
    from miqi.protocol.events import ToolErrorEvent
    events.emit.assert_called()
    call_args = events.emit.call_args
    assert call_args is not None
    emitted_event = call_args[0][0]  # first positional arg of first call
    assert isinstance(emitted_event, ToolErrorEvent)
    assert emitted_event.tool_name == "broken_tool"
    assert emitted_event.tool_call_id == "call-1"
    assert "RuntimeError" in emitted_event.message
    assert "simulated failure" in emitted_event.message
    assert emitted_event.recoverable is True


# ── Orchestrator arg normalization before validate (issue #805 / CodeRabbit #840) ──

def _make_permissive_orchestrator(tool):
    """ToolOrchestrator with allow-all engines for arg-flow tests."""
    from miqi.execution.hook_runtime import HookOutcome
    from miqi.execution.orchestrator import (
        ToolOrchestrator,
    )
    from miqi.execution.permission_engine import (
        PermissionDecision,
        PermissionEngine,
        PermissionVerdict,
    )
    from miqi.execution.sandbox_policy import (
        SandboxPolicyEngine,
        SandboxSelection,
        SandboxType,
    )
    from miqi.protocol.permissions import (
        FileSystemSandboxPolicy,
        NetworkSandboxPolicy,
    )

    permission_engine = MagicMock(spec=PermissionEngine)
    permission_engine.check = AsyncMock(return_value=PermissionDecision(
        verdict=PermissionVerdict.ALLOW, reason="",
    ))

    sandbox_engine = MagicMock(spec=SandboxPolicyEngine)
    sandbox_engine.select = AsyncMock(return_value=SandboxSelection(
        sandbox_type=SandboxType.NONE,
        filesystem_policy=FileSystemSandboxPolicy(),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        env_passthrough=[],
        reason="no sandbox",
    ))

    hooks = MagicMock()
    hooks.run_with_outcome = AsyncMock(return_value=HookOutcome(action="continue"))

    tool_registry = MagicMock()
    tool_registry.get.return_value = tool

    return ToolOrchestrator(
        permission_engine=permission_engine,
        sandbox_engine=sandbox_engine,
        hook_runtime=hooks,
        tool_registry=tool_registry,
        event_emitter=MagicMock(emit=AsyncMock()),
        session_id="test",
    )


@pytest.mark.asyncio
async def test_orchestrator_normalizes_filename_before_validate_pdf_read():
    """Regression for #805 + CodeRabbit #840: passing ``filename`` to pdf_read
    must reach execute() as ``file_path`` — and must not be rejected by the
    pre-validation step that runs on the raw arguments."""
    from miqi.documents.pdf_read_tool import PdfReadTool
    from miqi.execution.orchestrator import ToolExecutionContext

    tool = PdfReadTool()
    tool.execute = AsyncMock(return_value="ok")
    orchestrator = _make_permissive_orchestrator(tool)

    ctx = ToolExecutionContext(
        tool_name="pdf_read",
        tool_call_id="call-805",
        arguments={"filename": "uploads/report.pdf"},
        turn_id="turn-1",
        thread_id="thread-1",
        agent_type="main",
        cancel_event=None,
        permission_profile=None,
    )

    result_ctx = await orchestrator.execute(ctx)

    # Validation must not reject the aliased input…
    assert result_ctx.result == "ok"
    # …and execute() must receive the canonical file_path arg.
    call_kwargs = tool.execute.call_args.kwargs
    assert call_kwargs.get("file_path") == "uploads/report.pdf"
    assert "filename" not in call_kwargs
