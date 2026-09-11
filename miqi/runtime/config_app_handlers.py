"""Codex-style config read and batch write AppServer handlers.

Provides config/read (redacted) and config/batchWrite (atomic multi-edit).
Also houses shared redaction and deep-merge helpers used by legacy config
handlers so they no longer need direct bridge-module access.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

import miqi.runtime.protocol_specs as protocol_specs
from miqi.runtime.app_server import AppServer, AppServerError, get_bridge_state
from miqi.runtime.core_request_models import validate_core_params

# ── Secret field names ────────────────────────────────────────────────────
_SECRET_FIELDS = {
    "api_key", "apiKey", "apikey",
    "token", "secret", "password", "claw_token",
}


# ── Redaction ──────────────────────────────────────────────────────────────


def _redact_secrets(obj: Any, parent_key: str = "") -> None:
    """Redact secret values in-place.

    Replaces secret string values with stable hints (first 4 chars + ellipsis
    + last char when possible).  Recurses into nested dicts and lists.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _SECRET_FIELDS or _is_secret_key(k):
                if isinstance(v, str) and v:
                    obj[k] = _secret_hint(v)
            elif isinstance(v, (dict, list)):
                _redact_secrets(v, k)
    elif isinstance(obj, list):
        for item in obj:
            _redact_secrets(item, parent_key)


def _is_secret_key(key: str) -> bool:
    """True if the key name implies a secret value."""
    lower = key.lower()
    return any(s in lower for s in ("secret", "token", "password", "api_key", "apikey"))


def _secret_hint(value: str) -> str:
    """Build a stable obfuscated hint for a secret string."""
    if len(value) >= 8:
        return value[:4] + "…" + value[-4:]
    if len(value) > 4:
        return value[:4] + "****"
    return "****"


# ── Deep merge ─────────────────────────────────────────────────────────────


def _deep_merge(base: dict, updates: dict) -> dict:
    """Deep merge *updates* into *base*, returning a new dict."""
    result = base.copy()
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ── Dot-path validation ────────────────────────────────────────────────────


def _validate_dot_path(path: str) -> None:
    """Raise AppServerError if *path* contains an invalid segment.

    Rules:
    - No empty segment
    - No segment starting with ``_``
    - No segment containing ``__``
    """
    if not path:
        raise AppServerError("Path is required", code="INVALID_PARAMS")
    for seg in path.split("."):
        if not seg:
            raise AppServerError(
                f"Invalid config path (empty segment): {path}", code="INVALID_PARAMS"
            )
        if seg.startswith("_"):
            raise AppServerError(
                f"Invalid config path (private segment): {path}", code="INVALID_PARAMS"
            )
        if "__" in seg:
            raise AppServerError(
                f"Invalid config path (dunder segment): {path}", code="INVALID_PARAMS"
            )


def _reject_provider_credential_path(path: str) -> None:
    """拒绝 batchWrite 写入 providers 子树（整个 providers 都是凭据领域）。

    拦截 "providers" 及 "providers.*" 任意层级，防止整对象替换
    （如 path="providers.deepseek"）绕过字段级拦截（CodeRabbit #912 review）。
    """
    if path == "providers" or path.startswith("providers."):
        raise AppServerError(
            f"Provider 配置（{path}）已禁用写入，请使用内置激活码",
            code="NOT_SUPPORTED",
        )


def _apply_edit(target: dict, edit: dict) -> None:
    """Apply a single edit dict to *target* in-place.

    Raises AppServerError(INVALID_PARAMS) when the path targets a key that
    does not exist in *target*, unless it is a ``desktop.*`` opaque path.
    """
    op = edit.get("op", "set")
    path = edit.get("path", "")
    _validate_dot_path(path)
    _reject_provider_credential_path(path)  # 后端收口（#835）

    segments = path.split(".")
    if op in ("set", None):
        value = edit.get("value")
        if value is None and op == "set":
            value = edit.get("value")  # allow explicit None for set
    elif op == "delete":
        value = _DELETE_SENTINEL
        # delete must target an existing key
        _ensure_path_exists(target, segments, path)
    else:
        raise AppServerError(
            f"Unsupported edit op: {op}", code="INVALID_PARAMS"
        )

    # Navigate to the parent
    current: Any = target
    is_opaque_desktop = path.startswith("desktop.")
    for seg in segments[:-1]:
        if isinstance(current, dict):
            if seg not in current:
                if is_opaque_desktop:
                    current[seg] = {}
                else:
                    raise AppServerError(
                        "Unknown config path", code="INVALID_PARAMS"
                    )
            current = current[seg]
        elif isinstance(current, list):
            try:
                idx = int(seg)
                while len(current) <= idx:
                    current.append({})
                current = current[idx]
            except ValueError:
                raise AppServerError(
                    "Invalid config path", code="INVALID_PARAMS"
                )
        else:
            raise AppServerError(
                "Invalid config path", code="INVALID_PARAMS"
            )

    last = segments[-1]
    if value is _DELETE_SENTINEL:
        if isinstance(current, dict) and last in current:
            del current[last]
            return
        raise AppServerError("Unknown config path", code="INVALID_PARAMS")
    else:
        # For set ops on non-opaque paths, the parent must exist (already
        # checked during navigation).  The last segment itself can be new.
        if not is_opaque_desktop and isinstance(current, dict) and last not in current:
            raise AppServerError("Unknown config path", code="INVALID_PARAMS")
        current[last] = value


def _ensure_path_exists(target: dict, segments: list[str], path: str) -> None:
    """Verify every segment of *path* exists in *target*.

    Used for delete ops to avoid silent no-ops on missing keys.
    desktop.* paths are exempt.
    """
    if path.startswith("desktop."):
        return
    current: Any = target
    for seg in segments:
        if isinstance(current, dict):
            if seg not in current:
                raise AppServerError("Unknown config path", code="INVALID_PARAMS")
            current = current[seg]
        elif isinstance(current, list):
            try:
                idx = int(seg)
                current = current[idx]
            except (ValueError, IndexError):
                raise AppServerError("Unknown config path", code="INVALID_PARAMS")
        else:
            raise AppServerError("Unknown config path", code="INVALID_PARAMS")


class _DeleteSentinel:
    pass


_DELETE_SENTINEL = _DeleteSentinel()


# ── Handlers ───────────────────────────────────────────────────────────────


def register_config_app_handlers(server: AppServer) -> None:

    async def _config_read(request_id, params, client_id, session_id, registry):
        """config/read — redacted effective config.

        Returns the full config dict with all secret values replaced by
        stable hints.  Does not expose raw api_key / token / password.
        """
        validate_core_params("config/read", params)

        state = get_bridge_state(registry)
        config = state.load_config()
        data = config.model_dump(by_alias=True)
        _redact_secrets(data)
        return {"result": data}

    async def _config_batch_write(request_id, params, client_id, session_id, registry):
        """config/batchWrite — atomic multi-edit save.

        Params:
            edits: list of {op, path, value} dicts
            reloadUserConfig: bool = True
        Response:
            {"saved": True, "applied": N, "propagatedSessions": N}

        All edits are applied to an in-memory copy, then validated once.
        If validation fails, nothing is written to disk.
        """
        from miqi.config.loader import save_config
        from miqi.config.schema import Config

        typed = validate_core_params("config/batchWrite", params)
        edits = [edit.model_dump(by_alias=True) for edit in typed.edits]
        reload_user_config = typed.reload_user_config

        state = get_bridge_state(registry)
        config = state.load_config()

        # Apply all edits to an in-memory dict copy
        current_dict = config.model_dump(by_alias=True)
        applied = 0
        for edit in edits:
            _apply_edit(current_dict, edit)
            applied += 1

        # Validate once
        try:
            new_config = Config.model_validate(current_dict)
        except Exception as exc:
            logger.warning("config.batchWrite: validation failed: {}", exc)
            raise AppServerError(
                "Invalid config after applying edits",
                code="INVALID_PARAMS",
            ) from exc

        # 收口（#929）：batchWrite 与 config.update 采用同一模型策略 ——
        # 仅当编辑改写了默认模型时校验「可解析到有凭据 provider / 经网关
        # 路由」，且拒绝空值。此前该路径完全没有模型门控，可原样写入
        # custom/default 等运行时无法使用的值。
        if any(e.get("path") == "agents.defaults.model" for e in edits):
            model_value = new_config.agents.defaults.model
            if not model_value:
                raise AppServerError(
                    "默认模型不能为空，请从下拉列表选择预设模型",
                    code="INVALID_PARAMS",
                )
            from miqi.runtime.provider_handlers import _model_provider_resolvable

            if not _model_provider_resolvable(new_config, model_value):
                raise AppServerError(
                    f"Unsupported model: {model_value}", code="INVALID_PARAMS",
                )

        # Save to disk (atomic from the caller's perspective — validation
        # passed, so this write is the only side effect)
        try:
            save_config(new_config)
        except Exception as exc:
            logger.error("config.batchWrite: save failed: {}", exc)
            raise AppServerError(
                "Failed to save config",
                code="INTERNAL",
            ) from exc

        # Update bridge state cache
        state.config = new_config

        # Issue #789: hot-apply to active sessions + broadcast config_updated
        # (only when the caller requested a runtime reload).
        from miqi.runtime.config_handlers import hot_apply_and_broadcast

        report = None
        propagated = 0
        if reload_user_config:
            report, propagated = await hot_apply_and_broadcast(
                registry, client_id, config, new_config,
            )

        logger.info(
            "config.batchWrite: saved {} edit(s), hot-applied to {} session(s) (client={})",
            applied, propagated, client_id,
        )

        result: dict[str, Any] = {
            "saved": True,
            "applied": applied,
            "propagatedSessions": propagated,
        }
        if report is not None:
            result["tierReport"] = report.to_dict()

        return {"result": result}

    server.register_method("config/read", _config_read, spec=protocol_specs.CONFIG_READ)
    server.register_method("config/batchWrite", _config_batch_write, spec=protocol_specs.CONFIG_BATCH_WRITE)
