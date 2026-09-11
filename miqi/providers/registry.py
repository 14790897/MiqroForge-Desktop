"""
Provider Registry — single source of truth for LLM provider metadata.

Adding a new provider:
  1. Add a ProviderSpec to PROVIDERS below.
  2. Add a field to ProvidersConfig in config/schema.py.
  Done. Env vars, prefixing, config matching, status display all derive from here.

Order matters — it controls match priority and fallback. Gateways first.
Every entry writes out all fields so you can copy-paste as a template.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider's metadata. See PROVIDERS below for real examples.

    Placeholders in env_extras values:
      {api_key}  — the user's API key
      {api_base} — api_base from config, or this spec's default_api_base
    """

    # identity
    name: str                       # config field name, e.g. "dashscope"
    keywords: tuple[str, ...]       # model-name keywords for matching (lowercase)
    env_key: str                    # API key env var, e.g. "DASHSCOPE_API_KEY"
    display_name: str = ""          # shown in `miqi status`

    # provider routing and model prefixing
    provider_type: str = "openai"            # "openai" | "anthropic" | "gemini"
    model_prefix: str = ""                   # prefix to strip from model name before API call
    skip_prefixes: tuple[str, ...] = ()      # kept for reference

    # extra env vars, e.g. (("ZHIPUAI_API_KEY", "{api_key}"),)
    env_extras: tuple[tuple[str, str], ...] = ()

    # gateway / local detection
    is_gateway: bool = False                 # routes any model (OpenRouter, AiHubMix)
    is_local: bool = False                   # local deployment (vLLM, Ollama)
    detect_by_key_prefix: str = ""           # match api_key prefix, e.g. "sk-or-"
    detect_by_base_keyword: str = ""         # match substring in api_base URL
    default_api_base: str = ""               # fallback base URL

    # gateway behavior
    strip_model_prefix: bool = False         # strip "provider/" before re-prefixing

    # per-model param overrides, e.g. (("kimi-k2.5", {"temperature": 1.0}),)
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = ()

    # Direct providers use their own SDK (e.g., CustomProvider)
    is_direct: bool = False

    # Provider supports cache_control on content blocks (e.g. Anthropic prompt caching)
    supports_prompt_caching: bool = False

    # Provider requires reasoning_content preserved in message history for multi-turn
    # conversations (DeepSeek official API for deepseek-reasoner requires this).
    supports_reasoning_history: bool = False

    # #834: provider streams reasoning CoT incrementally (interleaved with
    # content — Kimi/Qwen/GPT-5 style) instead of buffering the whole pass
    # server-side (DeepSeek).  For streaming providers the request→first-delta
    # proxy does NOT equal thinking time, so it must be suppressed regardless
    # of delta ordering.
    streams_reasoning: bool = False

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


# ---------------------------------------------------------------------------
# PROVIDERS — the registry. Order = priority. Copy any entry as template.
# ---------------------------------------------------------------------------

PROVIDERS: tuple[ProviderSpec, ...] = (

    # === Gateways (detected by api_key / api_base, not model name) =========
    # Gateways can route any model, so they win in fallback.

    # OpenRouter: global gateway, keys start with "sk-or-"
    ProviderSpec(
        name="openrouter",
        keywords=("openrouter",),
        env_key="OPENROUTER_API_KEY",
        display_name="OpenRouter",
        model_prefix="openrouter",           # strip 'openrouter/' before passing to API
        skip_prefixes=(),
        env_extras=(),
        is_gateway=True,
        is_local=False,
        detect_by_key_prefix="sk-or-",
        detect_by_base_keyword="openrouter",
        default_api_base="https://openrouter.ai/api/v1",
        strip_model_prefix=False,
        model_overrides=(),
        supports_prompt_caching=True,
    ),

    # AiHubMix: global gateway, OpenAI-compatible interface.
    # strip_model_prefix=True: strips any 'provider/' to get bare model name.
    ProviderSpec(
        name="aihubmix",
        keywords=("aihubmix",),
        env_key="OPENAI_API_KEY",           # OpenAI-compatible
        display_name="AiHubMix",
        model_prefix="",                    # strip_model_prefix=True handles stripping
        skip_prefixes=(),
        env_extras=(),
        is_gateway=True,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="aihubmix",
        default_api_base="https://aihubmix.com/v1",
        strip_model_prefix=True,            # anthropic/claude-3 → claude-3 → openai/claude-3
        model_overrides=(),
    ),

    # SiliconFlow (硅基流动): OpenAI-compatible gateway, model names keep org prefix
    ProviderSpec(
        name="siliconflow",
        keywords=("siliconflow",),
        env_key="SILICONFLOW_API_KEY",
        display_name="SiliconFlow",
        model_prefix="",
        skip_prefixes=(),
        env_extras=(),
        is_gateway=True,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="siliconflow",
        default_api_base="https://api.siliconflow.cn/v1",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # VolcEngine (火山引擎): OpenAI-compatible gateway
    ProviderSpec(
        name="volcengine",
        keywords=("volcengine", "volces", "ark"),
        env_key="OPENAI_API_KEY",
        display_name="VolcEngine",
        model_prefix="volcengine",
        skip_prefixes=(),
        env_extras=(),
        is_gateway=True,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="volces",
        default_api_base="https://ark.cn-beijing.volces.com/api/v3",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # === Standard providers (matched by model-name keywords) ===============

    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        provider_type="anthropic",
        model_prefix="",
        skip_prefixes=(),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="",
        strip_model_prefix=False,
        model_overrides=(),
        supports_prompt_caching=True,
    ),

    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        model_prefix="",
        skip_prefixes=(),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # DeepSeek: deepseek-reasoner (R1) requires reasoning_content in multi-turn history.
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        model_prefix="deepseek",
        skip_prefixes=("deepseek/",),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="https://api.deepseek.com/v1",
        strip_model_prefix=False,
        model_overrides=(),
        supports_reasoning_history=True,    # R1 multi-turn needs reasoning_content back
    ),

    ProviderSpec(
        name="gemini",
        keywords=("gemini",),
        env_key="GEMINI_API_KEY",
        display_name="Gemini",
        provider_type="gemini",
        model_prefix="gemini",
        skip_prefixes=("gemini/",),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="",                # GeminiProvider uses its own fixed endpoint
        strip_model_prefix=False,
        model_overrides=(),
    ),

    ProviderSpec(
        name="zhipu",
        keywords=("zhipu", "glm", "zai"),
        env_key="ZAI_API_KEY",
        display_name="Zhipu AI",
        model_prefix="zai",
        skip_prefixes=("zhipu/", "zai/", "openrouter/", "hosted_vllm/"),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="https://open.bigmodel.cn/api/paas/v4/",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    ProviderSpec(
        name="dashscope",
        keywords=("qwen", "dashscope"),
        env_key="DASHSCOPE_API_KEY",
        display_name="DashScope",
        model_prefix="dashscope",
        skip_prefixes=("dashscope/", "openrouter/"),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # Moonshot: Kimi models. Kimi K2.5 API enforces temperature >= 1.0.
    ProviderSpec(
        name="moonshot",
        keywords=("moonshot", "kimi"),
        env_key="MOONSHOT_API_KEY",
        display_name="Moonshot",
        model_prefix="moonshot",
        skip_prefixes=("moonshot/", "openrouter/"),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        # #834: Kimi streams reasoning CoT incrementally (interleaved with
        # content) — the request→first-delta thinking proxy is invalid here.
        streams_reasoning=True,
        detect_by_base_keyword="",
        default_api_base="https://api.moonshot.ai/v1",   # intl; use api.moonshot.cn for China
        strip_model_prefix=False,
        model_overrides=(
            ("kimi-k2.5", {"temperature": 1.0}),
        ),
    ),

    # MiniMax: Token Plan API lives at api.minimaxi.com/v1.
    ProviderSpec(
        name="minimax",
        keywords=("minimax",),
        env_key="MINIMAX_API_KEY",
        display_name="MiniMax",
        model_prefix="minimax",
        skip_prefixes=("minimax/", "openrouter/"),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="https://api.minimaxi.com/v1",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # Ollama Cloud: hosted Ollama API, requires API key.
    ProviderSpec(
        name="ollama_cloud",
        keywords=("ollama_cloud", "ollama-cloud", "-cloud", ":cloud"),
        env_key="OLLAMA_API_KEY",
        display_name="Ollama Cloud",
        model_prefix="ollama",
        skip_prefixes=("ollama/", "ollama_chat/"),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="https://ollama.com/api",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # === Local deployment (matched by config key, NOT by api_base) =========

    # vLLM / any OpenAI-compatible local server.
    # Detected when config key is "vllm" (provider_name="vllm").
    ProviderSpec(
        name="vllm",
        keywords=("vllm",),
        env_key="HOSTED_VLLM_API_KEY",
        display_name="vLLM/Local",
        model_prefix="hosted_vllm",
        skip_prefixes=(),
        env_extras=(),
        is_gateway=False,
        is_local=True,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="",                # user must provide in config
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # Ollama local server.
    # Detected when config key is "ollama_local" (provider_name="ollama_local").
    ProviderSpec(
        name="ollama_local",
        keywords=("ollama_local", "ollama-local", "ollama"),
        env_key="OLLAMA_API_KEY",
        display_name="Ollama Local",
        model_prefix="ollama",
        skip_prefixes=("ollama/", "ollama_chat/"),
        env_extras=(),
        is_gateway=False,
        is_local=True,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="http://localhost:11434",
        strip_model_prefix=False,
        model_overrides=(),
    ),

    # === Auxiliary (not a primary LLM provider) ============================

    # Groq: mainly used for Whisper voice transcription, also usable for LLM.
    ProviderSpec(
        name="groq",
        keywords=("groq",),
        env_key="GROQ_API_KEY",
        display_name="Groq",
        model_prefix="groq",
        skip_prefixes=("groq/",),
        env_extras=(),
        is_gateway=False,
        is_local=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="https://api.groq.com/openai/v1",
        strip_model_prefix=False,
        model_overrides=(),
    ),
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

# Test/fallback model per provider — used by connection tests, model-picking
# fallbacks (loader migration / deactivation reset), and the catalog defaults.
# Gateway/local entries are FULL model ids (routed as-is); standard-provider
# entries are bare names to be prefixed with "spec.name/".
PROVIDER_TEST_MODELS: dict[str, str] = {
    "anthropic": "claude-opus-4-5",
    "openai": "gpt-4.1",
    "deepseek": "deepseek-v4-flash",
    "gemini": "gemini-2.5-pro",
    "moonshot": "kimi-k2.5",
    "dashscope": "qwen-max",
    "zhipu": "glm-4",
    "minimax": "MiniMax-M2.7",
    "aihubmix": "claude-opus-4.1",
    "siliconflow": "deepseek-ai/DeepSeek-V3",
    "vllm": "meta-llama/Llama-3.1-8B-Instruct",
    "ollama_local": "llama3.2",
    "ollama_cloud": "gpt-oss:20b-cloud",
    "openrouter": "anthropic/claude-opus-4-5",
    "custom": "default",
}

def find_by_model(model: str) -> ProviderSpec | None:
    """Match a standard provider by model-name keyword (case-insensitive).
    Skips gateways/local — those are matched by api_key/api_base instead."""
    model_lower = model.lower()
    model_normalized = model_lower.replace("-", "_")
    model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
    normalized_prefix = model_prefix.replace("-", "_")
    std_specs = [s for s in PROVIDERS if not s.is_gateway and not s.is_local]

    # Prefer explicit provider prefix match to avoid ambiguous keyword fallbacks.
    for spec in std_specs:
        if model_prefix and normalized_prefix == spec.name:
            return spec

    for spec in std_specs:
        if any(kw in model_lower or kw.replace("-", "_") in model_normalized for kw in spec.keywords):
            return spec
    return None


def find_gateway(
    provider_name: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
) -> ProviderSpec | None:
    """Detect gateway/local provider.

    Priority:
      1. provider_name — if it maps to a gateway/local spec, use it directly.
      2. api_key prefix — e.g. "sk-or-" → OpenRouter.
      3. api_base keyword — e.g. "aihubmix" in URL → AiHubMix.

    A standard provider with a custom api_base (e.g. DeepSeek behind a proxy)
    will NOT be mistaken for vLLM — the old fallback is gone.
    """
    # 1. Direct match by config key
    if provider_name:
        spec = find_by_name(provider_name)
        if spec and (spec.is_gateway or spec.is_local):
            return spec

    # 2. Auto-detect by api_key prefix / api_base keyword
    for spec in PROVIDERS:
        if spec.detect_by_key_prefix and api_key and api_key.startswith(spec.detect_by_key_prefix):
            return spec
        if spec.detect_by_base_keyword and api_base and spec.detect_by_base_keyword in api_base:
            return spec

    return None


def find_by_name(name: str) -> ProviderSpec | None:
    """Find a provider spec by config field name, e.g. "dashscope"."""
    for spec in PROVIDERS:
        if spec.name == name:
            return spec
    return None
