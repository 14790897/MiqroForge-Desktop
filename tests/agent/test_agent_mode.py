"""Agent Reasoning Modes (issue #680): config + SearchOrchestrator unit tests."""

from __future__ import annotations

from types import SimpleNamespace

from miqi.agent.agent_mode import FAST, THINK, get_mode_config, mode_names
from miqi.agent.search_orchestrator import (
    SearchOrchestrator,
    _dedup_urls,
    _normalize_url,
)

# ── AgentModeConfig ──────────────────────────────────────────────────────────

def test_fast_config_budget() -> None:
    """Fast = Answer-oriented: short generation, 3-round fuse, breadth search."""
    assert FAST.mode == "fast"
    assert FAST.generation.max_tokens == 2048
    assert FAST.tool.max_tool_rounds == 3
    assert FAST.tool.parallel_limit == 5
    assert FAST.tool.confirm_policy == "info_only"
    assert FAST.search.name == "breadth"
    assert FAST.search.fanout_queries == 2
    assert FAST.search.fanout_fetches == 3
    assert "30 秒" in FAST.prompt_snippet


def test_think_config_matches_current_behavior() -> None:
    """Think = Task-oriented: current loop limits, depth-guidance prompt."""
    assert THINK.mode == "think"
    assert THINK.generation.max_tokens == 8192
    assert THINK.tool.max_tool_rounds is None  # unlimited decision loop
    assert THINK.tool.confirm_policy == "full"
    assert THINK.search.name == "depth"  # reserved (Phase 2)
    assert THINK.search.fanout_queries == 1
    assert "深度研究" in THINK.prompt_snippet  # depth guidance injected


def test_get_mode_config_fallbacks() -> None:
    """None/unknown mode resolves to fast (默认极速版); think explicit."""
    assert get_mode_config(None).mode == "fast"
    assert get_mode_config("").mode == "fast"
    assert get_mode_config("fast").mode == "fast"
    assert get_mode_config("think").mode == "think"
    assert get_mode_config("whatever").mode == "fast"
    assert set(mode_names()) == {"fast", "think"}


# ── SearchOrchestrator helpers ───────────────────────────────────────────────

def test_normalize_url() -> None:
    assert _normalize_url("https://a.com/x?utm=1#frag") == "https://a.com/x"
    assert _normalize_url("http://b.io") == "http://b.io"
    assert _normalize_url("ftp://c.org/f") == ""  # non-http dropped


def test_dedup_urls_domain_level() -> None:
    urls = [
        "https://a.com/x",
        "https://a.com/y",  # same domain → skipped
        "https://b.com/z",
        "https://a.com/x",  # exact dup
        "https://c.org/1",
    ]
    assert _dedup_urls(urls, 2) == ["https://a.com/x", "https://b.com/z"]
    assert _dedup_urls(urls, 10) == [
        "https://a.com/x",
        "https://b.com/z",
        "https://c.org/1",
    ]
    assert _dedup_urls([], 3) == []


# ── SearchOrchestrator (mocked network) ──────────────────────────────────────

class _FakeSearch:
    """WebSearchTool stand-in: returns canned ddgs-shaped text per query."""

    def __init__(self, blocks: list[str]):
        self._blocks = blocks

    async def _parallel_search(self, query: str, n_queries: int, n: int) -> list[str]:
        return self._blocks[:n_queries]


class _FakeFetch:
    """WebFetchTool stand-in: returns canned page bodies."""

    def __init__(self, pages: dict[str, str]):
        self._pages = pages

    async def execute(self, url: str, extract_mode: str = "markdown", max_chars: int | None = None, **kw):
        return self._pages.get(url, '{"error": "not found", "url": "' + url + '"}')


async def test_orchestrator_merges_search_and_fetch() -> None:
    """One call returns search blocks + fetched page summaries together."""
    search = _FakeSearch([
        "Results for: q (region: wt-wt)\n- A title\n  https://a.com/page1\n  snippet",
        "Results for: q (region: us-en)\n- B title\n  https://b.com/page2\n  snippet",
    ])
    fetch = _FakeFetch({
        "https://a.com/page1": "**Page 1 content**",
        "https://b.com/page2": "Page 2 content",
    })
    orch = SearchOrchestrator(search_tool=search, fetch_tool=fetch)  # type: ignore[arg-type]
    strategy = SimpleNamespace(fanout_queries=2, fanout_fetches=2)

    out = await orch.run("q", strategy, n_results=8)

    assert "Results for: q (region: wt-wt)" in out
    assert "Results for: q (region: us-en)" in out
    assert "网页正文摘要" in out
    assert "Page 1 content" in out
    assert "Page 2 content" in out


async def test_orchestrator_degrades_when_no_fetches() -> None:
    """fanout_fetches=0 (think mode) → search blocks only, no fetch section."""
    search = _FakeSearch(["Results for: q\n- A title\n  https://a.com/x\n  s"])
    orch = SearchOrchestrator(search_tool=search, fetch_tool=_FakeFetch({}))  # type: ignore[arg-type]
    strategy = SimpleNamespace(fanout_queries=1, fanout_fetches=0)

    out = await orch.run("q", strategy, n_results=8)

    assert "Results for: q" in out
    assert "网页正文摘要" not in out


async def test_orchestrator_skips_failed_fetches() -> None:
    """Fetch errors never fail the search — page section just omits them."""
    search = _FakeSearch(["Results for: q\n- A title\n  https://a.com/x\n  s"])
    fetch = _FakeFetch({})  # all fetches "error"
    orch = SearchOrchestrator(search_tool=search, fetch_tool=fetch)  # type: ignore[arg-type]
    strategy = SimpleNamespace(fanout_queries=1, fanout_fetches=3)

    out = await orch.run("q", strategy, n_results=8)

    assert "Results for: q" in out
    assert "网页正文摘要" not in out  # no successful pages → no section
