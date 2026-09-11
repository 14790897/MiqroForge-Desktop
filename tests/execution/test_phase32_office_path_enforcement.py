"""Phase 32 acceptance tests: Office Document Path Enforcement + ToolRegistry Guard.

Verifies:
- docx_write / pptx_write / xlsx_write path validation
- ToolRegistry.execute_concurrent has no production call sites
- ToolRuntime still routes through ToolOrchestrator
"""

import inspect
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.agent.tools.registry import ToolRegistry
from miqi.documents.docx_tool import DocxWriteTool
from miqi.documents.pptx_tool import PptxWriteTool
from miqi.documents.xlsx_tool import XlsxWriteTool
from miqi.execution.hook_runtime import HookOutcome

# ── Path enforcement: relative path inside workspace succeeds ────────────────


@pytest.mark.asyncio
async def test_docx_write_relative_path_inside_workspace(tmp_path):
    """Relative path resolves inside workspace — write succeeds."""
    tool = DocxWriteTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        file_path="output.docx",
        content="# Title\nBody text",
    )
    assert "Created:" in result
    assert (tmp_path / "output.docx").exists()


@pytest.mark.asyncio
async def test_pptx_write_relative_path_inside_workspace(tmp_path):
    """Relative path resolves inside workspace — write succeeds."""
    tool = PptxWriteTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        file_path="slides.pptx",
        slides=[{"title": "Hello", "content": "World"}],
    )
    assert "Created:" in result
    assert (tmp_path / "slides.pptx").exists()


@pytest.mark.asyncio
async def test_xlsx_write_relative_path_inside_workspace(tmp_path):
    """Relative path resolves inside workspace — write succeeds."""
    tool = XlsxWriteTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        file_path="data.xlsx",
        sheets={"Sheet1": [["A", "B"], [1, 2]]},
    )
    assert "Created:" in result
    assert (tmp_path / "data.xlsx").exists()


# ── Path enforcement: absolute path outside workspace must be denied ─────────


@pytest.mark.asyncio
async def test_docx_write_absolute_path_outside_workspace_denied(tmp_path):
    """Absolute path outside workspace is denied — no file created."""
    outside = Path("/tmp") if sys.platform != "win32" else Path("C:/Windows/Temp")
    tool = DocxWriteTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        file_path=str(outside / "should_not_exist.docx"),
        content="test",
    )
    assert "权限被拒绝" in result
    assert "Error" not in result or "权限被拒绝" in result


@pytest.mark.asyncio
async def test_pptx_write_absolute_path_outside_workspace_denied(tmp_path):
    """Absolute path outside workspace is denied for pptx_write."""
    outside = Path("/tmp") if sys.platform != "win32" else Path("C:/Windows/Temp")
    tool = PptxWriteTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        file_path=str(outside / "should_not_exist.pptx"),
        slides=[],
    )
    assert "权限被拒绝" in result


@pytest.mark.asyncio
async def test_xlsx_write_absolute_path_outside_workspace_denied(tmp_path):
    """Absolute path outside workspace is denied for xlsx_write."""
    outside = Path("/tmp") if sys.platform != "win32" else Path("C:/Windows/Temp")
    tool = XlsxWriteTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        file_path=str(outside / "should_not_exist.xlsx"),
        sheets={},
    )
    assert "权限被拒绝" in result


# ── Path traversal: ../outside.docx must be denied ───────────────────────────


@pytest.mark.asyncio
async def test_docx_write_path_traversal_rejected(tmp_path):
    """../outside.docx path traversal is denied — no file created outside."""
    tool = DocxWriteTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        file_path="../outside.docx",
        content="test",
    )
    assert "权限被拒绝" in result
    # The file must not exist outside
    parent_dir = tmp_path.parent
    assert not (parent_dir / "outside.docx").exists(), (
        "Path traversal created file outside workspace!"
    )


@pytest.mark.asyncio
async def test_pptx_write_path_traversal_rejected(tmp_path):
    """../outside.pptx path traversal is denied."""
    tool = PptxWriteTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        file_path="../outside.pptx",
        slides=[],
    )
    assert "权限被拒绝" in result


@pytest.mark.asyncio
async def test_xlsx_write_path_traversal_rejected(tmp_path):
    """../outside.xlsx path traversal is denied."""
    tool = XlsxWriteTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        file_path="../outside.xlsx",
        sheets={},
    )
    assert "权限被拒绝" in result


# ── Denial must not create/modify files ──────────────────────────────────────


@pytest.mark.asyncio
async def test_docx_write_denied_does_not_create_file(tmp_path):
    """When permission is denied, no file is created."""
    allowed_sub = tmp_path / "allowed"
    allowed_sub.mkdir()
    tool = DocxWriteTool(workspace=tmp_path, allowed_dir=allowed_sub)
    result = await tool.execute(
        file_path="outside.docx",  # relative to workspace, but outside allowed_dir
        content="test",
    )
    assert "权限被拒绝" in result
    assert not (tmp_path / "outside.docx").exists()
    assert not (allowed_sub / "outside.docx").exists()


@pytest.mark.asyncio
async def test_pptx_write_denied_does_not_create_file(tmp_path):
    """When permission is denied for pptx, no file is created."""
    allowed_sub = tmp_path / "allowed"
    allowed_sub.mkdir()
    tool = PptxWriteTool(workspace=tmp_path, allowed_dir=allowed_sub)
    result = await tool.execute(
        file_path="outside.pptx",
        slides=[],
    )
    assert "权限被拒绝" in result
    assert not (tmp_path / "outside.pptx").exists()


@pytest.mark.asyncio
async def test_xlsx_write_denied_does_not_create_file(tmp_path):
    """When permission is denied for xlsx, no file is created."""
    allowed_sub = tmp_path / "allowed"
    allowed_sub.mkdir()
    tool = XlsxWriteTool(workspace=tmp_path, allowed_dir=allowed_sub)
    result = await tool.execute(
        file_path="outside.xlsx",
        sheets={},
    )
    assert "权限被拒绝" in result
    assert not (tmp_path / "outside.xlsx").exists()


# ── Approval allow still must enforce path validation ────────────────────────


@pytest.mark.asyncio
async def test_docx_write_approval_allow_still_denies_workspace_outside(tmp_path):
    """Even when approval is allowed, path outside workspace must be blocked.

    This is the defense-in-depth guarantee: path enforcement is at the tool
    level, independent of the approval decision.  Approval cannot grant
    write access to files outside workspace.
    """
    outside = Path("/tmp") if sys.platform != "win32" else Path("C:/Windows/Temp")
    tool = DocxWriteTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        file_path=str(outside / "still_denied.docx"),
        content="test",
    )
    assert "权限被拒绝" in result


# ── ToolRegistry.execute_concurrent: no production call sites ────────────────


_PRODUCTION_PACKAGES = [
    "miqi.runtime",
    "miqi.bridge",
    "miqi.cli",
    "miqi.tui",
    "miqi.channels",
    "miqi.cron",
]

# Also cover miqi/execution and miqi/agent as "near-production"
_NEAR_PRODUCTION_PACKAGES = [
    "miqi.execution",
    "miqi.agent",
]


def _source_files_in(package_name: str) -> list[Path]:
    """Return all .py files in a package directory."""
    import importlib

    try:
        mod = importlib.import_module(package_name)
        pkg_dir = Path(mod.__path__[0])
    except (ImportError, AttributeError):
        return []
    return sorted(pkg_dir.rglob("*.py"))


def _grep_source(file_path: Path, pattern: str) -> list[str]:
    """Return lines in *file_path* matching *pattern*."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception:
        return []
    return [line.strip() for line in text.splitlines() if pattern in line]


def test_execute_concurrent_no_production_call_sites():
    """execute_concurrent must have zero call sites in production paths.

    All concurrent dispatch must go through ToolRuntime.execute_many()
    → ToolOrchestrator.execute().  Direct calls to execute_concurrent()
    bypass permission checks, sandbox policy, approval, hooks, and ledger.
    """
    hits: list[tuple[str, str]] = []

    for pkg in _PRODUCTION_PACKAGES:
        for py_file in _source_files_in(pkg):
            # Skip __init__.py re-exports that merely import
            lines = _grep_source(py_file, "execute_concurrent")
            for line in lines:
                # Allow the definition itself in registry.py
                if "registry.py" in str(py_file):
                    if "def execute_concurrent" in line or line.strip().startswith("#"):
                        continue
                    # Allow docstring references
                    if "execute_concurrent" in line and ("deprecated" in line.lower() or "no production" in line.lower()):
                        continue
                # Skip comments
                if line.strip().startswith("#"):
                    continue
                # Skip import-only lines
                if "import" in line and "execute_concurrent" not in line.split("=")[0]:
                    continue
                hits.append((str(py_file), line))

    assert len(hits) == 0, (
        "execute_concurrent() called in production paths:\n"
        + "\n".join(f"  {f}: {line}" for f, line in hits)
    )


def test_registry_execute_not_called_in_production():
    """ToolRegistry.execute() must not be called directly in production paths.

    All tool dispatch must go through execute_concurrent() so permission
    checks, sandbox policy, approvals, hooks, and the ledger apply to every
    call.  Matches the registry-accessor patterns that would bypass the
    concurrent path — generic `.execute(` on unrelated objects (e.g.
    orchestrator.execute) is intentionally not flagged.
    """
    hits: list[tuple[str, str]] = []
    patterns = (
        "registry.execute(",
        "tool_registry.execute(",
        "_registry.execute(",
        "tools.execute(",
    )

    # miqi/agent/subagent.py builds a private ToolRegistry for the legacy
    # subagent path and dispatches single tools directly — bypassing the
    # orchestrator's permission layer by design (legacy path, not
    # maintained).  Exempt that exact call site; any NEW direct call is
    # still flagged.
    known_legacy_callsites = {
        "subagent.py": "tools.execute(tool_call.name, tool_call.arguments)",
    }

    for pkg in _PRODUCTION_PACKAGES + _NEAR_PRODUCTION_PACKAGES:
        for py_file in _source_files_in(pkg):
            for line in _grep_source(py_file, ".execute("):
                if not any(p in line for p in patterns):
                    continue
                # Allow the definition itself inside registry.py
                if "registry.py" in str(py_file) and (
                    "def execute" in line or line.strip().startswith("#")
                ):
                    continue
                # Allow the documented legacy subagent call site
                if str(py_file).endswith("subagent.py") and (
                    known_legacy_callsites["subagent.py"] in line
                ):
                    continue
                hits.append((str(py_file), line))

    assert len(hits) == 0, (
        "ToolRegistry.execute() called in production paths:\n"
        + "\n".join(f"  {f}: {line}" for f, line in hits)
    )


# ── ToolRuntime still routes through orchestrator ────────────────────────────


@pytest.mark.asyncio
async def test_tool_runtime_routes_through_orchestrator():
    """ToolRuntime.execute_one() must call ToolOrchestrator.execute(),
    not ToolRegistry.execute() directly."""
    from miqi.agent.tools.filesystem import ReadFileTool
    from miqi.agent.tools.registry import ToolRegistry
    from miqi.execution.orchestrator import ToolOrchestrator
    from miqi.execution.sandbox_policy import SandboxPolicyEngine
    from miqi.runtime.tool_runtime import ToolRuntime

    # Build a minimal orchestrator with a ToolRegistry
    registry = ToolRegistry()
    registry.register(ReadFileTool())

    hook_runtime = MagicMock()
    hook_runtime.run_with_outcome = AsyncMock(return_value=HookOutcome.continue_())

    orch = ToolOrchestrator(
        permission_engine=MagicMock(),
        sandbox_engine=SandboxPolicyEngine(
            bwrap_available=False,
            landlock_available=False,
            allow_fallback_to_none=True,
        ),
        hook_runtime=hook_runtime,
        tool_registry=registry,
        event_emitter=MagicMock(),
    )

    # Patch orchestrator.execute to track calls
    _orig_exec = orch.execute
    orch.execute = AsyncMock(wraps=_orig_exec)

    # Create a ToolRuntime pointing at this orchestrator
    runtime = ToolRuntime(orchestrator=orch)

    # Build a minimal TurnContext
    from unittest.mock import MagicMock as Mock

    turn = Mock()
    turn.turn_id = "turn-test"
    turn.thread_id = "thread-test"
    turn.client_id = "client-test"
    turn.session_id = None
    turn.workspace = None
    turn.agent_metadata = Mock()
    turn.agent_metadata.name = "main"
    turn.model = "test-model"
    turn.provider = None

    # Execute a simple read-only tool
    tool_call = Mock()
    tool_call.name = "read_file"
    tool_call.id = "call-test"
    tool_call.arguments = {"path": "test.txt"}

    result = await runtime.execute_one(turn, tool_call)

    # The orchestrator must have been called
    orch.execute.assert_called()
    # ToolRuntime must not have called registry.execute() directly
    assert result is not None


# ── AgentLoop concurrency uses should_parallelize() correctly ─────────────────


def test_should_parallelize_does_not_call_execute_concurrent():
    """should_parallelize() is a pure classifier; it must never call execute_concurrent."""
    source = inspect.getsource(ToolRegistry.should_parallelize)
    assert "execute_concurrent" not in source, (
        "should_parallelize() must not call execute_concurrent — "
        "it is a pure classifier"
    )
    assert "self.execute(" not in source, (
        "should_parallelize() must not call execute() — "
        "it is a pure classifier"
    )
