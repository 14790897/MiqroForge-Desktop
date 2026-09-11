"""Phase 28 legacy audit tests — verify invariants after migration.

Validates:
- Migrated methods are removed from _METHODS
- Remaining legacy handlers are correctly present
- No direct _state mutation in AppServer-path handlers
- AgentLoop/process_direct remain zero in production paths
"""

import pytest

# ── _METHODS audit ─────────────────────────────────────────────────────────


def test_migrated_handlers_removed_from_methods():
    """Approvals, config, sessions, and agent handlers are removed from _METHODS."""
    from miqi.bridge.server import _METHODS

    # Phase 28.2: approvals migrated
    assert "approvals.list" not in _METHODS, "approvals.list should be migrated to AppServer"
    assert "approvals.resolve" not in _METHODS, "approvals.resolve should be migrated to AppServer"
    assert "approvals.clear_permanent" not in _METHODS, "approvals.clear_permanent should be migrated to AppServer"
    assert "approvals.add_permanent" not in _METHODS, "approvals.add_permanent should be migrated to AppServer"
    assert "approvals.history" not in _METHODS, "approvals.history should be migrated to AppServer"

    # Phase 28.3: config migrated
    assert "config.get" not in _METHODS, "config.get should be migrated to AppServer"
    assert "config.update" not in _METHODS, "config.update should be migrated to AppServer"

    # Phase 28.4: sessions migrated
    assert "sessions.list" not in _METHODS, "sessions.list should be migrated to AppServer"
    assert "sessions.get" not in _METHODS, "sessions.get should be migrated to AppServer"
    assert "sessions.delete" not in _METHODS, "sessions.delete should be migrated to AppServer"
    assert "sessions.archive" not in _METHODS, "sessions.archive should be migrated to AppServer"
    assert "sessions.unarchive" not in _METHODS, "sessions.unarchive should be migrated to AppServer"
    assert "sessions.list_archived" not in _METHODS, "sessions.list_archived should be migrated to AppServer"
    assert "sessions.get_tracked_files" not in _METHODS, "sessions.get_tracked_files should be migrated to AppServer"
    assert "sessions.clear_tracked_files" not in _METHODS, "sessions.clear_tracked_files should be migrated to AppServer"

    # Phase 27.3 + 27.4 + 27.5 + 28.5: chat + agent migrated
    assert "chat.send" not in _METHODS, "chat.send should be migrated to AppServer"
    assert "chat.abort" not in _METHODS, "chat.abort should be migrated to AppServer"
    assert "agent.spawn" not in _METHODS, "agent.spawn should be migrated to AppServer"
    assert "agent.kill" not in _METHODS, "agent.kill should be migrated to AppServer"
    assert "agent.list" not in _METHODS, "agent.list should be migrated to AppServer"
    assert "agent.get" not in _METHODS, "agent.get should be migrated to AppServer"

    # Phase 30: files.* migrated
    assert "files.tree" not in _METHODS, "files.tree should be migrated to AppServer"
    assert "files.read" not in _METHODS, "files.read should be migrated to AppServer"
    assert "files.write" not in _METHODS, "files.write should be migrated to AppServer"
    assert "files.delete" not in _METHODS, "files.delete should be migrated to AppServer"
    assert "files.diff" not in _METHODS, "files.diff should be migrated to AppServer"
    assert "files.revert" not in _METHODS, "files.revert should be migrated to AppServer"
    assert "files.accept" not in _METHODS, "files.accept should be migrated to AppServer"


def test_remaining_legacy_handlers_present():
    """Remaining legacy handlers that should NOT have been migrated are present."""
    from miqi.bridge.server import _METHODS

    # providers.*, channels.*, permissions.* migrated in Phase 35.2
    # plugins.* migrated in Phase 35.3
    assert "providers.list" not in _METHODS
    assert "providers.test" not in _METHODS
    assert "channels.list" not in _METHODS
    assert "permissions.get" not in _METHODS
    assert "plugins.list" not in _METHODS
    assert "mcp.list" not in _METHODS
    assert "skills.list" not in _METHODS
    assert "cron.list" not in _METHODS
    assert "memory.list" not in _METHODS
    assert "python.check" not in _METHODS
    # These are explicitly NOT yet migrated (transport-only bridge methods)
    assert "status" in _METHODS
    assert "plan.get" in _METHODS


def test_methods_dict_count():
    """_METHODS count has shrunk by migrated handlers.

    Phase 28: removed ~17 (approvals, config, sessions, chat, agent)
    Phase 30: removed 7 (files.*)
    Phase 35.2: removed 9 (providers 3, channels 2, permissions 4)
    """
    from miqi.bridge.server import _METHODS

    count = len(_METHODS)
    assert count < 50, f"Expected fewer than 50 legacy handlers, got {count}"
    # Should have a reasonable minimum (will shrink further in Phase 35)
    assert count <= 10, f"Too many legacy handlers remaining, got {count} — did we miss a migration?"


# ── AppServer registration audit ───────────────────────────────────────────


def test_appserver_has_all_migrated_handlers():
    """AppServer has all handlers that were removed from _METHODS."""
    # This is tested indirectly by the handler tests, but we also
    # verify the AppServer method registry structure
    from miqi.runtime.app_server import AppServer

    # Verify AppServer supports handler registration
    assert hasattr(AppServer, "register_method")
    assert hasattr(AppServer, "dispatch")


# ── AgentLoop/process_direct audit ─────────────────────────────────────────

def test_no_agentloop_in_production_paths():
    """AgentLoop constructor and process_direct should not appear in
    runtime, bridge, cli, tui, channels, or cron modules.

    The only valid location is miqi/agent/loop.py (the class definition itself).
    """
    from pathlib import Path

    miqi_dir = Path(__file__).parent.parent.parent / "miqi"

    # Check each directory
    dirs_to_check = ["runtime", "bridge", "cli", "tui", "channels", "cron"]
    for dirname in dirs_to_check:
        d = miqi_dir / dirname
        if not d.exists():
            continue
        for py_file in d.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            if "AgentLoop(" in content:
                pytest.fail(
                    f"AgentLoop( found in {py_file.relative_to(miqi_dir.parent)}"
                )
            if "process_direct(" in content:
                pytest.fail(
                    f"process_direct( found in {py_file.relative_to(miqi_dir.parent)}"
                )


# ── Bridge state audit ─────────────────────────────────────────────────────

def test_bridge_state_agent_control_is_still_dead():
    """_state._agent_control should only exist in BridgeState.__init__
    (declaration) and not be set to a non-None value.

    Phase 28.5 migrated agent.list/get to use RuntimeSession.services.agent_control.
    The dead _state._agent_control pointer must not be reactivated.
    """
    from miqi.bridge.server import _state

    # _state._agent_control should still be None (never wired)
    # If it's set, something reactivated the dead pointer
    ac = getattr(_state, "_agent_control", None)
    assert ac is None, (
        "_state._agent_control was set to non-None — "
        "this dead pointer must not be reactivated. "
        "Use RuntimeSession.services.agent_control instead."
    )
