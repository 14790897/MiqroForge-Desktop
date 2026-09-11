"""Phase 1: Session-scoped file operations write to sessions/{key}/files/ by default."""
import asyncio

from miqi.agent.tools.filesystem import ReadFileTool, WriteFileTool
from miqi.config.schema import AgentSessionConfig


def test_session_workspace_enabled_by_default():
    """AgentSessionConfig.session_workspace_enabled must default to True."""
    assert AgentSessionConfig().session_workspace_enabled is True


def test_write_relative_to_session_dir(tmp_path):
    """WriteFileTool with session workspace writes relative paths there."""
    session_files_dir = tmp_path / "sessions" / "test_key" / "files"
    session_files_dir.mkdir(parents=True)

    tool = WriteFileTool(workspace=session_files_dir, allowed_dir=None)
    result = asyncio.run(tool.execute(path="output.py", content="print('hello')"))
    assert "Successfully wrote" in result
    assert (session_files_dir / "output.py").exists()
    # Must NOT pollute workspace root
    assert not (tmp_path / "output.py").exists()


def test_write_absolute_bypasses_session_dir(tmp_path):
    """WriteFileTool: absolute paths resolve directly, ignoring session workspace."""
    session_files_dir = tmp_path / "sessions" / "k" / "files"
    session_files_dir.mkdir(parents=True)
    target = tmp_path / "project" / "code.py"
    target.parent.mkdir(parents=True)

    tool = WriteFileTool(workspace=session_files_dir, allowed_dir=None)
    result = asyncio.run(tool.execute(path=str(target), content="x = 1"))
    assert "Successfully wrote" in result
    assert target.exists()


def test_read_uses_project_workspace(tmp_path):
    """ReadFileTool with project workspace can read project files by relative path."""
    project_file = tmp_path / "src" / "main.py"
    project_file.parent.mkdir(parents=True)
    project_file.write_text("# main")

    tool = ReadFileTool(workspace=tmp_path, allowed_dir=None)
    result = asyncio.run(tool.execute(path="src/main.py"))
    assert "# main" in result


def test_session_workspace_disabled_writes_to_project(tmp_path):
    """When workspace=project root (feature disabled), relative writes go to project root."""
    tool = WriteFileTool(workspace=tmp_path, allowed_dir=None)
    asyncio.run(tool.execute(path="legacy.py", content="pass"))
    assert (tmp_path / "legacy.py").exists()
