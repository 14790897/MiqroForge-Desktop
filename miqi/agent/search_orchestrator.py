"""SearchOrchestrator (issue #680): parallel breadth search for fast mode.

One web_search call in fast mode fans out into N parallel queries + M page
fetches, then merges everything into a single result — the model perceives a
single tool call and gets what used to take 5-8 serial rounds.

Scope (deliberately NOT Perplexity):
  DO   - rule-based query variants (ddgs region/language), parallel search,
         url normalize + domain dedup, parallel fetch with existing extractor
  DON'T - LLM query rewrite, search planning, source ranking, citation
         verification, multi-agent research (Phase 2+)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from miqi.agent.agent_mode import SearchStrategy
from miqi.agent.tools.web import WebFetchTool, WebSearchTool

logger = logging.getLogger(__name__)

# ddgs regions: (code, label) — broad coverage without LLM rewrite.
_REGIONS = [("wt-wt", "全球"), ("us-en", "英文"), ("cn-zh", "中文")]
_URL_RE = re.compile(r"https?://[^\s\)\]\}]+")


def _normalize_url(url: str) -> str:
    """Strip tracking junk; keep scheme+host+path for dedup."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}" if p.scheme in ("http", "https") else ""


def _dedup_urls(urls: list[str], limit: int) -> list[str]:
    """Domain-level dedup: keep at most one URL per domain, then fill."""
    seen_domains: set[str] = set()
    out: list[str] = []
    for u in urls:
        d = urlparse(u).netloc
        if d and d not in seen_domains:
            seen_domains.add(d)
            out.append(u)
        if len(out) >= limit:
            break
    return out


async def _ddgs_regional_search(query: str, n_queries: int, n: int) -> list[str]:
    """ddgs 区域变体搜索（无 key 兜底，原 SearchOrchestrator._parallel_search 逻辑）。

    每个区域查询有 15s 超时（#804/#803 审阅：ddgs 挂起会拖死整个工具调用——
    search 没有 fetch 的 12s 超时保护）。
    """
    try:
        from ddgs import DDGS
    except ImportError:
        return []

    regions = [r for r, _ in _REGIONS[: max(1, n_queries)]]

    def _one(region: str) -> list[dict[str, Any]]:
        try:
            # backend="html"：国内网络下 auto/lite 会撞 Brave/Yandex 超时
            # 20s+（#854 后实测：html 3.1s / lite 22.4s / auto 20.0s），
            # html 后端稳定且快；region 参数不受影响。
            return list(DDGS().text(query, region=region, max_results=n, backend="html"))
        except Exception as e:  # ddgs rate-limit / network — degrade per-region
            logger.warning("ddgs region=%s failed: %s", region, e)
            return []

    raw_lists = await asyncio.gather(
        *(asyncio.wait_for(asyncio.to_thread(_one, r), timeout=15.0) for r in regions),
        return_exceptions=True,
    )

    blocks: list[str] = []
    seen: set[str] = set()
    for region, raw in zip(regions, raw_lists):
        if not isinstance(raw, list) or not raw:
            continue
        lines = [f"Results for: {query} (region: {region})"]
        for item in raw:
            href = item.get("href") or item.get("url", "")
            if not href or href in seen:
                continue
            seen.add(href)
            lines.append(
                f"- {item.get('title', '')}\n  {href}\n  {item.get('body') or item.get('description', '')}"
            )
        blocks.append("\n".join(lines))
    return blocks


class SearchOrchestrator:
    """Parallel breadth search: N queries + M fetches, one merged result."""

    def __init__(self, search_tool: WebSearchTool, fetch_tool: WebFetchTool):
        self._search = search_tool
        self._fetch = fetch_tool

    async def run(self, query: str, strategy: SearchStrategy, n_results: int = 8) -> str:
        """Execute the fan-out. Always returns a usable string (degrades gracefully)."""
        # 1. Parallel queries across regions (rule-based variants)
        search_results = await self._parallel_search(
            query, strategy.fanout_queries, n_results
        )
        if not search_results:
            return f"No results for: {query}"

        # 2. Collect + dedup URLs from all result blocks
        urls: list[str] = []
        for block in search_results:
            for m in _URL_RE.findall(block):
                norm = _normalize_url(m.rstrip(".,;"))
                if norm and norm not in urls:
                    urls.append(norm)

        merged = "\n\n".join(search_results)

        # 3. Parallel fetches (best-effort; failures never fail the search)
        if strategy.fanout_fetches > 0 and urls:
            targets = _dedup_urls(urls, strategy.fanout_fetches)
            pages = await asyncio.gather(
                *(self._safe_fetch(u) for u in targets), return_exceptions=True
            )
            summaries = [p for p in pages if isinstance(p, str) and p and not p.startswith('{"error"')]
            if summaries:
                merged += "\n\n--- 网页正文摘要（并行抓取）---\n" + "\n\n".join(summaries[: strategy.fanout_fetches])

        return merged

    async def _parallel_search(self, query: str, n_queries: int, n: int) -> list[str]:
        """Run up to n_queries ddgs queries concurrently (region variants).

        A search tool may provide its own ``_parallel_search`` (tests,
        providers) — prefer that over the built-in ddgs implementation.
        WebSearchTool implements it to route through the configured provider
        chain first (#804); the built-in ddgs fallback lives in
        ``_ddgs_regional_search``.
        """
        impl = getattr(self._search, "_parallel_search", None)
        if callable(impl):
            return await impl(query, n_queries, n)
        return await _ddgs_regional_search(query, n_queries, n)

    async def _safe_fetch(self, url: str) -> str:
        """Fetch one page reusing WebFetchTool's extractor; errors → "" (skip).

        Rejects non-http(s)/private hosts up front (SSRF guard — CodeRabbit
        #741): search-result URLs must never trigger fetches of internal
        metadata/loopback addresses, even though WebFetchTool re-validates.
        """
        from miqi.agent.tools.web import _validate_url

        is_valid, _ = _validate_url(url)
        if not is_valid:
            logger.debug("fanout fetch rejected URL: %s", url)
            return ""
        try:
            return await asyncio.wait_for(
                self._fetch.execute(url, extract_mode="markdown", max_chars=3000),
                timeout=12.0,
            )
        except Exception as e:
            logger.debug("fanout fetch %s failed: %s", url, e)
            return ""


def parse_search_json(payload: str) -> dict[str, Any]:
    """Parse a web_search tool output (text or JSON) for testing/consumers."""
    if payload.startswith("{"):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            pass
    return {"text": payload}
