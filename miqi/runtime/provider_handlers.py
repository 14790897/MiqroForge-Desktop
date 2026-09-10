"""Provider handlers for AppServer dispatch.

Phase 35.2: Migrates providers.list, providers.test, and providers.update
from bridge legacy handlers to AppServer async handlers. provider.test
uses the persistent event loop instead of asyncio.run().

Phase 35 hardening: Uses get_bridge_state(registry) for DI instead of
importing miqi.bridge.server directly.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from miqi.providers.registry import PROVIDER_TEST_MODELS
from miqi.runtime.app_server import AppServerError, get_bridge_state

VERIFICATION_KEY = "providerVerification"
VERIFICATION_STATUSES = {"success", "failed", "unverified"}


def _provider_fingerprint(provider_config: Any, model: str | None = None) -> str | None:
    """Return a stable fingerprint for provider fields that affect verification."""
    if provider_config is None:
        return None
    payload = {
        "api_key": getattr(provider_config, "api_key", "") or "",
        "api_base": getattr(provider_config, "api_base", None) or "",
        "extra_headers": getattr(provider_config, "extra_headers", None) or {},
        "model": model or "",
    }
    if not any(payload.values()):
        return None
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _provider_usable(pc: Any, spec: Any) -> bool:
    """Whether a provider config holds usable credentials.

    Used consistently by providers.list and the update auto-switch.
    - local providers: api_base (user endpoint) counts as usable
    - standard providers: an api_key is required; a stored default
      endpoint without a key (e.g. auto-filled when the key was saved,
      then the key cleared) is NOT usable credentials
    """
    if pc is None:
        return False
    if spec.is_local:
        return bool(pc.api_base)
    return bool(pc.api_key)


def _pick_usable_default_model(config: Any, exclude: str | None = None) -> str:
    """Pick a default model the runtime can actually use.

    Prefers the first usable provider's test model (gateway/local entries are
    full model ids; standard providers get "name/" prefixed). Returns the
    empty string when nothing usable exists — an explicit "not selected"
    state (UI shows 未设置 and prompts model selection), rather than an
    unusable factory default that misrepresents the real state
    （#933 review）.
    """
    from miqi.providers.registry import PROVIDERS

    for spec in PROVIDERS:
        if spec.name == exclude:
            continue
        pc = getattr(config.providers, spec.name, None)
        if not _provider_usable(pc, spec):
            continue
        model = PROVIDER_TEST_MODELS.get(spec.name)
        if not model:
            continue
        if spec.is_gateway or spec.is_local:
            return model
        return f"{spec.name}/{model}"
    return ""


def _qraft_gateway_routable(config: Any, model: str) -> bool:
    """Whether the model is routed via the platform AI gateway at runtime.

    镜像 factory.make_provider 的前置网关分支（#922）：登录凭据
    （<workspace>/.qraft/token.json 的 aiGateway 块）active 且模型是网关
    实测模型时，调用经平台网关转发（Anthropic Messages 兼容），不需要任何
    本地 provider 凭据。保存门控必须认识这条路径，否则「已开通」状态下
    唯一可用的网关模型会被拒之门外（实测复现：登录用户无任何本地 key，
    保存 deepseek/deepseek-v4-flash 报 Unsupported model）。
    """
    if not model.startswith("deepseek/"):
        return False
    from miqi.providers.gateway import (
        GATEWAY_MODEL,
        gateway_origin,
        gateway_token_file,
        read_gateway_creds,
    )

    if model[len("deepseek/"):] != GATEWAY_MODEL:
        return False
    workspace = getattr(config, "workspace_path", None)
    if not workspace:
        return False
    creds = read_gateway_creds(gateway_token_file(config))
    return bool(creds and gateway_origin())


def _model_provider_resolvable(config: Any, model: str) -> bool:
    """Whether the model resolves to a USABLE provider at runtime.

    镜像 Config._match_provider 的路由契约：模型自身 provider 必须持有可用
    凭据（标准/gateway 需要 api_key，本地需要 api_base），或者有已配置的
    gateway 可以兜底路由。仅注册表成员资格不够 —— 无凭据的 provider 会
    落到 _match_provider 的「第一个已配置 provider」兜底，把模型发到错误
    的 API（#929 review）。
    """
    from miqi.providers.registry import PROVIDERS, find_by_model, find_by_name

    if not model or not str(model).strip():
        return False
    if model.lower().startswith("custom/"):
        return False  # custom provider 已移除（#835），网关兜底不得复活它（#933 review）
    # 平台 AI 网关路由（#922）：登录凭据 active 时经平台网关转发，无需本地
    # 凭据 —— 与 factory.make_provider 的路由前提保持一致。
    if _qraft_gateway_routable(config, model):
        return True
    model_prefix = model.split("/", 1)[0] if "/" in model else ""
    spec = find_by_name(model_prefix) if model_prefix else find_by_model(model)
    if spec is None:
        spec = find_by_model(model)
    if spec is not None:
        pc = getattr(config.providers, spec.name, None)
        if _provider_usable(pc, spec):
            return True
        # 归属 provider 无凭据时运行时还会继续走关键字与网关兜底 ——
        # 不直接拒绝，交给下面的网关判定。
    # 没有可用归属 provider —— 只有已配置的 gateway 能路由这个模型。
    for gateway_spec in PROVIDERS:
        if not gateway_spec.is_gateway:
            continue
        pc = getattr(config.providers, gateway_spec.name, None)
        if _provider_usable(pc, gateway_spec):
            return True
    return False


def _provider_verification_store(config: Any) -> dict[str, Any]:
    desktop = getattr(config, "desktop", None)
    if not isinstance(desktop, dict):
        desktop = {}
        config.desktop = desktop
    store = desktop.get(VERIFICATION_KEY)
    if not isinstance(store, dict):
        store = {}
        desktop[VERIFICATION_KEY] = store
    return store


def _set_provider_verification(
    config: Any,
    provider_name: str,
    status: str,
    fingerprint: str | None,
    message: str = "",
) -> None:
    if status not in VERIFICATION_STATUSES:
        status = "unverified"
    store = _provider_verification_store(config)
    store[provider_name] = {
        "status": status,
        "fingerprint": fingerprint or "",
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "message": message,
    }


async def providers_list_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """List all configured providers with API key hints.

    Returns provider metadata: name, display_name, env_key, provider_type,
    configured status, api_key hint, default model, etc.
    """
    from miqi.providers.registry import PROVIDERS, find_by_model

    state = get_bridge_state(registry)
    config = state.load_config()
    model = config.agents.defaults.model
    # Only report the default model as "configured_model" when it genuinely
    # belongs to this provider. get_provider_name() falls back to the first
    # configured provider, which would mislabel e.g. "anthropic/claude-opus-4-5"
    # as DeepSeek's configured model after the user saved a DeepSeek key —
    # the wrong model was then sent to the DeepSeek API on test (#602).
    matched_spec = find_by_model(model)
    model_provider = matched_spec.name if matched_spec else None
    verification_store = _provider_verification_store(config)

    providers_out = []
    for spec in PROVIDERS:
        pc = getattr(config.providers, spec.name, None)
        api_key = pc.api_key if pc else None
        hint = None
        builtin_available = spec.name in _BUILTIN_PROVIDERS
        builtin_activated = config.is_builtin_activated(spec.name)
        if builtin_activated:
            # Hide the real key from the frontend for built-in activations
            hint = "企业共享密钥"
        elif api_key and len(api_key) >= 8:
            hint = api_key[:4] + "…" + api_key[-4:]
        elif api_key:
            hint = "***"
        configured = _provider_usable(pc, spec)
        provider_model = (
            model
            if configured and model_provider == spec.name
            else PROVIDER_TEST_MODELS.get(spec.name)
        )
        fingerprint = _provider_fingerprint(pc, provider_model)
        record = verification_store.get(spec.name)
        record_matches = (
            configured
            and fingerprint
            and isinstance(record, dict)
            and record.get("fingerprint") == fingerprint
        )
        if not configured:
            verification_status = "missing"
        elif record_matches and record.get("status") in {"success", "failed"}:
            verification_status = str(record.get("status"))
        else:
            verification_status = "unverified"

        providers_out.append({
            "name": spec.name,
            "display_name": spec.display_name or spec.name.title(),
            "env_key": spec.env_key,
            "provider_type": spec.provider_type,
            "is_gateway": spec.is_gateway,
            "is_local": spec.is_local,
            "default_api_base": spec.default_api_base,
            "configured": configured,
            "api_key_hint": hint,
            "api_base": pc.api_base if pc else None,
            "configured_model": model if configured and model_provider == spec.name else None,
            "verification_status": verification_status,
            "verified_at": record.get("checkedAt") if record_matches else None,
            "verification_message": record.get("message") if record_matches else None,
            "builtin_available": builtin_available,
            "builtin_activated": builtin_activated,
        })

    return {
        "result": {
            "providers": providers_out,
            "active_model": model,
            "active_provider": model_provider,
            # 发送门禁的判定依据（#1010 后续）：前端只看得见「本地 provider 是否
            # 有凭据」，登录后经平台网关路由的默认模型却不需要任何本地凭据
            # （make_provider 的网关分支），只看 configured 会把可用模型误判成
            # 「未配置模型服务」。这里用与运行时同一套判定（含网关路由）告诉
            # 前端当前默认模型是否可以真正发起会话。
            "active_model_resolvable": _model_provider_resolvable(config, model),
        }
    }


async def providers_test_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Test a provider by making a chat API call.

    Uses the persistent event loop (no asyncio.run()).
    Sanitizes error messages before returning to the frontend.
    """
    provider_name = params.get("provider_name", "")
    api_key = params.get("api_key") or ""
    api_base = params.get("api_base") or None
    requested_model = str(params.get("model") or "").strip()

    # 后端收口（#835）：不再接受自配凭据，仅支持测试已保存的内置 key。
    if api_key:
        raise AppServerError("自定义 API Key 已禁用，仅支持内置密钥", code="NOT_SUPPORTED")
    if api_base:
        raise AppServerError("自定义 API Base 已禁用", code="NOT_SUPPORTED")

    if not provider_name:
        raise AppServerError("provider_name is required", code="INVALID_PARAMS")

    from miqi.providers.registry import find_by_name

    spec = find_by_name(provider_name)
    if spec is None:
        raise AppServerError(
            f"Unknown provider: {provider_name}",
            code="NOT_FOUND",
        )

    state = get_bridge_state(registry)
    config = state.load_config()
    pc = getattr(config.providers, provider_name, None)

    # 内置激活的 provider 强制走官方端点：历史遗留的自定义 api_base 不得与
    # 内置共享密钥一起使用，防止密钥被发送到第三方地址（CodeRabbit #929）。
    builtin_activated = config.is_builtin_activated(provider_name)

    test_model = (
        requested_model
        or PROVIDER_TEST_MODELS.get(provider_name)
        or "gpt-4o"
    )
    explicit_api_key = bool(api_key)
    saved_api_base = (pc.api_base if pc is not None else None) or spec.default_api_base or None
    explicit_api_base = bool(api_base) and api_base != saved_api_base
    should_persist_result = not explicit_api_key and not explicit_api_base

    # If no API key provided, read from current saved config
    if not api_key:
        if pc is not None:
            api_key = pc.api_key or ""
            # 内置 key 只测官方默认端点，忽略遗留的自定义 api_base（防止内置
            # 凭据被发到用户以前配过的第三方端点，CodeRabbit #929）。未内置
            # 激活时保留历史行为：本地部署/遗留端点仍需读取保存的 api_base。
            if not api_base:
                api_base = None if builtin_activated else pc.api_base

    if not api_key:
        raise AppServerError(
            "No API key configured — enter one in Edit or save a provider first",
            code="INVALID_PARAMS",
        )

    if spec.provider_type == "anthropic":
        from miqi.providers.anthropic_provider import AnthropicProvider
        provider = AnthropicProvider(
            api_key=api_key,
            api_base=api_base,
            provider_name=provider_name,
            default_model=test_model,
        )
    elif spec.provider_type == "gemini":
        from miqi.providers.gemini_provider import GeminiProvider
        provider = GeminiProvider(
            api_key=api_key,
            api_base=api_base,
            provider_name=provider_name,
            default_model=test_model,
        )
    else:
        from miqi.providers.openai_provider import OpenAIProvider
        provider = OpenAIProvider(
            api_key=api_key,
            api_base=api_base,
            provider_name=provider_name,
            default_model=test_model,
        )

    try:
        response = await provider.chat(
            messages=[{"role": "user", "content": "Hello, respond with just 'ok'."}],
            model=test_model,
            max_tokens=16,
            temperature=0.0,
        )
        finish_reason = getattr(response, "finish_reason", "stop")
        error_kind = getattr(response, "error_kind", None)
        ok = finish_reason != "error" and not error_kind
        if not ok:
            raise RuntimeError(response.content or "Provider returned an error response")
        fingerprint = _provider_fingerprint(pc, test_model)
        if ok and should_persist_result and fingerprint:
            from miqi.config.loader import save_config
            _set_provider_verification(
                config,
                provider_name,
                "success",
                fingerprint,
                "Connection test succeeded",
            )
            save_config(config)
            state.config = config
        return {"result": {"ok": ok, "model": test_model}}
    except Exception as exc:
        fingerprint = _provider_fingerprint(pc, test_model)
        if should_persist_result and fingerprint:
            from miqi.config.loader import save_config
            _set_provider_verification(
                config,
                provider_name,
                "failed",
                fingerprint,
                "Provider test failed",
            )
            save_config(config)
            state.config = config
        # Sanitize: log full details server-side, return sanitized message
        logger.warning(
            "providers.test: provider={} error: {}", provider_name, exc,
        )
        raise AppServerError(
            "Provider test failed — check API key and network",
            code="PROVIDER_ERROR",
        ) from exc


async def providers_update_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Update the default model only（自配凭据已禁用，#835 后端收口）。"""
    from miqi.config.loader import save_config
    from miqi.providers.registry import find_by_name

    provider_name = params.get("provider_name", "").strip()
    if not provider_name:
        raise AppServerError("provider_name is required", code="INVALID_PARAMS")

    # 后端收口（#835）：用 registry 白名单校验，而非存储 schema
    #（schema 仍含 custom，但 runtime 已不支持它，CodeRabbit #929）。
    spec = find_by_name(provider_name)
    if spec is None:
        raise AppServerError(
            f"Unknown provider: {provider_name}", code="INVALID_PARAMS",
        )

    state = get_bridge_state(registry)
    config = state.load_config()
    # Snapshot before mutation — used for hot-reload classification (#789).
    prev_config = config.model_copy(deep=True)
    pc = getattr(config.providers, provider_name, None)
    if pc is None:
        raise AppServerError(
            f"Provider config not found: {provider_name}", code="NOT_FOUND",
        )

    # 后端收口（#835）：拒绝自配凭据（api_key / api_base / extra_headers），
    # 只允许更新默认模型（agents.defaults.model）。
    if params.get("api_key"):
        raise AppServerError("自定义 API Key 已禁用，请使用内置激活码", code="NOT_SUPPORTED")
    if params.get("api_base"):
        raise AppServerError("自定义 API Base 已禁用", code="NOT_SUPPORTED")
    if params.get("extra_headers"):
        raise AppServerError("自定义 Extra Headers 已禁用", code="NOT_SUPPORTED")

    model_override: str | None = None
    if "model" in params and params["model"]:
        model_override = str(params["model"]).strip()

    if not model_override:
        raise AppServerError("No fields to update", code="INVALID_PARAMS")

    # 模型值必须能被运行时解析到「有凭据可用」的 provider（#929 review：
    # 仅注册表成员资格不够，无凭据 provider 会兜底错发到错误 API）。
    if not _model_provider_resolvable(config, model_override):
        raise AppServerError(
            f"Unsupported model: {model_override}", code="INVALID_PARAMS",
        )

    config.agents.defaults.model = model_override

    save_config(config)
    state.config = config

    # Issue #789: hot-apply provider/model change to active sessions and
    # broadcast config_updated so the UI shows "已生效" instead of forcing
    # a restart. The provider is rebuilt for the next turn; an in-flight
    # turn keeps the provider it captured.
    try:
        from miqi.runtime.config_handlers import hot_apply_and_broadcast

        report, propagated = await hot_apply_and_broadcast(
            registry, client_id, prev_config, config,
        )
        logger.info(
            "providers.update: saved and hot-applied to {} session(s) "
            "tiers: {} applied / {} new-session / {} restart",
            propagated, len(report.applied), len(report.new_sessions_only),
            len(report.restart_required),
        )
    except Exception as exc:
        # Hot-apply is best-effort; the config is already persisted and the
        # new values will take effect for new sessions regardless.
        logger.warning("providers.update: hot-apply failed: {}", exc)

    return {"result": {"saved": True, "provider_name": provider_name}}


# ---------------------------------------------------------------------------
# Built-in credential: activation code → decrypt embedded API key
# ---------------------------------------------------------------------------

# Providers that support built-in (enterprise shared) keys
_BUILTIN_PROVIDERS = {"deepseek"}

# Encrypted API key for DeepSeek internal testing.
# Token generated via Fernet with activation-code-derived key.
_BUILTIN_KEYS: dict[str, str] = {"deepseek": "gAAAAABqmSpZGjsRS0YDYm8ZvtPLtFG1sQOtIbT3TsKBn9WQP_YkHqmr3x2-Wx16sq9VR84jmmmguoBx-mqIPTN9uOmpdyY2vees1-bhE54fvxFZyrXEkGkyUbIP-LwxuFA_v6vWJw8V"}  # provider_name → encrypted_key

# Default activation code — the company name / internal code
_DEFAULT_ACTIVATION_CODE = "weiguanjiyuan5g"


def _get_fernet() -> Any:
    """Get Fernet instance for the built-in key encryption."""
    # Derive a Fernet key from the activation code (for MVP)
    import base64
    import hashlib

    from cryptography.fernet import Fernet
    digest = hashlib.sha256(_DEFAULT_ACTIVATION_CODE.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def _decrypt_builtin_key(provider_name: str) -> str | None:
    """Decrypt the built-in API key for a provider. Returns None if not configured."""
    token = _BUILTIN_KEYS.get(provider_name)
    if not token:
        return None
    try:
        fernet = _get_fernet()
        return fernet.decrypt(token.encode()).decode()
    except Exception:
        return None


async def providers_activate_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Activate a provider's built-in enterprise API key with an activation code.

    The activation code is validated, and if correct, the built-in API key is
    decrypted and stored in the provider config. The frontend never sees the
    actual key — only the activation status.
    """
    from miqi.config.loader import save_config
    from miqi.config.schema import ProviderConfig

    provider_name = params.get("provider_name", "").strip()
    activation_code = params.get("activation_code", "").strip()

    if not provider_name:
        raise AppServerError("provider_name is required", code="INVALID_PARAMS")
    if not activation_code:
        raise AppServerError("activation_code is required", code="INVALID_PARAMS")
    if provider_name not in _BUILTIN_PROVIDERS:
        raise AppServerError(
            f"Provider '{provider_name}' does not support built-in activation",
            code="NOT_SUPPORTED",
        )

    # Validate activation code
    if activation_code != _DEFAULT_ACTIVATION_CODE:
        raise AppServerError("激活码无效", code="INVALID_CODE")

    # Decrypt the built-in key
    api_key = _decrypt_builtin_key(provider_name)
    if not api_key:
        raise AppServerError(
            "未配置内置密钥，请联系管理员",
            code="NO_BUILTIN_KEY",
        )

    state = get_bridge_state(registry)
    config = state.load_config()
    pc = getattr(config.providers, provider_name, None)
    if pc is None:
        raise AppServerError(
            f"Provider config not found: {provider_name}", code="NOT_FOUND",
        )

    # Write the decrypted key to provider config. Also clear any legacy
    # custom api_base / extra_headers: the builtin enterprise key must only
    # ever go to the official endpoint (#929 review).
    current_dict = pc.model_dump(by_alias=False)
    current_dict["api_key"] = api_key
    current_dict["api_base"] = None
    current_dict["extra_headers"] = None
    new_pc = ProviderConfig.model_validate(current_dict)
    setattr(config.providers, provider_name, new_pc)

    # Mark as built-in activated so the frontend hides the real key
    activation_store = _provider_activation_store(config)
    activation_store[provider_name] = {
        "builtin": True,
        "activatedAt": datetime.now(timezone.utc).isoformat(),
    }

    save_config(config)
    state.config = config

    logger.info(
        "providers.activate: provider={} activated via built-in key", provider_name,
    )

    return {
        "result": {
            "activated": True,
            "provider_name": provider_name,
        }
    }


async def providers_deactivate_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """取消内置 provider 的激活：清空 api_key 并移除 activation 标记（#835 收口）。"""
    from miqi.config.loader import save_config
    from miqi.config.schema import ProviderConfig

    provider_name = params.get("provider_name", "").strip()

    if not provider_name:
        raise AppServerError("provider_name is required", code="INVALID_PARAMS")
    if provider_name not in _BUILTIN_PROVIDERS:
        raise AppServerError(
            f"Provider '{provider_name}' does not support built-in deactivation",
            code="NOT_SUPPORTED",
        )

    state = get_bridge_state(registry)
    config = state.load_config()
    pc = getattr(config.providers, provider_name, None)
    if pc is None:
        raise AppServerError(
            f"Provider config not found: {provider_name}", code="NOT_FOUND",
        )

    # 只有带内置激活标记的配置才允许取消：历史遗留的自配 key 没有标记，
    # 误调用会把用户自己的 key 清掉（CodeRabbit #929）。
    builtin_marked = config.is_builtin_activated(provider_name)
    if not builtin_marked:
        raise AppServerError(
            f"Provider '{provider_name}' has no built-in activation to deactivate",
            code="NOT_ACTIVATED",
        )

    # Clear the built-in key (and any legacy endpoint overrides — the same
    # exfiltration vector activation now sanitizes, #929 review)
    current_dict = pc.model_dump(by_alias=False)
    current_dict["api_key"] = ""
    current_dict["api_base"] = None
    current_dict["extra_headers"] = None
    new_pc = ProviderConfig.model_validate(current_dict)
    setattr(config.providers, provider_name, new_pc)

    # Remove the activation marker
    activation_store = _provider_activation_store(config)
    activation_store.pop(provider_name, None)

    # 默认模型若属于被取消激活的 provider，重置为一个运行时真正可用的
    # 模型（优先其他已配置 provider 的测试模型），否则新会话全部
    # NO_API_KEY 且 UI 与配置不一致（#929 review / #933 review）。
    from miqi.providers.registry import find_by_model

    current_model = config.agents.defaults.model
    matched_spec = find_by_model(current_model)
    if matched_spec is not None and matched_spec.name == provider_name:
        config.agents.defaults.model = _pick_usable_default_model(config, exclude=provider_name)
        logger.info(
            "providers.deactivate: default model '{}' owned by {} — reset to '{}'",
            current_model, provider_name, config.agents.defaults.model,
        )

    save_config(config)
    state.config = config

    logger.info(
        "providers.deactivate: provider={} deactivated", provider_name,
    )

    return {
        "result": {
            "deactivated": True,
            "provider_name": provider_name,
        }
    }


def _provider_activation_store(config: Any) -> dict[str, Any]:
    """Get or create the provider activation store in desktop config."""
    desktop = getattr(config, "desktop", None)
    if not isinstance(desktop, dict):
        desktop = {}
        config.desktop = desktop
    store = desktop.get("providerActivation")
    if not isinstance(store, dict):
        store = {}
        desktop["providerActivation"] = store
    return store
