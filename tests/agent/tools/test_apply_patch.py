"""Tests for miqi.agent.tools.apply_patch."""

import pytest

from miqi.agent.tools.apply_patch import (
    ApplyPatchTool,
    PatchApplyError,
    PatchParseError,
    apply_file_patch,
    parse_patch,
)


def test_parse_single_file_single_hunk():
    patch = (
        "--- a/foo.txt\n"
        "+++ b/foo.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+line2-changed\n"
        " line3\n"
    )
    files = parse_patch(patch)
    assert len(files) == 1
    assert files[0].path == "foo.txt"
    assert len(files[0].hunks) == 1
    hunk = files[0].hunks[0]
    assert hunk.old_start == 1
    assert hunk.old_count == 3
    assert hunk.new_start == 1
    assert hunk.new_count == 3
    assert hunk.lines == [" line1", "-line2", "+line2-changed", " line3"]


def test_parse_multiple_files():
    patch = (
        "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-x\n+y\n"
        "--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-1\n+2\n"
    )
    files = parse_patch(patch)
    assert [f.path for f in files] == ["a.txt", "b.txt"]


def test_parse_rejects_garbage():
    with pytest.raises(PatchParseError):
        parse_patch("not a patch")


def test_parse_rejects_missing_new_file():
    with pytest.raises(PatchParseError):
        parse_patch("--- a/foo.txt\n")


def test_parse_multi_hunk():
    patch = (
        "--- a/foo.txt\n"
        "+++ b/foo.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " line1\n"
        "-line2\n"
        "+line2a\n"
        "@@ -5,2 +5,2 @@\n"
        " line5\n"
        "-line6\n"
        "+line6a\n"
    )
    files = parse_patch(patch)
    assert len(files) == 1
    assert len(files[0].hunks) == 2


def test_apply_modifies_content():
    original = "line1\nline2\nline3\n"
    patch = parse_patch(
        "--- a/f.txt\n+++ b/f.txt\n@@ -1,3 +1,3 @@\n line1\n-line2\n+CHANGED\n line3\n"
    )[0]
    result = apply_file_patch(original, patch)
    assert result == "line1\nCHANGED\nline3\n"


def test_apply_detects_context_mismatch():
    original = "totally\ndifferent\n"
    patch = parse_patch(
        "--- a/f.txt\n+++ b/f.txt\n@@ -1,3 +1,3 @@\n line1\n-line2\n+X\n line3\n"
    )[0]
    with pytest.raises(PatchApplyError):
        apply_file_patch(original, patch)


def test_apply_multi_hunk_with_context_lines():
    original = "a\nb\nc\nd\ne\nf\n"
    patch = parse_patch(
        "--- a/f.txt\n+++ b/f.txt\n"
        "@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n"
        "@@ -4,3 +4,3 @@\n d\n-e\n+E\n f\n"
    )[0]
    result = apply_file_patch(original, patch)
    assert result == "a\nB\nc\nd\nE\nf\n"


def test_apply_preserves_no_trailing_newline():
    original = "line1\nline2"
    patch = parse_patch(
        "--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n line1\n-line2\n+CHANGED"
    )[0]
    result = apply_file_patch(original, patch)
    assert result == "line1\nCHANGED"


def test_apply_adds_new_line_at_end():
    original = "line1\nline2\n"
    patch = parse_patch(
        "--- a/f.txt\n+++ b/f.txt\n@@ -2,1 +2,2 @@\n line2\n+line3\n"
    )[0]
    result = apply_file_patch(original, patch)
    assert result == "line1\nline2\nline3\n"


@pytest.mark.asyncio
async def test_tool_applies_multi_file_patch(tmp_path):
    (tmp_path / "a.txt").write_text("x\n")
    (tmp_path / "b.txt").write_text("1\n")
    tool = ApplyPatchTool(workspace=tmp_path, allowed_dir=tmp_path)
    patch = (
        "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-x\n+y\n"
        "--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-1\n+2\n"
    )
    result = await tool.execute(patch=patch)
    assert "Applied patch to: a.txt, b.txt" in result
    assert (tmp_path / "a.txt").read_text() == "y\n"
    assert (tmp_path / "b.txt").read_text() == "2\n"


@pytest.mark.asyncio
async def test_tool_creates_snapshots(tmp_path):
    original = "line1\nline2\nline3\n"
    (tmp_path / "f.txt").write_text(original)

    snapshot_dir = tmp_path / "snaps"
    tool = ApplyPatchTool(
        workspace=tmp_path, allowed_dir=tmp_path, snapshot_dir=snapshot_dir
    )
    patch = (
        "--- a/f.txt\n+++ b/f.txt\n@@ -1,3 +1,3 @@\n"
        " line1\n-line2\n+CHANGED\n line3\n"
    )
    result = await tool.execute(patch=patch)
    assert "Applied patch to: f.txt" in result
    assert snapshot_dir.exists()
    assert any(snapshot_dir.iterdir())


@pytest.mark.asyncio
async def test_tool_reports_resolved_path_when_absolute_target_redirected(tmp_path):
    """A workspace-root absolute patch target that exists only in the session
    files dir is redirected there (session isolation) — the aggregate message
    must report the resolved path and the requested path (delivery truth)."""
    root = tmp_path / "ws"
    root.mkdir()
    session_dir = root / "sessions" / "desktop_x" / "files"
    session_dir.mkdir(parents=True)
    (session_dir / "note.txt").write_text("line1\nline2\nline3\n")
    tool = ApplyPatchTool(
        workspace=session_dir, allowed_dir=session_dir,
        base_workspace=root, session_files_dir=session_dir,
    )
    target = str(root / "note.txt")
    patch = (
        f"--- a/{target}\n+++ b/{target}\n"
        "@@ -1,3 +1,3 @@\n line1\n-line2\n+CHANGED\n line3\n"
    )
    result = await tool.execute(patch=patch)
    assert str(session_dir / "note.txt") in result
    assert target in result
    assert "已按会话隔离归一化到会话 files 目录" in result
    assert (session_dir / "note.txt").read_text() == "line1\nCHANGED\nline3\n"
    assert not (root / "note.txt").exists()


@pytest.mark.asyncio
async def test_relative_patch_message_keeps_original_format(tmp_path):
    """A relative patch target that exists in the session dir is edited in
    place (no re-anchor) — the message keeps the original relative path,
    which is the model's own address space under session isolation."""
    root = tmp_path / "ws"
    root.mkdir()
    session_dir = root / "sessions" / "desktop_y" / "files"
    session_dir.mkdir(parents=True)
    (session_dir / "note.txt").write_text("line1\nline2\nline3\n")
    tool = ApplyPatchTool(
        workspace=session_dir, allowed_dir=session_dir,
        base_workspace=root, session_files_dir=session_dir,
    )
    patch = (
        "--- a/note.txt\n+++ b/note.txt\n@@ -1,3 +1,3 @@\n line1\n-line2\n+CHANGED\n line3\n"
    )
    result = await tool.execute(patch=patch)
    assert "Applied patch to: note.txt" in result
    assert "已按会话隔离归一化" not in result
    assert (session_dir / "note.txt").read_text() == "line1\nCHANGED\nline3\n"


@pytest.mark.asyncio
async def test_tool_rejects_traversal(tmp_path):
    tool = ApplyPatchTool(workspace=tmp_path, allowed_dir=tmp_path)
    patch = "--- a/../etc/passwd\n+++ b/../etc/passwd\n@@ -1 +1 @@\n-x\n+y\n"
    result = await tool.execute(patch=patch)
    assert "Error" in result
    assert "权限被拒绝" in result


@pytest.mark.asyncio
async def test_tool_returns_error_on_context_mismatch(tmp_path):
    (tmp_path / "f.txt").write_text("totally\ndifferent\n")
    tool = ApplyPatchTool(workspace=tmp_path, allowed_dir=tmp_path)
    patch = (
        "--- a/f.txt\n+++ b/f.txt\n@@ -1,3 +1,3 @@\n"
        " line1\n-line2\n+X\n line3\n"
    )
    result = await tool.execute(patch=patch)
    assert "Error applying patch to f.txt" in result


@pytest.mark.asyncio
async def test_tool_returns_error_on_garbage_patch(tmp_path):
    tool = ApplyPatchTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(patch="not a patch")
    assert "Error: 补丁格式无效" in result
