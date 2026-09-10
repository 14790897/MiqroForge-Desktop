"""Tests for platform gateway web search."""

import pytest

from miqi.agent.tools.gateway_web import GatewaySearchProvider, GatewaySearchProviderManager
from miqi.agent.tools.web import SearchResult, TavilyProvider


async def test_gateway_search_success(monkeypatch):
    calls = []

    async def _fake_post(self, url, **kwargs):
        calls.append((url, kwargs))

        class _Response:
            status_code = 200

            def json(self):
                return {
                    "results": [
                        {
                            "title": "官方结果",
                            "url": "https://example.com/article",
                            "snippet": "摘要",
                        }
                    ]
                }

            def raise_for_status(self):
                return None

        return _Response()

    monkeypatch.setattr("miqi.agent.tools.gateway_web.httpx.AsyncClient.post", _fake_post)
    provider = GatewaySearchProvider("gateway-secret", "https://gateway.example.com")
    result = await provider.search("MiQroForge", 5)

    assert result.success
    assert result.provider == "gateway"
    assert result.results == [
        {
            "title": "官方结果",
            "url": "https://example.com/article",
            "snippet": "摘要",
        }
    ]
    assert calls[0][0] == "https://gateway.example.com/miqroera-deepseek/v1/web_search"
    assert calls[0][1]["headers"]["X-Api-Key"] == "gateway-secret"
    assert calls[0][1]["json"] == {"query": "MiQroForge", "max_results": 5}


@pytest.mark.asyncio
async def test_gateway_search_404_is_unsupported(monkeypatch):
    async def _fake_post(self, url, **kwargs):
        class _Response:
            status_code = 404

            def raise_for_status(self):
                return None

        return _Response()

    monkeypatch.setattr("miqi.agent.tools.gateway_web.httpx.AsyncClient.post", _fake_post)
    result = await GatewaySearchProvider("k", "https://gateway.example.com").search("q", 5)
    assert not result.success and result.error_type == "UNSUPPORTED"


@pytest.mark.asyncio
async def test_gateway_search_http_origin_fails_closed():
    result = await GatewaySearchProvider("k", "http://gateway.example.com").search("q", 5)
    assert not result.success and result.error_type == "UNSUPPORTED"


@pytest.mark.asyncio
async def test_gateway_search_401_is_auth_error(monkeypatch):
    async def _fake_post(self, url, **kwargs):
        class _Response:
            status_code = 401

            def raise_for_status(self):
                return None

        return _Response()

    monkeypatch.setattr("miqi.agent.tools.gateway_web.httpx.AsyncClient.post", _fake_post)
    result = await GatewaySearchProvider("k", "https://gateway.example.com").search("q", 5)
    assert not result.success and result.error_type == "AUTH_ERROR"


def test_gateway_manager_is_first_in_auto_chain():
    manager = GatewaySearchProviderManager(
        "auto",
        gateway_api_key="gateway-key",
        gateway_origin="https://gateway.example.com",
        tavily_api_key="tavily-key",
        brave_api_key="brave-key",
        deepseek_api_key="deepseek-key",
        deepseek_api_base="https://api.deepseek.com",
        model="deepseek/deepseek-v4-flash",
    )
    assert [p.name for p in manager._chain()] == [
        "gateway", "deepseek", "tavily", "brave", "ddgs"
    ]


def test_gateway_manager_skips_insecure_or_missing_gateway():
    manager = GatewaySearchProviderManager(
        "auto", gateway_api_key="gateway-key", gateway_origin="http://gateway.example.com"
    )
    assert [p.name for p in manager._chain()] == ["ddgs"]

    manager = GatewaySearchProviderManager(
        "auto", gateway_origin="https://gateway.example.com"
    )
    assert [p.name for p in manager._chain()] == ["ddgs"]


@pytest.mark.asyncio
async def test_gateway_unsupported_falls_back_to_existing_chain(monkeypatch):
    manager = GatewaySearchProviderManager(
        "auto",
        gateway_api_key="gateway-key",
        gateway_origin="https://gateway.example.com",
        tavily_api_key="tavily-key",
    )

    async def _gateway_unsupported(self, query, count):
        return SearchResult(False, error_type="UNSUPPORTED", provider="gateway")

    async def _tavily_ok(self, query, count):
        return SearchResult(
            True,
            [{"title": "T", "url": "https://example.com", "snippet": "S"}],
            provider="tavily",
        )

    monkeypatch.setattr(GatewaySearchProvider, "search", _gateway_unsupported)
    monkeypatch.setattr(TavilyProvider, "search", _tavily_ok)
    result = await manager.search("q", 5)

    assert result.success and result.provider == "tavily"


def test_gateway_explicit_provider_semantics_are_unchanged():
    manager = GatewaySearchProviderManager(
        "tavily",
        gateway_api_key="gateway-key",
        gateway_origin="https://gateway.example.com",
        tavily_api_key="tavily-key",
    )
    assert [p.name for p in manager._chain()] == ["tavily"]
