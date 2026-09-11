"""WebSearchTool provider / fallback chain tests (#561).

Covers: provider normalization (hybrid→auto), the auto fallback chain
(Tavily → Brave → DDGS), error classification (RATE_LIMIT/AUTH/NO_RESULT),
the model-facing string format, and the legacy api_key → brave_api_key
config migration.
"""

from miqi.agent.tools.web import (
    BraveProvider,
    DDGSProvider,
    DeepSeekSearchProvider,
    SearchProviderManager,
    SearchResult,
    TavilyProvider,
    WebSearchTool,
)
from miqi.config.loader import _migrate_config

# ── provider normalization ───────────────────────────────────────────────


def test_provider_auto_is_default():
    manager = SearchProviderManager("auto")
    assert manager.provider == "auto"


def test_provider_hybrid_maps_to_auto():
    manager = SearchProviderManager("hybrid")
    assert manager.provider == "auto"


def test_provider_unknown_maps_to_auto():
    manager = SearchProviderManager("nonsense")
    assert manager.provider == "auto"


def test_provider_explicit_brave_stays_brave():
    manager = SearchProviderManager("brave")
    assert manager.provider == "brave"


def test_auto_chain_uses_only_configured_key_providers():
    manager = SearchProviderManager("auto", tavily_api_key="tv-key")
    chain = manager._chain()
    assert [p.name for p in chain] == ["tavily", "ddgs"]

    manager2 = SearchProviderManager("auto", brave_api_key="bs-key")
    assert [p.name for p in manager2._chain()] == ["brave", "ddgs"]

    manager3 = SearchProviderManager("auto", tavily_api_key="t", brave_api_key="b")
    assert [p.name for p in manager3._chain()] == ["tavily", "brave", "ddgs"]

    manager4 = SearchProviderManager("auto")
    assert [p.name for p in manager4._chain()] == ["ddgs"]


# ── fallback chain behaviour ─────────────────────────────────────────────


async def test_auto_falls_through_on_rate_limit(monkeypatch):
    calls = []

    class _Tavily(TavilyProvider):
        async def search(self, query, count):
            calls.append("tavily")
            return SearchResult(False, error_type="RATE_LIMIT")

    class _Brave(BraveProvider):
        async def search(self, query, count):
            calls.append("brave")
            return SearchResult(False, error_type="RATE_LIMIT")

    manager = SearchProviderManager("auto", tavily_api_key="t", brave_api_key="b")
    manager._chain = lambda: [_Tavily("t"), _Brave("b"), DDGSProvider()]

    async def _ok(self, query, count):
        return SearchResult(True, [{"title": "ok", "url": "https://x", "snippet": "s"}])

    monkeypatch.setattr(DDGSProvider, "search", _ok)

    result = await manager.search("q", 5)
    assert result.success
    assert calls == ["tavily", "brave"]


async def test_auto_auth_error_degrades_with_warning(monkeypatch, caplog):
    calls = []

    class _Tavily(TavilyProvider):
        async def search(self, query, count):
            calls.append("tavily")
            return SearchResult(False, error_type="AUTH_ERROR")

    manager = SearchProviderManager("auto", tavily_api_key="bad")
    manager._chain = lambda: [_Tavily("bad"), DDGSProvider()]

    async def _ok(self, query, count):
        return SearchResult(True, [{"title": "ok", "url": "https://x", "snippet": "s"}])

    monkeypatch.setattr(DDGSProvider, "search", _ok)

    result = await manager.search("q", 5)
    assert result.success
    assert calls == ["tavily"]
    assert any("authentication failed" in r.message for r in caplog.records)


async def test_no_result_does_not_fallback(monkeypatch):
    calls = []

    class _Tavily(TavilyProvider):
        async def search(self, query, count):
            calls.append("tavily")
            return SearchResult(True, error_type="NO_RESULT")

    manager = SearchProviderManager("auto", tavily_api_key="t")
    manager._chain = lambda: [_Tavily("t"), DDGSProvider()]
    monkeypatch.setattr(
        DDGSProvider, "search",
        lambda self, query, count: SearchResult(True, [{"title": "x", "url": "https://x", "snippet": ""}]),
    )

    result = await manager.search("q", 5)
    assert result.success and result.error_type == "NO_RESULT"
    assert calls == ["tavily"]  # ddgs never tried


async def test_explicit_provider_does_not_chain(monkeypatch):
    calls = []

    class _Tavily(TavilyProvider):
        async def search(self, query, count):
            calls.append("tavily")
            return SearchResult(False, error_type="RATE_LIMIT")

    manager = SearchProviderManager("tavily", tavily_api_key="t")
    manager._chain = lambda: [_Tavily("t")]
    result = await manager.search("q", 5)
    assert not result.success
    assert calls == ["tavily"]


# ── provider error classification ────────────────────────────────────────


async def test_tavily_401_classified_auth(monkeypatch):
    async def _fake_post(self, url, **kwargs):
        class _R:
            status_code = 401

            def json(self):
                return {}

            def raise_for_status(self):
                return None

        return _R()

    monkeypatch.setattr("miqi.agent.tools.web.httpx.AsyncClient.post", _fake_post)
    result = await TavilyProvider("k").search("q", 5)
    assert not result.success and result.error_type == "AUTH_ERROR"


async def test_tavily_429_classified_rate_limit(monkeypatch):
    async def _fake_post(self, url, **kwargs):
        class _R:
            status_code = 429

            def json(self):
                return {}

            def raise_for_status(self):
                return None

        return _R()

    monkeypatch.setattr("miqi.agent.tools.web.httpx.AsyncClient.post", _fake_post)
    result = await TavilyProvider("k").search("q", 5)
    assert not result.success and result.error_type == "RATE_LIMIT"


async def test_brave_missing_key_is_no_key_error():
    result = await BraveProvider("").search("q", 5)
    assert not result.success and result.error_type == "NO_KEY"
    assert result.provider == "brave"


# ── tool-level string format (model contract) ────────────────────────────


async def test_execute_returns_string_format(monkeypatch):
    async def _fake_search(self, query, count):
        return SearchResult(True, [
            {"title": "T1", "url": "https://example.com/a", "snippet": "s1"},
            {"title": "T2", "url": "https://example.com/b", "snippet": "s2"},
        ])

    monkeypatch.setattr(SearchProviderManager, "search", _fake_search)
    tool = WebSearchTool(provider="auto")
    out = await tool.execute("hello")
    assert out.startswith("Results for: hello")
    assert "1. T1" in out and "https://example.com/a" in out and "s1" in out


async def test_execute_all_providers_down_exposes_reason(monkeypatch):
    async def _fake_search(self, query, count):
        return SearchResult(False, error_type="NETWORK", provider="ddgs")

    monkeypatch.setattr(SearchProviderManager, "search", _fake_search)
    tool = WebSearchTool(provider="auto")
    out = await tool.execute("hello")
    assert out.startswith("Error:")
    assert "网络搜索失败" in out
    assert "网络连接失败" in out  # actionable reason surfaced (#804)
    assert "web_fetch" in out


async def test_execute_no_key_exposes_setting_hint(monkeypatch):
    async def _fake_search(self, query, count):
        return SearchResult(False, error_type="NO_KEY", provider="tavily")

    monkeypatch.setattr(SearchProviderManager, "search", _fake_search)
    tool = WebSearchTool(provider="auto")
    out = await tool.execute("hello")
    assert "未配置 Tavily API key，请在设置中填写" in out


async def test_execute_auth_error_exposes_bad_key_hint(monkeypatch):
    async def _fake_search(self, query, count):
        return SearchResult(False, error_type="AUTH_ERROR", provider="brave")

    monkeypatch.setattr(SearchProviderManager, "search", _fake_search)
    tool = WebSearchTool(provider="auto")
    out = await tool.execute("hello")
    assert "Brave API key 无效" in out


async def test_manager_chain_exhausted_surfaces_last_failure(monkeypatch):
    """Chain exhaustion must carry the last real failure, not a generic error."""

    class _Tavily(TavilyProvider):
        async def search(self, query, count):
            return SearchResult(False, error_type="NETWORK")

    class _Brave(BraveProvider):
        async def search(self, query, count):
            return SearchResult(False, error_type="RATE_LIMIT")

    manager = SearchProviderManager("auto", tavily_api_key="t", brave_api_key="b")
    manager._chain = lambda: [_Tavily("t"), _Brave("b")]
    result = await manager.search("q", 5)
    assert not result.success
    assert result.error_type == "RATE_LIMIT"
    assert result.provider == "brave"


async def test_execute_no_result_message(monkeypatch):
    async def _fake_search(self, query, count):
        return SearchResult(True, error_type="NO_RESULT")

    monkeypatch.setattr(SearchProviderManager, "search", _fake_search)
    tool = WebSearchTool(provider="auto")
    assert await tool.execute("qqq") == "No results for: qqq"


# ── legacy config migration (#561) ───────────────────────────────────────


def test_migrate_old_api_key_to_brave_api_key():
    data = {
        "tools": {
            "web": {
                "search": {"provider": "brave", "api_key": "old-brave-key"},
            }
        }
    }
    migrated = _migrate_config(data)
    search = migrated["tools"]["web"]["search"]
    assert search["brave_api_key"] == "old-brave-key"
    assert "api_key" not in search


def test_migrate_keeps_existing_brave_key():
    data = {
        "tools": {
            "web": {
                "search": {"provider": "auto", "api_key": "old", "brave_api_key": "new"},
            }
        }
    }
    migrated = _migrate_config(data)
    search = migrated["tools"]["web"]["search"]
    assert search["brave_api_key"] == "new"
    assert "api_key" not in search


# ── fast 模式扇出搜索走配置链（#804）──────────────────────────────────


async def test_parallel_search_uses_manager_chain_first(monkeypatch):
    """fast 扇出：配置链（Tavily→Brave→DDGS）优先，成功就不碰 ddgs 区域兜底。"""
    calls = []

    async def _fake_search(self, query, count):
        calls.append("manager")
        return SearchResult(True, [
            {"title": "T1", "url": "https://example.com/a", "snippet": "s1"},
        ])

    monkeypatch.setattr(SearchProviderManager, "search", _fake_search)

    async def _boom(query, n_queries, n):
        raise AssertionError("ddgs fallback must not run when chain succeeds")

    monkeypatch.setattr(
        "miqi.agent.search_orchestrator._ddgs_regional_search", _boom
    )
    tool = WebSearchTool(provider="auto")
    blocks = await tool._parallel_search("hello", n_queries=2, n=5)
    assert calls == ["manager"]
    assert len(blocks) == 1
    assert blocks[0].startswith("Results for: hello")
    assert "https://example.com/a" in blocks[0]


async def test_parallel_search_falls_back_to_regional_ddgs(monkeypatch):
    """fast 扇出：配置链失败/空结果 → 回退 ddgs 区域变体。"""
    calls = []

    async def _fake_search(self, query, count):
        calls.append("manager")
        return SearchResult(False, error_type="NETWORK", provider="ddgs")

    monkeypatch.setattr(SearchProviderManager, "search", _fake_search)

    async def _fake_ddgs(query, n_queries, n):
        calls.append("ddgs")
        return ["Results for: hello (region: 全球)\n- T\n  https://example.com/x\n  s"]

    monkeypatch.setattr(
        "miqi.agent.search_orchestrator._ddgs_regional_search", _fake_ddgs
    )
    tool = WebSearchTool(provider="auto")
    blocks = await tool._parallel_search("hello", n_queries=2, n=5)
    assert calls == ["manager", "ddgs"]
    assert blocks and "https://example.com/x" in blocks[0]


async def test_parallel_search_falls_back_on_empty_chain_result(monkeypatch):
    """fast 扇出：配置链成功但空结果 → 也回退 ddgs（否则空结果直接丢失）。"""

    async def _fake_search(self, query, count):
        return SearchResult(True, error_type="NO_RESULT")

    monkeypatch.setattr(SearchProviderManager, "search", _fake_search)

    async def _fake_ddgs(query, n_queries, n):
        return ["fallback block"]

    monkeypatch.setattr(
        "miqi.agent.search_orchestrator._ddgs_regional_search", _fake_ddgs
    )
    tool = WebSearchTool(provider="auto")
    blocks = await tool._parallel_search("hello", n_queries=1, n=5)
    assert blocks == ["fallback block"]


async def test_parallel_search_all_down_exposes_reason(monkeypatch):
    """fast 扇出：配置链 + ddgs 全失败 → 透出失败原因，不让模型误读为\"无结果\"。"""

    async def _fake_search(self, query, count):
        return SearchResult(False, error_type="NO_KEY", provider="tavily")

    monkeypatch.setattr(SearchProviderManager, "search", _fake_search)

    async def _empty_ddgs(query, n_queries, n):
        return []

    monkeypatch.setattr(
        "miqi.agent.search_orchestrator._ddgs_regional_search", _empty_ddgs
    )
    tool = WebSearchTool(provider="auto")
    blocks = await tool._parallel_search("hello", n_queries=2, n=5)
    assert len(blocks) == 1
    assert "未配置 Tavily API key，请在设置中填写" in blocks[0]


# ── DeepSeek 官方联网搜索（零配置，复用 LLM key）───────────────────────


def _ds_response(text: str) -> dict:
    """构造 DeepSeek /responses 的 output 结构。"""
    return {
        "output": [
            {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "..."}]},
            {"type": "web_search_call", "status": "completed",
             "action": {"type": "search", "queries": ["q1"]}},
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ]
    }


async def test_deepseek_extracts_output_text(monkeypatch):
    calls = []

    async def _fake_post(self, url, **kwargs):
        calls.append((url, kwargs))

        class _R:
            status_code = 200

            def json(self):
                return _ds_response("根据工信部数据，2025年AI核心产业规模超1.2万亿元。")

            def raise_for_status(self):
                return None

        return _R()

    monkeypatch.setattr("miqi.agent.tools.web.httpx.AsyncClient.post", _fake_post)
    result = await DeepSeekSearchProvider("k", api_base="https://api.deepseek.com/v1").search("q", 5)
    assert result.success
    assert result.provider == "deepseek"
    assert result.results and "1.2万亿" in result.results[0]["snippet"]
    # 请求体校验：base 带 /v1（配置自动填充）→ 规范化到文档化 /responses 路径（外部审阅 #844）
    assert calls and calls[0][0] == "https://api.deepseek.com/responses"
    body = calls[0][1]["json"]
    assert body["tools"][0]["type"] == "web_search"
    assert body["tools"][0]["web_search"]["context_size"] == "medium"
    assert body["tool_choice"] == {"type": "web_search"}


async def test_deepseek_401_classified_auth(monkeypatch):
    async def _fake_post(self, url, **kwargs):
        class _R:
            status_code = 401

            def json(self):
                return {}

            def raise_for_status(self):
                return None

        return _R()

    monkeypatch.setattr("miqi.agent.tools.web.httpx.AsyncClient.post", _fake_post)
    result = await DeepSeekSearchProvider("bad").search("q", 5)
    assert not result.success and result.error_type == "AUTH_ERROR"


async def test_deepseek_402_classified_balance(monkeypatch):
    """402 余额不足 → BALANCE_ERROR 专属分类透出（外部审阅 #844）。"""

    async def _fake_post(self, url, **kwargs):
        class _R:
            status_code = 402

            def json(self):
                return {}

            def raise_for_status(self):
                return None

        return _R()

    monkeypatch.setattr("miqi.agent.tools.web.httpx.AsyncClient.post", _fake_post)
    result = await DeepSeekSearchProvider("k").search("q", 5)
    assert not result.success and result.error_type == "BALANCE_ERROR"


async def test_deepseek_balance_error_exposes_message(monkeypatch):
    """余额不足透出可操作文案（不是笼统的'服务端错误'）。"""

    async def _fake_post(self, url, **kwargs):
        class _R:
            status_code = 402

            def json(self):
                return {}

            def raise_for_status(self):
                return None

        return _R()

    monkeypatch.setattr("miqi.agent.tools.web.httpx.AsyncClient.post", _fake_post)
    tool = WebSearchTool(provider="deepseek", model="deepseek/deepseek-v4-flash",
                         deepseek_api_key="k", deepseek_api_base="https://api.deepseek.com")
    out = await tool.execute("hello")
    assert "余额不足" in out
    assert "服务端错误" not in out


async def test_deepseek_429_classified_rate_limit(monkeypatch):
    async def _fake_post(self, url, **kwargs):
        class _R:
            status_code = 429

            def json(self):
                return {}

            def raise_for_status(self):
                return None

        return _R()

    monkeypatch.setattr("miqi.agent.tools.web.httpx.AsyncClient.post", _fake_post)
    result = await DeepSeekSearchProvider("k").search("q", 5)
    assert not result.success and result.error_type == "RATE_LIMIT"


async def test_deepseek_missing_key_is_no_key():
    result = await DeepSeekSearchProvider("").search("q", 5)
    assert not result.success and result.error_type == "NO_KEY"


async def test_deepseek_resolve_model():
    """请求模型跟随 v4 用户模型（去前缀）；legacy/未知用 flash 兜底（CodeRabbit #844）。"""
    assert DeepSeekSearchProvider("k", model="deepseek/deepseek-v4-flash")._resolve_model() == "deepseek-v4-flash"
    assert DeepSeekSearchProvider("k", model="deepseek/deepseek-v4-pro")._resolve_model() == "deepseek-v4-pro"
    assert DeepSeekSearchProvider("k", model="deepseek-v4-flash")._resolve_model() == "deepseek-v4-flash"
    # legacy 模型实测只返回 web_search_call 无总结文本 → flash 兜底
    assert DeepSeekSearchProvider("k", model="deepseek/deepseek-chat")._resolve_model() == "deepseek-v4-flash"
    assert DeepSeekSearchProvider("k", model="deepseek-reasoner")._resolve_model() == "deepseek-v4-flash"
    # 非 deepseek 模型（显式选 deepseek 搜索时）→ flash 兜底
    assert DeepSeekSearchProvider("k", model="openai/gpt-4o")._resolve_model() == "deepseek-v4-flash"
    assert DeepSeekSearchProvider("k", model="")._resolve_model() == "deepseek-v4-flash"


async def test_deepseek_non_official_base_unsupported():
    """中转站/腾讯云 base 没有 /responses 端点 → UNSUPPORTED 透出。"""
    result = await DeepSeekSearchProvider("k", api_base="https://api.tencent.com/v1").search("q", 5)
    assert not result.success and result.error_type == "UNSUPPORTED"


async def test_deepseek_http_base_rejected():
    """http:// 明文 base 拒绝（bearer token 不泄露明文，CodeRabbit #844）。"""
    result = await DeepSeekSearchProvider("k", api_base="http://api.deepseek.com").search("q", 5)
    assert not result.success and result.error_type == "UNSUPPORTED"


async def test_deepseek_failed_status_is_error(monkeypatch):
    """status != completed → 失败（走 fallback，CodeRabbit #844）。"""

    async def _fake_post(self, url, **kwargs):
        class _R:
            status_code = 200

            def json(self):
                return {"status": "failed", "output": []}

            def raise_for_status(self):
                return None

        return _R()

    monkeypatch.setattr("miqi.agent.tools.web.httpx.AsyncClient.post", _fake_post)
    result = await DeepSeekSearchProvider("k").search("q", 5)
    assert not result.success and result.error_type == "SERVER_ERROR"


async def test_deepseek_incomplete_status_is_error(monkeypatch):
    async def _fake_post(self, url, **kwargs):
        class _R:
            status_code = 200

            def json(self):
                return {"status": "incomplete", "output": []}

            def raise_for_status(self):
                return None

        return _R()

    monkeypatch.setattr("miqi.agent.tools.web.httpx.AsyncClient.post", _fake_post)
    result = await DeepSeekSearchProvider("k").search("q", 5)
    assert not result.success and result.error_type == "SERVER_ERROR"


async def test_auto_deepseek_failed_status_falls_through(monkeypatch):
    """auto 链：DeepSeek status=failed → 回落 ddgs 成功（fallback 链兜底）。"""
    calls = []

    class _DS(DeepSeekSearchProvider):
        async def search(self, query, count):
            calls.append("deepseek")
            return SearchResult(False, error_type="SERVER_ERROR", provider="deepseek")

    manager = SearchProviderManager(
        "auto", model="deepseek/deepseek-v4-flash",
        deepseek_api_key="ds-key",
        deepseek_api_base="https://api.deepseek.com",
    )
    manager._chain = lambda: [_DS("ds-key"), DDGSProvider()]

    async def _ok(self, query, count):
        return SearchResult(True, [{"title": "ok", "url": "https://example.com", "snippet": "s"}])

    monkeypatch.setattr(DDGSProvider, "search", _ok)
    result = await manager.search("q", 5)
    assert result.success and calls == ["deepseek"]


async def test_auto_chain_deepseek_first():
    """auto 链：DeepSeek 模型 + 官方 base + key → DeepSeek 优先（对应模型搜索）。"""
    manager = SearchProviderManager(
        "auto", model="deepseek/deepseek-v4-flash",
        deepseek_api_key="ds-key",
        deepseek_api_base="https://api.deepseek.com",
        tavily_api_key="t", brave_api_key="b",
    )
    assert [p.name for p in manager._chain()] == ["deepseek", "tavily", "brave", "ddgs"]


async def test_auto_chain_model_provider_dynamic():
    """model_provider 回调动态生效：配置换模型即时反映到链（外部审阅 #844）。"""
    holder = {"model": "deepseek/deepseek-v4-flash"}
    manager = SearchProviderManager(
        "auto", model_provider=lambda: holder["model"],
        deepseek_api_key="ds-key",
        deepseek_api_base="https://api.deepseek.com",
    )
    assert [p.name for p in manager._chain()] == ["deepseek", "ddgs"]
    # 配置改模型后，下次链构建立即更新（不冻结）
    holder["model"] = "openai/gpt-4o"
    assert [p.name for p in manager._chain()] == ["ddgs"]


async def test_auto_chain_tavily_first_without_deepseek_model():
    """auto 链：非 DeepSeek 模型 → 不用 DeepSeek 搜索，配了 key 的 Tavily 优先。"""
    manager = SearchProviderManager(
        "auto", model="openai/gpt-4o",
        deepseek_api_key="ds-key",
        deepseek_api_base="https://api.deepseek.com",
        tavily_api_key="t", brave_api_key="b",
    )
    assert [p.name for p in manager._chain()] == ["tavily", "brave", "ddgs"]


async def test_auto_chain_ddgs_only_for_other_model_without_keys():
    """auto 链：非 DeepSeek 模型 + 无第三方 key → DDGS 兜底（DeepSeek 搜索不启用）。"""
    manager = SearchProviderManager(
        "auto", model="openai/gpt-4o",
        deepseek_api_key="ds-key",
        deepseek_api_base="https://api.deepseek.com",
    )
    assert [p.name for p in manager._chain()] == ["ddgs"]


async def test_auto_chain_skips_deepseek_for_non_official_base():
    """auto 链：中转站 base → 跳过 DeepSeek（避免无谓的 /responses 404）。"""
    manager = SearchProviderManager(
        "auto", model="deepseek/deepseek-v4-flash",
        deepseek_api_key="ds-key",
        deepseek_api_base="https://api.tencent.com/v1",
    )
    assert [p.name for p in manager._chain()] == ["ddgs"]


async def test_auto_deepseek_falls_through(monkeypatch):
    """auto 链：DeepSeek 失败 → 回落 ddgs 成功。"""
    calls = []

    class _DS(DeepSeekSearchProvider):
        async def search(self, query, count):
            calls.append("deepseek")
            return SearchResult(False, error_type="NETWORK", provider="deepseek")

    manager = SearchProviderManager(
        "auto", model="deepseek/deepseek-v4-flash",
        deepseek_api_key="ds-key",
        deepseek_api_base="https://api.deepseek.com",
    )
    manager._chain = lambda: [_DS("ds-key"), DDGSProvider()]

    async def _ok(self, query, count):
        return SearchResult(True, [{"title": "ok", "url": "https://example.com", "snippet": "s"}])

    monkeypatch.setattr(DDGSProvider, "search", _ok)
    result = await manager.search("q", 5)
    assert result.success and calls == ["deepseek"]


async def test_auto_deepseek_balance_error_falls_through(monkeypatch, caplog):
    """auto 链：DeepSeek 余额不足（402）→ 自动切换兜底，不中断链（#979）。

    DeepSeek 配置了 key 但账户余额耗尽时，搜索功能仍应可用——回落 ddgs。
    """
    calls = []

    class _DS(DeepSeekSearchProvider):
        async def search(self, query, count):
            calls.append("deepseek")
            return SearchResult(False, error_type="BALANCE_ERROR", provider="deepseek")

    manager = SearchProviderManager(
        "auto", model="deepseek/deepseek-v4-flash",
        deepseek_api_key="ds-key",
        deepseek_api_base="https://api.deepseek.com",
    )
    manager._chain = lambda: [_DS("ds-key"), DDGSProvider()]

    async def _ok(self, query, count):
        return SearchResult(True, [{"title": "ok", "url": "https://example.com", "snippet": "s"}])

    monkeypatch.setattr(DDGSProvider, "search", _ok)
    result = await manager.search("q", 5)
    assert result.success
    assert calls == ["deepseek"]
    assert any("trying next provider" in r.message for r in caplog.records)


async def test_execute_unsupported_exposes_message(monkeypatch):
    """显式 deepseek + 非官方 base → 透出\"不支持联网搜索\"（可操作文案）。"""
    tool = WebSearchTool(provider="deepseek", deepseek_api_key="k",
                         deepseek_api_base="https://api.tencent.com/v1")
    out = await tool.execute("hello")
    assert "不支持联网搜索" in out
    assert "DeepSeek" in out


async def test_parallel_search_explicit_deepseek_no_ddgs_fallback(monkeypatch):
    """显式 deepseek 失败 → fast 扇出不回退 ddgs（尊重显式语义，外部审阅 #844）。"""
    called = {"ddgs": False}

    async def _fake_search(self, query, count):
        return SearchResult(False, error_type="NETWORK", provider="deepseek")

    monkeypatch.setattr(SearchProviderManager, "search", _fake_search)

    async def _mark_ddgs(query, n_queries, n):
        called["ddgs"] = True
        return [{"title": "ddgs", "snippet": "x"}]

    monkeypatch.setattr(
        "miqi.agent.search_orchestrator._ddgs_regional_search", _mark_ddgs
    )
    tool = WebSearchTool(provider="deepseek", model="deepseek/deepseek-v4-flash",
                         deepseek_api_key="k", deepseek_api_base="https://api.deepseek.com")
    blocks = await tool._parallel_search("hello", n_queries=2, n=5)
    assert not called["ddgs"], "显式模式不得回退 ddgs"
    assert len(blocks) == 1
    assert "网络连接失败" in blocks[0]


async def test_parallel_search_auto_still_falls_back(monkeypatch):
    """auto 模式失败 → fast 扇出仍回退 ddgs（兜底设计保持不变）。"""
    async def _fake_search(self, query, count):
        return SearchResult(False, error_type="NETWORK", provider="deepseek")

    monkeypatch.setattr(SearchProviderManager, "search", _fake_search)

    async def _ok_ddgs(query, n_queries, n):
        return ["ddgs结果块"]

    monkeypatch.setattr(
        "miqi.agent.search_orchestrator._ddgs_regional_search", _ok_ddgs
    )
    tool = WebSearchTool(provider="auto", model="deepseek/deepseek-v4-flash",
                         deepseek_api_key="k", deepseek_api_base="https://api.deepseek.com")
    blocks = await tool._parallel_search("hello", n_queries=2, n=5)
    assert len(blocks) == 1 and "ddgs结果" in blocks[0]
