"""Document parse handler for AppServer dispatch.

Provides documents.parse — a handler that extracts text content from uploaded
documents (PDF, Word, Excel, PowerPoint) for preview and RAG usage.
"""

from __future__ import annotations

import base64
import shutil
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from miqi.documents.document_parser import (
    MAX_PREVIEW_CHARS,
    is_supported_document,
    parse_document,
)
from miqi.runtime.app_server import AppServerError
from miqi.runtime.file_handlers import (
    _validate_file_path,
)

# #889 (CWE-400): 内联附件(data_base64)在解码前按编码长度拒绝、解码后按
# 实际字节数再校验——正常 Office 附件远小于 25MB,防止超大数据撑爆内存。
MAX_INLINE_ATTACHMENT_BYTES = 25 * 1024 * 1024
_MAX_INLINE_ATTACHMENT_B64_CHARS = MAX_INLINE_ATTACHMENT_BYTES * 4 // 3 + 4


async def documents_parse_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Parse a document and return extracted text.

    Params:
        path (required): Document file path (workspace-relative or absolute).
        session_key (optional): Session scope for path resolution.
        force_ocr (optional): Force OCR even for text-based PDFs.
        preview (optional): If True, limit to MAX_PREVIEW_CHARS.
        structured (optional): If True, also return structured render data
            (issue #877): spreadsheets as sheets/rows/merges, DOCX as ordered
            heading/paragraph/table/image blocks.
        data_base64 (optional): In-memory file bytes (chat attachment preview,
            issue #877). When provided the file is parsed from a temp copy —
            `path` is then only used for suffix detection and is NOT resolved
            against the workspace.
    """
    file_path = params.get("path", "").strip()
    session_key = params.get("session_key")
    force_ocr = params.get("force_ocr", False)
    is_preview = params.get("preview", False)
    structured = params.get("structured", False)
    data_base64 = params.get("data_base64")

    logger.info(
        "[docs:parse] req={} path={} session_key={} preview={} structured={} has_base64={}",
        request_id, file_path, session_key, is_preview, structured, bool(data_base64),
    )

    if not file_path:
        raise AppServerError("path is required", code="INVALID_PARAMS")

    # In-memory bytes (chat attachment chips): stage a temp file whose suffix
    # matches the attachment name so is_supported_document() can dispatch.
    temp_dir: str | None = None
    staged: Path | None = None
    if data_base64:
        # Reject oversized payloads BEFORE decoding (encoded-length check is
        # O(1); decoding a multi-GB string is what the check protects against).
        if len(data_base64) > _MAX_INLINE_ATTACHMENT_B64_CHARS:
            logger.warning("[docs:parse] inline attachment too large: {} b64 chars", len(data_base64))
            raise AppServerError("Attachment too large", code="INVALID_PARAMS")
        try:
            data = base64.b64decode(data_base64, validate=False)
        except Exception as exc:
            raise AppServerError("Invalid data_base64", code="INVALID_PARAMS") from exc
        if len(data) > MAX_INLINE_ATTACHMENT_BYTES:
            logger.warning("[docs:parse] inline attachment too large: {} bytes", len(data))
            raise AppServerError("Attachment too large", code="INVALID_PARAMS")
        suffix = Path(file_path).suffix.lower()
        temp_dir = tempfile.mkdtemp(prefix="miqi_parse_")
        staged = Path(temp_dir) / f"attachment{suffix}"
        staged.write_bytes(data)
        resolved = staged
    else:
        # Resolve and validate the path
        try:
            resolved = _validate_file_path(file_path, client_id, session_key)
        except AppServerError:
            raise
        except ValueError as exc:
            logger.warning("[docs:parse] invalid path {}: {}", file_path, exc)
            raise AppServerError("Invalid file path", code="INVALID_PARAMS") from exc

    try:
        if not resolved.exists():
            logger.warning("[docs:parse] file not found: {}", resolved)
            raise AppServerError(
                f"File not found: {file_path}"
                + (f" (session: {session_key})" if session_key else ""),
                code="NOT_FOUND",
            )

        if not is_supported_document(resolved):
            suffix = resolved.suffix.lower()
            raise AppServerError(
                f"Unsupported document format: {suffix or resolved.name}",
                code="INVALID_PARAMS",
            )

        try:
            max_chars = MAX_PREVIEW_CHARS if is_preview else 50_000
            result = parse_document(
                resolved, max_chars=max_chars, force_ocr=force_ocr, structured=structured,
            )
        except Exception as exc:
            logger.error("[docs:parse] parsing failed for {}: {} type={}", file_path, exc, type(exc).__name__)
            raise AppServerError(
                f"Document parsing failed: {exc}", code="INTERNAL",
            ) from exc

        logger.info(
            "[docs:parse] ok path={} text_len={} ocr={} ms={} structured={}",
            file_path, len(result["text"]), result.get("ocr_used"), result.get("parse_ms"),
            "yes" if result.get("structured") else "no",
        )

        return {
            "result": {
                "path": file_path,
                "text": result["text"],
                "page_count": result["page_count"],
                "size_bytes": result["size_bytes"],
                "mime_type": result["mime_type"],
                "ocr_used": result["ocr_used"],
                "parse_ms": result["parse_ms"],
                "structured": result.get("structured"),
            },
        }
    finally:
        # Clean up the staged temp copy for in-memory parses
        if temp_dir:
            try:
                if staged is not None:
                    staged.unlink(missing_ok=True)
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
