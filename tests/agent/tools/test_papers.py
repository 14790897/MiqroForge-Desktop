"""Tests for paper research tools: search, get, download."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from miqi.agent.tools.papers import (
    PaperDownloadTool,
    PaperGetTool,
    PaperSearchTool,
    _detect_paywall_text,
    _extract_arxiv_id,
    _looks_like_html,
    _looks_like_pdf,
    _parse_arxiv_entry,
    _safe_file_component,
    _to_record,
)

# ── _extract_arxiv_id ────────────────────────────────────────────────────────


def test_extract_arxiv_id_from_abs_url():
    assert _extract_arxiv_id("http://arxiv.org/abs/2301.00001") == "2301.00001"


def test_extract_arxiv_id_raw():
    assert _extract_arxiv_id("2301.00001v2") == "2301.00001v2"


def test_extract_arxiv_id_empty():
    assert _extract_arxiv_id("") == ""
    assert _extract_arxiv_id(None) == ""


# ── _safe_file_component ─────────────────────────────────────────────────────


def test_safe_file_component_normal():
    result = _safe_file_component("My Paper Title!")
    assert result == "My_Paper_Title"  # trailing ! stripped by strip("._")


def test_safe_file_component_special_chars():
    result = _safe_file_component("test:file/name\\here.pdf")
    assert ":" not in result
    assert "/" not in result
    assert "\\" not in result


def test_safe_file_component_empty_uses_fallback():
    assert _safe_file_component("") == "paper"
    assert _safe_file_component("   ") == "paper"


# ── _looks_like_pdf ──────────────────────────────────────────────────────────


def test_looks_like_pdf_positive():
    assert _looks_like_pdf(b"%PDF-1.4\n%...") is True


def test_looks_like_pdf_negative():
    assert _looks_like_pdf(b"<html>") is False
    assert _looks_like_pdf(b"") is False


# ── _looks_like_html ─────────────────────────────────────────────────────────


def test_looks_like_html_by_content_type():
    assert _looks_like_html("text/html", b"") is True


def test_looks_like_html_by_content():
    assert _looks_like_html("application/octet-stream", b"<!doctype html><html>") is True


def test_looks_like_html_negative():
    assert _looks_like_html("application/pdf", b"%PDF-1.4") is False


# ── _detect_paywall_text ─────────────────────────────────────────────────────


def test_detect_paywall_sign_in():
    found, tags = _detect_paywall_text(b"Please sign in to access this article")
    assert found is True
    assert "login_required" in tags


def test_detect_paywall_purchase():
    found, tags = _detect_paywall_text(b"Buy this article for $39.99")
    assert found is True
    assert "purchase" in tags


def test_detect_paywall_subscribe():
    found, tags = _detect_paywall_text(b"Subscribe to continue reading")
    assert found is True
    assert "subscribe" in tags


def test_detect_paywall_clean_text():
    found, tags = _detect_paywall_text(b"This is a free open-access paper about physics")
    assert found is False
    assert tags == []


def test_detect_paywall_multiple_signals():
    found, tags = _detect_paywall_text(
        b"Please purchase a subscription or log in through your institution"
    )
    assert found is True
    assert len(tags) >= 2


# ── _to_record ───────────────────────────────────────────────────────────────


def test_to_record_basic():
    rec = _to_record(
        source="arxiv",
        paper_id="ARXIV:2301.00001",
        title="Test Paper",
        abstract="An abstract.",
        authors=["Alice", "Bob"],
        year=2023,
        venue="cs.AI",
        doi="10.1234/test",
        arxiv_id="2301.00001",
        citation_count=42,
        reference_count=15,
        is_open_access=True,
        open_access_pdf_url="https://arxiv.org/pdf/2301.00001.pdf",
        source_url="https://arxiv.org/abs/2301.00001",
    )
    assert rec["id"] == "ARXIV:2301.00001"
    assert rec["title"] == "Test Paper"
    assert rec["authors"] == ["Alice", "Bob"]
    assert rec["year"] == 2023
    assert rec["citation_count"] == 42
    assert rec["is_open_access"] is True


def test_to_record_with_extra():
    rec = _to_record(
        source="semantic_scholar",
        paper_id="123",
        title="T",
        abstract="A",
        authors=[],
        year=None,
        venue="",
        doi="",
        arxiv_id="",
        citation_count=None,
        reference_count=None,
        is_open_access=False,
        open_access_pdf_url="",
        source_url="",
        extra={"keywords": ["AI", "ML"]},
    )
    assert rec["extra"] == {"keywords": ["AI", "ML"]}


# ── _parse_arxiv_entry ──────────────────────────────────────────────────────


_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2301.00001v2</id>
    <title>Deep Learning for Quantum Physics</title>
    <summary>This paper explores deep learning methods applied to quantum systems.</summary>
    <published>2023-01-01T12:00:00Z</published>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <link title="pdf" href="https://arxiv.org/pdf/2301.00001.pdf" rel="related"/>
    <link title="doi" href="http://dx.doi.org/10.1234/test" rel="related"/>
    <category term="cs.AI"/>
    <category term="quant-ph"/>
  </entry>
</feed>"""


def test_parse_arxiv_entry():
    root = ET.fromstring(_ARXIV_XML)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    assert entry is not None

    parsed = _parse_arxiv_entry(entry, ns)
    assert parsed["arxiv_id"] == "2301.00001v2"
    assert parsed["title"] == "Deep Learning for Quantum Physics"
    assert "deep learning" in parsed["summary"].lower()
    assert parsed["year"] == 2023
    assert parsed["authors"] == ["Alice Smith", "Bob Jones"]
    assert parsed["pdf_url"] == "https://arxiv.org/pdf/2301.00001.pdf"
    assert parsed["doi"] == "http://dx.doi.org/10.1234/test"
    assert parsed["venue"] == "cs.AI"


# ── PaperSearchTool construction ─────────────────────────────────────────────


def test_paper_search_tool_defaults():
    tool = PaperSearchTool()
    assert tool.name == "paper_search"
    assert tool.provider == "hybrid"


def test_paper_search_tool_custom():
    tool = PaperSearchTool(
        provider="arxiv",
        semantic_scholar_api_key="test-key",
        timeout_seconds=30,
        default_limit=5,
        max_limit=10,
    )
    assert tool.provider == "arxiv"
    assert tool.semantic_scholar_api_key == "test-key"
    assert tool.default_limit == 5


# ── PaperGetTool construction ────────────────────────────────────────────────


def test_paper_get_tool_defaults():
    tool = PaperGetTool()
    assert tool.name == "paper_get"
    assert tool.provider == "hybrid"


# ── PaperDownloadTool construction ───────────────────────────────────────────


def test_paper_download_tool_construction(tmp_path: Path):
    tool = PaperDownloadTool(workspace=tmp_path / "ws")
    assert tool.name == "paper_download"
    assert tool.max_size_mb == 80
    assert tool.workspace == (tmp_path / "ws").resolve()


def test_paper_download_save_path_default(tmp_path: Path):
    """Default save path must be workspace/papers/<sanitized>.pdf (no artifacts)."""
    tool = PaperDownloadTool(workspace=tmp_path / "ws")
    path = tool._resolve_save_path(
        out_path=None,
        paper_id="2301.00001",
        download_url="https://arxiv.org/pdf/2301.00001.pdf",
    )
    assert "papers" in str(path)
    assert "artifacts" not in str(path)
    assert path.suffix == ".pdf"
    # Must be inside workspace
    path.resolve().relative_to(tool.workspace)


def test_paper_download_save_path_custom(tmp_path: Path):
    tool = PaperDownloadTool(workspace=tmp_path / "ws")
    path = tool._resolve_save_path(
        out_path="my-papers/test.pdf",
        paper_id="",
        download_url="https://example.com/paper.pdf",
    )
    assert path.parts[-2:] == ("my-papers", "test.pdf")


def test_paper_download_rejects_outside_workspace(tmp_path: Path):
    tool = PaperDownloadTool(workspace=tmp_path / "ws")
    with pytest.raises(PermissionError):
        tool._resolve_save_path(
            out_path="/etc/passwd",
            paper_id="",
            download_url="https://example.com/paper.pdf",
        )


# ── Error handling ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_empty_query():
    tool = PaperSearchTool()
    result = await tool.execute(query="")
    data = json.loads(result)
    assert "error" in data


@pytest.mark.asyncio
async def test_download_missing_id_and_url(tmp_path: Path):
    tool = PaperDownloadTool(workspace=tmp_path / "ws")
    result = await tool.execute(paper_id=None, url=None)
    data = json.loads(result)
    assert data["ok"] is False
    assert "error" in data
