"""Shared provider factory — creates the appropriate LLM provider from config.

Used by CLI, TUI, and any other entry point that needs a provider.
"""

from __future__ import annotations

from typing import Any


def make_provider(config: Any) -> Any:
    """Create the appropriate LLM provider from config.

    Args:
        config: Config object with agents.defaults.model, providers, etc.

    Returns:
        An LLMProvider instance.

    Raises:
        ValueError: If no API key is configured and the provider is not local.
    """
    from miqi.providers.registry import find_by_name

    model = config.agents.defaults.model

    # custom provider 已从运行时移除（#835 收口）：遗留的 custom/* 默认模型
    # 会经 _match_provider 兜底错发到第一个已配置 provider 的 API（#929
    # review），这里给出明确报错而不是静默错发。
    if model.lower().startswith("custom/"):
        raise ValueError(
            "自定义 provider（custom/*）已移除（#835 合规收口），请在 设置 → 模型 中改用内置模型。"
        )

    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)

    spec = find_by_name(provider_name)

    # AI 网关路由(issue #922):登录且 aiGateway 状态为 active、且模型为网关实测
    # 模型时,把该模型调用经 AnthropicProvider(Anthropic Messages 兼容)指向平台
    # 网关 —— 走用户 encryptedApiKey,计入平台消费组配额。前提不满足则落回直连。
    # 必须位于下方 API-key 守卫之前:登录用户即使未激活内置密钥也能经网关调用。
    workspace = getattr(config, "workspace_path", None)
    if provider_name == "deepseek" and workspace:
        from miqi.providers.gateway import (
            GATEWAY_MODEL,
            GATEWAY_PREFIX,
            gateway_origin,
            gateway_token_file,
            read_gateway_creds,
        )

        bare = model[len("deepseek/") :] if model.startswith("deepseek/") else model
        if bare == GATEWAY_MODEL:
            creds = read_gateway_creds(gateway_token_file(config))
            # 网关 origin 必须可用（显式配置强制 https，非法则回退直连）
            origin = gateway_origin() if creds else None
            if creds and origin:
                from miqi.providers.anthropic_provider import AnthropicProvider

                return AnthropicProvider(
                    api_key=creds["encryptedApiKey"],
                    api_base=f"{origin}{GATEWAY_PREFIX}",
                    default_model=model,
                    provider_name="deepseek",
                    model_prefix="deepseek",
                )

    if not model.startswith("bedrock/") and not (p and p.api_key) and not (spec and spec.is_local):
        raise ValueError(
            "No API key configured. Set one in your config file under the providers section."
        )

    provider_type = spec.provider_type if spec else "openai"

    # 内置激活的 provider 强制走官方端点（#929 review：聊天路径同样收口，
    # 企业共享密钥不得发往历史遗留的自定义 api_base / extra_headers）。
    builtin_activated = bool(provider_name) and config.is_builtin_activated(provider_name)
    api_base = None if builtin_activated else config.get_api_base(model)

    common_kwargs = dict(
        api_key=p.api_key if p else None,
        api_base=api_base,
        default_model=model,
        extra_headers=(p.extra_headers if p else None) if not builtin_activated else None,
        provider_name=provider_name,
    )

    if provider_type == "anthropic":
        from miqi.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(**common_kwargs)

    if provider_type == "gemini":
        from miqi.providers.gemini_provider import GeminiProvider

        return GeminiProvider(**common_kwargs)

    from miqi.providers.openai_provider import OpenAIProvider

    return OpenAIProvider(**common_kwargs)
