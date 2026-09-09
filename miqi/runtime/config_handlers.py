"""Config handlers for AppServer dispatch.

Phase 28.3: Migrates config.get and config.update from bridge legacy
handlers to AppServer async handlers. config.update propagates changes
to active RuntimeSessions by updating their SessionState.config_snapshot.

Phase 38.5: Removed direct import of miqi.bridge.server. Uses
get_bridge_state(registry) for DI and shared helpers from
config_app_handlers for redaction and deep merge.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from miqi.runtime.app_server import AppServerError, get_bridge_state
from miqi.runtime.core_request_models import validate_core_params


def _reject_provider_credentials(updates: Any) -> None:
    """拒绝经 config.update 写入 providers（整个 providers 子树都是凭据领域）。

    providers 下只有 api_key / api_base / extra_headers 等凭据字段，无合法可写项；
    拦整个子树可同时堵住整对象替换与空值/null 写入（CodeRabbit #912 review）。
    """
    if isinstance(updates, dict) and "providers" in updates:
        raise AppServerError(
            "Provider 配置（providers）已禁用写入，请使用内置激活码",
            code="NOT_SUPPORTED",
        )


def _update_touches_model(updates: Any) -> bool:
    """Whether the update payload rewrites agents.defaults.model."""
    if not isinstance(updates, dict):
        return False
    agents = updates.get("agents")
    defaults = agents.get("defaults") if isinstance(agents, dict) else None
    return isinstance(defaults, dict) and "model" in defaults


def _apply_runtime_approval_bypass(runtime: Any, config: Any) -> None:
    services = getattr(runtime, "services", None)
    orchestrator = getattr(services, "orchestrator", None)
    permissions = getattr(orchestrator, "permissions", None)
    if permissions is not None and hasattr(permissions, "approval_bypass"):
        effective_bypass = getattr(config, "effective_approval_bypass", None)
        permissions.approval_bypass = (
            effective_bypass()
            if callable(effective_bypass)
            else getattr(config, "approvals", None)
        )


async def hot_apply_and_broadcast(
    registry: Any,
    client_id: str,
    old_config: Any,
    new_config: Any,
) -> tuple[Any, int]:
    """Issue #789: classify a config change, hot-apply to active sessions, broadcast.

    Steps:
    1. Classify the change into tiers A (hot-applied) / B (new-session) /
       C (restart-required) via :func:`miqi.config.hot_reload.classify_config_update`.
    2. Hot-apply the new config to every active RuntimeSession of this client
       (provider / model settings / snapshot / approval bypass / allowlist).
    3. Broadcast a ``config_updated`` event (with the tier report) to the
       saving client and the desktop sink so the UI can show the right
       message ("已生效" / "对新建会话生效" / "需要重启(原因)") and refresh
       its config cache.

    Returns:
        ``(report, propagated)`` — the ConfigChangeReport and the number of
        sessions hot-applied.
    """
    from miqi.config.hot_reload import classify_config_update, pending_restart_paths

    report = classify_config_update(old_config, new_config)

    # Restart banner = PENDING state, not the latest diff (2026-08-31
    # review): after a tier-C save, later tier-A/B saves must not clear the
    # banner; only a save that converges the tier-C field back to its
    # startup value may.  The bridge snapshots the startup config, so the
    # broadcast always carries the authoritative pending list.  Without a
    # snapshot (mocks/tests), keep the diff-based classification.
    state = get_bridge_state(registry)
    startup = getattr(state, "config_at_startup", None)
    if startup is not None:
        pending, pending_reasons = pending_restart_paths(new_config, startup)
        report.restart_required = pending
        report.restart_reasons = pending_reasons

    # Gate every hot-apply step by the ACTUAL changed tier-A paths — the
    # classifier table and apply_config_update share this contract, so a save
    # that didn't touch providers/model won't rebuild the provider nor clobber
    # the compressor's incremental summary state (#1/#2/#5/#6 review).
    applied_paths = report.applied

    propagated = 0
    provider_rebuilt = True  # no sessions → nothing failed; new sessions rebuild
    for sid in registry.list_sessions(client_id):
        runtime = await registry.get_session(client_id, sid)
        if runtime is None:
            continue
        try:
            services = getattr(runtime, "services", None)
            if services is not None and hasattr(services, "apply_config_update"):
                applied_info = services.apply_config_update(
                    new_config,
                    changed_paths=applied_paths,
                )
                if applied_info.get("provider_rebuilt") is False:
                    provider_rebuilt = False
            else:
                # Fallback for runtimes without apply_config_update: keep the
                # historical behavior (snapshot + approval bypass).
                session_state = getattr(services, "session_state", None)
                if session_state is not None:
                    session_state.config_snapshot = new_config
                _apply_runtime_approval_bypass(runtime, new_config)
            propagated += 1
        except Exception as exc:
            # A raise during hot-apply means THIS session's apply failed —
            # when a provider rebuild was attempted, the broadcast must
            # report that honestly (2026-09-01 review: the exception path
            # kept the True default and claimed a rebuilt provider).
            provider_rebuilt = False
            logger.warning(
                "config hot-apply: failed to apply to session {}: {}",
                sid, exc,
            )

    app_server = getattr(registry, "bridge_context", {}).get("app_server")
    if app_server is not None:
        payload = {
            **report.to_dict(),
            "propagatedSessions": propagated,
        }
        # providerRebuilt is only meaningful when a rebuild was ATTEMPTED
        # (providers / model touched) — an unrelated save (temperature,
        # approval policy, …) must not emit the field at all, even as False
        # (2026-08-31 review; the UI keys the "重建失败" toast off it).
        rebuild_attempted = any(
            p == "providers" or p.startswith("providers.")
            or p == "agents.defaults.model"
            for p in applied_paths
        )
        if rebuild_attempted:
            payload["providerRebuilt"] = provider_rebuilt
        # Emit ONCE per logical audience (#13 review): the desktop client's
        # sink IS the global "desktop" sink (loop.py mirrors it at client
        # registration), so sending both would double every event.  Only
        # distinct sinks get separate sends.
        sinks = getattr(app_server, "_event_sinks", {})
        desktop_sink = sinks.get("desktop")
        client_sink = sinks.get(client_id)
        if client_sink is not None and client_sink is desktop_sink:
            emit_targets = ("desktop",)
        else:
            emit_targets = ("desktop",) if client_id == "desktop" else (
                client_id, "desktop",
            )
        for target in emit_targets:
            try:
                await app_server.emit_client_event(target, "config_updated", payload)
            except Exception as exc:
                logger.debug(
                    "config hot-apply: config_updated emit to {} failed: {}",
                    target, exc,
                )

    return report, propagated


async def config_get_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Get current configuration with secrets redacted.

    Returns the full config dict with API key values replaced by hints
    (e.g., "sk-a…b123").
    """
    validate_core_params("config.get", params)

    from miqi.runtime.config_app_handlers import _redact_secrets

    state = get_bridge_state(registry)
    config = state.load_config()
    data = config.model_dump(by_alias=True)
    _redact_secrets(data)
    return {"result": data}


async def config_update_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Update configuration and propagate to active sessions.

    1. Deep-merge updates into current config
    2. Validate the merged config
    3. Save to disk
    4. Update AppServer-level config reference
    5. Propagate config snapshot to active RuntimeSessions
    """
    typed = validate_core_params("config.update", params)
    updates = typed.config

    _reject_provider_credentials(updates)  # 后端收口（#835）

    from miqi.config.loader import save_config
    from miqi.config.schema import Config
    from miqi.runtime.config_app_handlers import _deep_merge

    state = get_bridge_state(registry)

    current = state.load_config()

    # 比较并设置（#991）：本次更新改写默认模型且调用方声明了期望的当前
    # 模型值时，只在磁盘当前值仍与期望一致时写入。登录后的网关模型自动
    # 同步（GatewayModelAutoSync）读取快照与写入之间用户可能已手动选了
    # 别的模型 —— 不一致时跳过，保留用户更新的选择。
    expected_model = getattr(typed, "expect_model", None)
    if expected_model is not None and _update_touches_model(updates):
        current_model = getattr(current.agents.defaults, "model", "") or ""
        if current_model != expected_model:
            logger.info(
                "config.update: skipped, expect_model mismatch (current={!r}, expected={!r})",
                current_model, expected_model,
            )
            return {"result": {"saved": False, "skipped": "expect_model_mismatch"}}

    # Merge in snake_case (by_alias=False): the wire format accepts both
    # snake_case and camelCase keys, but Pydantic's alias takes precedence
    # when both exist.  Dumping snake_case means a snake_case update wins
    # AND a camelCase update (which creates a duplicate camel key on top of
    # the snake dump) also wins via the alias — previously a snake_case
    # update was silently ignored (#789 实录: wsl_distro save lost).
    merged = _deep_merge(current.model_dump(by_alias=False), updates)

    # Validate
    try:
        new_config = Config.model_validate(merged)
    except Exception as exc:
        logger.warning("config.update: validation failed: {}", exc)
        raise AppServerError(
            "Invalid config",
            code="INVALID_PARAMS",
        ) from exc

    # 收口（#929）：仅当本次更新改写了默认模型时才校验 —— 模型必须能被
    # 运行时解析到「有凭据可用」的 provider（或经已配置网关路由），否则会
    # 落到 _match_provider 兜底错发到错误 API。无关字段的保存不受历史遗留
    # 模型值影响（#929 review：此前按合并后的当前模型校验会拒绝一切保存）。
    if _update_touches_model(updates):
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

    # Save to disk
    try:
        save_config(new_config)
    except Exception as exc:
        logger.error("config.update: save failed: {}", exc)
        raise AppServerError(
            "Failed to save config",
            code="INTERNAL",
        ) from exc

    # Update bridge state cache
    state.config = new_config

    # Issue #789: hot-apply to active sessions + broadcast config_updated
    # (tier A hot-applied, tier B new-session, tier C restart-required).
    report, propagated = await hot_apply_and_broadcast(
        registry, client_id, current, new_config,
    )

    logger.info(
        "config.update: saved and hot-applied to {} session(s) (client={}) "
        "tiers: {} applied / {} new-session / {} restart",
        propagated, client_id,
        len(report.applied), len(report.new_sessions_only),
        len(report.restart_required),
    )

    return {"result": {"saved": True, "propagated_sessions": propagated}}
