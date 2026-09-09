"""PDF creation tool for the AI agent — creates PDF documents with Chinese font support.

Replaces the skill-based ad-hoc approach with a proper tool that the agent can call
directly to generate PDFs with consistent formatting and font handling.
"""

from __future__ import annotations

import os
import subprocess
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
) -> None:
    """Build a PDF document using reportlab."""
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

    story: list[Any] = []

    # Add title
    if title:
        story.append(Paragraph(title, ptitle_style))
        story.append(Spacer(1, 12))

    # Add content blocks
    blocks = content if isinstance(content, list) else [{"type": "paragraph", "text": str(content)}]
    for block in blocks:
        if not isinstance(block, dict):
            story.append(Paragraph(str(block), pbody_style))
            continue

        block_type = str(block.get("type", "paragraph")).lower()

        if block_type == "heading":
            level = int(block.get("level", 1))
            text = str(block.get("text", ""))
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
                story.append(Paragraph(f"<b>{text}</b>", pbody_style))

        elif block_type == "paragraph":
            text = str(block.get("text", ""))
            if text.strip():
                story.append(Paragraph(text, pbody_style))

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
                story.append(Paragraph(f"• {str(item)}", pbody_style))

        elif block_type == "spacer":
            height = float(block.get("height", 12))
            story.append(Spacer(1, height))

        elif block_type == "page_break":
            story.append(PageBreak())

    # Build
    doc.build(story)


# ── Agent Tool ──────────────────────────────────────────────────────────

class CreatePdfTool(Tool):
    """Create a PDF document in the workspace with proper formatting and Chinese font support."""

    name = "create_pdf"
    description = (
        "Create a PDF document in the session files directory. "
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
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir

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
                        "{type: 'page_break'}."
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
                {"type": "object", "required": ["filename"]},
                {"type": "object", "required": ["file_path"]},
                {"type": "object", "required": ["path"]},
            ],
        }

    async def execute(self, **kwargs: Any) -> str:
        _sess_key = kwargs.pop("_session_key", None)
        raw_path = raw_output_path(kwargs)
        content = kwargs.get("content", "")

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
        if file_path.exists():
            age = (time.time() - file_path.stat().st_mtime)
            if age < 30:
                _persist_tracked_file(self._workspace, file_path, op="write", session_key=_sess_key)
                return f"Created: {file_path}"

        # Validate content
        has_title = bool(kwargs.get("title"))
        has_content = bool(content)
        if not has_title and not has_content:
            return "Error: 至少提供 title 或 content"

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
            )
            _persist_tracked_file(self._workspace, file_path, op="write", session_key=_sess_key)
            return f"Created: {file_path}"
        except Exception as e:
            logger.exception(f"PDF creation failed for {raw_path}")
            return f"Error creating PDF {raw_path}: {e}"


class PdfWriteTool(CreatePdfTool):
    """Backward-compatible alias for create_pdf."""

    name = "pdf_write"
    description = (
        "Create a new PDF document with the given content. "
        "Prefer create_pdf for new calls."
    )
