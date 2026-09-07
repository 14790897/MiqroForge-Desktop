"""Configuration loading utilities."""

import json
import threading
import time
from pathlib import Path
from typing import Callable

from loguru import logger

from miqi.config.schema import Config
from miqi.paths import get_config_path, get_legacy_config_path

_cache: dict[tuple, tuple[float, Config]] = {}
_CACHE_TTL_S = 5.0

#: 跨写入方共享的 config.json 写锁（#875 review F2/F12）：所有"单字段
#: fresh-read-修改-写回"路径（系统包安装开关、extra-root persister、
#: loop.py 开关 handler）都必须持同一把锁，否则并发写会互相覆盖
#: （stale-cache 回写 / 丢失对方更新）。
_config_write_lock = threading.Lock()


def update_config_field(mutator: Callable[[Config], None]) -> bool:
    """Atomically read-modify-write the user config under the shared lock.

    #875 review F1/F2: every writer that persists one field of the user
    config must go through this helper so concurrent writers cannot
    clobber each other:

    - reads from disk under :data:`_config_write_lock` (bypassing the 5s
      module cache) via the legacy-path fallback (``_get_load_path``) and
      applies ``_migrate_config`` — a raw ``open(get_config_path())`` would
      silently write a default config next to a legacy-path config and wipe
      all user settings;
    - calls *mutator* on the freshly loaded config, then saves it back to
      the same path.

    Returns:
        True when the write succeeded; False when the config file could not
        be read **or saved** — the failure contract is uniform for every
        caller (approver persists persist_failed, settings IPC raises
        "Failed to save config", extra-root persister stays silent), instead
        of read-failures returning False while save-failures raised.
    """
    with _config_write_lock:
        path = _get_load_path()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("update_config_field: cannot read config %s: %s", path, exc)
            return False
        data = _migrate_config(data)
        config = Config.model_validate(data) if data else Config()
        mutator(config)
        try:
            save_config(config, path)
        except Exception as exc:  # noqa: BLE001 - 契约：写盘失败 = False
            logger.warning("update_config_field: cannot save config %s: %s", path, exc)
            return False
        return True


def _get_load_path() -> Path:
    """Return the preferred config path, falling back to legacy for reads only."""
    preferred = get_config_path()
    legacy = get_legacy_config_path()
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy


def get_data_dir() -> Path:
    """Get runtime data directory."""
    from miqi.utils.helpers import get_data_path
    return get_data_path()


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or _get_load_path()
    cache_key = (str(path),)
    now = time.time()
    entry = _cache.get(cache_key)
    if entry is not None:
        ts, cfg = entry
        if now - ts < _CACHE_TTL_S:
            return cfg

    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data = _migrate_config(data)
            config = Config.model_validate(data)

            # Phase 31.X: load permanent approvals into global allowlist
            _init_permanent_approvals(config)

            _cache[cache_key] = (now, config)
            return config
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to load config from {path}: {e}")
            logger.warning("Using default configuration.")

    config = Config()
    _cache[cache_key] = (now, config)
    return config


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(by_alias=True)

    # Prune providers that are completely unconfigured (apiKey="" apiBase=null
    # extraHeaders=null) so they don't clutter the config file.
    providers = data.get("providers", {})
    pruned = {
        k: v
        for k, v in providers.items()
        if v.get("apiKey") or v.get("apiBase") is not None or v.get("extraHeaders")
    }
    if pruned != providers:
        data["providers"] = pruned

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    path.chmod(0o600)  # Restrict to owner only — config contains API keys

    # Bust cache so next read picks up the new config.
    _cache.pop((str(path),), None)


def save_config_allowlist(patterns: set[str]) -> None:
    """Persist permanent approval patterns into the user config.

    Writes the supplied patterns as an EXACT replacement of
    ``permanent_approvals`` in config.json so approved tool+argument keys
    survive bridge restarts — and REMOVED patterns do not resurrect
    (2026-09-01 review: the previous union-with-existing merge made a
    clear-all persist a no-op on disk, so cleared patterns came back on
    the next load).
    """

    # #875 review: route through update_config_field (shared lock +
    # fresh disk read) like every other single-field writer.  The old
    # bare load_config(path)+save_config(path) read a 5-second-cached
    # Config — a stale read could resurrect removed patterns, and a
    # concurrent writer (e.g. allow_always persist) could clobber this
    # field or vice versa.
    def _mutate(config) -> None:
        config.agents.permanent_approvals = sorted(patterns)

    if not update_config_field(_mutate):
        logger.warning(
            "save_config_allowlist: failed to persist permanent approvals "
            "(patterns kept in memory for this session only)"
        )


def _init_permanent_approvals(config: Config) -> None:
    """Load permanent approval patterns from config into global allowlist."""
    patterns = getattr(config.agents, "permanent_approvals", None) or []
    if patterns:
        try:
            from miqi.agent.command_approval import load_permanent_allowlist
            load_permanent_allowlist(set(patterns))
            logger.debug("Loaded {} permanent approval patterns", len(patterns))
        except Exception as exc:
            logger.warning("Failed to load permanent approvals: {}", exc)


def _pick_migrated_model(data: dict) -> str:
    """Choose a default model the runtime can use after a custom/* reset.

    Prefers the first configured provider's test model from the raw config
    dict. Returns the empty string when nothing usable exists — an explicit
    "not selected" state (UI shows 未设置 and prompts model selection),
    rather than an unusable factory default（#933 review）.
    """
    from miqi.providers.registry import PROVIDER_TEST_MODELS, PROVIDERS

    providers_raw = data.get("providers") or {}
    for spec in PROVIDERS:
        entry = providers_raw.get(spec.name) or {}
        if not isinstance(entry, dict):
            continue
        api_key = entry.get("api_key") or entry.get("apiKey") or ""
        api_base = entry.get("api_base") or entry.get("apiBase") or ""
        if spec.is_local:
            if not api_base:
                continue
        elif not api_key:
            continue
        model = PROVIDER_TEST_MODELS.get(spec.name)
        if not model:
            continue
        if spec.is_gateway or spec.is_local:
            return model
        return f"{spec.name}/{model}"
    return ""


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")

    # #561: web_search key split — old single api_key was the Brave key.
    search = tools.get("web", {}).get("search", {})
    old_key = search.get("api_key")
    if old_key and not search.get("brave_api_key"):
        search["brave_api_key"] = old_key
    search.pop("api_key", None)  # drop the legacy field after migration

    # #835 收口：custom provider 已从运行时移除。遗留的 custom/* 默认模型
    # 会经 _match_provider 兜底错发到第一个已配置 provider 的 API（#929
    # review）——迁移时重置为一个运行时真正可用的模型（优先已配置
    # provider 的测试模型，#933 review），让用户在设置页重新选择。
    model = (data.get("agents") or {}).get("defaults") or {}
    if isinstance(model, dict) and isinstance(model.get("model"), str) and model["model"].lower().startswith("custom/"):
        logger.warning(
            "migrate: default model '{}' uses removed custom provider — "
            "reset to a usable model", model["model"],
        )
        model["model"] = _pick_migrated_model(data)
        data.setdefault("agents", {})["defaults"] = model
    return data
