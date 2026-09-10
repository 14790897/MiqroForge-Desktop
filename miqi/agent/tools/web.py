"""Web tools: web_search and web_fetch."""

import asyncio
import html
import ipaddress
import json
import logging
import os
import re
import socket
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from miqi.agent.tools.base import Tool

# Suppress noisy readability tracebacks for empty/broken pages
logging.getLogger("readability.readability").setLevel(logging.WARNING)

# Shared constants
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5  # Limit redirects to prevent DoS attacks


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# Private / reserved IP networks that must never be fetched (SSRF protection).
_PRIVATE_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("10.0.0.0/8"),        # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),     # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),    # RFC 1918
    ipaddress.ip_network("169.254.0.0/16"),    # Link-local / cloud metadata (AWS/GCP/Azure)
    ipaddress.ip_network("100.64.0.0/10"),     # Shared address space (RFC 6598)
    ipaddress.ip_network("0.0.0.0/8"),         # "This" network
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
]

# Hostnames that must never be fetched regardless of resolved IP.
_BLOCKED_HOSTNAMES: frozenset[str] = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata.aws.com",
})


def _is_private_host(host: str) -> bool:
    """Return True if *host* is a private/loopback/link-local address.

    Checks literal IPs directly; for hostnames, performs a DNS look-up and
    inspects every returned address.  Blocks known metadata service hostnames
    by name even before resolution.
    """
    host_lower = host.lower()
    if host_lower in _BLOCKED_HOSTNAMES:
        return True

    # Fast path: if the host is already a numeric IP, check it directly.
    try:
        addr = ipaddress.ip_address(host)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        pass  # Not a literal IP; fall through to DNS resolution.

    # Resolve the hostname and check every returned address.
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for info in infos:
            ip_str = info[4][0].split("%")[0]  # Strip IPv6 zone ID if present.
            try:
                addr = ipaddress.ip_address(ip_str)
                if any(addr in net for net in _PRIVATE_NETWORKS):
                    return True
            except ValueError:
                continue
    except socket.gaierror:
        pass  # DNS lookup failed; let the HTTP layer report the error.

    return False


# Search-engine result/query pages that must never be fetched directly —
# scraping them yields redirect junk, and searching belongs in web_search.
_SEARCH_PAGE_HOSTS = (
    "so.com",
    "sogou.com",
    "bing.com",
    "google.com",
    "google.com.hk",
    "duckduckgo.com",
    "search.brave.com",
    "baidu.com",
)


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate URL: must be http(s) with a publicly routable destination.

    Rejects requests targeting private, loopback, or link-local addresses to
    prevent Server-Side Request Forgery (SSRF) attacks, and rejects search-
    engine query pages (the model should use web_search instead of scraping
    search result HTML).
    """
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        host = p.hostname
        if not host:
            return False, "Missing domain"
        if _is_private_host(host):
            return False, (
                f"Requests to private/reserved addresses are not allowed (host: {host})"
            )
        host_l = host.lower()
        # Block only search-query pages, never bare roots — docs.google.com/ or
        # pan.baidu.com/ are legitimate pages, not search result pages.
        is_search_host = any(
            host_l == h or host_l.endswith("." + h) for h in _SEARCH_PAGE_HOSTS
        )
        is_query_path = "/search" in p.path or p.path == "/s" or p.path == "/web"
        if is_search_host and is_query_path:
            return False, (
                "Search-engine result pages are blocked — use web_search instead "
                f"of fetching '{host}' query pages"
            )
        return True, ""
    except Exception as e:
        return False, str(e)


class SearchResult:
    """Structured internal result — the tool layer formats it back to a
    string for the model (the model-facing contract stays unchanged)."""

    __slots__ = ("success", "results", "error_type", "provider")

    def __init__(
        self,
        success: bool,
        results: list[dict[str, str]] | None = None,
        error_type: str | None = None,
        provider: str | None = None,
    ):
        self.success = success
        self.results = results or []
        # RATE_LIMIT | NETWORK | SERVER_ERROR | AUTH_ERROR | NO_KEY | NO_RESULT | None
        self.error_type = error_type
        self.provider = provider  # which provider produced this outcome (#804)


# Error categories that should trigger fallback in auto mode.
# BALANCE_ERROR（余额不足）也回落——DeepSeek 配置了 key 但账户没钱时，
# 用户仍应能得到搜索结果（#979：配置了不能搜索 → 自动切换兜底）。
_FALLBACK_ERRORS = {"RATE_LIMIT", "NETWORK", "SERVER_ERROR", "NO_RESULT", "BALANCE_ERROR"}
# Errors that must NOT silently fall back (config problems) — log, but
# degrade to the keyless provider once so the user still gets an answer.
_AUTH_ERRORS = {"AUTH_ERROR", "NO_KEY"}


class SearchProvider:
    """Uniform interface for a search backend."""

    name = "base"

    async def search(self, query: str, count: int) -> SearchResult:  # pragma: no cover - interface
        raise NotImplementedError


def _format_results(query: str, results: list[dict[str, str]]) -> str:
    """Model-facing string format (unchanged from the old tool output)."""
    lines = [f"Results for: {query}\n"]
    for i, item in enumerate(results, 1):
        title = item.get("title", "")
        url = item.get("url", "")
        # DeepSeek 搜索结果是总结文本，无来源 URL——不伪造可抓取地址（外部审阅 #844）
        lines.append(f"{i}. {title}\n   {url}" if url else f"{i}. {title}")
        if snippet := item.get("snippet"):
            lines.append(f"   {snippet}")
    return "\n".join(lines)


class DDGSProvider(SearchProvider):
    """DuckDuckGo via the ddgs library — keyless, always available."""

    name = "ddgs"

    async def search(self, query: str, count: int) -> SearchResult:
        try:
            from ddgs import DDGS
        except ImportError:
            return SearchResult(False, error_type="NETWORK")

        last_error = "UNKNOWN"

        async def _query(backend: str, timeout: float) -> list:
            """单后端查询 + 硬超时（ddgs 库自身无超时，国内网络下
            lite/auto 后端会撞 Brave/Yandex 超时 20s+ —— #854 后实测：
            html 3.1s / lite 22.4s 超时 / auto 20.0s 超时）。"""
            return await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: list(
                        DDGS().text(query, max_results=count, backend=backend)
                    )
                ),
                timeout=timeout,
            )

        # Rate limits are usually short-lived (seconds) — retry once with a
        # small backoff before giving up and falling through the chain (#561).
        for attempt in (1, 2):
            try:
                # html 优先（国内网络稳定且快）；失败或无结果再试 lite（8s 上限）
                results = []
                try:
                    results = await _query("html", 12.0)
                except Exception:
                    results = []
                if not results:
                    try:
                        results = await _query("lite", 8.0)
                    except Exception:
                        results = []
                if not results:
                    return SearchResult(True, error_type="NO_RESULT")
                out = []
                for item in results[:count]:
                    href = item.get("href") or item.get("url", "")
                    if not href:
                        continue
                    out.append({
                        "title": item.get("title", ""),
                        "url": href,
                        "snippet": item.get("body") or item.get("description", ""),
                    })
                if not out:
                    return SearchResult(True, error_type="NO_RESULT")
                return SearchResult(True, out)
            except Exception as e:
                cls = type(e).__name__
                if "Ratelimit" in cls:
                    last_error = "RATE_LIMIT"
                elif "Timeout" in cls:
                    last_error = "NETWORK"
                else:
                    last_error = "SERVER_ERROR"
                if attempt == 1:
                    await asyncio.sleep(3.0)  # short backoff before retry
        return SearchResult(False, error_type=last_error)


class BraveProvider(SearchProvider):
    """Brave Search API — requires BRAVE_API_KEY."""

    name = "brave"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def search(self, query: str, count: int) -> SearchResult:
        if not self.api_key:
            return SearchResult(False, error_type="NO_KEY", provider="brave")
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": count},
                    headers={"Accept": "application/json",
                             "X-Subscription-Token": self.api_key},
                    timeout=10.0,
                )
                if r.status_code == 429:
                    return SearchResult(False, error_type="RATE_LIMIT", provider="brave")
                if r.status_code in (401, 403):
                    return SearchResult(False, error_type="AUTH_ERROR", provider="brave")
                if r.status_code >= 500:
                    return SearchResult(False, error_type="SERVER_ERROR", provider="brave")
                r.raise_for_status()

            items = r.json().get("web", {}).get("results", [])
            out = [
                {"title": it.get("title", ""), "url": it.get("url", ""),
                 "snippet": it.get("description", "")}
                for it in items[:count] if it.get("url")
            ]
            if not out:
                return SearchResult(True, error_type="NO_RESULT", provider="brave")
            return SearchResult(True, out, provider="brave")
        except httpx.TimeoutException:
            return SearchResult(False, error_type="NETWORK", provider="brave")
        except httpx.HTTPStatusError:
            return SearchResult(False, error_type="SERVER_ERROR", provider="brave")
        except Exception:
            return SearchResult(False, error_type="NETWORK", provider="brave")


class TavilyProvider(SearchProvider):
    """Tavily Search API (https://tavily.com) — AI-friendly, structured."""

    name = "tavily"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def search(self, query: str, count: int) -> SearchResult:
        if not self.api_key:
            return SearchResult(False, error_type="NO_KEY", provider="tavily")
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://api.tavily.com/search",
                    json={"query": query, "max_results": count},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=10.0,
                )
                if r.status_code == 429:
                    return SearchResult(False, error_type="RATE_LIMIT", provider="tavily")
                if r.status_code in (401, 403):
                    return SearchResult(False, error_type="AUTH_ERROR", provider="tavily")
                if r.status_code >= 500:
                    return SearchResult(False, error_type="SERVER_ERROR", provider="tavily")
                r.raise_for_status()

            items = r.json().get("results", [])
            out = [
                {"title": it.get("title", ""), "url": it.get("url", ""),
                 "snippet": it.get("content", "")}
                for it in items[:count] if it.get("url")
            ]
            if not out:
                return SearchResult(True, error_type="NO_RESULT", provider="tavily")
            return SearchResult(True, out, provider="tavily")
        except httpx.TimeoutException:
            return SearchResult(False, error_type="NETWORK", provider="tavily")
        except httpx.HTTPStatusError:
            return SearchResult(False, error_type="SERVER_ERROR", provider="tavily")
        except Exception:
            return SearchResult(False, error_type="NETWORK", provider="tavily")


def _is_official_deepseek_base(api_base: str) -> bool:
    """DeepSeek 官方 base 判断（hostname 精确 + HTTPS 强制，避免子串/明文误判）。

    /responses 端点是官方专属——中转站/腾讯云只有 chat/completions。
    http:// 明文会泄露 bearer token，拒绝（CodeRabbit #844）。
    """
    try:
        p = urlparse(api_base or "")
    except ValueError:
        return False
    return p.scheme == "https" and p.hostname == "api.deepseek.com"


def _model_is_deepseek(model: str | None) -> bool:
    """当前对话模型是否为 DeepSeek（"对应模型的联网搜索"判定）。

    兼容 "deepseek/deepseek-v4-flash"（provider 前缀）与
    "deepseek-v4-flash"（裸模型名）两种写法。
    """
    if not model:
        return False
    m = model.strip().lower()
    return m == "deepseek" or m.startswith("deepseek/") or m.startswith("deepseek-")


class DeepSeekSearchProvider(SearchProvider):
    """DeepSeek 官方联网搜索（Responses API，复用 LLM key，零配置）。

    仅支持官方 api_base（api.deepseek.com）——中转站/腾讯云没有 /responses
    端点。模型自动拆多查询 + 打开原文核实，返回已总结文本（含来源）。
    一次搜索 ≈ 7K tokens（约 0.3 分钱），medium 档实测 ~8s。
    """

    name = "deepseek"

    def __init__(self, api_key: str, api_base: str = "https://api.deepseek.com",
                 timeout: float = 30.0, model: str | None = None):
        self.api_key = api_key
        self.api_base = (api_base or "https://api.deepseek.com").rstrip("/")
        self.timeout = timeout
        # 跟随用户模型（去 provider 前缀，如 deepseek/deepseek-chat → deepseek-chat）；
        # 非 deepseek 模型/未知时用 deepseek-v4-flash 兜底（CodeRabbit #844）
        self.model = model or ""

    def _resolve_model(self) -> str:
        m = self.model.strip()
        if m.startswith("deepseek/"):
            m = m.split("/", 1)[1]
        # v4 系列跟随用户模型；legacy（deepseek-chat/reasoner）与未知用 flash——
        # 实测 legacy + 强制 tool_choice 只返回 web_search_call 无总结文本（NO_RESULT）
        if m.startswith("deepseek-v4-"):
            return m
        return "deepseek-v4-flash"

    async def search(self, query: str, count: int) -> SearchResult:
        if not self.api_key:
            return SearchResult(False, error_type="NO_KEY", provider="deepseek")
        if not _is_official_deepseek_base(self.api_base):
            return SearchResult(False, error_type="UNSUPPORTED", provider="deepseek")
        # 实测 medium ~8s（与消费端一致）；high 35s 超过客户端超时，一律 medium
        #（外部审阅 #844：count>=8 选 high 会 30s 必超时）
        context = "medium"
        # 规范化：官方 base 可能带 /v1（配置自动填充）——统一走文档化 /responses 路径
        url = f"{self.api_base.rstrip('/').removesuffix('/v1')}/responses"
        try:
            async with asyncio.timeout(self.timeout):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout, connect=8.0, write=8.0, pool=8.0)
                ) as client:
                    r = await client.post(
                        url,
                        json={
                            "model": self._resolve_model(),
                            "input": query,
                            "tools": [{"type": "web_search", "web_search": {"context_size": context}}],
                            # 强制 web_search：防止"仅文本输出"被当成成功结果（CodeRabbit #844）
                            "tool_choice": {"type": "web_search"},
                        },
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    if r.status_code == 429:
                        return SearchResult(False, error_type="RATE_LIMIT", provider="deepseek")
                    if r.status_code in (401, 403):
                        return SearchResult(False, error_type="AUTH_ERROR", provider="deepseek")
                    if r.status_code == 402:
                        # 预付账户余额不足——专属分类，不能当"服务端错误"（外部审阅 #844）
                        return SearchResult(False, error_type="BALANCE_ERROR", provider="deepseek")
                    if r.status_code >= 500:
                        return SearchResult(False, error_type="SERVER_ERROR", provider="deepseek")
                    r.raise_for_status()

            data = r.json()
            # 仅 completed 视为成功（failed/incomplete → 走 fallback 链，CodeRabbit #844）
            if data.get("status") not in (None, "completed"):
                return SearchResult(False, error_type="SERVER_ERROR", provider="deepseek")
            text = ""
            for item in data.get("output", []):
                if item.get("type") != "message":
                    continue
                for c in item.get("content", []):
                    if c.get("type") == "output_text" and c.get("text"):
                        text = c["text"]
            if not text:
                # completed 但无输出（被拒/内容过滤等）——按失败回落，不能当"成功无结果"
                # 短路整条链（外部审阅 #844）
                return SearchResult(False, error_type="NO_RESULT", provider="deepseek")
            return SearchResult(True, [{
                "title": "DeepSeek 联网搜索（官方）",
                "url": "",  # 总结文本无来源 URL——不伪造可抓取地址（外部审阅 #844）
                "snippet": text,
            }], provider="deepseek")
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return SearchResult(False, error_type="NETWORK", provider="deepseek")
        except httpx.HTTPStatusError:
            return SearchResult(False, error_type="SERVER_ERROR", provider="deepseek")
        except Exception:
            return SearchResult(False, error_type="NETWORK", provider="deepseek")


class SearchProviderManager:
    """Orchestrates providers for the configured search strategy.

    provider=auto: Tavily(if key) → Brave(if key) → DDGS — falling through
    on RATE_LIMIT/NETWORK/SERVER_ERROR.  AUTH_ERROR degrades straight to the
    keyless provider with a warning (never silently loops).
    """

    _PROVIDERS = {
        "tavily": TavilyProvider,
        "brave": BraveProvider,
        "ddgs": DDGSProvider,
        "deepseek": DeepSeekSearchProvider,
    }

    def __init__(
        self,
        provider: str,
        *,
        model: str | None = None,
        model_provider: Callable[[], str | None] | None = None,
        tavily_api_key: str = "",
        brave_api_key: str = "",
        deepseek_api_key: str = "",
        deepseek_api_base: str = "https://api.deepseek.com",
    ):
        self.model = model
        # 动态模型解析：每次 _chain() 时重读（配置改动即时生效，外部审阅 #844）
        self._model_provider = model_provider
        self.tavily_api_key = tavily_api_key
        self.brave_api_key = brave_api_key
        self.deepseek_api_key = deepseek_api_key
        self.deepseek_api_base = (deepseek_api_base or "https://api.deepseek.com").rstrip("/")
        name = (provider or "auto").lower()
        # Old "hybrid" value means the auto fallback chain now.
        self.provider = "auto" if name in {"auto", "hybrid"} else name
        if self.provider not in self._PROVIDERS:
            self.provider = "auto"

    def _current_model(self) -> str | None:
        if self._model_provider is not None:
            return self._model_provider()
        return self.model

    def _make(self, name: str) -> SearchProvider | None:
        if name == "tavily":
            return TavilyProvider(self.tavily_api_key)
        if name == "brave":
            return BraveProvider(self.brave_api_key)
        if name == "ddgs":
            return DDGSProvider()
        if name == "deepseek":
            return DeepSeekSearchProvider(self.deepseek_api_key, self.deepseek_api_base,
                                          model=self._current_model() or "")
        return None

    def _chain(self) -> list[SearchProvider]:
        if self.provider != "auto":
            return [self._make(self.provider)]
        chain = []
        # ① 对话模型对应的官方联网搜索优先（如 DeepSeek /responses，零配置复用
        #    模型 key）；② 配了 key 的第三方搜索（Tavily/Brave，快）；
        #    ③ DDGS 兜底
        if (
            _model_is_deepseek(self._current_model())
            and self.deepseek_api_key
            and _is_official_deepseek_base(self.deepseek_api_base)
        ):
            chain.append(DeepSeekSearchProvider(
                self.deepseek_api_key, self.deepseek_api_base,
                model=self._current_model() or "",
            ))
        if self.tavily_api_key:
            chain.append(TavilyProvider(self.tavily_api_key))
        if self.brave_api_key:
            chain.append(BraveProvider(self.brave_api_key))
        chain.append(DDGSProvider())
        return chain

    async def search(self, query: str, count: int) -> SearchResult:
        chain = self._chain()
        last_auth_warned = False
        last_failure: SearchResult | None = None
        for provider in chain:
            result = await provider.search(query, count)
            if result.success:
                return result
            # Tag the failing provider for error surfacing (#804).
            if not result.provider:
                result.provider = provider.name
            last_failure = result
            if result.error_type in _FALLBACK_ERRORS:
                logging.getLogger(__name__).warning(
                    "web_search: %s failed (%s), trying next provider",
                    provider.name, result.error_type,
                )
                continue
            if result.error_type in _AUTH_ERRORS:
                if not last_auth_warned:
                    logging.getLogger(__name__).warning(
                        "web_search: %s authentication failed — check API key",
                        provider.name,
                    )
                    last_auth_warned = True
                continue  # degrade to the next (keyless) provider once
            return result  # NO_RESULT etc. — not a fallback condition
        # Whole chain exhausted — surface the last real failure instead of a
        # generic SERVER_ERROR so the model/user can act on it (#804).
        if last_failure is not None:
            return SearchResult(
                False,
                error_type=last_failure.error_type or "SERVER_ERROR",
                provider=last_failure.provider,
            )
        return SearchResult(False, error_type="SERVER_ERROR")


# Model/user-facing reason mapping for failed searches (#804: surface the
# actionable cause instead of an abstract error).
_ERROR_REASONS: dict[str, str] = {
    "NO_KEY": "未配置 {provider} API key，请在设置中填写",
    "AUTH_ERROR": "{provider} API key 无效（401/403），请在设置中检查",
    "BALANCE_ERROR": "{provider} 账户余额不足（402），请充值后重试",
    "RATE_LIMIT": "搜索服务限流（429），请稍后重试",
    "NETWORK": "网络连接失败，请检查网络后重试",
    "SERVER_ERROR": "搜索服务暂时不可用（服务端错误）",
    "UNSUPPORTED": "当前 {provider} 服务商不支持联网搜索（仅官方 api.deepseek.com 支持）",
}
_PROVIDER_LABELS: dict[str, str] = {
    "tavily": "Tavily",
    "brave": "Brave",
    "ddgs": "DuckDuckGo",
    "deepseek": "DeepSeek",
}


def _failure_message(result: SearchResult) -> str:
    """Human-readable, model-actionable failure message for web_search."""
    label = _PROVIDER_LABELS.get(result.provider or "", result.provider or "")
    template = _ERROR_REASONS.get(result.error_type or "")
    if template is None:
        reason = f"未知错误（{result.error_type or 'unknown'}）"
    else:
        reason = template.format(provider=label or "搜索")
    provider_hint = f"（服务：{label}）" if label else ""
    return (
        f"Error: 网络搜索失败{provider_hint}。原因：{reason}。"
        "请勿尝试用 web_fetch 抓取搜索引擎页面（会被拒绝且结果不可用）。"
        "直接告知用户搜索暂不可用，或建议稍后重试。"
    )


class WebSearchTool(Tool):
    """Search the web. provider=auto falls back 对应模型搜索 → Tavily → Brave → DDGS."""

    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query", "minLength": 1},
            "count": {
                "type": "integer",
                "description": "Results (1-10)",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        api_key: str | None = None,
        max_results: int = 5,
        provider: str = "auto",
        model: str | None = None,
        model_provider: Callable[[], str | None] | None = None,
        tavily_api_key: str | None = None,
        brave_api_key: str | None = None,
        deepseek_api_key: str | None = None,
        deepseek_api_base: str = "https://api.deepseek.com",
    ):
        self.manager = SearchProviderManager(
            provider,
            # 当前对话模型：决定"对应模型的联网搜索"（如 DeepSeek）；model_provider
            # 优先（每次链构建动态读取，配置换模型即时生效）
            model=model,
            model_provider=model_provider,
            # legacy api_key was the Brave key — never feed it to Tavily (#561)
            tavily_api_key=tavily_api_key or os.environ.get("TAVILY_API_KEY", ""),
            brave_api_key=brave_api_key or api_key or os.environ.get("BRAVE_API_KEY", ""),
            # DeepSeek 联网搜索复用 LLM key（零配置；仅官方 base 生效）
            deepseek_api_key=deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY", ""),
            deepseek_api_base=deepseek_api_base or "https://api.deepseek.com",
        )
        self.max_results = max_results

    async def execute(self, query: str, count: int | None = None, **kwargs: Any) -> str:
        n = min(max(count or self.max_results, 1), 10)

        # Reasoning mode (issue #680): fast mode fans out into parallel
        # queries + fetches via SearchOrchestrator; think mode = current path.
        search_strategy = kwargs.get("_search_strategy")
        if search_strategy is not None and getattr(search_strategy, "fanout_queries", 1) > 1:
            from miqi.agent.search_orchestrator import SearchOrchestrator

            orchestrator = SearchOrchestrator(search_tool=self, fetch_tool=WebFetchTool())
            return await orchestrator.run(query, search_strategy, n_results=n)

        result = await self.manager.search(query, n)
        if not result.success:
            return _failure_message(result)
        if result.error_type == "NO_RESULT":
            return f"No results for: {query}"
        return _format_results(query, result.results)

    async def _parallel_search(self, query: str, n_queries: int, n: int) -> list[str]:
        """#804: fast 模式扇出搜索先走**配置的 provider 链**（对应模型搜索 → Tavily → Brave → DDGS），
        不再被 SearchOrchestrator 直接 ddgs 绕过——用户配的 key 在 fast 模式
        同样生效（#748 的 fallback 链在默认 fast 路径下此前是死代码）。链失败
        /空结果才回退 ddgs 区域变体（原 orchestrator 内置逻辑，含 15s 超时）；
        两者都失败时透出配置链的最后失败原因，不让模型误读为"无结果"。
        显式选择引擎时（provider != auto）不回退 ddgs——尊重显式语义
        （如"仅使用 DeepSeek"），失败直接透出原因（外部审阅 #844）。
        """
        last_failure: SearchResult | None = None
        try:
            result = await self.manager.search(query, n)
            if result.success and result.results:
                return [_format_results(query, result.results)]
            last_failure = result
        except Exception:
            logging.getLogger(__name__).warning(
                "web_search parallel: manager chain failed for %r — falling back to ddgs",
                query[:60],
            )
        if self.manager.provider != "auto":
            # 显式引擎：不静默回落，透出失败原因
            if last_failure is not None and not last_failure.success:
                return [_failure_message(last_failure)]
            return []
        from miqi.agent.search_orchestrator import _ddgs_regional_search

        blocks = await _ddgs_regional_search(query, n_queries, n)
        if blocks:
            return blocks
        if last_failure is not None and not last_failure.success:
            return [_failure_message(last_failure)]
        return []


class WebFetchTool(Tool):
    """Fetch and extract content from a URL using Readability."""

    name = "web_fetch"
    description = "Fetch URL and extract readable content (HTML → markdown/text)."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch", "minLength": 1},
            "extractMode": {"type": "string", "enum": ["markdown", "text"], "default": "markdown"},
            "maxChars": {"type": "integer", "minimum": 100},
        },
        "required": ["url"],
    }

    def __init__(
        self,
        max_chars: int = 50000,
        provider: str = "builtin",
        ollama_api_key: str | None = None,
        ollama_api_base: str | None = None,
    ):
        self.max_chars = max_chars
        # Keep backward compatibility with old "miqi" value.
        self.provider = "builtin" if provider == "miqi" else provider
        self.ollama_api_key = ollama_api_key or os.environ.get(
            "OLLAMA_API_KEY", "")
        self.ollama_api_base = (
            ollama_api_base or "https://ollama.com").rstrip("/")

    async def execute(
        self,
        url: str,
        extract_mode: str = "markdown",
        max_chars: int | None = None,
        **kwargs: Any,
    ) -> str:
        extract_mode = kwargs.get("extractMode", extract_mode)
        max_chars = kwargs.get("maxChars", max_chars)

        if self.provider == "ollama":
            return await self._ollama_fetch(url, max_chars=max_chars)
        if self.provider == "hybrid":
            result = await self._builtin_fetch(url, extract_mode, max_chars)
            if not result.startswith('{"error"'):
                return result
            return await self._ollama_fetch(url, max_chars=max_chars)
        return await self._builtin_fetch(url, extract_mode, max_chars)

    async def _builtin_fetch(
        self,
        url: str,
        extract_mode: str = "markdown",
        max_chars: int | None = None,
    ) -> str:
        from readability import Document

        max_chars = max_chars or self.max_chars

        # Validate URL before fetching
        is_valid, error_msg = _validate_url(url)
        if not is_valid:
            return json.dumps({"error": f"URL validation failed: {error_msg}", "url": url}, ensure_ascii=False)

        try:
            # 手动跟随重定向，每跳都验证目标 URL（CodeRabbit #741：公共 URL
            # 可重定向到内网/元数据地址，自动 follow_redirects 会绕过 _validate_url）。
            current = url
            for _ in range(MAX_REDIRECTS + 1):
                async with httpx.AsyncClient(
                    follow_redirects=False, timeout=30.0
                ) as client:
                    r = await client.get(current, headers={"User-Agent": USER_AGENT})
                if r.status_code in (301, 302, 303, 307, 308):
                    location = r.headers.get("location")
                    if not location:
                        break
                    next_url = str(httpx.URL(current).join(location))
                    is_valid, error_msg = _validate_url(next_url)
                    if not is_valid:
                        return json.dumps(
                            {"error": f"Redirect target rejected: {error_msg}", "url": next_url},
                            ensure_ascii=False,
                        )
                    current = next_url
                    continue
                r.raise_for_status()
                break
            else:
                return json.dumps(
                    {"error": "Too many redirects", "url": url}, ensure_ascii=False
                )

            ctype = r.headers.get("content-type", "")

            # JSON
            if "application/json" in ctype:
                text, extractor = json.dumps(r.json(), indent=2, ensure_ascii=False), "json"
            # HTML
            elif "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
                raw_html = r.text.strip()
                if not raw_html:
                    text, extractor = "(empty page)", "raw"
                else:
                    doc = Document(raw_html)
                    try:
                        summary_html = doc.summary()
                    except Exception:
                        summary_html = raw_html[:max_chars]
                    content = (
                        self._to_markdown(summary_html)
                        if extract_mode == "markdown"
                        else _strip_tags(summary_html)
                    )
                    text = f"# {doc.title()}\n\n{content}" if doc.title(
                    ) else content
                    extractor = "readability"
            else:
                text, extractor = r.text, "raw"

            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]

            return json.dumps({"url": url, "finalUrl": str(r.url), "status": r.status_code,
                              "extractor": extractor, "truncated": truncated, "length": len(text), "text": text}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e), "url": url}, ensure_ascii=False)

    async def _miqi_fetch(
        self,
        url: str,
        extract_mode: str = "markdown",
        max_chars: int | None = None,
    ) -> str:
        """Backward-compatible alias for old internal method name."""
        return await self._builtin_fetch(url, extract_mode, max_chars)

    async def _ollama_fetch(self, url: str, max_chars: int | None = None) -> str:
        if not self.ollama_api_key:
            return json.dumps(
                {"error": "OLLAMA_API_KEY not configured for Ollama web_fetch", "url": url},
                ensure_ascii=False,
            )

        # SEC-11: Validate URL before forwarding to Ollama backend to prevent
        # SSRF via Ollama-as-proxy.  The same guard as _builtin_fetch applies.
        is_valid, error_msg = _validate_url(url)
        if not is_valid:
            return json.dumps(
                {"error": f"URL validation failed: {error_msg}", "url": url},
                ensure_ascii=False,
            )

        resolved_max_chars = self.max_chars if max_chars is None else max_chars
        endpoint = f"{self.ollama_api_base}/api/web_fetch"

        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    endpoint,
                    json={"url": url},
                    headers={
                        "Authorization": f"Bearer {self.ollama_api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=20.0,
                )
                r.raise_for_status()

            payload = r.json()
            content = payload.get("content", "")
            truncated = len(content) > resolved_max_chars
            if truncated:
                content = content[:resolved_max_chars]

            return json.dumps(
                {
                    "url": url,
                    "extractor": "ollama_web_fetch",
                    "title": payload.get("title", ""),
                    "links": payload.get("links", []),
                    "truncated": truncated,
                    "length": len(content),
                    "text": content,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"error": str(e), "url": url}, ensure_ascii=False)

    def _to_markdown(self, html: str) -> str:
        """Convert HTML to markdown."""
        # Convert links, headings, lists before stripping tags
        text = re.sub(
            r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
            lambda m: f"[{_strip_tags(m[2])}]({m[1]})",
            html,
            flags=re.I,
        )
        text = re.sub(
            r"<h([1-6])[^>]*>([\s\S]*?)</h\1>",
            lambda m: f"\n{'#' * int(m[1])} {_strip_tags(m[2])}\n",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"<li[^>]*>([\s\S]*?)</li>", lambda m: f"\n- {_strip_tags(m[1])}", text, flags=re.I
        )
        text = re.sub(r"</(p|div|section|article)>", "\n\n", text, flags=re.I)
        text = re.sub(r"<(br|hr)\s*/?>", "\n", text, flags=re.I)
        return _normalize(_strip_tags(text))
