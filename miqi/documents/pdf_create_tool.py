"""PDF creation tool for the AI agent — creates PDF documents with Chinese font support.

Replaces the skill-based ad-hoc approach with a proper tool that the agent can call
directly to generate PDFs with consistent formatting and font handling.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from loguru import logger

from miqi.agent.tools.base import Tool
from miqi.agent.tools.filesystem import _persist_tracked_file
from miqi.documents.path_utils import (
    enforce_boundary,
    ensure_suffix,
    raw_output_path,
    resolve_output_path,
)

# ── Chinese font discovery ──────────────────────────────────────────────

_CHINESE_FONT_CANDIDATES: list[tuple[str, str]] = [
    # Common Linux / WSL fonts (TrueType)
    ("SimSun", "/usr/share/fonts/truetype/SimSun.ttf"),
    ("SimHei", "/usr/share/fonts/truetype/SimHei.ttf"),
    # wqy
    ("WenQuanYiZenHei", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ("WenQuanYiMicroHei", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    # Noto Sans CJK / Noto Sans SC (TrueType)
    ("NotoSansSC", "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf"),
    ("NotoSansSC", "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
    ("NotoSansSC", "/usr/share/fonts/truetype/NotoSansSC-Regular.ttf"),
    ("NotoSansSC", "/usr/share/fonts/NotoSansSC-Regular.ttf"),
    # SourceHanSansSC (download location)
    ("SourceHanSansSC", "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Regular.otf"),
    ("SourceHanSansSC", "/home/miqi/.fonts/SourceHanSansSC-Regular.otf"),
    ("SourceHanSansSC", "/home/miqi/.fonts/NotoSansCJKsc-Regular.otf"),
    ("SourceHanSansSC", "/home/miqi/.fonts/test.otf"),
    # Droid Sans Fallback (often available)
    ("DroidSansFallback", "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
    # Windows fonts via WSL
    ("SimSun", "/mnt/c/Windows/Fonts/simsun.ttc"),
    ("SimHei", "/mnt/c/Windows/Fonts/simhei.ttf"),
    ("MicrosoftYaHei", "/mnt/c/Windows/Fonts/msyh.ttc"),
    ("MicrosoftYaHei", "/mnt/c/Windows/Fonts/msyhbd.ttc"),
    # Windows native paths — TTF preferred over TTC (reportlab TTFont
    # handles .ttf directly but needs fontNumber= for .ttc collections)
    ("SimHei", "C:/Windows/Fonts/simhei.ttf"),
    ("SimSun", "C:/Windows/Fonts/simsun.ttc"),
    ("MicrosoftYaHei", "C:/Windows/Fonts/msyh.ttc"),
    ("MicrosoftYaHei", "C:/Windows/Fonts/msyhbd.ttc"),
    # User-installed fonts
    ("SimSun", str(Path.home() / "AppData/Local/Microsoft/Windows/Fonts/simsun.ttc")),
    ("SimHei", str(Path.home() / "AppData/Local/Microsoft/Windows/Fonts/simhei.ttf")),
]


def _discover_chinese_font() -> tuple[str, str | None]:
    """Auto-discover an available Chinese font on the system.

    Returns (font_name, font_path_or_None).  font_path is None when no
    Chinese font is found — callers should fall back to a built-in font.
    """
    for name, path in _CHINESE_FONT_CANDIDATES:
        if os.path.exists(path):
            logger.info(f"PDF: found Chinese font '{name}' at {path}")
            return name, path

    # Try fc-list as a last resort
    try:
        result = subprocess.run(
            ["fc-list", ":lang=zh", "-f", "%{file}\n"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line and os.path.exists(line):
                    logger.info(f"PDF: found Chinese font via fc-list: {line}")
                    return os.path.basename(line), line
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    logger.warning("PDF: no Chinese font found; CJK text may not render correctly")
    return "Helvetica", None


_FONT_CACHE: dict[str, tuple[str, str | None]] = {}


def _get_chinese_font() -> tuple[str, str | None]:
    """Cached version of font discovery."""
    key = "chinese"
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = _discover_chinese_font()
    return _FONT_CACHE[key]


# ── Style presets ───────────────────────────────────────────────────────

_CHINESE_PDF_STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "chinese_document": {
        "title_font_name": "SimHei",
        "title_font_size_pt": 16,
        "title_bold": True,
        "title_alignment": "center",
        "body_font_name": "SimHei",
        "body_font_size_pt": 12,
        "body_line_spacing": 1.5,
        "body_alignment": "justify",
    },
    "chinese_essay": {
        "title_font_name": "SimHei",
        "title_font_size_pt": 16,
        "title_bold": True,
        "title_alignment": "center",
        "body_font_name": "SimHei",
        "body_font_size_pt": 12,
        "body_line_spacing": 1.5,
        "body_alignment": "justify",
    },
    "report": {
        "title_font_name": "Helvetica",
        "title_font_size_pt": 18,
        "title_bold": True,
        "title_alignment": "left",
        "body_font_name": "Helvetica",
        "body_font_size_pt": 11,
        "body_line_spacing": 1.15,
        "body_alignment": "left",
    },
}

# Chinese size names to points
_CHINESE_SIZE_TO_PT = {
    "初号": 42, "小初": 36,
    "一号": 26, "小一": 24,
    "二号": 22, "小二": 18,
    "三号": 16, "小三": 15,
    "四号": 14, "小四": 12,
    "五号": 10.5, "小五": 9,
}


# ── Path helpers ────────────────────────────────────────────────────────
#
# raw_output_path / ensure_suffix / resolve_output_path / enforce_boundary
# live in miqi.documents.path_utils (shared by docx/pptx/xlsx/pdf tools).
# resolve_output_path semantics:
#   - Relative paths resolve against the session files root (workspace).
#   - Paths starting with `sessions/<当前会话>/files/...` are normalized
#     (issue #806) — they were written relative to the workspace base.
#   - Paths pointing at another session's directory are rejected.


# ── Style helpers (mirror docx_tool patterns) ──────────────────────────

def _size_to_pt(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    if text in _CHINESE_SIZE_TO_PT:
        return float(_CHINESE_SIZE_TO_PT[text])
    try:
        return float(text.replace("pt", "").strip())
    except ValueError:
        return None


def _alignment_from_value(value: Any) -> str:
    if value is None:
        return "left"
    normalized = str(value).strip().lower()
    mapping = {
        "center": "CENTER", "centered": "CENTER", "centre": "CENTER", "居中": "CENTER",
        "left": "LEFT", "左对齐": "LEFT",
        "right": "RIGHT", "右对齐": "RIGHT",
        "justify": "JUSTIFY", "justified": "JUSTIFY", "两端对齐": "JUSTIFY",
    }
    return mapping.get(normalized, "LEFT")


def _merge_style(*styles: dict[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for style in styles:
        if isinstance(style, dict):
            merged.update({k: v for k, v in style.items() if v is not None})
    return merged


def _size_in_clause(clause: str) -> float | None:
    for chinese_size in sorted(_CHINESE_SIZE_TO_PT, key=len, reverse=True):
        if chinese_size in clause:
            return float(_CHINESE_SIZE_TO_PT[chinese_size])
    return None


def _style_from_kwargs(kwargs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    preset_name = str(kwargs.get("style_preset") or "").strip()
    preset = _CHINESE_PDF_STYLE_PRESETS.get(preset_name, {})

    title_style = _merge_style(
        {
            "font_name": preset.get("title_font_name"),
            "font_size_pt": preset.get("title_font_size_pt"),
            "bold": preset.get("title_bold"),
            "alignment": preset.get("title_alignment"),
        },
        {
            "font_name": kwargs.get("title_font_name") or kwargs.get("title_font"),
            "font_size_pt": kwargs.get("title_font_size_pt") or kwargs.get("title_size_pt"),
            "bold": kwargs.get("title_bold"),
            "alignment": kwargs.get("title_alignment"),
        },
    )
    body_style = _merge_style(
        {
            "font_name": preset.get("body_font_name"),
            "font_size_pt": preset.get("body_font_size_pt"),
            "line_spacing": preset.get("body_line_spacing"),
            "alignment": preset.get("body_alignment"),
        },
        {
            "font_name": kwargs.get("body_font_name") or kwargs.get("body_font"),
            "font_size_pt": kwargs.get("body_font_size_pt") or kwargs.get("body_size_pt"),
            "line_spacing": kwargs.get("line_spacing"),
            "alignment": kwargs.get("body_alignment"),
        },
    )

    # Process natural language formatting instructions
    instructions = str(kwargs.get("format_instructions") or "")
    if instructions:
        if "黑体" in instructions:
            title_style["font_name"] = "SimHei"
        if "宋体" in instructions:
            body_style["font_name"] = "SimSun"
        if "居中" in instructions:
            title_style["alignment"] = "center"
        if "加粗" in instructions:
            title_style["bold"] = True
        if "1.5" in instructions or "1.5倍" in instructions:
            body_style["line_spacing"] = 1.5
        title_size = _size_in_clause(instructions)
        if title_size is not None:
            title_style["font_size_pt"] = title_size

    # Defaults — leave font_name as None so _build_pdf can apply
    # the discovered CJK font (instead of hardcoding Helvetica).
    if "font_size_pt" not in title_style:
        title_style["font_size_pt"] = 16
    if "font_size_pt" not in body_style:
        body_style["font_size_pt"] = 12

    return title_style, body_style


# ── PDF building ────────────────────────────────────────────────────────

# Alias constants (used to document parameter choices)
PAGE_SIZE_A4 = "A4"
PAGE_SIZE_LETTER = "letter"
PAGE_SIZE_A3 = "A3"

_PAGE_SIZE_MAP = {
    "a4": (595.27, 841.89),
    "letter": (612, 792),
    "a3": (841.89, 1190.55),
}


def _get_page_size(name: str) -> tuple[float, float]:
    key = str(name).strip().lower()
    return _PAGE_SIZE_MAP.get(key, _PAGE_SIZE_MAP["a4"])


def _register_fonts() -> dict[str, str]:
    """Register discovered Chinese fonts with reportlab.

    Returns a mapping {logical_name: font_name} for use in Paragraph styles.
    Handles both .ttf and .ttc (TrueType Collection) font files.  If
    registration fails (e.g. fontNumber kwarg missing in older reportlab),
    falls back to using a built-in PDF font so CJK text is not catastrophic.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    registered = {}
    cn_name, cn_path = _get_chinese_font()
    if cn_path:
        # TTC files may need fontNumber=0 to reference the first face,
        # but only newer reportlab supports it.  Try with then without.
        is_ttc = cn_path.lower().endswith(".ttc")
        candidates = [cn_name, "CJKFont", "CJK"]
        last_exc = None
        for fnt_name in candidates:
            # TTC files: try with fontNumber=0 first, then without
            # TTF files: single attempt with no kwargs
            kwarg_sets = ({"fontNumber": 0}, {}) if is_ttc else ({},)
            for kwargs in kwarg_sets:
                try:
                    pdfmetrics.registerFont(TTFont(fnt_name, cn_path, **kwargs))
                    registered["default_cjk"] = fnt_name
                    logger.info(f"PDF: registered font '{fnt_name}' from {cn_path}")
                    return registered
                except TypeError as exc:
                    # kwarg not supported by this reportlab — try next variant
                    last_exc = exc
                    continue
                except Exception as exc:
                    logger.warning(f"PDF: failed to register font '{fnt_name}': {exc}")
                    last_exc = exc
                    break
        logger.warning(f"PDF: all font registration failed ({last_exc}); falling back to Helvetica")
    return registered


def _build_pdf(
    output_path: Path,
    title: str | None,
    content: Any,
    *,
    author: str | None = None,
    page_size_name: str = "A4",
    title_style: dict[str, Any] | None = None,
    body_style: dict[str, Any] | None = None,
    trusted_images: bool = False,
    content_escaped: bool = False,
) -> None:
    """Build a PDF document using reportlab.

    已知架构限制（本次不改）：``SimpleDocTemplate`` 直接写入目标文件，若 ``build``
    中途抛异常，已写入的半成品文件不会被清理（由调用方 ``execute`` 返回 Error）。
    本次只保证「解析阶段降级不产生半成品」——图片/文本在解析阶段降级为占位文字，
    不会走到异常分支。

    ``trusted_images`` 是**不可由模型伪造**的图片可信通道：只有 ``content_path``
    的内部转换路径（``_md_to_blocks`` 已逐张做过边界复校）才由 ``execute`` 传
    ``True``；模型侧 ``content`` 手写的 image 块恒为 ``False`` → 一律降级为占位
    文字 + ``logger.warning``，**不打开任何文件**。此前用块内 JSON 字段
    ``validated`` 做判据，而该字段模型可自设 → 可绕过边界读任意文件（H1 破口）。

    ``content_escaped`` 是**不可由模型伪造**的转义通道（P1 修复）：文本进入
    ``Paragraph()`` 前一律 ``_md_escape``，否则源稿/``content`` 里的 raw
    ``<img src=...>`` 会被 reportlab paraparser 当图片标签直接打开文件，绕过
    ``_resolve_md_image`` 的边界校验。只有 ``content_path`` 分支传 ``True``
    （``_md_to_blocks`` 已用 ``_md_inline``/``_md_escape`` 转过，再转一次会显示
    成字面 ``&amp;``）；用函数参数而非块内 JSON 字段，理由同 H1。
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    title_style = title_style or {}
    body_style = body_style or {}

    # Register Chinese fonts
    font_map = _register_fonts()
    cjk_font = font_map.get("default_cjk", "Helvetica")
    _has_cjk = cjk_font != "Helvetica"

    # Resolve font names: if a CJK font was registered, use it for any
    # Chinese-oriented font name (SimSun, SimHei, MsYaHei, 宋体, 黑体, etc.)
    # so the style presets work regardless of which font was actually discovered.
    # If NO CJK font could be registered, force EVERYTHING to Helvetica —
    # passing an unregistered Chinese name to reportlab crashes with "Can't map".
    _cn_names = ("sim", "song", "hei", "kai", "fang", "yahei", "ming", "cjk", "chinese", "noto", "wenquan")
    def _resolve_font(name: str | None) -> str:
        if not name or name == "Helvetica":
            return cjk_font if _has_cjk else "Helvetica"
        is_cn = any(cn in name.lower() for cn in _cn_names)
        if is_cn:
            return cjk_font if _has_cjk else "Helvetica"
        return name

    # Page size
    page_size = _get_page_size(page_size_name)

    # Build paragraph styles
    t_align_map = {
        "CENTER": TA_CENTER,
        "LEFT": TA_LEFT,
        "RIGHT": TA_RIGHT,
        "JUSTIFY": TA_JUSTIFY,
    }
    title_font = _resolve_font(title_style.get("font_name"))
    title_size = _size_to_pt(title_style.get("font_size_pt", 16)) or 16
    title_align = t_align_map.get(str(title_style.get("alignment", "CENTER")).upper(), TA_CENTER)

    body_font = _resolve_font(body_style.get("font_name"))
    body_size = _size_to_pt(body_style.get("font_size_pt", 12)) or 12
    body_align = t_align_map.get(str(body_style.get("alignment", "LEFT")).upper(), TA_LEFT)
    body_line_spacing = float(body_style.get("line_spacing", 1.5))

    # Create styles
    ptitle_style = ParagraphStyle(
        "DocTitle",
        fontName=title_font,
        fontSize=title_size,
        alignment=title_align,
        leading=title_size * 1.4,
        spaceAfter=20,
    )
    pbody_style = ParagraphStyle(
        "DocBody",
        fontName=body_font,
        fontSize=body_size,
        alignment=body_align,
        leading=body_size * body_line_spacing,
        spaceAfter=6,
        firstLineIndent=body_size * 2 if body_align == TA_JUSTIFY else 0,
    )
    caption_size = max(body_size - 2, 8)
    pcaption_style = ParagraphStyle(
        "DocCaption",
        fontName=body_font,
        fontSize=caption_size,
        alignment=TA_CENTER,
        leading=caption_size * 1.3,
        textColor=colors.grey,
        spaceAfter=8,
    )

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=page_size,
        topMargin=2.54 * cm,
        bottomMargin=2.54 * cm,
        leftMargin=3.17 * cm,
        rightMargin=3.17 * cm,
        author=author or "",
        title=title or "",
    )

    # 图片可用尺寸：frame 内宽/内高（去掉 SimpleDocTemplate 默认 6pt padding）。
    # 不写死 A4，兼容 letter/A3；高度上限再取 660pt 余量（见 _build_image_flowable）。
    frame_width = page_size[0] - doc.leftMargin - doc.rightMargin - 2 * _FRAME_PADDING_PT
    frame_height = page_size[1] - doc.topMargin - doc.bottomMargin - 2 * _FRAME_PADDING_PT
    image_work_dir = Path(tempfile.mkdtemp(prefix="miqi_pdf_img_"))

    def _block_text(raw: Any) -> str:
        """块文本进 ``Paragraph()`` 前统一转义（P1：源稿/content 均属不可信输入）。

        ``content_escaped=True`` 表示文本已由 ``_md_to_blocks`` 转过，跳过以免
        显示成字面 ``&amp;``；表格单元格不走本函数（``Table`` 用 drawString，
        不解析 XML，转义反而会显示成 ``R&amp;D``）。
        """
        text = str(raw)
        return text if content_escaped else _md_escape(text)

    story: list[Any] = []

    # Add title
    if title:
        # title 恒为模型输入（content_path 分支也不例外），始终转义。
        story.append(Paragraph(_md_escape(str(title)), ptitle_style))
        story.append(Spacer(1, 12))

    # Add content blocks
    blocks = content if isinstance(content, list) else [{"type": "paragraph", "text": str(content)}]
    try:
        for block in blocks:
            if not isinstance(block, dict):
                story.append(Paragraph(_block_text(block), pbody_style))
                continue

            block_type = str(block.get("type", "paragraph")).lower()

            if block_type == "heading":
                level = int(block.get("level", 1))
                text = _block_text(block.get("text", ""))
                if level <= 2:
                    h_style = ParagraphStyle(
                        f"Heading{level}",
                        fontName=title_font if title_font != "Helvetica" else body_font,
                        fontSize=body_size + (4 if level == 1 else 2),
                        alignment=TA_LEFT,
                        leading=(body_size + (4 if level == 1 else 2)) * 1.4,
                        spaceBefore=16,
                        spaceAfter=8,
                    )
                    story.append(Paragraph(text, h_style))
                else:
                    # <b> 由工具生成，只能包在已转义的文本外（顺序不能反）。
                    story.append(Paragraph(f"<b>{text}</b>", pbody_style))

            elif block_type == "paragraph":
                text = _block_text(block.get("text", ""))
                if text.strip():
                    story.append(Paragraph(text, pbody_style))

            elif block_type == "image":
                # 仅接受 content_path 解析阶段校验过的图片块；content 里手写的
                # 图片块一律降级，避免绕过边界检查读任意文件。可信标记来自函数
                # 参数 trusted_images —— 模型无法通过 JSON 字段伪造（H1 修复：
                # 原 `block.get("validated") is True` 判据模型可自设）。
                flow = None
                if trusted_images:
                    flow = _build_image_flowable(
                        str(block.get("path") or ""),
                        frame_width,
                        frame_height,
                        image_work_dir,
                    )
                else:
                    logger.warning("PDF: 忽略未经校验的图片块（仅 content_path 行首图片可嵌入）")
                if flow is None:
                    alt = str(block.get("alt") or "图表")
                    story.append(Paragraph(_md_escape(f"[图表：{alt}（见源稿）]"), pbody_style))
                else:
                    story.append(flow)
                    caption = _block_text(block.get("caption") or "")
                    if caption.strip():
                        story.append(Paragraph(caption, pcaption_style))

            elif block_type == "table":
                headers = block.get("headers", [])
                rows = block.get("rows", [])
                table_data = []
                if headers:
                    table_data.append([str(h) if h else "" for h in headers])
                for row in rows:
                    table_data.append([str(c) if c is not None else "" for c in row])
                if table_data:
                    # Calculate column widths
                    avail_width = page_size[0] - 3.17 * 2 * cm
                    col_width = avail_width / max(len(table_data[0]), 1)
                    col_widths = [col_width] * len(table_data[0])

                    tbl = Table(table_data, colWidths=col_widths)
                    tbl_style = TableStyle([
                        ("FONTNAME", (0, 0), (-1, -1), body_font),
                        ("FONTSIZE", (0, 0), (-1, -1), body_size - 1),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F0F0")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ])
                    tbl.setStyle(tbl_style)
                    story.append(tbl)
                    story.append(Spacer(1, 8))

            elif block_type == "list":
                items = block.get("items", [])
                for item in items:
                    story.append(Paragraph(f"• {_block_text(item)}", pbody_style))

            elif block_type == "spacer":
                height = float(block.get("height", 12))
                story.append(Spacer(1, height))

            elif block_type == "page_break":
                story.append(PageBreak())

        # Build
        doc.build(story)
    finally:
        # 降采样后的图片落在临时目录，reportlab 直到 build 阶段才真正读取，
        # 因此只能在 build 结束后清理（解析阶段降级不产生半成品 PDF）。
        shutil.rmtree(image_work_dir, ignore_errors=True)


# ── Markdown source rendering (content_path) ────────────────────────────────

_MD_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_LISTITEM_RE = re.compile(r"^([-\*]|\d+\.)\s+(.*)$")
_MD_TBL_SEP_RE = re.compile(r"^:?-{3,}:?$")
_MD_IMG_RE = re.compile(r"^!\[([^\]]*)\]")
# 完整图片语法 ![alt](dest)；dest 贪婪匹配，兼容路径内的括号（fig(1).png）
_MD_IMG_FULL_RE = re.compile(r"^!\[([^\]]*)\]\((.*)\)$")
# 行内链接：仅 http/https；URL 排除空白与 *<>()，避免与 ** 粗体标记互相吞并
_MD_LINK_RE = re.compile(r"\[([^\[\]]*)\]\((https?://[^\s*<>()]*)\)")
# 行内粗体 **...**（首尾均非空白）
_MD_BOLD_RE = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", re.S)

# 图片资源上限（防解压炸弹）与渲染上限（#994：高度必须小于 frame 高度，否则 LayoutError）
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_IMAGE_PIXELS = 40_000_000
_MAX_IMAGE_HEIGHT_PT = 660.0
_IMAGE_TARGET_DPI = 200
_FRAME_PADDING_PT = 6.0  # SimpleDocTemplate 默认 frame padding
_SUPPORTED_IMAGE_FORMATS = ("PNG", "JPEG")


def _md_escape(text: str) -> str:
    """转义 XML 保留字符（& 必须最先处理，否则会把后面生成的实体再转义一次）。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_inline(text: str) -> str:
    """把受支持的行内 Markdown 转成 reportlab 段落标记。

    顺序：先转义 ``& < >``，再插入工具自身生成的 ``<b>`` / ``<link>``——顺序
    反了会把自己的标签也转义掉。作用域只有段落/标题/列表（经 Paragraph 渲染）；
    表格单元格（Table 走 drawString、不解析 XML）与代码围栏内容**不**经过本函数，
    否则表格里的 ``R&D`` 会变成 ``R&amp;D``。

    链接仅接受 http/https；URL 属性里的 ``"`` 必须转成 ``&quot;``，否则
    paraparser 抛 ``invalid attribute name``，异常被 ``execute`` 的 except 吞掉
    后产出零 PDF。
    """
    out = _md_escape(text)
    out = _MD_LINK_RE.sub(
        lambda m: '<link href="{}">{}</link>'.format(
            m.group(2).replace('"', "&quot;"), m.group(1)
        ),
        out,
    )
    return _MD_BOLD_RE.sub(r"<b>\1</b>", out)


def _md_image_dest(line: str) -> tuple[str, str | None]:
    """解析行首图片语法，返回 ``(alt, dest)``；dest 为 None 表示语法不完整。"""
    m = _MD_IMG_FULL_RE.match(line)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = _MD_IMG_RE.match(line)
    return (m.group(1).strip() if m else "图表"), None


def _source_root_of(
    source_path: Path,
    workspace: Path | None,
    allowed_dir: Path | None,
    user_roots: Any,
) -> Path | None:
    """返回源稿自身通过校验的那个边界根（顺序与 _resolve_source_path 一致）。

    图片只能落在源稿自身通过校验的那个根之内，因此需要把源稿的根单独记下来，
    不接受跨根读取。
    """
    roots: list[Path] = []
    effective = allowed_dir if allowed_dir is not None else workspace
    if effective is not None:
        roots.append(Path(effective))
    if user_roots:
        for r in user_roots:
            try:
                roots.append(Path(str(r)))
            except TypeError:  # pragma: no cover - 防御
                continue
    src = source_path.resolve()
    for root in roots:
        try:
            src.relative_to(root.resolve())
            return root.resolve()
        except (OSError, ValueError):
            continue
    return None


def _resolve_md_image(
    dest: str | None,
    source_path: Path | None,
    source_root: Path | None,
    workspace: Path | None,
    allowed_dir: Path | None,
    user_roots: Any,
    allow_user_roots: bool,
) -> Path | None:
    """解析行首图片路径；任何越界/不可用情况返回 None（调用方降级为占位文字）。

    相对路径先 join 源稿父目录，再进 ``_resolve_source_path`` 同一入口复校白名单
    （``../`` 越界同样被拦截），最后要求落在源稿自身通过校验的那个根之内。
    ``%20`` 这类 URL 编码先按字面解析，字面不存在再按 percent-decode 重试。
    """
    raw = (dest or "").strip()
    if not raw:
        logger.warning("PDF: 图片路径为空，降级为占位文字")
        return None
    if source_path is None or source_root is None:
        logger.warning(f"PDF: 缺少源稿上下文，图片 {raw} 降级为占位文字")
        return None
    if raw.lower().endswith(".svg"):
        logger.warning(f"PDF: 暂不支持 SVG 图片 {raw}（需 svglib/cairosvg），降级为占位文字")
        return None

    candidates = [raw]
    if "%" in raw:
        from urllib.parse import unquote

        decoded = unquote(raw)
        if decoded and decoded != raw:
            candidates.append(decoded)

    for cand_raw in candidates:
        cand = Path(cand_raw)
        if not cand.is_absolute():
            cand = source_path.parent / cand
        try:
            resolved = _resolve_source_path(
                str(cand), workspace, allowed_dir, user_roots, allow_user_roots
            )
        except (OSError, PermissionError, ValueError) as exc:
            logger.warning(f"PDF: 图片 {cand_raw} 越界被拒绝（{exc}），降级为占位文字")
            continue
        try:
            resolved.relative_to(source_root)
        except ValueError:
            logger.warning(
                f"PDF: 图片 {cand_raw} 不在源稿授权根 {source_root} 内，降级为占位文字"
            )
            continue
        if not resolved.is_file():
            logger.warning(f"PDF: 图片 {cand_raw} 不存在或不是文件，降级为占位文字")
            continue
        return resolved
    return None


def _downsample_image(im: Any, draw_width_pt: float, work_dir: Path) -> Path | None:
    """按 ~200 DPI 目标宽度降采样并落盘到临时目录，返回临时文件路径。

    原图（2700px 宽）直接嵌入会让 PDF 体积膨胀 5 倍以上；这里按实际绘制宽度
    折算目标像素，PNG 保持 alpha（透明图），JPEG 转 RGB 存 JPEG。
    """
    from PIL import Image as PilImage

    try:
        target_px = max(1, int(round(draw_width_pt / 72.0 * _IMAGE_TARGET_DPI)))
        iw, ih = im.size
        if iw > target_px:
            new_h = max(1, int(round(ih * target_px / iw)))
            resized = im.resize((target_px, new_h), PilImage.LANCZOS)
        else:
            resized = im
        work_dir.mkdir(parents=True, exist_ok=True)
        if (im.format or "").upper() == "JPEG":
            fd, name = tempfile.mkstemp(prefix="img", suffix=".jpg", dir=str(work_dir))
            os.close(fd)
            out = Path(name)
            rgb = resized.convert("RGB") if resized.mode != "RGB" else resized
            rgb.save(out, format="JPEG", quality=88)
        else:
            fd, name = tempfile.mkstemp(prefix="img", suffix=".png", dir=str(work_dir))
            os.close(fd)
            out = Path(name)
            png = resized
            if png.mode not in ("RGBA", "RGB", "L", "LA", "P"):
                png = png.convert("RGBA")
            png.save(out, format="PNG", optimize=True)
        return out
    except Exception as exc:  # noqa: BLE001 — 降级不抛异常（含 PIL DecompressionBombError）
        logger.warning(f"PDF: 图片降采样失败（{exc}），降级为占位文字")
        return None


def _build_image_flowable(
    path: str,
    max_width: float,
    max_height: float,
    work_dir: Path,
) -> Any | None:
    """构造按可用宽度等比缩放、且不高于 frame 的 Image flowable。

    必须用构造参数 ``Image(path, width=W, height=H)``（而不是改 drawWidth），
    并在构造后复核「实际绘制尺寸 == 期望尺寸」——reportlab 在尺寸被静默重置时
    会产出坏 PDF 且不报错。任何异常/超限一律返回 None，由调用方降级为占位文字。

    高度用 ``min(_MAX_IMAGE_HEIGHT_PT, max_height)`` **钳制（clamp ≤ 660pt）**，
    不是「缩放后仍超 frame 高度 → 降级」：钳制后 ``height <= limit <= max_height``
    恒成立，降级分支永远走不到（第二轮评审 M6 认定的死代码，已删除）。保留钳制
    即可达成「不触发 LayoutError」的目标。此处与 plan §3.2 的措辞差异如实记录。
    """
    from reportlab.platypus import Image as ReportLabImage

    if not path:
        return None
    src = Path(path)
    try:
        if not src.is_file():
            logger.warning(f"PDF: 图片 {src} 不存在，降级为占位文字")
            return None
        if src.stat().st_size > _MAX_IMAGE_BYTES:
            logger.warning(f"PDF: 图片 {src} 超过 {_MAX_IMAGE_BYTES} 字节上限，降级为占位文字")
            return None
    except OSError as exc:
        logger.warning(f"PDF: 图片 {src} 不可访问（{exc}），降级为占位文字")
        return None

    try:
        from PIL import Image as PilImage
    except ImportError:  # pragma: no cover - reportlab 硬依赖 pillow
        logger.warning("PDF: 未安装 pillow，图片降级为占位文字")
        return None

    try:
        with PilImage.open(src) as im:
            fmt = (im.format or "").upper()
            if fmt not in _SUPPORTED_IMAGE_FORMATS:
                logger.warning(
                    f"PDF: 图片 {src} 格式 {fmt or '未知'} 不受支持（仅 PNG/JPEG），降级为占位文字"
                )
                return None
            iw, ih = im.size
            if iw <= 0 or ih <= 0 or iw * ih > _MAX_IMAGE_PIXELS:
                logger.warning(f"PDF: 图片 {src} 尺寸 {iw}x{ih} 超出上限，降级为占位文字")
                return None
            width = max_width
            height = width * ih / iw
            # 高度钳制（clamp）：钳制后 height <= limit <= max_height，必然放得下，
            # 不会触发 LayoutError。原「缩放后仍超 frame 高度 → 降级」分支恒假，已删。
            limit = min(_MAX_IMAGE_HEIGHT_PT, max_height)
            if height > limit:
                width *= limit / height
                height = limit
            target = _downsample_image(im, width, work_dir)
    except Exception as exc:  # noqa: BLE001 — 降级不抛异常（含 PIL DecompressionBombError）
        logger.warning(f"PDF: 图片 {src} 读取失败（{exc}），降级为占位文字")
        return None
    if target is None:
        return None

    try:
        flow = ReportLabImage(str(target), width=width, height=height)
        drawn_w, drawn_h = float(flow.drawWidth), float(flow.drawHeight)
    except Exception as exc:
        logger.warning(f"PDF: 图片 {src} 构造失败（{exc}），降级为占位文字")
        return None
    if abs(drawn_w - width) > 0.01 or abs(drawn_h - height) > 0.01:
        logger.warning(
            f"PDF: 图片 {src} 实际绘制尺寸 {drawn_w:.2f}x{drawn_h:.2f} != "
            f"期望 {width:.2f}x{height:.2f}，降级为占位文字"
        )
        return None
    return flow


def _md_to_blocks(
    text: str,
    *,
    source_path: Path | None = None,
    workspace: Path | None = None,
    allowed_dir: Path | None = None,
    user_roots: Any = None,
    allow_user_roots: bool = False,
) -> list[dict[str, Any]]:
    """按受支持的 Markdown 子集把源稿文本转成内容块（专供 content_path 直渲）。

    支持：# / ## / ### 标题、段落、连续 -/*/数字 列表、连续 | 表格行、
    > 引用（按段落处理）、代码围栏（内容按独立段落处理，不与相邻叙述合并）、
    行首图片 ``![alt](path)``（嵌入 PNG/JPEG）。

    行内 Markdown（仅 content_path 路径）：``**粗体**`` 与 ``[文字](http(s)://…)``
    链接，作用域仅段落/标题/列表；表格单元格**不**做转义或行内转换（表格走
    Table/drawString 不解析 XML，转义会显示成字面量 ``R&amp;D``）；代码围栏
    只转义、不做行内转换（``<img ...>`` 保留为字面文字，见下「已知限制 1」）。

    图片：相对路径先 join 源稿父目录，再进 ``_resolve_source_path`` 复校白名单，
    并要求落在源稿自身通过校验的那个根之内（不接受跨根读取）；越界/损坏/超限/
    SVG 一律降级为占位文字 + ``logger.warning``，不抛异常。alt 文本渲染为图注，
    与降级占位保持同等信息量。

    返回的 image 块**不带任何「已校验」标记字段**——模型可自设 JSON 键，信任只能由
    调用方（``execute`` 的 ``content_path`` 分支）通过 ``_build_pdf(trusted_images=True)``
    显式表达；``content`` 路径的块恒不可信（H1 修复）。

    本函数产出的块文本**已全部 XML 转义**（段落/标题/列表/图注走 ``_md_inline``，
    代码围栏走 ``_md_escape``），调用方必须传 ``_build_pdf(content_escaped=True)``
    才不会二次转义；表格单元格除外（不走 Paragraph）。

    这是**受支持的 Markdown 子集**，不是完整 Markdown renderer——只恢复结构骨架。
    已知限制：
    1. 代码块按普通段落渲染（多行以空格拼接，不做等宽排版），且内容只转义、不做
       行内转换——围栏内的 ``&``/``<``/``>`` 以字面文字输出，raw ``<img src=...>``
       只是文字、**不会**触发读文件（P1 修复；此前未转义会被 reportlab 解析）；
    2. 仅支持行首图片，段落中间的行内图按普通文字保留；不支持 SVG；
    3. 表格按标准 GFM 解析（分隔行单元格 ≥3 个短横线；不做转义管道 \\| 处理），
       表格内不支持加粗等行内标记。

    其他已知极限：连续引用行拆为独立段落；嵌套/缩进列表拍平；斜体不做转换。

    新增参数均为 keyword-only 且带默认值，兼容既有直调用（tests 中 4 处）。
    """
    blocks: list[dict[str, Any]] = []
    paragraph: list[str] = []
    code_lines: list[str] = []
    list_items: list[str] = []
    table_rows: list[list[str]] = []
    in_code = False
    source_root = (
        _source_root_of(source_path, workspace, allowed_dir, user_roots)
        if source_path is not None
        else None
    )

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            txt = " ".join(x.strip() for x in paragraph).strip()
            paragraph = []
            if txt:
                blocks.append({"type": "paragraph", "text": _md_inline(txt)})

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines:
            txt = " ".join(x.strip() for x in code_lines).strip()
            code_lines = []
            if txt:
                # 不做行内转换（保留 <img ...> 为字面文字），但**必须转义**：源稿是
                # 不可信输入，未转义的 <img src=...> 会被 reportlab 当图片标签直接
                # 打开文件，绕过 _resolve_md_image 的边界校验（P1 修复）。
                blocks.append({"type": "paragraph", "text": _md_escape(txt)})

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            blocks.append(
                {"type": "list", "items": [_md_inline(x) for x in list_items]}
            )
            list_items = []

    def flush_table() -> None:
        nonlocal table_rows
        if table_rows:
            clean: list[list[str]] = [
                r for r in table_rows
                if not all(_MD_TBL_SEP_RE.fullmatch(c) or c == "" for c in r)
            ]
            if clean:
                blocks.append({"type": "table", "headers": clean[0], "rows": clean[1:]})
            table_rows = []

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            # 开/闭围栏都要先闭合正文缓冲，否则代码块会与相邻叙述合并成一段
            if in_code:
                flush_code()
            else:
                flush_paragraph()
                flush_list()
                flush_table()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line:
            flush_paragraph()
            flush_list()
            flush_table()
            continue
        m = _MD_HEAD_RE.match(line)
        if m:
            flush_paragraph()
            flush_list()
            flush_table()
            blocks.append(
                {
                    "type": "heading",
                    "text": _md_inline(m.group(2).strip()),
                    "level": len(m.group(1)),
                }
            )
            continue
        if line.startswith("|"):
            flush_paragraph()
            flush_list()
            table_rows.append([c.strip() for c in line.strip("|").split("|")])
            continue
        m = _MD_LISTITEM_RE.match(line)
        if m:
            flush_paragraph()
            flush_table()
            list_items.append(m.group(2).strip())
            continue
        if line.startswith("!["):
            flush_paragraph()
            flush_list()
            flush_table()
            alt, dest = _md_image_dest(line)
            label = alt or "图表"
            image_path = _resolve_md_image(
                dest,
                source_path,
                source_root,
                workspace,
                allowed_dir,
                user_roots,
                allow_user_roots,
            )
            if image_path is None:
                blocks.append(
                    {
                        "type": "paragraph",
                        "text": _md_inline(f"[图表：{label}（见源稿）]"),
                    }
                )
            else:
                # 不写任何「已校验」标记字段——模型可自设 JSON 键，信任只能由
                # 调用方通过 _build_pdf(trusted_images=True) 显式传递（H1 修复）。
                blocks.append(
                    {
                        "type": "image",
                        "path": str(image_path),
                        "alt": label,
                        "caption": _md_inline(label),
                    }
                )
            continue
        if line.startswith(">"):
            flush_paragraph()
            flush_list()
            flush_table()
            blocks.append(
                {"type": "paragraph", "text": _md_inline(line.lstrip("> ").strip())}
            )
            continue
        # 默认段落分支：先闭合未完成的列表/表格，避免顺序反转（列表/表格后无空行直接接段落）
        flush_list()
        flush_table()
        paragraph.append(line)

    flush_paragraph()
    flush_code()
    flush_list()
    flush_table()
    return blocks


def _resolve_source_path(
    content_path: str,
    workspace: Path | None,
    allowed_dir: Path | None,
    user_roots: Any,
    allow_user_roots: bool,
) -> Path:
    """解析 content_path 源文件：工作区/会话文件区优先，用户授权目录兜底。

    用户授权目录仅当 allow_user_roots 且已注入 _user_roots（#821 机制）时可用；
    与 ReadFileTool 同口径，不额外放开任何边界（受 #955 约束）。
    """
    try:
        return resolve_output_path(content_path, workspace, allowed_dir)
    except (PermissionError, ValueError):
        pass
    if not allow_user_roots or not user_roots:
        raise PermissionError(
            f"content_path '{content_path}' 不在可读范围（工作区/会话文件区/用户授权目录）"
        )
    cand = Path(content_path)
    if not cand.is_absolute():
        raise PermissionError(f"content_path '{content_path}' 非绝对路径且不在工作区内")
    cand = cand.resolve()
    for r in user_roots:
        try:
            cand.relative_to(Path(str(r)).resolve())
            return cand
        except (TypeError, ValueError, OSError):
            continue
    raise PermissionError(f"content_path '{content_path}' 不在用户授权目录内")


# ── 单文件契约（#993 第 2 条）──────────────────────────────────────────────
#
# #993 实测（20KB→200KB 五档源稿全部单次成功）证明引擎侧没有内容长度上限、工具
# 也从不自动分卷，因此第 2 条按「显式声明单文件契约 + 超大 content 引导到
# content_path」重新定义（原措辞「确实需要分卷时告知卷数与理由」的前提不成立）。
#
# 阈值依据：agents.defaults.maxTokens=8192（#993 实测模型无法单次输出 30KB+ 中文），
# CJK 约 1 token/字 → 单次调用能产出的 content 上限约 8K–12K 字符；取 16000
# （≥1.3× 余量）保证正常单次调用不受影响，超过者必然是多轮拼接或已被截断，
# 应改为落成源稿文件走 content_path。
_MAX_INLINE_CONTENT_CHARS = 16_000


def _write_md_source_copy(pdf_path: Path, md_bytes: bytes) -> tuple[Path | None, str]:
    """把 content_path 源稿副本落到 PDF 同目录（``<PDF 同名>.md``，#993 第 2 条）。

    按**原始字节**落盘（不做换行/编码转换），保证副本与源稿逐字节一致——文本模式
    写盘会在 Windows 上把 LF 改成 CRLF。同名文件已存在则**跳过、不覆盖**（用户可能
    已手工改过该副本）；写失败只告警、不影响 PDF 交付（PDF 才是主产物）。
    返回 ``(副本路径或 None, 状态说明)``。
    """
    copy_path = pdf_path.with_suffix(".md")
    if copy_path.exists():
        logger.info(f"PDF: 源稿副本 {copy_path} 已存在，跳过不覆盖")
        return copy_path, "已存在，跳过不覆盖"
    try:
        copy_path.write_bytes(md_bytes)
    except OSError as exc:
        logger.warning(f"PDF: 源稿副本 {copy_path} 写入失败（{exc}），PDF 已生成")
        return None, f"写入失败（{exc}）"
    logger.info(f"PDF: 源稿副本已落盘 {copy_path}（{len(md_bytes)} 字节）")
    return copy_path, "已落盘"


# ── Agent Tool ──────────────────────────────────────────────────────────

class CreatePdfTool(Tool):
    """Create a PDF document in the workspace with proper formatting and Chinese font support."""

    name = "create_pdf"
    description = (
        "Create a PDF document in the session files directory. "
        "单文件契约：本工具不自动分卷，单次调用只输出一个单文件 PDF（引擎侧无内容长度上限）；"
        f"content 超过 {_MAX_INLINE_CONTENT_CHARS} 字符时不会静默渲染、也不报错，"
        "而是返回提示要求改用 content_path（长正文先落成 Markdown 源稿文件再传 content_path）。"
        "filename 的相对路径以会话 files 根目录为基准（例如 report.pdf 或 子目录/报告.pdf）；"
        "若传入 sessions/<当前会话ID>/files/... 这类以工作区根为基准的路径，会自动归一化到会话 files 目录，"
        "指向其他会话的路径会被拒绝。生成后返回实际落盘路径。"
        "Supports title, paragraphs, headings, tables, lists, custom fonts, "
        "and common Chinese document formatting (标题黑体/宋体, 字号, 行距, 对齐). "
        "Automatically discovers Chinese fonts on the system. "
        "Use this instead of writing ad-hoc Python scripts for PDF generation."
    )

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        allow_user_roots: bool = False,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._allow_user_roots = allow_user_roots

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path for the output .pdf file. Alias for filename. 相对路径基于会话 files 根目录。",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Filename or relative path for the output .pdf file. "
                        "相对路径以会话 files 根目录为基准；"
                        "以 sessions/<当前会话ID>/files/ 开头的路径按工作区根相对解析（自动归一化），"
                        "指向其他会话的路径会被拒绝。"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Path for the output .pdf file. Alias for filename. 相对路径基于会话 files 根目录。",
                },
                "title": {
                    "type": "string",
                    "description": "Optional document title (displayed as a centered heading on page 1).",
                },
                "content": {
                    "description": (
                        "Document content. Use a string for simple paragraph text, "
                        "or an array of content blocks for structured documents. "
                        "Supported block types: "
                        "{type: 'paragraph', text: '...'}, "
                        "{type: 'heading', text: '...', level: 1}, "
                        "{type: 'table', headers: ['A','B'], rows: [[...]]}, "
                        "{type: 'list', items: ['...']}, "
                        "{type: 'spacer', height: 12}, "
                        "{type: 'page_break'}. "
                        "块文本按字面渲染（不解析 Markdown/HTML：<b>、<img src=...> 等"
                        "标记会原样显示，不会生效）。"
                    ),
                },
                "content_path": {
                    "type": "string",
                    "description": (
                        "Optional path to a source Markdown file to render directly "
                        "(workaround: long report bodies exceed what the model can emit "
                        "in a single call — render from the file instead). "
                        "content_path 优先于 content（content 的 "
                        f"{_MAX_INLINE_CONTENT_CHARS} 字符阈值对它不生效，"
                        "源稿直渲无长度限制）；相对路径以会话 files 根目录/工作区为基准；"
                        "仅可读取工作区/会话文件区或用户授权目录（#821 口径）内的文件；"
                        "渲染成功后会在 PDF 同目录落一份源稿副本 <PDF 同名>.md（同名已存在则"
                        "跳过、不覆盖）。"
                        "按受支持的 Markdown 子集渲染（不是完整 Markdown renderer）："
                        "① 行内 Markdown 支持 **粗体** 与 [文字](http(s)://…) 链接，"
                        "作用域仅段落/标题/列表（表格单元格与代码围栏不做行内转换）；"
                        "② 行首 ![alt](path) 会嵌入图片（PNG/JPEG），相对路径基于源稿所在目录，"
                        "并复校工作区/会话文件区/用户授权目录边界（越界、损坏、SVG 降级为占位文字），"
                        "alt 渲染为图注；仅支持行首图片，段落中间的行内图不嵌入；"
                        "③ 代码块按普通段落渲染（多行以空格拼接，不做等宽排版），"
                        "代码内容原样显示（raw <img src=...> 只是文字，不读文件）；"
                        "④ 表格按标准 GFM 解析（分隔行单元格 ≥3 个短横线；不做转义管道 \\| 处理）。"
                    ),
                },
                "author": {
                    "type": "string",
                    "description": "Optional author name for PDF metadata.",
                },
                "page_size": {
                    "type": "string",
                    "enum": ["A4", "letter", "A3"],
                    "description": "Page size. Default: A4.",
                    "default": "A4",
                },
                "style_preset": {
                    "type": "string",
                    "enum": list(_CHINESE_PDF_STYLE_PRESETS.keys()),
                    "description": (
                        "Optional formatting preset. Use 'chinese_document' or "
                        "'chinese_essay' for Chinese documents (标题黑体加粗居中、正文宋体1.5行距). "
                        "Use 'report' for English reports."
                    ),
                },
                "title_style": {
                    "type": "object",
                    "description": (
                        "Formatting for the main title. Supports: font_name, "
                        "font_size_pt, bold, alignment. "
                        "Use for requests like 标题黑体加粗三号字居中."
                    ),
                },
                "body_style": {
                    "type": "object",
                    "description": (
                        "Formatting for body paragraphs. Supports: font_name, "
                        "font_size_pt, line_spacing, alignment. "
                        "Use for requests like 正文宋体小四、段落1.5行距."
                    ),
                },
                "format_instructions": {
                    "type": "string",
                    "description": (
                        "Natural language formatting instructions, "
                        "e.g. '正文宋体小四，段落1.5行距，标题黑体加粗三号字居中'."
                    ),
                },
            },
            "anyOf": [
                {"required": ["filename"]},
                {"required": ["file_path"]},
                {"required": ["path"]},
            ],
        }

    async def execute(self, **kwargs: Any) -> str:
        _sess_key = kwargs.pop("_session_key", None)
        user_roots = kwargs.pop("_user_roots", None)
        raw_path = raw_output_path(kwargs)
        content = kwargs.get("content") or ""
        content_path = kwargs.get("content_path") or ""

        if not raw_path.strip():
            return "Error: 必须提供 filename"

        # Resolve path
        try:
            file_path = resolve_output_path(raw_path, self._workspace, self._allowed_dir)
            file_path = ensure_suffix(file_path, ".pdf")
            enforce_boundary(file_path, self._allowed_dir, self._workspace)
        except PermissionError as e:
            return f"Error: 权限被拒绝：{e}"
        except ValueError as e:
            return f"Error: {e}"

        # Dedup: if the same file was already created within the past 30 seconds,
        # the AI likely called create_pdf twice — skip the duplicate.
        # content_path 例外：源稿文件可能在 30 秒内被改写，按同名跳过会返回旧 PDF。
        if file_path.exists() and not content_path:
            age = (time.time() - file_path.stat().st_mtime)
            if age < 30:
                _persist_tracked_file(self._workspace, file_path, op="write", session_key=_sess_key)
                return f"Created: {file_path}"

        # Validate content
        has_title = bool(kwargs.get("title"))
        has_content = bool(content) or bool(content_path)
        if not has_title and not has_content:
            return "Error: 至少提供 title、content 或 content_path"

        # 单文件契约（#993 第 2 条）：本工具不自动分卷。超大 content 不静默渲染
        # （也不报错）——直接提示改用 content_path，由调用方重试。content_path
        # 分支不受此阈值约束（源稿直渲无长度限制）。
        if not content_path and isinstance(content, str) and len(content) > _MAX_INLINE_CONTENT_CHARS:
            return (
                f"未生成 PDF：content 长度 {len(content)} 字符，超过单次调用阈值 "
                f"{_MAX_INLINE_CONTENT_CHARS} 字符。本工具不自动分卷——单次调用只输出一个"
                "单文件 PDF。请先把内容写入 Markdown 源稿文件，再用 "
                "content_path=<源稿路径> 重新调用（源稿直渲单文件，无此长度限制）。"
            )

        # content_path 优先：直接从 Markdown 源稿渲染（绕开模型单次输出上限）
        md_bytes: bytes | None = None
        if content_path:
            try:
                src = _resolve_source_path(
                    str(content_path),
                    self._workspace,
                    self._allowed_dir,
                    user_roots,
                    self._allow_user_roots,
                )
            except PermissionError as e:
                return f"Error: {e}"
            try:
                md_bytes = src.read_bytes()
                md_text = md_bytes.decode("utf-8")
            except (OSError, UnicodeDecodeError) as e:
                return f"Error: 无法读取内容源文件 {src}: {e}"
            content = _md_to_blocks(
                md_text,
                source_path=src,
                workspace=self._workspace,
                allowed_dir=self._allowed_dir,
                user_roots=user_roots,
                allow_user_roots=self._allow_user_roots,
            )

        # Parse styles
        title_style, body_style = _style_from_kwargs(kwargs)

        # Build PDF
        try:
            import reportlab  # noqa: F401 — verify importable
        except ImportError:
            return (
                "Error: 未安装 reportlab。 "
                "Run: pip install reportlab"
            )

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            _build_pdf(
                file_path,
                title=kwargs.get("title"),
                content=content,
                author=kwargs.get("author"),
                page_size_name=str(kwargs.get("page_size", "A4")),
                title_style=title_style,
                body_style=body_style,
                # 只有 content_path 分支（content 已被 _md_to_blocks 替换、图片逐张
                # 复校过边界）才可信；模型侧 content 的 image 块恒不可信（H1 修复）。
                trusted_images=bool(content_path),
                # 同理：_md_to_blocks 产出的文本已转义，content 手写文本没有 → 由
                # _build_pdf 统一转义（P1 修复，函数参数而非块内 JSON 字段）。
                content_escaped=bool(content_path),
            )
            _persist_tracked_file(self._workspace, file_path, op="write", session_key=_sess_key)
            if md_bytes is None:
                # 老调用（content/title）返回文本保持逐字节不变（向后兼容）。
                return f"Created: {file_path}"
            # #993 第 2 条：源稿副本随产物落盘（同名跳过不覆盖；写失败不影响 PDF）。
            copy_path, copy_note = _write_md_source_copy(file_path, md_bytes)
            if copy_path is not None:
                _persist_tracked_file(self._workspace, copy_path, op="write", session_key=_sess_key)
            return (
                f"Created: {file_path}\n"
                f"源稿副本: {copy_path or file_path.with_suffix('.md')}（{copy_note}）"
            )
        except Exception as e:
            logger.exception(f"PDF creation failed for {raw_path}")
            return f"Error creating PDF {raw_path}: {e}"


class PdfWriteTool(CreatePdfTool):
    """Backward-compatible alias for create_pdf."""

    name = "pdf_write"
    description = (
        "Create a new PDF document with the given content. "
        "单文件契约同 create_pdf：不自动分卷，单次调用只输出一个单文件 PDF；"
        "超大 content 不渲染，返回提示要求改用 content_path。"
        "Prefer create_pdf for new calls."
    )
