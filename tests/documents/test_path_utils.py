"""Tests for shared office-tool path resolution (issue #806).

Covers the nested-path bug: `create_pdf` (and the docx/pptx/xlsx tools)
received a `filename` like ``sessions\\desktop_xxx\\files\\...`` — a path
written relative to the *workspace base* — and naively joined it onto the
*session files root*, producing a nested
``files\\sessions\\desktop_xxx\\files\\...`` location.
"""

from __future__ import annotations

import pytest

from miqi.documents.path_utils import resolve_output_path, resolve_read_path


def _session_files_dir(tmp_path, key: str = "desktop_test123"):
    """Build a session-files-style workspace: <base>/sessions/<key>/files."""
    files_dir = tmp_path / "workspace" / "sessions" / key / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    return files_dir


# ── resolve_output_path unit tests ─────────────────────────────────────


def test_plain_filename_resolves_against_session_files_root(tmp_path):
    files_dir = _session_files_dir(tmp_path)

    resolved = resolve_output_path("report.pdf", files_dir, files_dir)

    assert resolved == (files_dir / "report.pdf").resolve()


def test_relative_subdir_resolves_against_session_files_root(tmp_path):
    files_dir = _session_files_dir(tmp_path)

    resolved = resolve_output_path(
        "_ai_pest_run/step7_report/报告.pdf", files_dir, files_dir,
    )

    assert resolved == (files_dir / "_ai_pest_run/step7_report/报告.pdf").resolve()


def test_workspace_base_prefixed_path_is_normalized_not_nested(tmp_path):
    """#806 repro: `sessions/<key>/files/...` must not nest under files/."""
    key = "desktop_1787565338938"
    files_dir = _session_files_dir(tmp_path, key=key)

    resolved = resolve_output_path(
        f"sessions/{key}/files/_ai_pest_run/step7_report/中国AI行业PEST分析报告.pdf",
        files_dir,
        files_dir,
    )

    expected = files_dir / "_ai_pest_run/step7_report/中国AI行业PEST分析报告.pdf"
    assert resolved == expected.resolve()
    # The nested (buggy) location must NOT be produced.
    assert not (files_dir / "sessions").exists()


def test_backslash_prefixed_path_is_normalized(tmp_path):
    """Backslash-separated paths (as emitted by the agent on Windows) parse
    correctly on every platform."""
    key = "desktop_1787565338938"
    files_dir = _session_files_dir(tmp_path, key=key)

    resolved = resolve_output_path(
        rf"sessions\{key}\files\_ai_pest_run\step7_report\报告.pdf",
        files_dir,
        files_dir,
    )

    assert resolved == (files_dir / "_ai_pest_run/step7_report/报告.pdf").resolve()


def test_leading_separator_prefixed_path_is_normalized(tmp_path):
    r"""`\sessions\...` (rooted-relative, leading backslash) is equivalent to
    `sessions\...` and must be normalized the same way."""
    key = "desktop_1787565338938"
    files_dir = _session_files_dir(tmp_path, key=key)

    resolved = resolve_output_path(
        rf"\sessions\{key}\files\_ai_pest_run\step7_report\报告.pdf",
        files_dir,
        files_dir,
    )

    assert resolved == (files_dir / "_ai_pest_run/step7_report/报告.pdf").resolve()


def test_other_session_prefix_is_rejected(tmp_path):
    files_dir = _session_files_dir(tmp_path, key="desktop_aaa")

    with pytest.raises(PermissionError, match="其他会话"):
        resolve_output_path(
            "sessions/desktop_bbb/files/secret.pdf",
            files_dir,
            files_dir,
        )


def test_session_prefix_with_dotdot_escape_is_rejected(tmp_path):
    key = "desktop_aaa"
    files_dir = _session_files_dir(tmp_path, key=key)

    with pytest.raises(PermissionError):
        resolve_output_path(
            f"sessions/{key}/files/../../escape.pdf",
            files_dir,
            files_dir,
        )


def test_non_session_workspace_keeps_plain_joining(tmp_path):
    """Workspaces that are not `<base>/sessions/<key>/files` keep the old
    plain-join behavior — no session-prefix magic."""
    plain = tmp_path / "plain_workspace"
    plain.mkdir()

    resolved = resolve_output_path("sessions/x/files/y.pdf", plain, plain)

    assert resolved == (plain / "sessions/x/files/y.pdf").resolve()


def test_absolute_path_inside_session_root_is_allowed(tmp_path):
    files_dir = _session_files_dir(tmp_path)

    resolved = resolve_output_path(
        str(files_dir / "sub" / "report.pdf"), files_dir, files_dir,
    )

    assert resolved == (files_dir / "sub" / "report.pdf").resolve()


def test_absolute_path_outside_session_root_is_rejected(tmp_path):
    files_dir = _session_files_dir(tmp_path)

    with pytest.raises(PermissionError):
        resolve_output_path(str(tmp_path / "outside.pdf"), files_dir, files_dir)


# ── End-to-end CreatePdfTool tests (issue #806) ────────────────────────


@pytest.mark.asyncio
async def test_create_pdf_nested_prefix_lands_in_session_root(tmp_path):
    """Full tool run with the reported filename pattern: the PDF must be
    created at the session files root (not nested), and the response must
    return the actual absolute path."""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    key = "desktop_1787565338938"
    files_dir = _session_files_dir(tmp_path, key=key)
    tool = CreatePdfTool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(
        filename=(
            rf"sessions\{key}\files\_ai_pest_run\step7_report\中国AI行业PEST分析报告.pdf"
        ),
        title="中国AI行业PEST分析报告",
        content="正文内容",
    )

    expected = files_dir / "_ai_pest_run/step7_report/中国AI行业PEST分析报告.pdf"
    assert "Created:" in result
    assert str(expected.resolve()) in result  # 返回实际落盘路径
    assert expected.exists()
    assert expected.stat().st_size > 200
    # 嵌套位置必须不存在
    assert not (files_dir / "sessions").exists()
    assert not (tmp_path / "workspace" / "sessions" / key / "files" / "sessions").exists()


@pytest.mark.asyncio
async def test_create_pdf_other_session_prefix_rejected(tmp_path):
    from miqi.documents.pdf_create_tool import CreatePdfTool

    files_dir = _session_files_dir(tmp_path, key="desktop_aaa")
    tool = CreatePdfTool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(
        filename="sessions/desktop_bbb/files/secret.pdf",
        title="t",
        content="c",
    )

    assert "Error: 权限被拒绝" in result
    assert "其他会话" in result
    assert not (tmp_path / "workspace" / "sessions" / "desktop_bbb").exists()


@pytest.mark.asyncio
async def test_create_pdf_returns_absolute_path_in_success(tmp_path):
    from miqi.documents.pdf_create_tool import CreatePdfTool

    files_dir = _session_files_dir(tmp_path)
    tool = CreatePdfTool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(filename="报告.pdf", title="t", content="c")

    expected = files_dir / "报告.pdf"
    assert "Created:" in result
    assert str(expected.resolve()) in result
    assert expected.exists()


# ── Sibling office tools use the same resolution (issue #806 family) ───


@pytest.mark.asyncio
async def test_create_docx_normalizes_workspace_base_prefix(tmp_path):
    from miqi.documents.docx_tool import CreateDocxTool

    key = "desktop_1787565338938"
    files_dir = _session_files_dir(tmp_path, key=key)
    tool = CreateDocxTool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(
        filename=rf"sessions\{key}\files\报告.docx",
        title="报告",
        paragraphs=["正文"],
    )

    expected = files_dir / "报告.docx"
    assert "Created:" in result
    assert expected.exists()
    assert not (files_dir / "sessions").exists()


@pytest.mark.asyncio
async def test_create_xlsx_normalizes_workspace_base_prefix(tmp_path):
    from miqi.documents.xlsx_tool import CreateXlsxTool

    key = "desktop_1787565338938"
    files_dir = _session_files_dir(tmp_path, key=key)
    tool = CreateXlsxTool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(
        filename=rf"sessions\{key}\files\数据.xlsx",
        sheet_name="Sheet1",
        rows=[["A", "B"], [1, 2]],
    )

    expected = files_dir / "数据.xlsx"
    assert "Created:" in result
    assert expected.exists()
    assert not (files_dir / "sessions").exists()


# ── resolve_read_path: 输入路径与输出路径同边界（issue #1005 节 2）─────


def test_read_path_in_bounds_matches_output_path(tmp_path):
    """界内相对路径：与 resolve_output_path 解析出同一结果。"""
    files_dir = _session_files_dir(tmp_path)

    read = resolve_read_path("assets/chart.png", files_dir, files_dir)
    out = resolve_output_path("assets/chart.png", files_dir, files_dir)

    assert read == out == (files_dir / "assets" / "chart.png").resolve()


def test_read_path_out_of_bounds_rejected_without_user_roots(tmp_path):
    files_dir = _session_files_dir(tmp_path)
    secret = tmp_path / "outside" / "secret.png"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(b"x")

    with pytest.raises(PermissionError, match="不在"):
        resolve_read_path(str(secret), files_dir, files_dir)


def test_read_path_requires_injected_user_roots(tmp_path):
    """allow_user_roots=True 但没有注入 _user_roots → 仍然拒绝（fail-closed）。"""
    files_dir = _session_files_dir(tmp_path)
    secret = tmp_path / "outside" / "secret.png"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(b"x")

    with pytest.raises(PermissionError, match="不在"):
        resolve_read_path(
            str(secret), files_dir, files_dir, None, True,
        )


def test_read_path_allows_authorized_user_root(tmp_path):
    files_dir = _session_files_dir(tmp_path)
    authorized = tmp_path / "user_pics"
    image = authorized / "photo.png"
    authorized.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"x")

    resolved = resolve_read_path(
        str(image), files_dir, files_dir, [str(authorized)], True,
    )

    assert resolved == image.resolve()


def test_read_path_rejects_path_outside_authorized_roots(tmp_path):
    files_dir = _session_files_dir(tmp_path)
    authorized = tmp_path / "user_pics"
    authorized.mkdir(parents=True, exist_ok=True)
    secret = tmp_path / "outside" / "secret.png"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(b"x")

    with pytest.raises(PermissionError, match="不在用户授权目录内"):
        resolve_read_path(
            str(secret), files_dir, files_dir, [str(authorized)], True,
        )


def test_read_path_never_joins_relative_path_onto_user_root(tmp_path):
    """越界的相对路径不落到用户授权目录（否则 ../../.. 可逃逸）。"""
    files_dir = _session_files_dir(tmp_path)
    authorized = tmp_path / "user_pics"
    authorized.mkdir(parents=True, exist_ok=True)

    with pytest.raises(PermissionError, match="非绝对路径"):
        resolve_read_path(
            "../../outside/secret.png", files_dir, files_dir,
            [str(authorized)], True,
        )


def test_read_path_rejects_cross_session(tmp_path):
    files_dir = _session_files_dir(tmp_path, key="desktop_A")
    other = _session_files_dir(tmp_path, key="desktop_B") / "secret.png"
    other.write_bytes(b"x")

    with pytest.raises(PermissionError, match="不在"):
        resolve_read_path(str(other), files_dir, files_dir)
