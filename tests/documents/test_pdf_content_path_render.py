"""#994：create_pdf(content_path=...) 的行内 Markdown 与图片嵌入测试。

覆盖 plan_994_v2 §4 的用例：行内粗体/链接（含属性里的 "）、表格与代码围栏
不转义、行首图片的相对/越界/空路径/特殊字符/损坏/SVG/重复引用、跨根读取拒绝、
绘制尺寸断言、page_size 宽度、既有直调用兼容。
"""

import pytest


def _png(path, size=(400, 200), mode="RGB", color=(200, 30, 30)):
    """写一张最小 PNG（可指定 RGBA 透明），返回 path。"""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    fill = color + (128,) if mode == "RGBA" else color
    Image.new(mode, size, fill).save(path)
    return path


def _pdf_text(path):
    import pymupdf

    doc = pymupdf.open(str(path))
    text = "".join(p.get_text() for p in doc)
    doc.close()
    return text


def _drawn_images(path):
    """返回 [(页码, 绘制宽, 绘制高)]——按实际绘制尺寸统计（同一图重复引用计多次）。"""
    import pymupdf

    out = []
    doc = pymupdf.open(str(path))
    for i, page in enumerate(doc):
        for info in page.get_image_info():
            bbox = info["bbox"]
            out.append((i + 1, bbox[2] - bbox[0], bbox[3] - bbox[1]))
    doc.close()
    return out


def _frame_width(page_size):
    """与 _build_pdf 同口径计算 frame 内宽。"""
    from reportlab.lib.units import cm

    from miqi.documents.pdf_create_tool import _FRAME_PADDING_PT, _get_page_size

    return _get_page_size(page_size)[0] - 3.17 * 2 * cm - 2 * _FRAME_PADDING_PT


@pytest.mark.asyncio
async def test_create_pdf_content_path_inline_bold_and_ampersand(tmp_path):
    """CreatePdfTool: 段落内 **粗体** 转 <b>，R&D 转义后 PDF 文本仍是 R&D。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text("这是 **粗体** 与 R&D 文本。\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    text = _pdf_text(tmp_path / "o.pdf")
    assert "粗体" in text
    assert "R&D" in text
    assert "**" not in text
    assert "&amp;" not in text
    assert "R&D;" not in text


@pytest.mark.asyncio
async def test_create_pdf_content_path_heading_ampersand(tmp_path):
    """CreatePdfTool: 标题里的 & 必须正确渲染，不得出现 &amp; / &; 残留。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text("## 研发 & 投入 R&D\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    text = _pdf_text(tmp_path / "o.pdf")
    assert "研发 & 投入 R&D" in text
    assert "&amp;" not in text


@pytest.mark.asyncio
async def test_create_pdf_content_path_list_ampersand(tmp_path):
    """CreatePdfTool: 列表项里的 & 也要转义（真实报告 R&D 主要出现在列表项）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text(
        "- R&D 经费与投入强度\n- 第二项\n", encoding="utf-8"
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    text = _pdf_text(tmp_path / "o.pdf")
    assert "R&D 经费与投入强度" in text
    assert "R&D;" not in text
    assert "&amp;" not in text


@pytest.mark.asyncio
async def test_create_pdf_content_path_table_ampersand_not_escaped(tmp_path):
    """CreatePdfTool: 表格单元格不转义（Table 不解析 XML），R&D 不得变成 R&amp;D。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text(
        "| 项目 | 值 |\n| --- | --- |\n| R&D 经费 | 3.93 |\n", encoding="utf-8"
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    text = _pdf_text(tmp_path / "o.pdf")
    assert "R&D 经费" in text
    assert "&amp;" not in text


@pytest.mark.asyncio
async def test_create_pdf_content_path_link_quote_in_url(tmp_path):
    """CreatePdfTool: 链接 URL 里的 " 必须转成 &quot;，否则 paraparser 抛错导致零 PDF。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text(
        '见 [点我](https://example.com/a"b) 说明。\n', encoding="utf-8"
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="o.pdf", content_path="r.md")

    assert "Created:" in result
    assert "Error creating PDF" not in result
    assert "点我" in _pdf_text(tmp_path / "o.pdf")

    import pymupdf

    doc = pymupdf.open(str(tmp_path / "o.pdf"))
    uris = [lnk.get("uri") for lnk in doc[0].get_links() if lnk.get("uri")]
    doc.close()
    assert any('a"b' in uri for uri in uris), uris


@pytest.mark.asyncio
async def test_create_pdf_content_path_bold_wrapping_link(tmp_path):
    """CreatePdfTool: **加粗** 包裹链接时两者都要生效（嵌套不互相吞并）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text("**[文字](https://example.com/a)**\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    text = _pdf_text(tmp_path / "o.pdf")
    assert "文字" in text
    assert "**" not in text

    import pymupdf

    doc = pymupdf.open(str(tmp_path / "o.pdf"))
    uris = [lnk.get("uri") for lnk in doc[0].get_links() if lnk.get("uri")]
    doc.close()
    assert "https://example.com/a" in uris


@pytest.mark.asyncio
async def test_create_pdf_content_path_code_fence_not_inline_processed(tmp_path):
    """CreatePdfTool: 代码围栏内容不做行内转换（** 与 # 保持字面量）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text(
        "```\n**not bold**\n# not heading\n```\n", encoding="utf-8"
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    text = _pdf_text(tmp_path / "o.pdf")
    assert "**not bold**" in text
    assert "# not heading" in text


@pytest.mark.asyncio
async def test_create_pdf_content_path_inline_image_kept_as_text(tmp_path):
    """CreatePdfTool: 段落中间的行内图不嵌入，按普通文字保留。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    _png(tmp_path / "a.png")
    (tmp_path / "r.md").write_text("前面 ![x](a.png) 后面\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert _drawn_images(tmp_path / "o.pdf") == []
    assert "![x](a.png)" in _pdf_text(tmp_path / "o.pdf")


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_relative_embeds(tmp_path):
    """CreatePdfTool: 行首相对路径图片（本报告真实用法）按源稿目录解析并嵌入。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    _png(tmp_path / "step6_charts" / "assets" / "fig1.png")
    (tmp_path / "step7_report").mkdir()
    (tmp_path / "step7_report" / "r.md").write_text(
        "![图1 说明](../step6_charts/assets/fig1.png)\n", encoding="utf-8"
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(
        filename="o.pdf", content_path="step7_report/r.md"
    )

    assert len(_drawn_images(tmp_path / "o.pdf")) == 1
    assert "图1 说明" in _pdf_text(tmp_path / "o.pdf")


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_relative_escape_rejected(tmp_path):
    """CreatePdfTool: ../ 越界图片 → 占位 + 不读文件（即使目标真实存在且是合法 PNG）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    ws = tmp_path / "ws"
    ws.mkdir()
    _png(tmp_path / "secret.png")
    (ws / "r.md").write_text("![x](../secret.png)\n", encoding="utf-8")

    tool = CreatePdfTool(workspace=ws, allowed_dir=ws)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert _drawn_images(ws / "o.pdf") == []
    assert "[图表：x（见源稿）]" in _pdf_text(ws / "o.pdf")


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_absolute_outside_rejected(tmp_path):
    """CreatePdfTool: 绝对路径指向边界外 → 占位 + 不读文件。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    ws = tmp_path / "ws"
    ws.mkdir()
    outside = _png(tmp_path / "secret.png")
    (ws / "r.md").write_text(f"![x]({outside.as_posix()})\n", encoding="utf-8")

    tool = CreatePdfTool(workspace=ws, allowed_dir=ws)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert _drawn_images(ws / "o.pdf") == []
    assert "[图表：x（见源稿）]" in _pdf_text(ws / "o.pdf")


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_empty_dest(tmp_path):
    """CreatePdfTool: ![alt]() 空路径 → 占位，不抛异常。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text("![alt]()\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert _drawn_images(tmp_path / "o.pdf") == []
    assert "[图表：alt（见源稿）]" in _pdf_text(tmp_path / "o.pdf")


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_path_variants(tmp_path):
    """CreatePdfTool: 路径含空格/中文/%20/括号都能解析并嵌入。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    assets = tmp_path / "assets"
    _png(assets / "my chart.png")
    _png(assets / "图表.png")
    _png(assets / "fig(1).png")
    (tmp_path / "r.md").write_text(
        "![a](assets/my chart.png)\n"
        "![b](assets/图表.png)\n"
        "![c](assets/my%20chart.png)\n"
        "![d](assets/fig(1).png)\n",
        encoding="utf-8",
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert len(_drawn_images(tmp_path / "o.pdf")) == 4


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_corrupt_and_directory(tmp_path):
    """CreatePdfTool: 损坏图片与指向目录 → 占位，不抛异常。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "bad.png").write_bytes(b"not a png at all")
    (tmp_path / "dir.png").mkdir()
    (tmp_path / "r.md").write_text("![a](bad.png)\n![b](dir.png)\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert _drawn_images(tmp_path / "o.pdf") == []
    text = _pdf_text(tmp_path / "o.pdf")
    assert "[图表：a（见源稿）]" in text
    assert "[图表：b（见源稿）]" in text


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_svg_degraded(tmp_path):
    """CreatePdfTool: SVG 不支持 → 占位 + 不嵌入（需 svglib/cairosvg）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "chart.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8"
    )
    (tmp_path / "r.md").write_text("![x](chart.svg)\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert _drawn_images(tmp_path / "o.pdf") == []
    assert "[图表：x（见源稿）]" in _pdf_text(tmp_path / "o.pdf")


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_duplicate_reference(tmp_path):
    """CreatePdfTool: 同一张图重复引用 → 每次引用都绘制（不被去重吞掉）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    _png(tmp_path / "a.png")
    (tmp_path / "r.md").write_text(
        "![一](a.png)\n\n中间段落\n\n![二](a.png)\n", encoding="utf-8"
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert len(_drawn_images(tmp_path / "o.pdf")) == 2


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_draw_size_matches_expected(tmp_path):
    """CreatePdfTool: 实际绘制尺寸 == 期望尺寸（宽=frame 内宽，高等比）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    _png(tmp_path / "a.png", size=(400, 200))
    (tmp_path / "r.md").write_text("![x](a.png)\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    drawn = _drawn_images(tmp_path / "o.pdf")
    assert len(drawn) == 1
    _, width, height = drawn[0]
    expected_w = _frame_width("A4")
    assert abs(width - expected_w) < 0.5
    assert abs(height - expected_w / 2) < 0.5


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_tall_clamped(tmp_path):
    """CreatePdfTool: 超高图片等比缩到 ≤660pt，不得触发 LayoutError。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    _png(tmp_path / "tall.png", size=(100, 4000))
    (tmp_path / "r.md").write_text("![x](tall.png)\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="o.pdf", content_path="r.md")

    assert "Created:" in result
    drawn = _drawn_images(tmp_path / "o.pdf")
    assert len(drawn) == 1
    _, width, height = drawn[0]
    assert height <= 660.5
    assert abs(width - 660.0 / 40) < 0.5


@pytest.mark.parametrize("page_size", ["letter", "A3"])
@pytest.mark.asyncio
async def test_create_pdf_content_path_image_width_follows_page_size(tmp_path, page_size):
    """CreatePdfTool: 图片宽度跟随 frame 内宽（不写死 A4），letter/A3 都要正确。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    _png(tmp_path / "a.png", size=(400, 200))
    (tmp_path / "r.md").write_text("![x](a.png)\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(
        filename="o.pdf", content_path="r.md", page_size=page_size
    )

    drawn = _drawn_images(tmp_path / "o.pdf")
    assert len(drawn) == 1
    assert abs(drawn[0][1] - _frame_width(page_size)) < 0.5


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_transparent_png(tmp_path):
    """CreatePdfTool: RGBA 透明 PNG 正常嵌入（不得被转成黑底或报错）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    _png(tmp_path / "a.png", size=(400, 200), mode="RGBA")
    (tmp_path / "r.md").write_text("![x](a.png)\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="o.pdf", content_path="r.md")

    assert "Created:" in result
    assert "Error creating PDF" not in result
    assert len(_drawn_images(tmp_path / "o.pdf")) == 1


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_unvalidated_block_degraded(tmp_path):
    """CreatePdfTool: content 里手写的 image 块未经校验 → 降级，不得读任意文件。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    ws = tmp_path / "ws"
    ws.mkdir()
    outside = _png(tmp_path / "secret.png")
    tool = CreatePdfTool(workspace=ws, allowed_dir=ws)
    result = await tool.execute(
        filename="o.pdf",
        content=[{"type": "image", "path": outside.as_posix(), "alt": "x"}],
    )

    assert "Created:" in result
    assert _drawn_images(ws / "o.pdf") == []
    assert "[图表：x（见源稿）]" in _pdf_text(ws / "o.pdf")


@pytest.mark.asyncio
async def test_create_pdf_content_path_beats_content(tmp_path):
    """CreatePdfTool: content 与 content_path 同时给出时 content_path 优先。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text("# 文件源标题\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(
        filename="o.pdf",
        content="内联源标题",
        content_path="r.md",
    )

    text = _pdf_text(tmp_path / "o.pdf")
    assert "文件源标题" in text
    assert "内联源标题" not in text


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_user_root_same_root_only(tmp_path):
    """CreatePdfTool: 源稿在用户授权根时，图片只能落在同一个根内（不接受跨根读取）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    ws = tmp_path / "ws"
    ws.mkdir()
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    _png(root_a / "in_root_a.png")
    _png(root_b / "in_root_b.png")
    (root_a / "same.md").write_text("![同根](in_root_a.png)\n", encoding="utf-8")
    (root_a / "cross.md").write_text("![跨根](in_root_b.png)\n", encoding="utf-8")

    tool = CreatePdfTool(workspace=ws, allowed_dir=ws, allow_user_roots=True)
    roots = [str(root_a), str(root_b)]

    assert "Created:" in await tool.execute(
        filename="same.pdf", content_path=str(root_a / "same.md"), _user_roots=roots
    )
    assert len(_drawn_images(ws / "same.pdf")) == 1

    assert "Created:" in await tool.execute(
        filename="cross.pdf", content_path=str(root_a / "cross.md"), _user_roots=roots
    )
    assert _drawn_images(ws / "cross.pdf") == []
    assert "[图表：跨根（见源稿）]" in _pdf_text(ws / "cross.pdf")


def test_md_to_blocks_direct_call_backward_compatible():
    """_md_to_blocks: 新增参数全部 keyword-only 且带默认值，既有直调用不受影响。"""
    from miqi.documents.pdf_create_tool import _md_to_blocks

    blocks = _md_to_blocks("![x](a.png)\n正文\n")

    assert [b["type"] for b in blocks] == ["paragraph", "paragraph"]
    assert blocks[0]["text"] == "[图表：x（见源稿）]"
    assert blocks[1]["text"] == "正文"


@pytest.mark.asyncio
async def test_create_pdf_content_keeps_bold_tags(tmp_path):
    """CreatePdfTool: content 参数路径不经过行内转换，手写 <b> 标签仍生效。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        filename="o.pdf", content=[{"type": "paragraph", "text": "<b>粗体</b>"}]
    )

    assert "Created:" in result
    text = _pdf_text(tmp_path / "o.pdf")
    assert "粗体" in text
    assert "<b>" not in text
