"""Platform gateway web-search provider.

The platform AI gateway already owns authentication and model routing. This
module adds the corresponding search capability as a separate provider, using
an explicit ``POST <gateway>/v1/web_search`` contract rather than probing the
LLM ``/responses`` endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from miqi.agent.tools.web import SearchProvider, SearchProviderManager, SearchResult, WebSearchTool

_log = logging.getLogger(__name__)


class GatewaySearchProvider(SearchProvider):
    """Search through the Qraft platform gateway."""

    name = "gateway"

    def __init__(self, api_key: str, origin: str, prefix: str = "/miqroera-deepseek"):
        self.api_key = api_key
        self.origin = (origin or "").rstrip("/")
        self.prefix = "/" + prefix.strip("/")

    @property
    def endpoint(self) -> str:
        return f"{self.origin}{self.prefix}/v1/web_search"

    @property
    def secure(self) -> bool:
        try:
            return urlparse(self.origin).scheme == "https"
        except ValueError:
            return False

    async def search(self, query: str, count: int) -> SearchResult:
        if not self.api_key:
            return SearchResult(False, error_type="NO_KEY", provider=self.name)
        if not self.secure:
            return SearchResult(False, error_type="UNSUPPORTED", provider=self.name)

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=8.0, write=8.0, pool=8.0)
            ) as client:
                response = await client.post(
                    self.endpoint,
                    json={"query": query, "max_results": count},
                    headers={
                        "X-Api-Key": self.api_key,
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )

            if response.status_code == 429:
                return SearchResult(False, error_type="RATE_LIMIT", provider=self.name)
            if response.status_code in (401, 403):
                return SearchResult(False, error_type="AUTH_ERROR", provider=self.name)
            if response.status_code in (404, 405):
                # Endpoint not deployed / contract not enabled yet.
                return SearchResult(False, error_type="UNSUPPORTED", provider=self.name)
            if response.status_code >= 500:
                return SearchResult(False, error_type="SERVER_ERROR", provider=self.name)
            response.raise_for_status()

            payload = response.json()
            items = payload.get("results", [])
            if not isinstance(items, list):
                return SearchResult(False, error_type="SERVER_ERROR", provider=self.name)

            results: list[dict[str, str]] = []
            for item in items[:count]:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "")
                if not url:
                    continue
                results.append({
                    "title": str(item.get("title") or ""),
                    "url": url,
                    "snippet": str(item.get("snippet") or item.get("content") or ""),
                })

            if not results:
                return SearchResult(True, error_type="NO_RESULT", provider=self.name)
            return SearchResult(True, results, provider=self.name)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return SearchResult(False, error_type="NETWORK", provider=self.name)
        except httpx.HTTPStatusError:
            return SearchResult(False, error_type="SERVER_ERROR", provider=self.name)
        except Exception as exc:  # noqa: BLE001 - provider boundary must fallback
            _log.warning("gateway web_search failed: %s", type(exc).__name__)
            return SearchResult(False, error_type="NETWORK", provider=self.name)


class GatewaySearchProviderManager(SearchProviderManager):
    """Existing search manager with gateway prepended in auto mode."""

    def __init__(
        self,
        *args: Any,
        gateway_api_key: str = "",
        gateway_origin: str = "",
        gateway_prefix: str = "/miqroera-deepseek",
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.gateway_api_key = gateway_api_key
        self.gateway_origin = gateway_origin
        self.gateway_prefix = gateway_prefix

    def _gateway_provider(self) -> GatewaySearchProvider | None:
        provider = GatewaySearchProvider(
            self.gateway_api_key,
            self.gateway_origin,
            self.gateway_prefix,
        )
        if not self.gateway_api_key or not provider.secure:
            return None
        return provider

    def _chain(self) -> list[SearchProvider]:
        chain = super()._chain()
        if self.provider == "auto":
            gateway = self._gateway_provider()
            if gateway is not None:
                chain.insert(0, gateway)
        return chain

    async def search(self, query: str, count: int) -> SearchResult:
        # Keep the established fallback semantics, with one extra case:
        # a missing gateway endpoint is a capability miss and must not stop
        # the existing auto chain.
        return await _search_with_gateway_fallback(self, query, count)


async def _search_with_gateway_fallback(
    manager: GatewaySearchProviderManager,
    query: str,
    count: int,
) -> SearchResult:
    chain = manager._chain()
    last_auth_warned = False
    last_failure: SearchResult | None = None
    for provider in chain:
        result = await provider.search(query, count)
        if result.success:
            return result
        if not result.provider:
            result.provider = provider.name
        last_failure = result
        if result.error_type == "UNSUPPORTED" and provider.name == "gateway":
            _log.info("web_search: platform gateway search unavailable, falling back")
            continue
        if result.error_type in {"RATE_LIMIT", "NETWORK", "SERVER_ERROR", "NO_RESULT", "BALANCE_ERROR"}:
            _log.warning(
                "web_search: %s failed (%s), trying next provider",
                provider.name,
                result.error_type,
            )
            continue
        if result.error_type in {"AUTH_ERROR", "NO_KEY"}:
            if not last_auth_warned:
                _log.warning(
                    "web_search: %s authentication failed — check credentials",
                    provider.name,
                )
                last_auth_warned = True
            continue
        return result
    if last_failure is not None:
        return SearchResult(
            False,
            error_type=last_failure.error_type or "SERVER_ERROR",
            provider=last_failure.provider,
        )
    return SearchResult(False, error_type="SERVER_ERROR")


class GatewayWebSearchTool(WebSearchTool):
    """Drop-in WebSearchTool using the platform gateway as auto-chain priority."""

    def __init__(
        self,
        gateway_api_key: str | None = None,
        gateway_origin: str | None = None,
        gateway_prefix: str = "/miqroera-deepseek",
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.manager = GatewaySearchProviderManager(
            self.manager.provider,
            model=self.manager.model,
            model_provider=self.manager._model_provider,
            tavily_api_key=self.manager.tavily_api_key,
            brave_api_key=self.manager.brave_api_key,
            deepseek_api_key=self.manager.deepseek_api_key,
            deepseek_api_base=self.manager.deepseek_api_base,
            gateway_api_key=gateway_api_key or "",
            gateway_origin=gateway_origin or "",
            gateway_prefix=gateway_prefix,
        )
