"""#994：create_pdf(content_path=...) 的行内 Markdown 与图片嵌入测试。

覆盖 plan_994_v2 §4 的用例：行内粗体/链接（含属性里的 "）、表格与代码围栏
不转义、行首图片的相对/越界/空路径/特殊字符/损坏/SVG/重复引用、跨根读取拒绝、
绘制尺寸断言、page_size 宽度、既有直调用兼容。

第二轮评审返工补充：content 路径 image 块的可信通道（H1）、跨根守卫真触发
（M1）、降采样像素宽度（M2）、JPEG 分支（M3）、字节/像素资源上限（M4）、
「越界不读文件」用 PIL.Image.open 调用记录断言（M5）。
"""

from contextlib import contextmanager

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


def _squash(text):
    """去掉全部空白——pymupdf 会把超长路径按行折断，字面文本断言需跨行匹配。"""
    return "".join(str(text).split())


def _has_image_xobject(path):
    """PDF 字节里是否含图像 XObject（``/Subtype /Image``）。

    不能用 ``b"/Image" in bytes`` 判定：reportlab 每份 PDF 的 ProcSet 都带
    ``/ImageB /ImageC /ImageI``，裸子串判定对纯文字 PDF 也恒为 True（实测）。
    """
    import re

    return bool(re.search(rb"/Subtype\s*/Image", path.read_bytes()))


def _frame_width(page_size):
    """与 _build_pdf 同口径计算 frame 内宽。"""
    from reportlab.lib.units import cm

    from miqi.documents.pdf_create_tool import _FRAME_PADDING_PT, _get_page_size

    return _get_page_size(page_size)[0] - 3.17 * 2 * cm - 2 * _FRAME_PADDING_PT


@contextmanager
def _capture_warnings():
    """捕获 loguru WARNING 消息——用于区分降级原因（跨根拒绝 vs 文件不存在）。"""
    from loguru import logger

    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


@pytest.fixture
def pil_open_spy(monkeypatch):
    """记录 PIL.Image.open 的调用参数。

    「未绘制图片」不等于「没读文件」——本 fixture 用来断言越界/超限路径**根本没打开**
    文件（``_build_image_flowable`` 在 stat/size 阶段就返回 None）。
    """
    import PIL.Image

    calls: list = []
    real_open = PIL.Image.open

    def _spy(fp, *args, **kwargs):
        calls.append(fp)
        return real_open(fp, *args, **kwargs)

    monkeypatch.setattr(PIL.Image, "open", _spy)
    return calls


@pytest.fixture
def paragraph_texts(monkeypatch):
    """记录传给 reportlab ``Paragraph`` 的**原始文本**（字体无关的渲染断言）。

    「从 PDF 提取文本」依赖字体：无 CJK 字体的 runner 上中文会被提取成缺字形
    （CI ubuntu-latest 实测 ``[图表：x（见源稿）]`` → ``[IIIxIIIII]``），因此中文
    断言不能建立在提取结果上。而降级占位发生在构造 ``Paragraph`` **之前**，捕获
    入参即可在任意字体环境下断言同一个字符串——且比提取结果更精确（保留 CJK
    括号原文，提取结果在正常字体下也会被 pymupdf 按行折断）。

    注意：``_build_pdf`` 是在函数内部 ``from reportlab.platypus import Paragraph``
    的，所以必须补丁 ``reportlab.platypus`` 上的类本身；补丁
    ``miqi.documents.pdf_create_tool.Paragraph`` 会被局部 import 覆盖，无效。
    """
    import reportlab.platypus as _platypus

    texts: list[str] = []
    real_paragraph = _platypus.Paragraph

    class _RecordingParagraph(real_paragraph):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            raw = args[0] if args else kwargs.get("text", "")
            texts.append(str(raw))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(_platypus, "Paragraph", _RecordingParagraph)
    return texts


def _assert_placeholder_rendered(texts, alt):
    """断言降级占位 ``[图表：{alt}（见源稿）]`` 进入了渲染（字体无关）。"""
    expected = _squash(f"[图表：{alt}（见源稿）]")
    assert any(expected in _squash(t) for t in texts), (expected, texts)


def _image_xref_info(pdf_path):
    """返回 PDF 中第一张嵌入图的 (ext, 像素宽, 像素高, 颜色分量数)。"""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    images = doc[0].get_images(full=True)
    assert len(images) == 1, images
    info = doc.extract_image(images[0][0])
    doc.close()
    return info["ext"], info["width"], info["height"], info["colorspace"]


@pytest.mark.asyncio
async def test_create_pdf_content_path_inline_bold_and_ampersand(tmp_path, paragraph_texts):
    """CreatePdfTool: 段落内 **粗体** 转 <b>，R&D 转义后 PDF 文本仍是 R&D。

    断言只用 ASCII：样本文本改成 ASCII，`**` 的消费与 `&` 的转义都不依赖中文排版，
    CI runner 无 CJK 字体也能判定。`<b>` 是否真的生成改用 Paragraph 入参断言
    （字体无关，且比原来只看「`**` 消失」更强——原来没有直接验证粗体标签）。
    """
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text("This is **bold** and R&D text.\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    text = _pdf_text(tmp_path / "o.pdf")
    assert "bold" in text
    assert "R&D" in text
    assert "**" not in text
    assert "&amp;" not in text
    assert "R&D;" not in text
    # 渲染输入断言：**bold** 已转成 <b>bold</b>，R&D 已转义成 R&amp;D
    assert any("<b>bold</b>" in t for t in paragraph_texts), paragraph_texts
    assert any("R&amp;D" in t for t in paragraph_texts), paragraph_texts


@pytest.mark.asyncio
async def test_create_pdf_content_path_heading_ampersand(tmp_path):
    """CreatePdfTool: 标题里的 & 必须正确渲染，不得出现 &amp; / &; 残留。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text("## R&D & growth plan\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    text = _pdf_text(tmp_path / "o.pdf")
    assert "R&D & growth plan" in text
    assert "&amp;" not in text


@pytest.mark.asyncio
async def test_create_pdf_content_path_list_ampersand(tmp_path):
    """CreatePdfTool: 列表项里的 & 也要转义（真实报告 R&D 主要出现在列表项）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text(
        "- R&D budget and intensity\n- second item\n", encoding="utf-8"
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    text = _pdf_text(tmp_path / "o.pdf")
    assert "R&D budget and intensity" in text
    assert "R&D;" not in text
    assert "&amp;" not in text


@pytest.mark.asyncio
async def test_create_pdf_content_path_table_ampersand_not_escaped(tmp_path):
    """CreatePdfTool: 表格单元格不转义（Table 不解析 XML），R&D 不得变成 R&amp;D。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text(
        "| Item | Value |\n| --- | --- |\n| R&D budget | 3.93 |\n", encoding="utf-8"
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    text = _pdf_text(tmp_path / "o.pdf")
    assert "R&D budget" in text
    assert "&amp;" not in text


@pytest.mark.asyncio
async def test_create_pdf_content_path_link_quote_in_url(tmp_path):
    """CreatePdfTool: 链接 URL 里的 " 必须转成 &quot;，否则 paraparser 抛错导致零 PDF。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text(
        'see [click me](https://example.com/a"b) here.\n', encoding="utf-8"
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="o.pdf", content_path="r.md")

    assert "Created:" in result
    assert "Error creating PDF" not in result
    assert "click me" in _pdf_text(tmp_path / "o.pdf")

    import pymupdf

    doc = pymupdf.open(str(tmp_path / "o.pdf"))
    uris = [lnk.get("uri") for lnk in doc[0].get_links() if lnk.get("uri")]
    doc.close()
    assert any('a"b' in uri for uri in uris), uris


@pytest.mark.asyncio
async def test_create_pdf_content_path_bold_wrapping_link(tmp_path):
    """CreatePdfTool: **加粗** 包裹链接时两者都要生效（嵌套不互相吞并）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text("**[text](https://example.com/a)**\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    text = _pdf_text(tmp_path / "o.pdf")
    assert "text" in text
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
        "![fig1 caption](../step6_charts/assets/fig1.png)\n", encoding="utf-8"
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(
        filename="o.pdf", content_path="step7_report/r.md"
    )

    assert len(_drawn_images(tmp_path / "o.pdf")) == 1
    assert "fig1 caption" in _pdf_text(tmp_path / "o.pdf")


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_relative_escape_rejected(
    tmp_path, pil_open_spy, paragraph_texts
):
    """CreatePdfTool: ../ 越界图片 → 占位 + 不读文件（即使目标真实存在且是合法 PNG）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    ws = tmp_path / "ws"
    ws.mkdir()
    _png(tmp_path / "secret.png")
    (ws / "r.md").write_text("![x](../secret.png)\n", encoding="utf-8")

    tool = CreatePdfTool(workspace=ws, allowed_dir=ws)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert _drawn_images(ws / "o.pdf") == []
    _assert_placeholder_rendered(paragraph_texts, "x")
    assert pil_open_spy == [], f"越界图片不应被打开: {pil_open_spy}"


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_absolute_outside_rejected(
    tmp_path, pil_open_spy, paragraph_texts
):
    """CreatePdfTool: 绝对路径指向边界外 → 占位 + 不读文件。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    ws = tmp_path / "ws"
    ws.mkdir()
    outside = _png(tmp_path / "secret.png")
    (ws / "r.md").write_text(f"![x]({outside.as_posix()})\n", encoding="utf-8")

    tool = CreatePdfTool(workspace=ws, allowed_dir=ws)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert _drawn_images(ws / "o.pdf") == []
    _assert_placeholder_rendered(paragraph_texts, "x")
    assert pil_open_spy == [], f"越界图片不应被打开: {pil_open_spy}"


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_empty_dest(tmp_path, paragraph_texts):
    """CreatePdfTool: ![alt]() 空路径 → 占位，不抛异常。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text("![alt]()\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert _drawn_images(tmp_path / "o.pdf") == []
    _assert_placeholder_rendered(paragraph_texts, "alt")


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
async def test_create_pdf_content_path_image_corrupt_and_directory(tmp_path, paragraph_texts):
    """CreatePdfTool: 损坏图片与指向目录 → 占位，不抛异常。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "bad.png").write_bytes(b"not a png at all")
    (tmp_path / "dir.png").mkdir()
    (tmp_path / "r.md").write_text("![a](bad.png)\n![b](dir.png)\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert _drawn_images(tmp_path / "o.pdf") == []
    _assert_placeholder_rendered(paragraph_texts, "a")
    _assert_placeholder_rendered(paragraph_texts, "b")


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_svg_degraded(tmp_path, paragraph_texts):
    """CreatePdfTool: SVG 不支持 → 占位 + 不嵌入（需 svglib/cairosvg）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "chart.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8"
    )
    (tmp_path / "r.md").write_text("![x](chart.svg)\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert _drawn_images(tmp_path / "o.pdf") == []
    _assert_placeholder_rendered(paragraph_texts, "x")


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
async def test_create_pdf_content_path_image_unvalidated_block_degraded(tmp_path, paragraph_texts):
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
    _assert_placeholder_rendered(paragraph_texts, "x")


@pytest.mark.asyncio
async def test_create_pdf_content_path_beats_content(tmp_path):
    """CreatePdfTool: content 与 content_path 同时给出时 content_path 优先。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text("# File source title\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(
        filename="o.pdf",
        content="Inline source title",
        content_path="r.md",
    )

    text = _pdf_text(tmp_path / "o.pdf")
    assert "File source title" in text
    assert "Inline source title" not in text


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_user_root_same_root_only(
    tmp_path, pil_open_spy, paragraph_texts
):
    """CreatePdfTool: 源稿在用户授权根时，图片只能落在同一个根内（不接受跨根读取）。

    注意：跨根用例必须写成 ``../root_b/in_root_b.png``。写成 ``in_root_b.png`` 会被
    join 成 ``root_a/in_root_b.png``——天然落在根内，走的是「文件不存在」分支，
    跨根守卫根本没触发（第二轮评审指出的假绿用例）。
    """
    from miqi.documents.pdf_create_tool import CreatePdfTool

    ws = tmp_path / "ws"
    ws.mkdir()
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    _png(root_a / "in_root_a.png")
    _png(root_b / "in_root_b.png")
    (root_a / "same.md").write_text("![同根](in_root_a.png)\n", encoding="utf-8")
    (root_a / "cross.md").write_text("![跨根](../root_b/in_root_b.png)\n", encoding="utf-8")

    tool = CreatePdfTool(workspace=ws, allowed_dir=ws, allow_user_roots=True)
    roots = [str(root_a), str(root_b)]

    assert "Created:" in await tool.execute(
        filename="same.pdf", content_path=str(root_a / "same.md"), _user_roots=roots
    )
    assert len(_drawn_images(ws / "same.pdf")) == 1

    # 目标图真实存在且可读（两个根都在 _user_roots 内），唯一拒绝理由是「跨根」
    assert (root_b / "in_root_b.png").is_file()
    pil_open_spy.clear()  # 同根用例已合法读过图，跨根用例从零开始计数
    with _capture_warnings() as warnings:
        assert "Created:" in await tool.execute(
            filename="cross.pdf", content_path=str(root_a / "cross.md"), _user_roots=roots
        )
    assert _drawn_images(ws / "cross.pdf") == []
    _assert_placeholder_rendered(paragraph_texts, "跨根")
    assert any("不在源稿授权根" in m for m in warnings), warnings
    assert not any("不存在" in m for m in warnings), warnings
    assert pil_open_spy == [], f"跨根图片不应被打开: {pil_open_spy}"


def test_md_to_blocks_direct_call_backward_compatible():
    """_md_to_blocks: 新增参数全部 keyword-only 且带默认值，既有直调用不受影响。"""
    from miqi.documents.pdf_create_tool import _md_to_blocks

    blocks = _md_to_blocks("![x](a.png)\n正文\n")

    assert [b["type"] for b in blocks] == ["paragraph", "paragraph"]
    assert blocks[0]["text"] == "[图表：x（见源稿）]"
    assert blocks[1]["text"] == "正文"


@pytest.mark.asyncio
async def test_create_pdf_content_markup_is_literal_text(tmp_path):
    """P1（行为变更）: content 手写 markup 不再被 reportlab 解析，原样作字面文本。

    修复前 ``content`` 的 ``<b>bold</b>`` 会渲染成粗体；转义后它与
    ``<img src=...>`` 一样只是文字——这是为堵住「模型提供的 markup 可读任意
    文件」通道而付出的代价（见本文件 P1 小节）。断言只用 ASCII：Ubuntu CI
    runner 无 CJK 字体，中文断言会因缺字形而失败（与本 PR 无关的既有环境问题）。
    """
    from miqi.documents.pdf_create_tool import CreatePdfTool

    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        filename="o.pdf", content=[{"type": "paragraph", "text": "<b>bold</b>"}]
    )

    assert "Created:" in result
    text = _pdf_text(tmp_path / "o.pdf")
    assert "<b>bold</b>" in text, text


# ── H1：content 的 image 块不得成为读任意文件的通道 ─────────────────────────


@pytest.mark.asyncio
async def test_create_pdf_content_image_block_cannot_forge_trust(
    tmp_path, pil_open_spy, paragraph_texts
):
    """CreatePdfTool: content 里 image 块自带 ``validated: true`` 不得成为可信通道。

    回归 H1（第二轮评审实测：base 6f8b880b 无此行为，本 PR 新引入）：``validated``
    曾是模型可写的普通 JSON 字段，伪造后能嵌入工作区外任意文件。现在「可信」只来自
    ``_build_pdf(trusted_images=...)`` 函数参数（模型不可见），content 路径恒为 False
    → 0 张图嵌入 + 占位 + warning，且 **根本没打开文件**。
    """
    from miqi.documents.pdf_create_tool import CreatePdfTool

    ws = tmp_path / "ws"
    ws.mkdir()
    outside = _png(tmp_path / "secret.png")
    tool = CreatePdfTool(workspace=ws, allowed_dir=ws)
    result = await tool.execute(
        filename="o.pdf",
        content=[
            {
                "type": "image",
                "path": outside.as_posix(),
                "alt": "x",
                "validated": True,
            }
        ],
    )

    assert "Created:" in result
    assert _drawn_images(ws / "o.pdf") == []
    _assert_placeholder_rendered(paragraph_texts, "x")
    assert pil_open_spy == [], f"边界外图片不应被打开: {pil_open_spy}"


@pytest.mark.asyncio
async def test_create_pdf_content_image_block_in_boundary_also_degraded(
    tmp_path, pil_open_spy, paragraph_texts
):
    """CreatePdfTool: 即使路径在边界内，content 路径也不再支持图片块（一律占位）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    ws = tmp_path / "ws"
    ws.mkdir()
    inside = _png(ws / "in.png")
    tool = CreatePdfTool(workspace=ws, allowed_dir=ws)
    result = await tool.execute(
        filename="o.pdf",
        content=[
            {
                "type": "image",
                "path": inside.as_posix(),
                "alt": "y",
                "validated": True,
            }
        ],
    )

    assert "Created:" in result
    assert _drawn_images(ws / "o.pdf") == []
    _assert_placeholder_rendered(paragraph_texts, "y")
    assert pil_open_spy == [], f"content 路径不应嵌入图片: {pil_open_spy}"


@pytest.mark.asyncio
async def test_create_pdf_content_path_still_embeds_four_images(tmp_path):
    """CreatePdfTool: H1 修复不得回归 content_path 的可信通道——4 张图仍全部嵌入。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "r.md").write_text(
        "".join(f"![图{i}](fig{i}.png)\n" for i in range(4)), encoding="utf-8"
    )
    for i in range(4):
        _png(tmp_path / f"fig{i}.png")

    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert len(_drawn_images(tmp_path / "o.pdf")) == 4


# ── M2：降采样后的实际像素宽度 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_downsampled_to_target_dpi(tmp_path):
    """CreatePdfTool: 嵌入图实际像素宽 == 200 DPI 换算值（A4 内容宽 403.55pt → 1121px）。

    此前所有用例的测试图都是 400×200，``_downsample_image`` 的 resize 分支一次都没进。
    """
    from miqi.documents.pdf_create_tool import CreatePdfTool

    _png(tmp_path / "big.png", size=(2700, 1639))
    (tmp_path / "r.md").write_text("![x](big.png)\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    expected_px = round(_frame_width("A4") / 72 * 200)
    assert expected_px == 1121
    ext, px_w, px_h, _ = _image_xref_info(tmp_path / "o.pdf")
    assert ext == "png"
    assert px_w == expected_px, (px_w, expected_px)
    assert px_h == round(1639 * expected_px / 2700)


# ── M3：JPEG 分支 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_jpeg_rgb_embeds_and_downsampled(tmp_path):
    """CreatePdfTool: RGB JPEG 走 JPEG 分支并按 200 DPI 降采样（PDF 内为 DCTDecode）。"""
    from PIL import Image

    from miqi.documents.pdf_create_tool import CreatePdfTool

    Image.new("RGB", (2700, 1639), (10, 120, 200)).save(
        tmp_path / "big.jpg", format="JPEG", quality=90
    )
    (tmp_path / "r.md").write_text("![x](big.jpg)\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert len(_drawn_images(tmp_path / "o.pdf")) == 1
    ext, px_w, _, colorspace = _image_xref_info(tmp_path / "o.pdf")
    assert ext == "jpeg"
    assert px_w == 1121
    assert colorspace == 3


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_jpeg_grayscale_converted_to_rgb(tmp_path):
    """CreatePdfTool: 灰度 JPEG（mode L）走 ``convert("RGB")`` 分支，输出仍为 JPEG/RGB。"""
    from PIL import Image

    from miqi.documents.pdf_create_tool import CreatePdfTool

    Image.new("L", (400, 200), 128).save(tmp_path / "gray.jpg", format="JPEG")
    (tmp_path / "r.md").write_text("![x](gray.jpg)\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert len(_drawn_images(tmp_path / "o.pdf")) == 1
    ext, px_w, px_h, colorspace = _image_xref_info(tmp_path / "o.pdf")
    assert ext == "jpeg"
    assert (px_w, px_h) == (400, 200)
    assert colorspace == 3


# ── M4：资源上限守卫（字节 / 像素） ────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_bytes_limit_rejected(
    tmp_path, pil_open_spy, paragraph_texts
):
    """CreatePdfTool: 单图超过 20MB 字节上限 → 占位，且在打开图片前就被拒（不读文件）。"""
    from miqi.documents.pdf_create_tool import _MAX_IMAGE_BYTES, CreatePdfTool

    (tmp_path / "big.png").write_bytes(b"\0" * (_MAX_IMAGE_BYTES + 1))
    (tmp_path / "r.md").write_text("![x](big.png)\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert _drawn_images(tmp_path / "o.pdf") == []
    _assert_placeholder_rendered(paragraph_texts, "x")
    assert pil_open_spy == [], f"超限图片不应被打开: {pil_open_spy}"


@pytest.mark.asyncio
async def test_create_pdf_content_path_image_pixels_limit_rejected(tmp_path, paragraph_texts):
    """CreatePdfTool: 单图超过 40Mpx 像素上限 → 占位（防解压炸弹）。"""
    from PIL import Image

    from miqi.documents.pdf_create_tool import _MAX_IMAGE_PIXELS, CreatePdfTool

    width, height = 7000, 5715  # 40,005,000 > 40,000,000
    assert width * height > _MAX_IMAGE_PIXELS
    Image.new("L", (width, height), 0).save(tmp_path / "huge.png")
    (tmp_path / "r.md").write_text("![x](huge.png)\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    assert "Created:" in await tool.execute(filename="o.pdf", content_path="r.md")

    assert _drawn_images(tmp_path / "o.pdf") == []
    _assert_placeholder_rendered(paragraph_texts, "x")


# ── P1：源稿/content 原文不得成为 reportlab markup 注入通道（第三轮评审） ────
#
# 根因：_md_inline 只覆盖段落/标题/列表/图注/引用，代码围栏与 content 路径的块
# 文本直接进 Paragraph()。源稿里的 raw <img src="..."> 由 reportlab paraparser
# → ImageReader 直接打开文件，绕过 _resolve_md_image 的边界校验（实测：PDF 里
# 出现 /Subtype /Image XObject；非图片文件则整档 build 抛 OSError）。
# 修复：文本进 Paragraph 前统一 XML 转义（& < >），<img ...> 只作字面文字。


@pytest.mark.asyncio
async def test_create_pdf_content_path_code_fence_raw_img_not_embedded(tmp_path, pil_open_spy):
    """P1: 围栏代码块里的 <img src=边界外> 必须是字面文字——不读文件、不嵌入。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    outside = _png(tmp_path.parent / "fence_secret.png")
    (tmp_path / "f.md").write_text(
        f'前一段\n```\n<img src="{outside.as_posix()}" width="100" height="50"/>\n```\n后一段\n',
        encoding="utf-8",
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="o.pdf", content_path="f.md")

    assert "Created:" in result, result
    out = tmp_path / "o.pdf"
    assert not _has_image_xobject(out), "围栏代码里的 <img> 不得产生图像 XObject"
    assert _drawn_images(out) == []
    assert pil_open_spy == [], f"边界外文件不得被打开: {pil_open_spy}"
    text = _pdf_text(out)
    assert _squash(f'<img src="{outside.as_posix()}" width="100" height="50"/>') in _squash(text), text


@pytest.mark.asyncio
async def test_create_pdf_content_path_code_fence_non_image_outside_not_read(tmp_path):
    """P1: 边界外文件做成**非图片**——若被 reportlab 读取必抛错，PDF 必须仍正常生成。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    outside = tmp_path.parent / "fence_not_an_image.png"
    outside.write_text("not an image\n", encoding="utf-8")
    (tmp_path / "f.md").write_text(
        f'```\n<img src="{outside.as_posix()}"/>\n```\n', encoding="utf-8"
    )
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)

    assert "Created:" in await tool.execute(filename="o.pdf", content_path="f.md")


@pytest.mark.asyncio
async def test_create_pdf_content_path_code_fence_angle_brackets_literal(tmp_path):
    """P1(coderabbit): 围栏里的 List<int> / a < b 必须原样显示，不得被当标签吞掉。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "c.md").write_text("```\nList<int> x;\nif a < b: pass\n```\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="o.pdf", content_path="c.md")

    assert "Created:" in result, result
    text = _pdf_text(tmp_path / "o.pdf")
    assert "List<int> x;" in text, text
    assert "a < b" in text, text


@pytest.mark.asyncio
async def test_create_pdf_content_paragraph_raw_img_not_embedded(tmp_path, pil_open_spy):
    """P1: content 段落里的 <img src=边界外> 必须是字面文字——不读文件、不嵌入。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    outside = _png(tmp_path.parent / "para_secret.png")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        filename="o.pdf",
        content=[
            {
                "type": "paragraph",
                "text": f'<img src="{outside.as_posix()}" width="100" height="50"/>',
            }
        ],
    )

    assert "Created:" in result, result
    out = tmp_path / "o.pdf"
    assert not _has_image_xobject(out), "content 段落里的 <img> 不得产生图像 XObject"
    assert _drawn_images(out) == []
    assert pil_open_spy == [], f"边界外文件不得被打开: {pil_open_spy}"
    text = _pdf_text(out)
    assert _squash(f'<img src="{outside.as_posix()}" width="100" height="50"/>') in _squash(text), text


@pytest.mark.asyncio
async def test_create_pdf_content_heading_raw_img_not_embedded(tmp_path, pil_open_spy):
    """P1: content 标题（level≤2 直渲 / level≥3 另包 <b>）里的 <img> 同样不得生效。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    outside = _png(tmp_path.parent / "head_secret.png")
    payload = f'<img src="{outside.as_posix()}"/>'
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        filename="o.pdf",
        content=[
            {"type": "heading", "text": payload, "level": 1},
            {"type": "heading", "text": payload, "level": 3},
        ],
    )

    assert "Created:" in result, result
    out = tmp_path / "o.pdf"
    assert not _has_image_xobject(out)
    assert _drawn_images(out) == []
    assert pil_open_spy == [], f"边界外文件不得被打开: {pil_open_spy}"
    squashed = _squash(_pdf_text(out))
    assert squashed.count(_squash(f'<img src="{outside.as_posix()}"/>')) >= 2, _pdf_text(out)


@pytest.mark.asyncio
async def test_create_pdf_content_list_raw_img_not_embedded(tmp_path, pil_open_spy):
    """P1: content 列表项里的 <img src=边界外> 必须是字面文字——不读文件、不嵌入。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    outside = _png(tmp_path.parent / "list_secret.png")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        filename="o.pdf",
        content=[{"type": "list", "items": [f'<img src="{outside.as_posix()}"/>']}],
    )

    assert "Created:" in result, result
    out = tmp_path / "o.pdf"
    assert not _has_image_xobject(out)
    assert _drawn_images(out) == []
    assert pil_open_spy == [], f"边界外文件不得被打开: {pil_open_spy}"
    text = _pdf_text(out)
    assert _squash(f'<img src="{outside.as_posix()}"/>') in _squash(text), text


@pytest.mark.asyncio
async def test_create_pdf_content_title_raw_img_not_embedded(tmp_path, pil_open_spy):
    """P1: title 也走 Paragraph（_build_pdf 首个渲染点），同样不得让 <img> 生效。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    outside = _png(tmp_path.parent / "title_secret.png")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        filename="o.pdf",
        title=f'<img src="{outside.as_posix()}"/>',
        content=[{"type": "paragraph", "text": "正文"}],
    )

    assert "Created:" in result, result
    out = tmp_path / "o.pdf"
    assert not _has_image_xobject(out)
    assert _drawn_images(out) == []
    assert pil_open_spy == [], f"边界外文件不得被打开: {pil_open_spy}"
    text = _pdf_text(out)
    assert _squash(f'<img src="{outside.as_posix()}"/>') in _squash(text), text


@pytest.mark.asyncio
async def test_create_pdf_content_non_image_outside_not_read(tmp_path):
    """P1: content 段落指向边界外**非图片**文件——若被读取必抛错，PDF 必须仍正常生成。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    outside = tmp_path.parent / "content_not_an_image.png"
    outside.write_text("not an image\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)

    result = await tool.execute(
        filename="o.pdf",
        content=[{"type": "paragraph", "text": f'<img src="{outside.as_posix()}"/>'}],
    )
    assert "Created:" in result, result


@pytest.mark.asyncio
async def test_create_pdf_content_angle_brackets_literal(tmp_path):
    """P1: content 段落/标题/列表里的裸 < 必须原样显示，不得被当标签吞掉或补成实体。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        filename="o.pdf",
        content=[
            {"type": "paragraph", "text": "a < b 与 R&D"},
            {"type": "heading", "text": "List<int>", "level": 1},
            {"type": "list", "items": ["x < y"]},
        ],
    )

    assert "Created:" in result, result
    text = _pdf_text(tmp_path / "o.pdf")
    assert "a < b" in text, text
    assert "List<int>" in text, text
    assert "x < y" in text, text
    assert "R&D" in text and "R&D;" not in text and "&amp;" not in text, text


# ── #993 第 2 条：单文件契约（不自动分卷）+ MD 源稿副本随产物落盘 ──────────────
#
# 第 2 条原文以「内容超引擎限制需要分卷」为前提，但 #993 实测（20KB→200KB 五档）
# 证明工具从不分卷、引擎无长度上限 → 按重新定义实现：
#   ① 工具显式声明单文件契约；超大 content 不静默渲染、不报错，改为提示 content_path；
#   ② content_path 渲染成功后把源稿副本 <PDF 同名>.md 落到 PDF 同目录（同名跳过）。
# 断言均为字体无关（只查返回文本/落盘文件字节，不查 PDF 提取文本）。


def test_tool_description_declares_single_file_no_split():
    """① 工具 description 必须显式声明「不自动分卷、单次调用输出单文件 PDF」。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    desc = CreatePdfTool().description
    assert "不自动分卷" in desc, desc
    assert "单文件" in desc, desc
    assert "content_path" in desc, desc


def test_content_path_description_declares_md_copy():
    """② content_path 参数说明必须写明「源稿副本随 PDF 落盘、同名不覆盖」。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    prop = CreatePdfTool().parameters["properties"]["content_path"]["description"]
    assert "源稿副本" in prop, prop
    assert "不覆盖" in prop, prop


@pytest.mark.asyncio
async def test_content_over_limit_hints_content_path(tmp_path):
    """① 超大 content：不静默渲染、不报错，返回文本提示改用 content_path。"""
    from miqi.documents.pdf_create_tool import _MAX_INLINE_CONTENT_CHARS, CreatePdfTool

    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    body = "A" * (_MAX_INLINE_CONTENT_CHARS + 1)
    result = await tool.execute(filename="o.pdf", content=body)

    assert not result.startswith("Error"), result
    assert "Created:" not in result, result
    assert "content_path" in result, result
    assert "不自动分卷" in result, result
    assert str(len(body)) in result, result
    assert str(_MAX_INLINE_CONTENT_CHARS) in result, result
    assert not (tmp_path / "o.pdf").exists(), "超限 content 不得静默产出 PDF"


@pytest.mark.asyncio
async def test_content_at_limit_still_renders(tmp_path):
    """① 边界：恰好等于阈值仍按老行为渲染（只有**超过**阈值才提示）。"""
    from miqi.documents.pdf_create_tool import _MAX_INLINE_CONTENT_CHARS, CreatePdfTool

    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="o.pdf", content="A" * _MAX_INLINE_CONTENT_CHARS)

    assert "Created:" in result, result
    assert (tmp_path / "o.pdf").is_file()


@pytest.mark.asyncio
async def test_content_path_writes_md_source_copy(tmp_path):
    """② content_path 渲染成功后：源稿副本 <PDF 同名>.md 落到 PDF 同目录，内容为渲染源。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    src_text = "# Title\n\nBody with **bold** and R&D.\n"
    (tmp_path / "src.md").write_text(src_text, encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="report.pdf", content_path="src.md")

    assert "Created:" in result, result
    pdf, copy = tmp_path / "report.pdf", tmp_path / "report.md"
    assert pdf.is_file() and copy.is_file(), sorted(p.name for p in tmp_path.iterdir())
    assert copy.read_text(encoding="utf-8") == src_text
    assert "源稿副本" in result, result


@pytest.mark.asyncio
async def test_content_path_md_copy_lands_next_to_pdf(tmp_path):
    """② 副本落在 PDF 同目录（而非源稿目录）——源稿在子目录、PDF 在另一子目录时也成立。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "r.md").write_text("body\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="out/o.pdf", content_path="src/r.md")

    assert "Created:" in result, result
    assert (tmp_path / "out" / "o.pdf").is_file(), result
    assert (tmp_path / "out" / "o.md").is_file(), result
    assert not (sub / "o.md").exists(), "副本不得落在源稿目录"


@pytest.mark.asyncio
async def test_content_path_md_copy_not_overwritten(tmp_path):
    """② 同名 md 已存在则跳过、不覆盖（用户可能已手工改过该副本）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "src.md").write_text("new source\n", encoding="utf-8")
    (tmp_path / "report.md").write_text("user edited copy\n", encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="report.pdf", content_path="src.md")

    assert "Created:" in result, result
    assert (tmp_path / "report.pdf").is_file(), result
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "user edited copy\n"
    assert "跳过" in result, result


@pytest.mark.asyncio
async def test_content_path_over_limit_source_renders_single_file(tmp_path):
    """① content_path 源稿远超 content 阈值仍单次渲染单文件——不提示、不分卷。"""
    from miqi.documents.pdf_create_tool import _MAX_INLINE_CONTENT_CHARS, CreatePdfTool

    src = "# Title\n\n" + ("word " * (_MAX_INLINE_CONTENT_CHARS // 2 + 100))
    assert len(src) > _MAX_INLINE_CONTENT_CHARS * 2
    (tmp_path / "big.md").write_text(src, encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="o.pdf", content_path="big.md")

    assert "Created:" in result, result
    assert (tmp_path / "o.pdf").is_file()
    assert (tmp_path / "o.md").is_file()
    assert (tmp_path / "o.pdf").stat().st_size > 0


@pytest.mark.asyncio
async def test_small_content_call_backward_compatible(tmp_path):
    """③ 向后兼容：不带新参数的老调用（小 content / 标题）返回文本与落盘行为不变。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(
        filename="o.pdf",
        title="T",
        content=[{"type": "paragraph", "text": "hello"}],
    )

    assert result == f"Created: {tmp_path / 'o.pdf'}", result
    assert (tmp_path / "o.pdf").is_file()
    # 老调用不得凭空多出 md 副本
    assert not (tmp_path / "o.md").exists()


@pytest.mark.asyncio
async def test_content_path_md_copy_is_byte_exact(tmp_path):
    """② 副本按原始字节落盘：CRLF/LF 原样保留（文本模式写盘会把 LF 改成 CRLF）。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    raw = b"# Title\r\n\r\nline with LF\nline with CRLF\r\n"
    (tmp_path / "src.md").write_bytes(raw)
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="report.pdf", content_path="src.md")

    assert "Created:" in result, result
    assert (tmp_path / "report.md").read_bytes() == raw


@pytest.mark.asyncio
async def test_content_path_md_copy_source_equals_target_skipped(tmp_path):
    """② 源稿与副本同名（src.md → src.pdf）：副本路径即源稿本身 → 跳过，不自我覆盖。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    src_text = "# Title\n\nbody\n"
    (tmp_path / "src.md").write_text(src_text, encoding="utf-8")
    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="src.pdf", content_path="src.md")

    assert "Created:" in result, result
    assert (tmp_path / "src.pdf").is_file(), result
    assert (tmp_path / "src.md").read_text(encoding="utf-8") == src_text
    assert "跳过" in result, result


# ── CodeRabbit 安全意见（CWE-59）：源稿副本改独占创建 ─────────────────────────
#
# 旧实现先 `exists()` 再 `write_bytes()`：`exists()` 跟随符号链接，目标位置是**悬空
# 链接**时判 False → 守卫不触发 → 写入跟随链接落到输出边界之外；且查与写之间存在
# TOCTOU，竞争下「不覆盖」契约失效。改用 "xb"（O_CREAT|O_EXCL）后，路径为任何符号
# 链接（含悬空）时内核直接 EEXIST，「不覆盖」不再依赖先查后写。
#
# 悬空链接用例的链接目标**父目录必须存在**——若父目录不存在，链接目标本就无法创建，
# 旧实现也只是写入失败（返回 None），变异回 "wb" 时用例不会变红、失去判别力。


def _dangling_symlink_or_skip(link, target):
    """在 link 处建一个指向不存在 target 的符号链接；平台不允许时 skip。"""
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # Windows 未开开发者模式会失败
        pytest.skip(f"平台不支持创建符号链接（{exc}）")


def test_write_md_source_copy_symlink_detected_without_real_link(tmp_path, monkeypatch):
    """lstat 前置判断必须拦住（悬空）符号链接——用 monkeypatch 伪造，**本机可跑**。

    真建链接的用例在 Windows 非管理员/未开开发者模式的机器上会被 skip，因此「判据
    是否生效」此前只有有权限的 CI runner 才能验。本用例改为让目标路径的
    ``Path.is_symlink()`` 返回 True（不碰文件系统、不需要任何权限）：若把
    ``is_symlink()`` 前置判断去掉、只剩 ``"xb"``，本用例在 Windows 上必然变红
    ——目标不存在时 ``"xb"`` 会成功写穿 reparse point（#994 Windows 回归）。
    """
    from pathlib import Path

    from miqi.documents.pdf_create_tool import _write_md_source_copy

    target = tmp_path / "o.md"
    real_is_symlink = Path.is_symlink

    def _fake_is_symlink(self):
        return True if self == target else real_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", _fake_is_symlink)

    copy_path, note = _write_md_source_copy(tmp_path / "o.pdf", b"payload\n")

    assert copy_path == target, copy_path
    assert note == "已存在，跳过不覆盖", note
    assert not target.exists(), "is_symlink() 为 True 时不得写入任何文件"


def test_write_md_source_copy_creates_byte_exact(tmp_path):
    """正常路径：.md 不存在 → 独占创建落盘，内容与源稿逐字节一致。"""
    from miqi.documents.pdf_create_tool import _write_md_source_copy

    raw = b"# Title\r\n\r\nraw bytes \xe4\xb8\xad\n"
    copy_path, note = _write_md_source_copy(tmp_path / "o.pdf", raw)

    assert copy_path == tmp_path / "o.md", copy_path
    assert note == "已落盘", note
    assert (tmp_path / "o.md").read_bytes() == raw


def test_write_md_source_copy_existing_file_skipped(tmp_path):
    """目标已是普通文件 → EEXIST → 跳过不覆盖，原文件内容与状态说明不变。"""
    from miqi.documents.pdf_create_tool import _write_md_source_copy

    (tmp_path / "o.md").write_bytes(b"user edited copy\n")
    copy_path, note = _write_md_source_copy(tmp_path / "o.pdf", b"new source\n")

    assert copy_path == tmp_path / "o.md", copy_path
    assert note == "已存在，跳过不覆盖", note
    assert (tmp_path / "o.md").read_bytes() == b"user edited copy\n"


def test_write_md_source_copy_dangling_symlink_not_followed(tmp_path):
    """悬空符号链接：EEXIST → 跳过；写操作不得跟随链接创建链接目标（CWE-59）。"""
    from miqi.documents.pdf_create_tool import _write_md_source_copy

    outside_dir = tmp_path / "outside"  # 父目录存在 → 旧实现真的能写穿链接
    outside_dir.mkdir()
    target = outside_dir / "escaped.md"
    link = tmp_path / "o.md"
    _dangling_symlink_or_skip(link, target)
    assert not target.exists(), "前置条件：链接必须是悬空的"

    copy_path, note = _write_md_source_copy(tmp_path / "o.pdf", b"payload\n")

    assert copy_path == link, copy_path
    assert note == "已存在，跳过不覆盖", note
    assert not target.exists(), "写操作跟随了悬空链接，越界创建了链接目标"
    assert list(outside_dir.iterdir()) == [], "输出边界之外不得出现任何新文件"
    assert link.is_symlink(), "符号链接本身不得被替换/删除"


def test_write_md_source_copy_symlink_to_existing_file_not_overwritten(tmp_path):
    """指向已存在文件的符号链接：链接目标内容原样、链接本身保留。"""
    from miqi.documents.pdf_create_tool import _write_md_source_copy

    real = tmp_path / "user_notes.md"
    real.write_bytes(b"irreplaceable\n")
    link = tmp_path / "o.md"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"平台不支持创建符号链接（{exc}）")

    copy_path, note = _write_md_source_copy(tmp_path / "o.pdf", b"payload\n")

    assert copy_path == link, copy_path
    assert note == "已存在，跳过不覆盖", note
    assert real.read_bytes() == b"irreplaceable\n"
    assert link.is_symlink()


@pytest.mark.asyncio
async def test_content_path_md_copy_dangling_symlink_not_written_through(tmp_path):
    """端到端：副本位置是悬空链接时 PDF 正常产出，链接目标不得被创建。"""
    from miqi.documents.pdf_create_tool import CreatePdfTool

    (tmp_path / "src.md").write_text("# T\n\nbody\n", encoding="utf-8")
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    target = outside_dir / "escaped.md"
    link = tmp_path / "report.md"
    _dangling_symlink_or_skip(link, target)

    tool = CreatePdfTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = await tool.execute(filename="report.pdf", content_path="src.md")

    assert "Created:" in result, result
    assert (tmp_path / "report.pdf").is_file(), result
    assert not target.exists(), "源稿副本写穿悬空链接，落到了输出目录之外"
    assert list(outside_dir.iterdir()) == [], result
    assert "跳过" in result, result
