"""Tests for Office document creation tools."""

import pytest


def _east_asia_font(run):
    from docx.oxml.ns import qn

    r_fonts = run._element.rPr.rFonts
    return r_fonts.get(qn("w:eastAsia")) if r_fonts is not None else None


@pytest.mark.asyncio
async def test_create_docx_supports_title_paragraphs_and_tables(tmp_path):
    from docx import Document

    from miqi.documents.docx_tool import CreateDocxTool

    files_dir = tmp_path / "files"
    tool = CreateDocxTool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(
        filename="report",
        title="Quarterly Report",
        paragraphs=["Summary paragraph."],
        tables=[{"rows": [["Metric", "Value"], ["Revenue", 42]]}],
    )

    path = files_dir / "report.docx"
    assert "Created:" in result
    assert path.exists()

    doc = Document(str(path))
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    assert "Quarterly Report" in texts
    assert "Summary paragraph." in texts
    assert doc.tables[0].cell(1, 0).text == "Revenue"


@pytest.mark.asyncio
async def test_create_docx_parses_markdown_heading_levels(tmp_path):
    from docx import Document

    from miqi.documents.docx_tool import CreateDocxTool

    files_dir = tmp_path / "files"
    tool = CreateDocxTool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(
        filename="headings",
        content="# Level 1\n### Level 3\n正文",
    )

    path = files_dir / "headings.docx"
    assert "Created:" in result

    doc = Document(str(path))
    paragraphs = [p for p in doc.paragraphs if p.text.strip()]
    assert [p.text for p in paragraphs] == ["Level 1", "Level 3", "正文"]
    assert paragraphs[0].style.name == "Heading 1"
    assert paragraphs[1].style.name == "Heading 3"


@pytest.mark.asyncio
async def test_docx_read_resolves_workspace_relative_paths(tmp_path):
    from miqi.documents.docx_tool import CreateDocxTool, DocxReadTool

    files_dir = tmp_path / "files"
    create = CreateDocxTool(workspace=files_dir, allowed_dir=files_dir)
    await create.execute(filename="relative_doc", title="Relative Title")

    read = DocxReadTool(workspace=files_dir, allowed_dir=files_dir)
    result = await read.execute(filename="relative_doc.docx")

    assert "Relative Title" in result


@pytest.mark.asyncio
async def test_create_docx_applies_chinese_title_and_body_formatting(tmp_path):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    from miqi.documents.docx_tool import CreateDocxTool

    files_dir = tmp_path / "files"
    tool = CreateDocxTool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(
        filename="essay",
        title="那山，那水，那故乡",
        paragraphs=["小时候，故乡在我眼中不过是一个地理名词。"],
        style_preset="chinese_document",
    )

    path = files_dir / "essay.docx"
    assert "Created:" in result

    doc = Document(str(path))
    title = next(p for p in doc.paragraphs if p.text == "那山，那水，那故乡")
    body = next(p for p in doc.paragraphs if p.text.startswith("小时候"))

    assert title.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert title.runs[0].bold is True
    assert title.runs[0].font.size == Pt(16)
    assert _east_asia_font(title.runs[0]) == "黑体"
    assert body.runs[0].font.size == Pt(12)
    assert _east_asia_font(body.runs[0]) == "宋体"
    assert body.paragraph_format.line_spacing == 1.5


@pytest.mark.asyncio
async def test_edit_docx_can_apply_formatting_without_text_change(tmp_path):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    from miqi.documents.docx_tool import EditDocxTool

    files_dir = tmp_path / "files"
    files_dir.mkdir()
    path = files_dir / "essay.docx"
    doc = Document()
    doc.add_heading("那山，那水，那故乡", level=0)
    doc.add_paragraph("小时候，故乡在我眼中不过是一个地理名词。")
    doc.save(str(path))

    tool = EditDocxTool(workspace=files_dir, allowed_dir=files_dir)
    result = await tool.execute(
        filename="essay.docx",
        format_instructions="正文宋体小四，段落1.5行距，标题黑体加粗，三号字居中",
    )

    assert "formatted" in result
    edited = Document(str(path))
    title = next(p for p in edited.paragraphs if p.text == "那山，那水，那故乡")
    body = next(p for p in edited.paragraphs if p.text.startswith("小时候"))

    assert title.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert title.runs[0].bold is True
    assert title.runs[0].font.size == Pt(16)
    assert _east_asia_font(title.runs[0]) == "黑体"
    assert body.runs[0].font.size == Pt(12)
    assert _east_asia_font(body.runs[0]) == "宋体"
    assert body.paragraph_format.line_spacing == 1.5


@pytest.mark.asyncio
async def test_edit_docx_format_instructions_override_bad_structured_style(tmp_path):
    from docx import Document
    from docx.shared import Pt

    from miqi.documents.docx_tool import EditDocxTool

    files_dir = tmp_path / "files"
    files_dir.mkdir()
    path = files_dir / "essay.docx"
    doc = Document()
    doc.add_heading("那山，那水，那故乡", level=1)
    doc.add_paragraph("小时候，故乡在我眼中不过是一个地理名词。")
    doc.save(str(path))

    tool = EditDocxTool(workspace=files_dir, allowed_dir=files_dir)
    result = await tool.execute(
        filename="essay.docx",
        title_style={"font_size_pt": 14},
        body_style={"font_size_pt": 16},
        format_instructions="正文宋体小四，段落1.5行距，标题黑体加粗，三号字居中",
    )

    assert "formatted" in result
    edited = Document(str(path))
    title = next(p for p in edited.paragraphs if p.text == "那山，那水，那故乡")
    body = next(p for p in edited.paragraphs if p.text.startswith("小时候"))

    assert title.runs[0].font.size == Pt(16)
    assert body.runs[0].font.size == Pt(12)


@pytest.mark.asyncio
async def test_create_xlsx_supports_multiple_sheets_formulas_and_charts(tmp_path):
    from openpyxl import load_workbook

    from miqi.documents.xlsx_tool import CreateXlsxTool

    files_dir = tmp_path / "files"
    tool = CreateXlsxTool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(
        filename="analysis",
        sheets=[
            {
                "name": "Sales",
                "rows": [["Month", "Sales"], ["Jan", 10], ["Feb", 20], ["Total", "=SUM(B2:B3)"]],
                "charts": [
                    {
                        "type": "bar",
                        "title": "Sales",
                        "data_range": "B1:B3",
                        "category_range": "A2:A3",
                        "anchor": "D2",
                    }
                ],
            },
            {"name": "Notes", "rows": [["Ready"]]},
        ],
    )

    path = files_dir / "analysis.xlsx"
    assert "Created:" in result
    assert path.exists()

    wb = load_workbook(str(path), data_only=False)
    assert wb.sheetnames == ["Sales", "Notes"]
    assert wb["Sales"]["B4"].value == "=SUM(B2:B3)"
    assert len(wb["Sales"]._charts) == 1


@pytest.mark.asyncio
async def test_create_xlsx_accepts_series_based_chart_specs(tmp_path):
    from openpyxl import load_workbook

    from miqi.documents.xlsx_tool import CreateXlsxTool

    files_dir = tmp_path / "files"
    tool = CreateXlsxTool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(
        filename="sales_analysis",
        sheets=[
            {
                "name": "Sales",
                "rows": [
                    ["Month", "Product A", "Product B", "Total"],
                    ["Jan", 100, 80, "=SUM(B2:C2)"],
                    ["Feb", 120, 90, "=SUM(B3:C3)"],
                    ["Mar", 150, 110, "=SUM(B4:C4)"],
                    ["Apr", 130, 140, "=SUM(B5:C5)"],
                ],
                "charts": [
                    {
                        "type": "bar",
                        "title": "Monthly Sales",
                        "categories": ["Jan", "Feb", "Mar", "Apr"],
                        "series": [
                            {"name": "Product A", "values": [100, 120, 150, 130]},
                            {"name": "Product B", "values": [80, 90, 110, 140]},
                        ],
                        "anchor_cell": "F2",
                    }
                ],
            }
        ],
    )

    path = files_dir / "sales_analysis.xlsx"
    assert "Created:" in result
    wb = load_workbook(str(path), data_only=False)
    assert len(wb["Sales"]._charts) == 1


@pytest.mark.asyncio
async def test_create_xlsx_accepts_top_level_rows_and_rejects_invalid_sheets(tmp_path):
    from openpyxl import load_workbook

    from miqi.documents.xlsx_tool import CreateXlsxTool, XlsxReadTool

    files_dir = tmp_path / "files"
    tool = CreateXlsxTool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(
        filename="top_rows",
        sheet_name="Data",
        rows=[["A", "B"], [1, 2]],
    )

    path = files_dir / "top_rows.xlsx"
    assert "Created:" in result
    wb = load_workbook(str(path), data_only=False)
    assert wb.sheetnames == ["Data"]
    assert wb["Data"]["B2"].value == 2

    read = XlsxReadTool(workspace=files_dir, allowed_dir=files_dir)
    read_result = await read.execute(filename="top_rows.xlsx", sheet_name="Data")
    assert "1 | 2" in read_result

    invalid = await tool.execute(filename="bad", sheets="not valid", rows=[["A"]])
    assert "sheets 必须是对象或数组" in invalid


@pytest.mark.asyncio
async def test_create_pptx_supports_multiple_slides_and_bullets(tmp_path):
    from pptx import Presentation

    from miqi.documents.pptx_tool import CreatePptxTool

    files_dir = tmp_path / "files"
    tool = CreatePptxTool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(
        filename="deck",
        slides=[
            {"title": "Overview", "content": "Opening"},
            {"title": "Plan", "bullets": ["Build", "Verify", "Ship"]},
        ],
    )

    path = files_dir / "deck.pptx"
    assert "Created:" in result
    assert path.exists()

    prs = Presentation(str(path))
    assert len(prs.slides) == 2
    text = "\n".join(
        shape.text
        for slide in prs.slides
        for shape in slide.shapes
        if hasattr(shape, "text")
    )
    assert "Overview" in text
    assert "Build" in text


@pytest.mark.asyncio
async def test_create_pptx_supports_subtitle_array_content_and_relative_read(tmp_path):
    from pptx import Presentation

    from miqi.documents.pptx_tool import CreatePptxTool, PptxReadTool

    files_dir = tmp_path / "files"
    tool = CreatePptxTool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(
        filename="deck",
        slides=[
            {"title": "Cover", "subtitle": "Sub title"},
            {"title": "Agenda", "content": ["A", "B"]},
        ],
    )

    path = files_dir / "deck.pptx"
    assert "Created:" in result
    prs = Presentation(str(path))
    text = "\n".join(
        shape.text
        for slide in prs.slides
        for shape in slide.shapes
        if hasattr(shape, "text")
    )
    assert "Sub title" in text
    assert "A" in text
    assert "B" in text
    assert "['A', 'B']" not in text

    read = PptxReadTool(workspace=files_dir, allowed_dir=files_dir)
    read_result = await read.execute(filename="deck.pptx")
    assert "Presentation: 2 slides" in read_result


@pytest.mark.asyncio
async def test_edit_docx_replaces_and_appends_text(tmp_path):
    from docx import Document

    from miqi.documents.docx_tool import EditDocxTool

    files_dir = tmp_path / "files"
    files_dir.mkdir()
    path = files_dir / "report.docx"
    doc = Document()
    doc.add_paragraph("Draft status")
    doc.save(str(path))

    tool = EditDocxTool(workspace=files_dir, allowed_dir=files_dir)
    result = await tool.execute(
        filename="report.docx",
        old_text="Draft",
        new_text="Final",
        append_paragraphs=["Approved"],
    )

    assert "Edited:" in result
    edited = Document(str(path))
    texts = [p.text for p in edited.paragraphs if p.text.strip()]
    assert "Final status" in texts
    assert "Approved" in texts


@pytest.mark.asyncio
async def test_append_xlsx_appends_rows_to_sheet(tmp_path):
    from openpyxl import Workbook, load_workbook

    from miqi.documents.xlsx_tool import AppendXlsxTool

    files_dir = tmp_path / "files"
    files_dir.mkdir()
    path = files_dir / "data.xlsx"
    wb = Workbook()
    wb.active.title = "Data"
    wb.active.append(["A", "B"])
    wb.save(str(path))

    tool = AppendXlsxTool(workspace=files_dir, allowed_dir=files_dir)
    result = await tool.execute(
        filename="data.xlsx",
        sheet_name="Data",
        rows=[[1, 2], [3, 4]],
    )

    assert "Appended:" in result
    edited = load_workbook(str(path))
    assert edited["Data"]["A2"].value == 1
    assert edited["Data"]["B3"].value == 4


@pytest.mark.parametrize(
    "tool_cls, kwargs, expected_name",
    [
        pytest.param("docx", {"filename": "../escape", "content": "x"}, "escape.docx"),
        pytest.param("xlsx", {"filename": "../escape", "sheets": {}}, "escape.xlsx"),
        pytest.param("pptx", {"filename": "../escape", "slides": []}, "escape.pptx"),
    ],
)
@pytest.mark.asyncio
async def test_create_office_tools_reject_path_traversal(
    tmp_path, tool_cls, kwargs, expected_name,
):
    if tool_cls == "docx":
        from miqi.documents.docx_tool import CreateDocxTool as Tool
    elif tool_cls == "xlsx":
        from miqi.documents.xlsx_tool import CreateXlsxTool as Tool
    else:
        from miqi.documents.pptx_tool import CreatePptxTool as Tool

    files_dir = tmp_path / "files"
    tool = Tool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(**kwargs)

    assert "权限被拒绝" in result
    assert not (tmp_path / expected_name).exists()


@pytest.mark.asyncio
async def test_create_pdf_simple(tmp_path):
    """CreatePdfTool: simple text PDF."""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        filename="simple.pdf",
        title="Test Title",
        content="Hello, PDF! 中文测试。",
    )
    path = tmp_path / "simple.pdf"
    assert "Created:" in result
    assert path.exists()
    assert path.stat().st_size > 200
    with open(path, "rb") as f:
        assert f.read(5) == b"%PDF-"


@pytest.mark.asyncio
async def test_create_pdf_structured_content(tmp_path):
    """CreatePdfTool: structured content blocks."""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        filename="structured.pdf",
        title="结构化文档",
        content=[
            {"type": "heading", "text": "第一章", "level": 1},
            {"type": "paragraph", "text": "正文内容。"},
            {"type": "table", "headers": ["姓名", "年龄"], "rows": [["张三", "28"]]},
            {"type": "list", "items": ["第一项", "第二项"]},
            {"type": "page_break"},
            {"type": "paragraph", "text": "第二页内容。"},
        ],
        author="MiQi",
        style_preset="chinese_document",
    )
    path = tmp_path / "structured.pdf"
    assert "Created:" in result
    assert path.exists()
    assert path.stat().st_size > 500
    with open(path, "rb") as f:
        assert f.read(5) == b"%PDF-"


@pytest.mark.asyncio
async def test_create_pdf_report_preset(tmp_path):
    """CreatePdfTool: report style preset."""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        filename="report.pdf",
        title="Annual Report",
        content="This is the report body text.",
        style_preset="report",
        page_size="A4",
    )
    path = tmp_path / "report.pdf"
    assert "Created:" in result
    assert path.exists()
    with open(path, "rb") as f:
        assert f.read(5) == b"%PDF-"


@pytest.mark.asyncio
async def test_create_pdf_chinese_font_discovery(tmp_path):
    """CreatePdfTool: font discovery finds a Chinese font."""
    from pathlib import Path

    from miqi.documents.pdf_create_tool import _get_chinese_font

    name, path = _get_chinese_font()
    assert name is not None
    if path is None:
        pytest.skip("no system CJK font available in this environment")
    assert Path(path).exists()


@pytest.mark.asyncio
async def test_create_pdf_errors(tmp_path):
    """CreatePdfTool: error handling."""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)

    # Missing filename
    result = await tool.execute(content="test")
    assert "Error: 必须提供 filename" in result

    # Missing content
    result = await tool.execute(filename="empty.pdf")
    assert "Error: 至少提供 title、content 或 content_path" in result

    # Permission denied (path traversal)
    result = await tool.execute(filename="../../escape.pdf", content="test")
    assert "Error: 权限被拒绝" in result


@pytest.mark.asyncio
async def test_pdf_write_alias(tmp_path):
    """PdfWriteTool alias works."""
    from miqi.documents.pdf_create_tool import PdfWriteTool

    tool = PdfWriteTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="alias.pdf", title="Alias Test", content="test")
    assert "Created:" in result
    path = tmp_path / "alias.pdf"
    assert path.exists()
    with open(path, "rb") as f:
        assert f.read(5) == b"%PDF-"


@pytest.mark.parametrize(
    "tool_cls, kwargs, expected_name",
    [
        pytest.param("pdf", {"filename": "../escape", "content": "x"}, "escape.pdf"),
    ],
)
@pytest.mark.asyncio
async def test_create_pdf_reject_path_traversal(
    tmp_path, tool_cls, kwargs, expected_name,
):
    from miqi.documents.pdf_create_tool import CreatePdfTool as Tool

    files_dir = tmp_path / "files"
    tool = Tool(workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(**kwargs)

    assert "权限被拒绝" in result
    assert not (tmp_path / expected_name).exists()


@pytest.mark.asyncio
async def test_create_pdf_content_path(tmp_path):
    """CreatePdfTool: render directly from a Markdown source file (content_path)."""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    src = tmp_path / "report.md"
    src.write_text(
        "# 报告标题\n\n"
        "## 第一章\n\n"
        "这是正文段落。\n\n"
        "- 列表项一\n"
        "- 列表项二\n\n"
        "| 姓名 | 年龄 |\n"
        "| --- | --- |\n"
        "| 张三 | 28 |\n\n"
        "![F001图表](step6_charts/assets/F001.svg)\n",
        encoding="utf-8",
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="from_md.pdf", content_path=src.name)
    path = tmp_path / "from_md.pdf"
    assert "Created:" in result
    assert path.exists()
    assert path.stat().st_size > 200
    with open(path, "rb") as f:
        assert f.read(5) == b"%PDF-"


@pytest.mark.asyncio
async def test_create_pdf_title_only_no_none(tmp_path):
    """CreatePdfTool: title-only 不得渲染出 'None' 段落（回归）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="title_only.pdf", title="唯一标题")
    path = tmp_path / "title_only.pdf"
    assert "Created:" in result
    assert path.exists()
    import pymupdf
    doc = pymupdf.open(str(path))
    text = "".join(p.get_text() for p in doc)
    doc.close()
    assert "None" not in text


@pytest.mark.asyncio
async def test_create_pdf_content_path_user_roots(tmp_path):
    """CreatePdfTool: 注入 _user_roots 时允许读取用户授权目录内绝对路径；未注入拒绝。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    ws = tmp_path / "ws"
    ws.mkdir()
    user_dir = tmp_path / "user"  # 位于 workspace 之外的用户授权目录
    user_dir.mkdir()
    (user_dir / "report.md").write_text("# 授权目录报告\n\n正文。\n", encoding="utf-8")

    tool = CreatePdfTool(workspace=ws, allowed_dir=ws, allow_user_roots=True)
    # 注入 _user_roots → 成功
    result = await tool.execute(
        filename="granted.pdf",
        content_path=str(user_dir / "report.md"),
        _user_roots=[str(user_dir)],
    )
    assert "Created:" in result
    assert (ws / "granted.pdf").exists()
    # 未注入 → 拒绝
    result2 = await tool.execute(
        filename="denied.pdf",
        content_path=str(user_dir / "report.md"),
    )
    assert "Error:" in result2
    assert "不在" in result2


@pytest.mark.asyncio
async def test_create_pdf_content_path_list_then_paragraph(tmp_path):
    """CreatePdfTool: 列表后无空行直接接段落，块顺序不得反转。"""
    from miqi.documents.pdf_create_tool import _md_to_blocks

    blocks = _md_to_blocks("- 项一\n- 项二\n紧接的段落\n")
    types = [b["type"] for b in blocks]
    assert types == ["list", "paragraph"]


@pytest.mark.asyncio
async def test_create_pdf_content_path_four_level_heading(tmp_path):
    """CreatePdfTool: 四级标题按 heading 块处理，不当作普通段落。"""
    from miqi.documents.pdf_create_tool import _md_to_blocks

    blocks = _md_to_blocks("#### 四级小标题\n正文\n")
    assert blocks[0]["type"] == "heading"
    assert blocks[0]["level"] == 4


@pytest.mark.asyncio
async def test_create_pdf_content_path_refuses_outside(tmp_path):
    """CreatePdfTool: content_path outside the allowed boundary is refused."""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path.parent / "secret.md").write_text("out of boundary", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        filename="no.pdf", content_path=(tmp_path.parent / "secret.md").as_posix()
    )
    assert "Error:" in result
    assert "不在" in result


@pytest.mark.asyncio
async def test_create_pdf_content_path_long_table_separator(tmp_path):
    """CreatePdfTool: 分隔行单元格 >=3 个短横线（| ---- |）也必须跳过，不得渲染成数据行。"""
    from miqi.documents.pdf_create_tool import _md_to_blocks

    blocks = _md_to_blocks("| 姓名 | 年龄 |\n| ---- | ---- |\n| 张三 | 28 |\n")
    assert [b["type"] for b in blocks] == ["table"]
    assert blocks[0]["headers"] == ["姓名", "年龄"]
    assert blocks[0]["rows"] == [["张三", "28"]]


@pytest.mark.asyncio
async def test_create_pdf_content_path_code_fence_separated(tmp_path):
    """CreatePdfTool: 围栏代码块必须与相邻叙述分段，不得被合并进同一段落。"""
    from miqi.documents.pdf_create_tool import _md_to_blocks

    blocks = _md_to_blocks("前一段\n```\nprint(1)\n```\n后一段\n")
    assert [b["text"] for b in blocks] == ["前一段", "print(1)", "后一段"]


@pytest.mark.asyncio
async def test_create_pdf_content_path_rerenders_after_source_change(tmp_path):
    """CreatePdfTool: 源稿 30 秒内改写后同名再渲染，必须重渲染而非返回旧 PDF。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    src = tmp_path / "report.md"
    src.write_text("# 第一版\n\n旧内容标记AAA。\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="dedup.pdf", content_path=src.name)

    src.write_text("# 第二版\n\n新内容标记BBB。\n", encoding="utf-8")
    assert "Created:" in await tool.execute(filename="dedup.pdf", content_path=src.name)

    import pymupdf
    doc = pymupdf.open(str(tmp_path / "dedup.pdf"))
    text = "".join(p.get_text() for p in doc)
    doc.close()
    assert "BBB" in text
    assert "AAA" not in text
