"""RuntimeServices.apply_config_update hot-apply tests (issue #789)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from miqi.config.schema import Config
from miqi.runtime.context_runtime import ContextRuntime
from miqi.runtime.services import RuntimeServices


def _make_services(provider=None):
    services = RuntimeServices(
        session_id="test:1",
        workspace=Path("."),
        bus=None,
        provider=provider,
        event_emitter=None,
        model_settings=None,
        tool_registry=None,
        orchestrator=SimpleNamespace(permissions=SimpleNamespace(approval_bypass=None)),
        agent_registry=None,
        agent_control=None,
        tool_runtime=None,
        context_runtime=None,
        turn_runner=SimpleNamespace(_provider=provider, _max_iterations=500, _running=False),
        plugin_manager=None,
    )
    services.session_state = SimpleNamespace(config_snapshot=None)
    return services


def test_updates_model_settings_and_snapshot():
    """test_updates_model_settings_and_snapshot: updates model settings and snapshot."""
    services = _make_services()
    cfg = Config()
    cfg.agents.defaults.model = "deepseek/deepseek-chat"
    cfg.agents.defaults.temperature = 0.7
    cfg.agents.defaults.max_tokens = 2048
    cfg.agents.defaults.max_tool_iterations = 250

    result = services.apply_config_update(
        cfg,
        changed_paths=[
            "agents.defaults.model",
            "agents.defaults.temperature",
            "agents.defaults.max_tokens",
            "agents.defaults.max_tool_iterations",
        ],
    )

    assert services.model_settings.model == "deepseek/deepseek-chat"
    assert services.model_settings.temperature == 0.7
    assert services.model_settings.max_tokens == 2048
    assert services.model_settings.max_tool_result_chars == cfg.agents.defaults.max_tool_result_chars
    assert services.session_state.config_snapshot is cfg
    assert services.turn_runner._max_iterations == 250
    # No API key configured → make_provider raises → old provider retained.
    assert result["provider_rebuilt"] is False


def test_rebuilds_provider_when_configured():
    """test_rebuilds_provider_when_configured: rebuilds provider when configured."""
    old_provider = object()
    services = _make_services(provider=old_provider)
    cfg = Config()
    cfg.providers.deepseek.api_key = "sk-test"

    result = services.apply_config_update(cfg, changed_paths=["providers.deepseek.api_key"])

    assert result["provider_rebuilt"] is True
    assert services.provider is not old_provider
    assert services.turn_runner._provider is services.provider


def test_failed_provider_rebuild_keeps_old_provider():
    """test_failed_provider_rebuild_keeps_old_provider: failed provider rebuild keeps old provider."""
    old_provider = object()
    services = _make_services(provider=old_provider)
    cfg = Config()  # no api key → make_provider raises ValueError
    cfg.agents.defaults.temperature = 0.9

    result = services.apply_config_update(
        cfg,
        changed_paths=["providers.deepseek.api_key", "agents.defaults.temperature"],
    )

    assert result["provider_rebuilt"] is False
    assert services.provider is old_provider
    # Other gated fields still hot-applied (no half-updated state).
    assert services.model_settings.temperature == 0.9
    assert services.session_state.config_snapshot is cfg


def test_in_flight_turn_keeps_captured_provider():
    """An in-flight turn must keep its captured provider (#1 review).

    The running flag is set on the turn_runner while run() executes; the
    provider reference is only swapped between turns, so a running turn
    never mixes a NEW provider with its OLD model string.  The rebuild
    still counts as successful — the replacement is parked on the runner
    and adopted at the start of the next run().
    """
    old_provider = object()
    services = _make_services(provider=old_provider)
    services.turn_runner = SimpleNamespace(
        _provider=old_provider, _max_iterations=500, _running=True
    )
    cfg = Config()
    cfg.providers.deepseek.api_key = "sk-test"

    result = services.apply_config_update(cfg, changed_paths=["providers.deepseek.api_key"])

    # services.provider is swapped (next turn uses it)…
    assert services.provider is not old_provider
    # …but the running turn_runner keeps the captured provider (#1).
    assert services.turn_runner._provider is old_provider
    # The swap is deferred, not failed — the next turn adopts it.
    assert result["provider_rebuilt"] is True
    assert services.turn_runner._pending_provider is services.provider


def test_pending_provider_adopted_at_next_turn():
    """A provider parked during a running turn is adopted by the NEXT run (#789)."""
    from miqi.runtime.turn_runner import TurnRunner

    old_provider = object()
    runner = TurnRunner(
        provider=old_provider,
        tool_runtime=None,
        context_runtime=None,
        event_emitter=None,
        max_iterations=10,
    )
    runner._running = True
    services = _make_services(provider=old_provider)
    services.turn_runner = runner
    cfg = Config()
    cfg.providers.deepseek.api_key = "sk-test"

    services.apply_config_update(cfg, changed_paths=["providers.deepseek.api_key"])

    assert runner._provider is old_provider
    assert runner._pending_provider is services.provider
    # Simulate the start of the next run().
    runner._adopt_pending_provider()
    assert runner._provider is services.provider
    assert runner._pending_provider is None


def test_failed_rebuild_keeps_previous_model():
    """A failed provider rebuild must not pair the old provider with a NEW model.

    make_provider raises when the API key is missing; the session keeps the
    old provider object, so the model string must stay the old one too —
    otherwise the next turn sends the new model name to the old provider
    (no half-updated provider/model state).
    """
    from miqi.runtime.services import RuntimeModelSettings

    old_provider = object()
    services = _make_services(provider=old_provider)
    services.model_settings = RuntimeModelSettings(
        model="old-model",
        temperature=0.5,
        max_tokens=1024,
        max_tool_result_chars=8000,
        context_limit_chars=300000,
    )
    cfg = Config()  # no api key → make_provider raises
    cfg.agents.defaults.model = "new-model"
    cfg.agents.defaults.temperature = 0.9

    result = services.apply_config_update(
        cfg,
        changed_paths=[
            "providers.deepseek.api_key",
            "agents.defaults.model",
            "agents.defaults.temperature",
        ],
    )

    assert result["provider_rebuilt"] is False
    assert services.provider is old_provider
    # Model rolls back with the failed rebuild — no provider/model mismatch.
    assert services.model_settings.model == "old-model"
    # Fields unaffected by the failure still hot-apply.
    assert services.model_settings.temperature == 0.9


def test_unrelated_save_does_not_rebuild_provider():
    """A save that didn't touch providers/model must not rebuild (#6 review)."""
    old_provider = object()
    services = _make_services(provider=old_provider)
    cfg = Config()
    cfg.desktop["ui"] = {"theme": "dark"}

    services.apply_config_update(cfg, changed_paths=["desktop.ui.theme"])

    assert services.provider is old_provider
    assert services.model_settings is None  # step 2 gated off too


def test_lone_iteration_cap_save_still_applies():
    """A save that ONLY changes max_tool_iterations must apply it (2nd review).

    The iteration cap used to be nested inside the model-settings gate — a
    lone iteration-cap save was classified tier A ("已生效") but never
    reached the update, silently keeping the old cap until restart.
    """
    services = _make_services()
    cfg = Config()
    cfg.agents.defaults.max_tool_iterations = 123

    services.apply_config_update(cfg, changed_paths=["agents.defaults.max_tool_iterations"])

    assert services.turn_runner._max_iterations == 123


def test_unrelated_save_preserves_compressor_state():
    """An unrelated save must not rebuild the context compressor (#5 review).

    Rebuilding unconditionally would destroy the five-phase incremental
    summary state (_previous_summary) and the failure cooldown.
    """
    services = _make_services()
    services.context_runtime = ContextRuntime(
        llm_call_fn=lambda msgs, model: "old",
        context_limit_chars=12345,
    )
    old_compressor = services.context_runtime._compressor
    old_compressor._previous_summary = "incremental summary state"

    cfg = Config()
    cfg.desktop["ui"] = {"theme": "dark"}

    services.apply_config_update(cfg, changed_paths=["desktop.ui.theme"])

    assert services.context_runtime._compressor is old_compressor
    assert old_compressor._previous_summary == "incremental summary state"


def test_updates_approval_bypass():
    """test_updates_approval_bypass: updates approval bypass."""
    services = _make_services()
    cfg = Config()
    cfg.approvals.bypass_all = True

    services.apply_config_update(cfg, changed_paths=["approvals.bypass_all"])

    bypass = services.orchestrator.permissions.approval_bypass
    assert bypass.bypass_all is True


def test_updates_permanent_allowlist():
    """test_updates_permanent_allowlist: updates permanent allowlist."""
    from miqi.agent.command_approval import get_permanent_allowlist, replace_permanent_allowlist

    replace_permanent_allowlist(set())  # reset global state before the test
    try:
        services = _make_services()
        cfg = Config()
        cfg.agents.permanent_approvals = ["pattern-a", "pattern-b"]

        services.apply_config_update(cfg, changed_paths=["agents.permanent_approvals"])

        assert get_permanent_allowlist() == {"pattern-a", "pattern-b"}
    finally:
        replace_permanent_allowlist(set())  # restore global state


def test_allowlist_not_refreshed_when_opt_out():
    """test_allowlist_not_refreshed_when_opt_out: an unrelated save must not clobber runtime-approved patterns."""
    from miqi.agent.command_approval import (
        approve_permanent,
        get_permanent_allowlist,
        replace_permanent_allowlist,
    )

    replace_permanent_allowlist(set())
    try:
        approve_permanent("user-approved-at-runtime")
        services = _make_services()
        cfg = Config()  # permanent_approvals is empty in the new config

        services.apply_config_update(cfg, changed_paths=["desktop.ui.theme"])

        # The runtime-approved pattern survives the unrelated save.
        assert "user-approved-at-runtime" in get_permanent_allowlist()
    finally:
        replace_permanent_allowlist(set())


def test_context_compressor_closure_rebuilt_with_new_provider():
    """test_context_compressor_closure_rebuilt_with_new_provider: compaction must use a NEW provider closure after a hot apply."""
    services = _make_services()
    services.context_runtime = ContextRuntime(
        llm_call_fn=lambda msgs, model: "old",
        context_limit_chars=12345,
    )
    old_compressor = services.context_runtime._compressor

    cfg = Config()
    cfg.providers.deepseek.api_key = "sk-test"
    services.apply_config_update(cfg, changed_paths=["providers.deepseek.api_key"])

    new_compressor = services.context_runtime._compressor
    assert new_compressor is not old_compressor  # rebuilt, not mutated
    # The rebuilt compressor uses the NEW config's threshold (#4 review) —
    # a provider change rebuilds the closure, and the threshold follows the
    # model settings that were just applied.
    assert new_compressor.context_limit_chars == cfg.agents.defaults.context_limit_chars
    # The closure itself was replaced (reads services.provider at call time).
    assert new_compressor._llm_call is not old_compressor._llm_call


def test_compressor_rebuilt_with_new_threshold():
    """Compression threshold changes must reach the rebuilt compressor (#4 review)."""
    services = _make_services()
    services.context_runtime = ContextRuntime(
        llm_call_fn=lambda msgs, model: "old",
        context_limit_chars=12345,
    )
    old_compressor = services.context_runtime._compressor

    cfg = Config()
    cfg.agents.defaults.context_limit_chars = 99999
    services.apply_config_update(cfg, changed_paths=["agents.defaults.context_limit_chars"])

    new_compressor = services.context_runtime._compressor
    assert new_compressor is not old_compressor
    assert new_compressor.context_limit_chars == 99999


def test_replace_preserves_surviving_pattern_timestamps():
    """replace_permanent_allowlist must keep timestamps of surviving patterns (#11 review)."""
    from miqi.agent.command_approval import (
        _permanent_added_at,
        replace_permanent_allowlist,
    )

    replace_permanent_allowlist(set())
    _permanent_added_at["keep-me"] = 1234567890.0
    _permanent_added_at["drop-me"] = 9999999999.0
    try:
        replace_permanent_allowlist({"keep-me"})
        assert _permanent_added_at.get("keep-me") == 1234567890.0
        assert "drop-me" not in _permanent_added_at
    finally:
        replace_permanent_allowlist(set())


@pytest.mark.asyncio
async def test_clear_permanent_persists_to_disk(monkeypatch):
    """clear_permanent must persist so removed patterns cannot resurrect (#7 review)."""
    from miqi.agent.command_approval import (
        replace_permanent_allowlist,
    )
    from miqi.runtime.approval_handlers import approvals_clear_permanent_handler

    replace_permanent_allowlist({"dangerous-pattern"})
    saved_calls: list[set[str]] = []

    import miqi.agent.command_approval as ca_module

    def _fake_save() -> bool:
        saved_calls.append(ca_module.get_permanent_allowlist())
        return True

    monkeypatch.setattr(ca_module, "_save_permanent_allowlist", _fake_save)
    try:
        registry = SimpleNamespace(
            bridge_context={"state": SimpleNamespace()}
        )
        resp = await approvals_clear_permanent_handler(
            "1", {}, "client-1", None, registry,
        )
        assert resp["result"]["cleared"] is True
        # The cleared (empty) allowlist was persisted — a later hot-reload
        # replace has nothing to resurrect.
        assert saved_calls and saved_calls[-1] == set()
    finally:
        replace_permanent_allowlist(set())


@pytest.mark.asyncio
async def test_clear_permanent_rolls_back_on_persist_failure(monkeypatch):
    """A failed persist must roll back the in-memory clear and report the error.

    The on-disk list is unchanged when the write fails — reporting
    cleared:true would lie, and the next hot-reload replace would resurrect
    the "cleared" patterns (2026-08-31 review).
    """
    import miqi.agent.command_approval as ca_module
    from miqi.agent.command_approval import (
        get_permanent_allowlist,
        replace_permanent_allowlist,
    )
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.approval_handlers import approvals_clear_permanent_handler

    replace_permanent_allowlist({"dangerous-pattern"})
    monkeypatch.setattr(ca_module, "_save_permanent_allowlist", lambda: False)
    try:
        registry = SimpleNamespace(
            bridge_context={"state": SimpleNamespace()}
        )
        with pytest.raises(AppServerError, match="Failed to persist"):
            await approvals_clear_permanent_handler(
                "1", {}, "client-1", None, registry,
            )
        # In-memory list matches the unchanged on-disk state.
        assert get_permanent_allowlist() == {"dangerous-pattern"}
    finally:
        replace_permanent_allowlist(set())


# ── broadcast payload contract (2026-08-31 review) ──────────────────────────


@pytest.mark.asyncio
async def test_broadcast_omits_provider_rebuilt_when_not_attempted():
    """providerRebuilt must only be emitted when a rebuild was ATTEMPTED.

    An unrelated tier-A save (temperature) leaves provider_rebuilt False —
    emitting it would let a buggy client show the "重建失败" toast for a
    save that never tried to rebuild anything (2026-08-31 review).
    """
    from miqi.runtime.config_handlers import hot_apply_and_broadcast

    captured: list[dict] = []

    app_server = SimpleNamespace(_event_sinks={})

    async def _emit(target, event, payload):
        captured.append(payload)

    app_server.emit_client_event = _emit
    registry = SimpleNamespace(
        bridge_context={
            "app_server": app_server,
            "state": SimpleNamespace(config_at_startup=None),
        },
    )
    registry.list_sessions = lambda client_id: []

    # Unrelated save: no providerRebuilt field.
    cfg_temperature = Config()
    cfg_temperature.agents.defaults.temperature = 0.7
    await hot_apply_and_broadcast(registry, "client-1", Config(), cfg_temperature)
    payload = captured[-1]
    assert payload["applied"] == ["agents.defaults.temperature"]
    assert "providerRebuilt" not in payload

    # Provider save: rebuild attempted → field present (True without sessions).
    cfg_provider = Config()
    cfg_provider.providers.deepseek.api_key = "sk-test"
    await hot_apply_and_broadcast(registry, "client-1", Config(), cfg_provider)
    payload = captured[-1]
    assert "providerRebuilt" in payload
    assert payload["providerRebuilt"] is True


@pytest.mark.asyncio
async def test_broadcast_reports_failed_rebuild_on_apply_exception():
    """A raise during hot-apply must report providerRebuilt: False (2026-09-01 review).

    The session loop's exception path used to keep the True default — a
    provider rebuild that blew up mid-apply was broadcast as successful.
    """
    from miqi.runtime.config_handlers import hot_apply_and_broadcast

    captured: list[dict] = []

    app_server = SimpleNamespace(_event_sinks={})

    async def _emit(target, event, payload):
        captured.append(payload)

    app_server.emit_client_event = _emit

    class _BoomServices:
        def apply_config_update(self, *args, **kwargs):
            raise RuntimeError("boom")

    class _BoomRuntime:
        services = _BoomServices()

    class _Registry:
        def __init__(self):
            self.bridge_context = {
                "app_server": app_server,
                "state": SimpleNamespace(config_at_startup=None),
            }

        def list_sessions(self, client_id):
            return ["s1"]

        async def get_session(self, client_id, sid):
            return _BoomRuntime()

    cfg_provider = Config()
    cfg_provider.providers.deepseek.api_key = "sk-test"
    await hot_apply_and_broadcast(
        _Registry(), "client-1", Config(), cfg_provider,
    )

    payload = captured[-1]
    # Rebuild was attempted (providers path) and the apply raised.
    assert "providerRebuilt" in payload
    assert payload["providerRebuilt"] is False


@pytest.mark.asyncio
async def test_add_permanent_rolls_back_on_persist_failure(monkeypatch):
    """A failed persist must roll back the just-added pattern (2026-09-01 review)."""
    import miqi.agent.command_approval as ca_module
    from miqi.agent.command_approval import (
        get_permanent_allowlist,
        replace_permanent_allowlist,
    )
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.approval_handlers import approvals_add_permanent_handler

    replace_permanent_allowlist({"existing-pattern"})
    monkeypatch.setattr(ca_module, "_save_permanent_allowlist", lambda: False)
    try:
        registry = SimpleNamespace(
            bridge_context={"state": SimpleNamespace()}
        )
        with pytest.raises(AppServerError, match="Failed to persist"):
            await approvals_add_permanent_handler(
                "1", {"pattern": "new-pattern"}, "client-1", None, registry,
            )
        # In-memory list matches the unchanged on-disk state.
        assert get_permanent_allowlist() == {"existing-pattern"}
    finally:
        replace_permanent_allowlist(set())
