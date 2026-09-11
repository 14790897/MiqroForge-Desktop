"""Hot config reload classification tests (issue #789)."""

from __future__ import annotations

from miqi.config.hot_reload import classify_config_update
from miqi.config.schema import Config


def _mutate(**changes) -> Config:
    """Return a config copy with the given dotted-path mutations applied."""
    cfg = Config()
    for path, value in changes.items():
        parts = path.split(".")
        target = cfg
        for part in parts[:-1]:
            target = getattr(target, part)
        setattr(target, parts[-1], value)
    return cfg


def test_model_change_is_tier_a():
    """test_model_change_is_tier_a: model change is tier a."""
    new = _mutate(**{"agents.defaults.model": "deepseek/deepseek-chat"})
    report = classify_config_update(Config(), new)
    assert "agents.defaults.model" in report.applied
    assert report.needs_restart is False


def test_temperature_change_is_tier_a():
    """test_temperature_change_is_tier_a: temperature change is tier a."""
    new = _mutate(**{"agents.defaults.temperature": 0.5})
    report = classify_config_update(Config(), new)
    assert "agents.defaults.temperature" in report.applied


def test_provider_api_key_change_is_tier_a():
    """test_provider_api_key_change_is_tier_a: provider api key change is tier a."""
    new = _mutate(**{"providers.deepseek.api_key": "sk-test"})
    report = classify_config_update(Config(), new)
    assert "providers.deepseek.api_key" in report.applied


def test_approval_bypass_change_is_tier_a():
    """test_approval_bypass_change_is_tier_a: approval bypass change is tier a."""
    new = _mutate(**{"approvals.bypass_all": True})
    report = classify_config_update(Config(), new)
    assert "approvals.bypass_all" in report.applied


def test_sandbox_enabled_toggle_is_new_session():
    """test_sandbox_enabled_toggle_is_new_session: sandbox toggle is tier B now.

    The sandbox runtime is built at process start; only the dedicated
    sandbox.setEnabled handler hot-switches it.  config.update of the flag
    must honestly report new-session (2026-08-26 review #2).
    """
    new = _mutate(**{"tools.sandbox.enabled": False})
    report = classify_config_update(Config(), new)
    assert "tools.sandbox.enabled" in report.new_sessions_only
    assert "tools.sandbox.enabled" not in report.applied


def test_web_search_provider_is_tier_b():
    """test_web_search_provider_is_tier_b: web search provider is tier b."""
    new = _mutate(**{"tools.web.search.provider": "tavily"})
    report = classify_config_update(Config(), new)
    assert "tools.web.search.provider" in report.new_sessions_only
    assert report.applied == []


def test_workspace_change_is_tier_b():
    """test_workspace_change_is_tier_b: workspace change is tier b."""
    new = _mutate(**{"agents.defaults.workspace": "/tmp/ws"})
    report = classify_config_update(Config(), new)
    assert "agents.defaults.workspace" in report.new_sessions_only


def test_wsl_distro_change_is_tier_c_with_reason():
    """test_wsl_distro_change_is_tier_c_with_reason: wsl distro change is tier c with reason."""
    new = _mutate(**{"tools.sandbox.wsl_distro": "Ubuntu"})
    report = classify_config_update(Config(), new)
    assert "tools.sandbox.wsl_distro" in report.restart_required
    assert report.needs_restart is True
    assert len(report.restart_reasons) == 1
    assert "WSL" in report.restart_reasons[0]


def test_gateway_port_change_is_tier_c():
    """test_gateway_port_change_is_tier_c: gateway port change is tier c."""
    new = _mutate(**{"gateway.port": 9999})
    report = classify_config_update(Config(), new)
    assert "gateway.port" in report.restart_required


def test_mcp_servers_change_is_tier_b():
    """MCP servers connect at session creation — new sessions pick them up
    without an app restart (Phase MCP integration)."""
    from miqi.config.schema import MCPServerConfig

    new = _mutate(**{"tools.mcp_servers": {"server1": MCPServerConfig(command="npx")}})
    report = classify_config_update(Config(), new)
    # Diff recurses into the dict; the prefix rule still classifies as B.
    assert any(p.startswith("tools.mcp_servers") for p in report.new_sessions_only)
    assert "tools.mcp_servers" not in report.restart_required
    assert report.needs_restart is False


def test_mixed_change_reports_all_tiers():
    """test_mixed_change_reports_all_tiers: mixed change reports all tiers."""
    new = Config()
    new.agents.defaults.model = "deepseek/deepseek-chat"  # A
    new.tools.web.search.provider = "tavily"  # B
    new.tools.sandbox.wsl_distro = "Ubuntu"  # C
    report = classify_config_update(Config(), new)
    assert report.applied == ["agents.defaults.model"]
    assert report.new_sessions_only == ["tools.web.search.provider"]
    assert report.restart_required == ["tools.sandbox.wsl_distro"]


def test_no_change_produces_empty_report():
    """test_no_change_produces_empty_report: no change produces empty report."""
    report = classify_config_update(Config(), Config())
    assert report.applied == []
    assert report.new_sessions_only == []
    assert report.restart_required == []
    assert report.needs_restart is False


def test_to_dict_uses_camel_case_keys():
    """test_to_dict_uses_camel_case_keys: to dict uses camel case keys."""
    new = _mutate(**{"tools.sandbox.wsl_distro": "Ubuntu"})
    d = classify_config_update(Config(), new).to_dict()
    assert "restartRequired" in d
    assert "restartReasons" in d
    assert "newSessionsOnly" in d
    assert "applied" in d


def test_prefix_boundary_no_false_positive():
    """max_tokens change must not match the max_tool_* prefixes."""
    new = _mutate(**{"agents.defaults.max_tokens": 4096})
    report = classify_config_update(Config(), new)
    assert "agents.defaults.max_tokens" in report.applied
    # A sibling path that is NOT tier A stays tier B.
    new2 = _mutate(**{"agents.defaults.max_tool_result_chars": 100})
    report2 = classify_config_update(Config(), new2)
    assert "agents.defaults.max_tool_result_chars" in report2.applied


def test_snake_case_update_survives_validation():
    """Regression: config.update deep-merge must not drop snake_case keys.

    The merged dict used to be built from a by_alias=True (camelCase) dump;
    Pydantic's alias takes precedence when both spellings exist, so a
    snake_case update (wsl_distro) was silently ignored and the value never
    changed (#789 E2E 实录).
    """
    from miqi.runtime.config_app_handlers import _deep_merge

    current = Config()
    merged = _deep_merge(
        current.model_dump(by_alias=False),
        {"tools": {"sandbox": {"wsl_distro": "Ubuntu"}}},
    )
    new_config = Config.model_validate(merged)
    assert new_config.tools.sandbox.wsl_distro == "Ubuntu"


def test_camel_case_update_survives_validation():
    """camelCase updates (config.get → modify → save round-trip) still work."""
    from miqi.runtime.config_app_handlers import _deep_merge

    current = Config()
    merged = _deep_merge(
        current.model_dump(by_alias=False),
        {"tools": {"sandbox": {"wslDistro": "Ubuntu"}}},
    )
    new_config = Config.model_validate(merged)
    assert new_config.tools.sandbox.wsl_distro == "Ubuntu"


# ── pending_restart_paths (banner semantics, 2026-08-31 review) ─────────────


def test_pending_restart_detects_tier_c_drift_from_startup():
    """A tier-C field differing from the startup snapshot stays pending."""
    from miqi.config.hot_reload import pending_restart_paths

    startup = Config()
    current = _mutate(**{"tools.sandbox.wsl_distro": "Debian"})
    paths, reasons = pending_restart_paths(current, startup)
    assert "tools.sandbox.wsl_distro" in paths
    assert any("WSL" in r for r in reasons)


def test_pending_restart_empty_when_converged_back_to_startup():
    """Reverting the tier-C field to its startup value clears the pending state."""
    from miqi.config.hot_reload import pending_restart_paths

    startup = _mutate(**{"tools.sandbox.wsl_distro": "Ubuntu"})
    changed = _mutate(**{"tools.sandbox.wsl_distro": "Debian"})
    reverted = _mutate(**{"tools.sandbox.wsl_distro": "Ubuntu"})

    paths, _ = pending_restart_paths(changed, startup)
    assert "tools.sandbox.wsl_distro" in paths
    paths2, reasons2 = pending_restart_paths(reverted, startup)
    assert paths2 == []
    assert reasons2 == []


def test_pending_restart_ignores_non_c_drift():
    """Tier-A/B differences vs startup must not appear in the pending list."""
    from miqi.config.hot_reload import pending_restart_paths

    startup = Config()
    current = _mutate(**{"agents.defaults.temperature": 0.9})
    paths, reasons = pending_restart_paths(current, startup)
    assert paths == []
    assert reasons == []


def test_pending_restart_unknown_without_startup_snapshot():
    """No startup snapshot → pending state unknown → empty (legacy behavior)."""
    from miqi.config.hot_reload import pending_restart_paths

    current = _mutate(**{"tools.sandbox.wsl_distro": "Debian"})
    assert pending_restart_paths(current, None) == ([], [])
