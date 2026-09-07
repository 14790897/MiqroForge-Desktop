"""Config hot-reload classification and runtime application.

Issue #789: after config.save, classify the changed fields into three tiers:

- **A (hot-applied)** — saved now and applied to active runtimes without a
  restart (provider / model / approval bypass / model settings / config
  snapshot).  New turns immediately see the new values; an in-flight turn
  keeps the provider it captured at turn start.
- **B (new-session)** — saved now, takes effect for new sessions / new turns
  (tool registries are built at session creation, channel connections are
  process-level).
- **C (restart)** — process-level settings that cannot change at runtime
  (WSL distro, gateway bind address, runtime engine selection, …).  These
  keep the "restart required" prompt with a human-readable reason.

The classification result is broadcast to the frontend as a
``config_updated`` event so the UI can show the right message per tier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

# ── Tier C: restart required ─────────────────────────────────────────────
# Exact dotted path (or prefix) → human-readable reason shown in the UI.
TIER_C_PATHS: dict[str, str] = {
    "tools.sandbox.wsl_distro": "WSL 发行版在进程启动时检测，修改后需重启应用",
    "tools.sandbox.wsl_base_dir": "沙箱目录在进程启动时初始化，修改后需重启应用",
    "tools.sandbox.sandbox_distro_name": "沙箱发行版在进程启动时导入，修改后需重启应用",
    "gateway.host": "网关监听地址在进程启动时绑定，修改后需重启应用",
    "gateway.port": "网关监听端口在进程启动时绑定，修改后需重启应用",
    "agents.defaults.runtime": "运行时引擎在会话创建时选择，修改后需重启应用",
    "agents.sessions.use_sqlite": "会话存储后端在启动时选择，修改后需重启应用",
}

# ── Tier A: hot-applied prefixes ─────────────────────────────────────────
# A changed path under any of these prefixes is hot-applied to active
# runtimes.  Everything else defaults to tier B (new-session).
#
# CONTRACT (2026-08-26 review): every tier-A prefix MUST have a real
# application step in RuntimeServices.apply_config_update().  Paths whose
# runtime consumer is built once at session/process start (sandbox runtime,
# channels manager, observability sink, memory/experience hooks) are tier B
# — claiming "已生效" for them would be a lie.  Keep this table in sync with
# services.apply_config_update's gated steps.
#
# NOTE: no trailing dots — matching uses ``path == p or path.startswith(p + ".")``
# and paths are compared in snake_case (``model_dump(by_alias=False)``).
TIER_A_PREFIXES: tuple[str, ...] = (
    # provider / model — rebuilt via make_provider (step 1)
    "providers",
    "agents.defaults.model",
    # model settings consumed per-turn from services.model_settings (step 2)
    "agents.defaults.temperature",
    "agents.defaults.max_tokens",
    "agents.defaults.max_tool_result_chars",
    "agents.defaults.context_limit_chars",
    "agents.defaults.max_tool_iterations",
    "agents.defaults.name",
    # approval policy — hot-applied to orchestrator permissions (step 4)
    "approvals",
    "agents.command_approval",
    # permanent allowlist — replaced from config (step 5)
    "agents.permanent_approvals",
    # desktop-owned UI settings (opaque dict, consumed by the frontend which
    # re-reads the config cache after the config_updated broadcast)
    "desktop",
)


@dataclass
class ConfigChangeReport:
    """Result of classifying a config update into hot-reload tiers."""

    applied: list[str] = field(default_factory=list)            # tier A paths
    new_sessions_only: list[str] = field(default_factory=list)  # tier B paths
    restart_required: list[str] = field(default_factory=list)   # tier C paths
    restart_reasons: list[str] = field(default_factory=list)    # reasons for C

    @property
    def needs_restart(self) -> bool:
        """True when any tier-C (restart-required) path changed."""
        return bool(self.restart_required)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report for the config_updated event payload."""
        return {
            "applied": self.applied,
            "newSessionsOnly": self.new_sessions_only,
            "restartRequired": self.restart_required,
            "restartReasons": self.restart_reasons,
        }


def _diff_paths(old: dict[str, Any], new: dict[str, Any], prefix: str = "") -> list[str]:
    """Return dotted paths that differ between two config dicts.

    Recurses into nested dicts; a value-type change at any level records
    the path at that level.  Lists and scalars are compared directly.
    """
    changed: list[str] = []
    for key, new_val in new.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in old:
            changed.append(path)
            continue
        old_val = old[key]
        if isinstance(new_val, dict) and isinstance(old_val, dict):
            changed.extend(_diff_paths(old_val, new_val, path))
        elif new_val != old_val:
            changed.append(path)
    # Keys removed from new (present in old only) also count as changes.
    for key in old:
        if key not in new:
            path = f"{prefix}.{key}" if prefix else key
            changed.append(path)
    return changed


def classify_config_update(old: Any, new: Any) -> ConfigChangeReport:
    """Classify changed config paths into hot-reload tiers A / B / C.

    Args:
        old: previous Config model (or any dict-like with ``model_dump``).
        new: new Config model (or any dict-like with ``model_dump``).

        NOTE: dicts are consumed as-is — snake_case keys (matching Pydantic
        field names, e.g. ``wsl_distro``) are expected; camelCase keys from
        the wire format are NOT auto-translated (2026-08-26 review, #15).
        All three production call sites pass Config models, so this is not
        a practical limitation.

    Returns:
        ConfigChangeReport with per-tier path lists.  Tier C paths also
        carry human-readable reasons for the UI.
    """
    # Diff in snake_case (by_alias=False) so rule tables match Pydantic
    # field names (wsl_distro, api_key, …) regardless of the wire format.
    old_dict = old.model_dump(by_alias=False) if hasattr(old, "model_dump") else dict(old)
    new_dict = new.model_dump(by_alias=False) if hasattr(new, "model_dump") else dict(new)

    changed = _diff_paths(old_dict, new_dict)

    report = ConfigChangeReport()
    seen_reasons: set[str] = set()

    for path in changed:
        # Tier C — exact match or prefix match against the C table.
        c_reason = None
        for c_path, reason in TIER_C_PATHS.items():
            if path == c_path or path.startswith(c_path + "."):
                c_reason = reason
                break
        if c_reason is not None:
            report.restart_required.append(path)
            if c_reason not in seen_reasons:
                seen_reasons.add(c_reason)
                report.restart_reasons.append(c_reason)
            continue

        # Tier A — prefix match.
        if any(path == p or path.startswith(p + ".") for p in TIER_A_PREFIXES):
            report.applied.append(path)
            continue

        # Default: tier B (new-session).
        report.new_sessions_only.append(path)

    logger.debug(
        "config hot-reload classify: {} applied / {} new-session / {} restart",
        len(report.applied), len(report.new_sessions_only), len(report.restart_required),
    )
    return report


def pending_restart_paths(
    current: Any, startup: Any | None,
) -> tuple[list[str], list[str]]:
    """Tier-C paths whose current value differs from the process-startup value.

    The restart banner must reflect PENDING state, not the latest diff
    (2026-08-31 review): after a tier-C save, later tier-A/B saves must not
    clear the banner, and only a save that converges the tier-C field back
    to its startup value (or an app restart) may clear it.  The backend is
    the single source of truth — every ``config_updated`` broadcast carries
    this pending list.

    Args:
        current: the config right after the save.
        startup: the config snapshot taken at process start
            (``BridgeState.config_at_startup``), or None when unavailable —
            in which case the pending state is unknown and ``([], [])`` is
            returned so legacy callers keep the diff-based behavior.

    Returns:
        ``(paths, reasons)`` — pending tier-C paths (snake_case) and their
        human-readable reasons (deduplicated, table order).
    """
    if startup is None:
        return [], []
    cur = current.model_dump(by_alias=False) if hasattr(current, "model_dump") else dict(current)
    st = startup.model_dump(by_alias=False) if hasattr(startup, "model_dump") else dict(startup)

    paths: list[str] = []
    reasons: list[str] = []
    seen: set[str] = set()
    for path in _diff_paths(st, cur):
        c_reason = next(
            (
                reason
                for c_path, reason in TIER_C_PATHS.items()
                if path == c_path or path.startswith(c_path + ".")
            ),
            None,
        )
        if c_reason is not None:
            paths.append(path)
            if c_reason not in seen:
                seen.add(c_reason)
                reasons.append(c_reason)
    return paths, reasons
