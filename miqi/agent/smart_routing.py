"""Smart model routing, ported from Hermes Agent's agent/smart_model_routing.py.

Routes simple conversational turns to a cheaper/faster model while keeping
complex multi-step reasoning on the primary model.

Detection logic matches Hermes exactly:
  - Check if the turn exceeds max_chars (default 160) or max_words (default 28)
  - Check for newlines > 1 (multi-line = complex)
  - Check for backtick characters (code = complex)
  - Check for URLs
  - Check for any keyword in _COMPLEX_KEYWORDS (~30 keywords)
  If ANY check triggers → keep primary model.
  Otherwise → route to cheap model.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Keywords that indicate a complex task requiring the primary model.
# Ported verbatim from Hermes.
_COMPLEX_KEYWORDS: frozenset[str] = frozenset({
    "debug",
    "implement",
    "refactor",
    "analyze",
    "analyse",
    "create",
    "build",
    "write",
    "fix",
    "update",
    "design",
    "architect",
    "optimize",
    "optimise",
    "review",
    "improve",
    "migrate",
    "integrate",
    "generate",
    "deploy",
    "configure",
    "setup",
    "install",
    "run",
    "execute",
    "test",
    "parse",
    "convert",
    "transform",
    "summarize",
    "summarise",
})

_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")


def is_simple_turn(
    content: str,
    max_chars: int = 160,
    max_words: int = 28,
) -> bool:
    """Return True if the turn is considered simple enough for a cheap model.

    Args:
        content: The raw user message text.
        max_chars: Character threshold above which the turn is considered complex.
        max_words: Word count threshold above which the turn is considered complex.

    Returns:
        True if the message is simple (safe to use cheap model).
        False if complex (must use primary model).
    """
    if not content:
        return True

    # Length checks
    if len(content) > max_chars:
        return False
    words = content.split()
    if len(words) > max_words:
        return False

    # Multi-line → complex
    if content.count("\n") > 1:
        return False

    # Code or URLs present → complex
    if "`" in content:
        return False
    if _URL_PATTERN.search(content):
        return False

    # Keyword match
    content_lower = content.lower()
    content_words = set(re.findall(r"\b\w+\b", content_lower))
    if content_words & _COMPLEX_KEYWORDS:
        return False

    return True


class SmartModelRouter:
    """Selects a model for each turn based on message complexity.

    Config schema::

        smart_routing:
          enabled: true
          cheap_model:
            provider: openai       # provider key in ProvidersConfig
            model: gpt-4o-mini
          max_chars: 160           # max length for cheap-model route
          max_words: 28            # max words for cheap-model route

    Usage::

        router = SmartModelRouter(config)
        model, provider_override = router.resolve(content, default_model, default_provider)
    """

    def __init__(self, routing_config: Optional[dict[str, Any]] = None):
        self._cfg = routing_config or {}

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.get("enabled", False))

    @property
    def cheap_model(self) -> Optional[str]:
        """Full model string, e.g. 'openai/gpt-4o-mini'."""
        cm = self._cfg.get("cheap_model") or {}
        if isinstance(cm, dict):
            provider = cm.get("provider", "")
            model = cm.get("model", "")
            if model:
                return f"{provider}/{model}" if provider else model
        if isinstance(cm, str):
            return cm
        return None

    @property
    def max_chars(self) -> int:
        return int(self._cfg.get("max_chars", 160))

    @property
    def max_words(self) -> int:
        return int(self._cfg.get("max_words", 28))

    def resolve(self, content: str, default_model: str) -> str:
        """Return the model to use for this turn.

        Args:
            content: Latest user message content.
            default_model: Primary model string.

        Returns:
            Model string to pass to provider.chat().
        """
        if not self.enabled:
            return default_model

        cheap = self.cheap_model
        if not cheap:
            return default_model

        if is_simple_turn(content, self.max_chars, self.max_words):
            return cheap

        return default_model
