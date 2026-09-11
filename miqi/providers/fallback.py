"""Provider fallback chain, inspired by Hermes Agent's run_agent.py.

When the primary provider fails (network error, rate limit, auth error),
automatically tries the next provider in a configured fallback chain.
After a successful fallback, the system can optionally restore the primary.

Configuration (in config.yaml)::

    agents:
      defaults:
        model: anthropic/claude-opus-4-5
      fallback_chain:
        - model: openai/gpt-4o
        - model: openrouter/anthropic/claude-opus-4-5

Usage::

    chain = ProviderFallbackChain(providers_cfg, fallback_cfg)
    response = await chain.chat_with_fallback(messages, tools, ...)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from miqi.config.schema import Config
    from miqi.providers.base import LLMProvider, LLMResponse


# HTTP error codes that indicate provider-level failure (trigger fallback)
_FALLBACK_STATUS_CODES: frozenset[int] = frozenset({
    401,   # Unauthorized (bad key, expired)
    403,   # Forbidden
    429,   # Rate limit exceeded
    500,   # Internal server error
    502,   # Bad gateway
    503,   # Service unavailable
    529,   # Overloaded (Anthropic-specific)
})


def _is_retriable_error(exc: Exception) -> bool:
    """Return True if the exception warrants trying the next provider."""
    msg = str(exc).lower()
    retriable_keywords = (
        "401", "403", "429", "500", "502", "503", "529",
        "unauthorized", "rate limit", "overloaded",
        "service unavailable", "bad gateway",
        "connection", "timeout", "timed out",
    )
    return any(kw in msg for kw in retriable_keywords)


class ProviderFallbackChain:
    """Wraps a primary provider with an ordered fallback chain.

    Tries each provider in sequence on retriable errors.
    After a successful response, records which provider succeeded.
    """

    def __init__(
        self,
        primary_provider: "LLMProvider",
        primary_model: str,
        fallback_entries: list[dict[str, Any]] | None = None,
        config: "Config | None" = None,
    ):
        """
        Args:
            primary_provider: The primary LLMProvider instance.
            primary_model: Primary model identifier string.
            fallback_entries: List of dicts with keys:
                - model (required): e.g. "openai/gpt-4o"
                - (future: custom api_key, api_base overrides)
            config: Root Config object — used to build fallback providers.
        """
        self._primary = primary_provider
        self._primary_model = primary_model
        self._fallback_entries = fallback_entries or []
        self._config = config
        self._fallback_providers: list[tuple[str, "LLMProvider"]] = []
        self._active_index: int = -1   # -1 = primary is active

        if fallback_entries and config:
            self._build_fallback_providers()

    def _build_fallback_providers(self) -> None:
        """Construct LLMProvider instances for each fallback entry."""
        if not self._config:
            return
        for entry in self._fallback_entries:
            model = entry.get("model", "")
            if not model:
                continue
            try:
                provider = self._config.build_provider(model)
                if provider:
                    self._fallback_providers.append((model, provider))
                    logger.debug("Fallback chain: added {} ({})", model, type(provider).__name__)
            except Exception as exc:
                logger.warning("Fallback chain: failed to build provider for {}: {}", model, exc)

    @property
    def active_provider(self) -> "LLMProvider":
        if self._active_index < 0 or not self._fallback_providers:
            return self._primary
        return self._fallback_providers[self._active_index][1]

    @property
    def active_model(self) -> str:
        if self._active_index < 0 or not self._fallback_providers:
            return self._primary_model
        return self._fallback_providers[self._active_index][0]

    def _try_next_fallback(self) -> bool:
        """Advance to the next fallback provider. Returns False if exhausted."""
        next_idx = self._active_index + 1
        if next_idx >= len(self._fallback_providers):
            return False
        self._active_index = next_idx
        model, prov = self._fallback_providers[next_idx]
        logger.warning(
            "Provider fallback: switching to {} ({})",
            model, type(prov).__name__,
        )
        return True

    def restore_primary(self) -> None:
        """Reset to the primary provider after a successful fallback."""
        if self._active_index >= 0:
            logger.info("Provider fallback: restoring primary ({})", self._primary_model)
            self._active_index = -1

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> "LLMResponse":
        """Send a chat request, falling back on retriable errors.

        Uses the currently active provider. On retriable failure, advances
        to the next fallback provider and retries once.
        """
        effective_model = model or self.active_model
        last_exc: Exception | None = None

        # Try current active + all remaining fallbacks
        while True:
            provider = self.active_provider
            try:
                response = await provider.chat(
                    messages=messages,
                    tools=tools,
                    model=effective_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                # Success — if we were on a fallback, try to restore primary next time
                return response

            except Exception as exc:
                last_exc = exc
                if not _is_retriable_error(exc):
                    raise

                logger.warning(
                    "Provider {} failed ({}); trying fallback…",
                    type(provider).__name__, str(exc)[:120],
                )
                advanced = self._try_next_fallback()
                if not advanced:
                    logger.error("All providers in fallback chain exhausted")
                    raise

                # Use the new fallback's model string
                effective_model = self.active_model

        # Should never reach here
        if last_exc:
            raise last_exc
