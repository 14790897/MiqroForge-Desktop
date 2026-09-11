"""PDF read tool for the AI agent — provides text extraction for uploaded PDFs.

Replaces the skill-based approach with a proper tool that the agent can call
directly to read PDF content, with OCR fallback for scanned documents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from miqi.agent.tools.base import Tool
from miqi.documents.document_parser import is_supported_document, parse_document


class PdfReadTool(Tool):
    """Read and extract text from an uploaded PDF file, with OCR fallback.

    Use this tool whenever the user references an uploaded PDF and you need
    to understand its contents. Works on both text-based PDFs and scanned/image
    PDFs (OCR via Tesseract when needed).
    """

    name = "pdf_read"
    description = (
        "Read and extract text content from a PDF file in the workspace. "
        "Supports both digital (text-based) and scanned (image-based) PDFs "
        "with automatic OCR fallback. Returns the extracted text and metadata "
        "including whether OCR was used and the page count. "
        "Use this when the user has uploaded a PDF and asks questions about its content."
    )

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None):
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the PDF file in the workspace (e.g. uploads/report.pdf).",
                },
                "force_ocr": {
                    "type": "boolean",
                    "description": "If true, skip direct text extraction and force OCR even for text PDFs.",
                    "default": False,
                },
            },
            "required": ["file_path"],
        }

    async def execute(self, **kwargs: Any) -> str:
        """Execute the pdf_read tool. Returns extracted text as a string."""
        file_path = kwargs.get("file_path", "")
        force_ocr = kwargs.get("force_ocr", False)

        if not file_path:
            return "Error: 必须提供 file_path"

        path = Path(file_path)
        if not path.is_absolute() and self._workspace is not None:
            path = self._workspace / path

        if not is_supported_document(path):
            return f"Error: 不支持的文件类型：{path.suffix}。pdf_read 仅支持 PDF 文件。"

        if not path.exists():
            # Try uploads/ subdirectory
            alt = self._workspace / "uploads" / Path(file_path).name if self._workspace else None
            if alt and alt.exists():
                path = alt
            else:
                return (
                    f"Error: 文件不存在：{file_path}。 "
                    f"Tried: {path}" + (f" and {alt}" if alt else "")
                )

        try:
            result = parse_document(path, force_ocr=force_ocr)
        except Exception as exc:
            return f"Error reading PDF: {exc}"

        text = result["text"]
        page_count = result["page_count"]
        ocr_used = result["ocr_used"]

        if not text.strip():
            return (
                f"PDF appears to be empty or a scanned image without OCR. "
                f"Pages: {page_count}, OCR attempted: {ocr_used}. "
                f"Try force_ocr=true if you suspect scanned content."
            )

        return (
            f"PDF: {path.name}\n"
            f"Pages: {page_count}\n"
            f"OCR used: {ocr_used}\n"
            f"Text length: {len(text)} chars\n\n"
            f"{text[:200000]}"
        )
