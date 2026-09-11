"""Runtime model catalog — deterministic model list over PROVIDERS + config.

Does not call network APIs. Builds model rows from a built-in catalog,
provider registry metadata, and the current configured model.
"""

from __future__ import annotations

from typing import Any

from miqi.runtime.model_protocol import ModelView, ProviderCapabilitiesView

# Built-in model entries.  Every keyed model references a provider config name.
# The current configured model is always included (visible) even if absent here.
_BUILTIN_MODELS: dict[str, dict[str, Any]] = {
    # ── Anthropic ──────────────────────────────────────────────────────
    "anthropic/claude-opus-4-5": {
        "name": "Claude Opus 4.5",
        "provider": "anthropic",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "anthropic/claude-sonnet-4-6": {
        "name": "Claude Sonnet 4.6",
        "provider": "anthropic",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "anthropic/claude-3.5-sonnet": {
        "name": "Claude 3.5 Sonnet",
        "provider": "anthropic",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "anthropic/claude-3.5-haiku": {
        "name": "Claude 3.5 Haiku",
        "provider": "anthropic",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "anthropic/claude-3-opus": {
        "name": "Claude 3 Opus",
        "provider": "anthropic",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "anthropic/claude-3-haiku": {
        "name": "Claude 3 Haiku",
        "provider": "anthropic",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "anthropic/claude-3-sonnet": {
        "name": "Claude 3 Sonnet",
        "provider": "anthropic",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "anthropic/claude-opus-4": {
        "name": "Claude Opus 4",
        "provider": "anthropic",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "anthropic/claude-sonnet-4": {
        "name": "Claude Sonnet 4",
        "provider": "anthropic",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "anthropic/claude-sonnet-4-5": {
        "name": "Claude Sonnet 4.5",
        "provider": "anthropic",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "anthropic/claude-haiku-4-5": {
        "name": "Claude Haiku 4.5",
        "provider": "anthropic",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    # ── OpenAI ─────────────────────────────────────────────────────────
    "openai/gpt-4.1": {
        "name": "GPT-4.1",
        "provider": "openai",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard", "turbo"],
        "default_service_tier": "standard",
    },
    "openai/gpt-4o": {
        "name": "GPT-4o",
        "provider": "openai",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "openai/gpt-4o-mini": {
        "name": "GPT-4o Mini",
        "provider": "openai",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "openai/gpt-4-turbo": {
        "name": "GPT-4 Turbo",
        "provider": "openai",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "openai/gpt-4": {
        "name": "GPT-4",
        "provider": "openai",
        "hidden": False,
        "supported_reasoning_efforts": ["low"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "openai/gpt-3.5-turbo": {
        "name": "GPT-3.5 Turbo",
        "provider": "openai",
        "hidden": True,
        "supported_reasoning_efforts": ["low"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "openai/o1": {
        "name": "OpenAI o1",
        "provider": "openai",
        "hidden": False,
        "supported_reasoning_efforts": ["medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "openai/o1-mini": {
        "name": "OpenAI o1 Mini",
        "provider": "openai",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "openai/o3": {
        "name": "OpenAI o3",
        "provider": "openai",
        "hidden": False,
        "supported_reasoning_efforts": ["medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "openai/o3-mini": {
        "name": "OpenAI o3 Mini",
        "provider": "openai",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "openai/o4-mini": {
        "name": "OpenAI o4 Mini",
        "provider": "openai",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    # ── DeepSeek ───────────────────────────────────────────────────────
    "deepseek/deepseek-v4-flash": {
        "name": "DeepSeek V4 Flash",
        "provider": "deepseek",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    # ── Groq ───────────────────────────────────────────────────────────
    "groq/llama-4-scout": {
        "name": "Llama 4 Scout",
        "provider": "groq",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["on-demand"],
        "default_service_tier": "on-demand",
    },
    # ── DashScope (Qwen) ───────────────────────────────────────────────
    "dashscope/qwen3-235b-a22b": {
        "name": "Qwen3 235B",
        "provider": "dashscope",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    # ── Zhipu ──────────────────────────────────────────────────────────
    "zhipu/glm-4.6": {
        "name": "GLM 4.6",
        "provider": "zhipu",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    # ── Moonshot ───────────────────────────────────────────────────────
    "moonshot/kimi-k2.5": {
        "name": "Kimi K2.5",
        "provider": "moonshot",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    # ── Gemini ──────────────────────────────────────────────────────────
    "gemini/gemini-2.5-pro": {
        "name": "Gemini 2.5 Pro",
        "provider": "gemini",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "gemini/gemini-2.5-flash": {
        "name": "Gemini 2.5 Flash",
        "provider": "gemini",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "gemini/gemini-2.0-flash": {
        "name": "Gemini 2.0 Flash",
        "provider": "gemini",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    # ── DashScope (Qwen) ────────────────────────────────────────────────
    "dashscope/qwen-max": {
        "name": "Qwen Max",
        "provider": "dashscope",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "dashscope/qwen-plus": {
        "name": "Qwen Plus",
        "provider": "dashscope",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    "dashscope/qwen-turbo": {
        "name": "Qwen Turbo",
        "provider": "dashscope",
        "hidden": False,
        "supported_reasoning_efforts": ["low"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    # ── Zhipu ───────────────────────────────────────────────────────────
    "zhipu/glm-4": {
        "name": "GLM-4",
        "provider": "zhipu",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    # ── Moonshot ────────────────────────────────────────────────────────
    "moonshot/kimi-k2": {
        "name": "Kimi K2",
        "provider": "moonshot",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
    # ── MiniMax ─────────────────────────────────────────────────────────
    "minimax/minimax-m1": {
        "name": "MiniMax M1",
        "provider": "minimax",
        "hidden": False,
        "supported_reasoning_efforts": ["low", "medium", "high"],
        "service_tiers": ["standard"],
        "default_service_tier": "standard",
    },
}


class ModelCatalog:
    """Deterministic, local-only model catalog.

    Reads providers from miqi.providers.registry.PROVIDERS and builds
    model rows from the built-in catalog above, augmented by the current
    configured model.  Does NOT call network APIs or expose secrets.
    """

    def __init__(self, current_config_model: str) -> None:
        self._current_model = current_config_model

    # ── model list ─────────────────────────────────────────────────────

    def list_models(self, *, include_hidden: bool = False) -> list[ModelView]:
        rows: list[ModelView] = []
        current_id = self._current_model

        # 1) Built-in catalog entries
        for model_id, info in _BUILTIN_MODELS.items():
            is_default = model_id == current_id
            rows.append(ModelView(
                id=model_id,
                name=info["name"],
                provider=info["provider"],
                provider_display_name=_provider_display_name(info["provider"]),
                hidden=info.get("hidden", False),
                default=is_default,
                supported_reasoning_efforts=info.get("supported_reasoning_efforts", []),
                additional_speed_tiers=info.get("additional_speed_tiers", []),
                service_tiers=info.get("service_tiers", []),
                default_service_tier=info.get("default_service_tier"),
                upgrade=info.get("upgrade", []),
                upgrade_info=info.get("upgrade_info"),
                availability_nux=info.get("availability_nux"),
            ))

        # 2) Current configured model — always visible if not already present
        if current_id and current_id not in _BUILTIN_MODELS:
            provider = _extract_provider(current_id)
            rows.append(ModelView(
                id=current_id,
                name=current_id.rsplit("/", 1)[-1].replace("-", " ").title(),
                provider=provider,
                provider_display_name=_provider_display_name(provider),
                hidden=False,
                default=True,
            ))

        if not include_hidden:
            rows = [r for r in rows if not r.hidden]

        # sort: default first, then by provider, then by id
        rows.sort(key=lambda r: (not r.default, r.provider, r.id))
        return rows

    # ── provider capabilities ──────────────────────────────────────────

    def get_capabilities(self, provider_name: str) -> ProviderCapabilitiesView:
        """Return capabilities for a named provider.

        Raises KeyError if the provider is unknown.
        """
        from miqi.providers.registry import find_by_name

        spec = find_by_name(provider_name)
        if spec is None:
            raise KeyError(provider_name)

        # Determine streaming/tools support from provider_type
        supports_streaming = True  # all current provider classes stream
        supports_tools = spec.provider_type in ("openai", "anthropic", "gemini")

        return ProviderCapabilitiesView(
            provider=spec.name,
            display_name=spec.display_name or spec.name.title(),
            provider_type=spec.provider_type,
            is_gateway=spec.is_gateway,
            is_local=spec.is_local,
            supports_prompt_caching=spec.supports_prompt_caching,
            supports_reasoning_history=spec.supports_reasoning_history,
            supports_streaming=supports_streaming,
            supports_tools=supports_tools,
            default_api_base=spec.default_api_base or None,
        )


# ── helpers ──────────────────────────────────────────────────────────────


def _extract_provider(model_id: str) -> str:
    """Extract provider name from a model id like 'anthropic/claude-opus-4-5'."""
    return model_id.split("/", 1)[0] if "/" in model_id else "custom"


def _provider_display_name(provider: str) -> str:
    """Human-readable provider display name."""
    from miqi.providers.registry import find_by_name

    spec = find_by_name(provider)
    if spec is not None:
        return spec.display_name or spec.name.title()
    return provider.title()
