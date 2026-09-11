"""Tests for permission handlers — Phase 35.2 / hardening.

Validates permissions.get, permissions.update,
permissions.permanent.add, and permissions.permanent.remove
migrated from bridge legacy to AppServer async handlers.

Hardening: Cross-client isolation tests and global control-plane documentation.
"""

from unittest.mock import MagicMock, patch

import pytest

from miqi.runtime.app_server import ClientSessionRegistry

# ── permissions.get ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_permissions_get_returns_structure():
    """permissions.get should return known structure with expected keys."""
    from miqi.runtime.permission_handlers import permissions_get_handler

    registry = ClientSessionRegistry()
    result = await permissions_get_handler("req-1", {}, "client-1", None, registry)

    data = result["result"]
    assert "filesystem" in data
    assert "network" in data
    assert "exec_approval" in data
    assert "permanent_allowlist" in data
    assert "deny_patterns" in data
    assert isinstance(data["permanent_allowlist"], list)
    assert isinstance(data["deny_patterns"], list)


# ── permissions.update ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_permissions_update_returns_saved():
    """permissions.update should return saved: True even without orchestrator."""
    from miqi.runtime.permission_handlers import permissions_update_handler

    registry = ClientSessionRegistry()
    result = await permissions_update_handler(
        "req-1",
        {"config": {"permanent_allowlist": ["npm test"], "deny_patterns": ["rm -rf"]}},
        "client-1", None, registry,
    )
    assert result["result"]["saved"] is True


# ── permissions.permanent.add ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_permissions_permanent_add_returns_added():
    """permissions.permanent.add should return added: True when pattern given."""
    from miqi.runtime.permission_handlers import permissions_permanent_add_handler

    registry = ClientSessionRegistry()
    result = await permissions_permanent_add_handler(
        "req-1", {"pattern": "git push"}, "client-1", None, registry,
    )
    assert result["result"]["added"] is True


@pytest.mark.asyncio
async def test_permissions_permanent_add_empty_pattern():
    """permissions.permanent.add with empty pattern returns added: False."""
    from miqi.runtime.permission_handlers import permissions_permanent_add_handler

    registry = ClientSessionRegistry()
    result = await permissions_permanent_add_handler(
        "req-1", {"pattern": ""}, "client-1", None, registry,
    )
    assert result["result"]["added"] is False


# ── permissions.permanent.remove ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_permissions_permanent_remove_returns_removed():
    """permissions.permanent.remove should return removed: True when pattern given."""
    from miqi.runtime.permission_handlers import permissions_permanent_remove_handler

    registry = ClientSessionRegistry()
    result = await permissions_permanent_remove_handler(
        "req-1", {"pattern": "git push"}, "client-1", None, registry,
    )
    assert result["result"]["removed"] is True


@pytest.mark.asyncio
async def test_permissions_permanent_remove_empty_pattern():
    """permissions.permanent.remove with empty pattern returns removed: False."""
    from miqi.runtime.permission_handlers import permissions_permanent_remove_handler

    registry = ClientSessionRegistry()
    result = await permissions_permanent_remove_handler(
        "req-1", {"pattern": ""}, "client-1", None, registry,
    )
    assert result["result"]["removed"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-client isolation tests (hardening)
# ═══════════════════════════════════════════════════════════════════════════════


class _MockPermissionEngine:
    """Mock permission engine for cross-client testing."""
    def __init__(self):
        self.permanent_allowlist: set[str] = set()
        self.deny_patterns: set[str] = set()


@pytest.mark.asyncio
async def test_permissions_are_global_control_plane():
    """Permissions are global control-plane: two clients share the same state.

    This test documents the CURRENT behavior (Phase 35 hardening):
    - Client A adds a pattern to the permanent allowlist
    - Client B sees that pattern via permissions.get
    - This is BY DESIGN for a single-user Desktop bridge

    If this behavior changes to per-client isolation, this test MUST
    be updated to reflect the new semantics.
    """
    from miqi.runtime.permission_handlers import (
        permissions_get_handler,
        permissions_permanent_add_handler,
    )

    # Create a shared mock orchestrator/permission engine
    mock_pe = _MockPermissionEngine()
    mock_orch = MagicMock()
    mock_orch.permissions = mock_pe
    mock_state = MagicMock()
    mock_state._orchestrator = mock_orch

    # Patch both the _get_orchestrator function and bridge state
    with patch(
        "miqi.runtime.permission_handlers._get_orchestrator",
        return_value=mock_orch,
    ):
        registry = ClientSessionRegistry()

        # Client A adds a pattern
        await permissions_permanent_add_handler(
            "req-1", {"pattern": "npm run build"}, "client-A", None, registry,
        )

        # Client B gets permissions — should see the pattern from client A
        result_b = await permissions_get_handler(
            "req-2", {}, "client-B", None, registry,
        )
        assert "npm run build" in result_b["result"]["permanent_allowlist"], (
            "Cross-client test FAILED: Client B should see pattern added by Client A. "
            "If per-client isolation is now implemented, update this test."
        )

        # Client B also adds a pattern
        await permissions_permanent_add_handler(
            "req-3", {"pattern": "git commit"}, "client-B", None, registry,
        )

        # Both patterns should be visible from any client
        assert "npm run build" in mock_pe.permanent_allowlist
        assert "git commit" in mock_pe.permanent_allowlist
        assert len(mock_pe.permanent_allowlist) == 2


@pytest.mark.asyncio
async def test_permissions_global_deny_patterns_cross_client():
    """Global deny patterns are shared across clients (documented behavior)."""
    from miqi.runtime.permission_handlers import (
        permissions_get_handler,
        permissions_update_handler,
    )

    mock_pe = _MockPermissionEngine()
    mock_orch = MagicMock()
    mock_orch.permissions = mock_pe

    with patch(
        "miqi.runtime.permission_handlers._get_orchestrator",
        return_value=mock_orch,
    ):
        registry = ClientSessionRegistry()

        # Client A updates deny patterns
        await permissions_update_handler(
            "req-1",
            {"config": {"deny_patterns": ["sudo", "chmod 777"]}},
            "client-A", None, registry,
        )

        # Client B reads — sees the same deny patterns
        result_b = await permissions_get_handler(
            "req-2", {}, "client-B", None, registry,
        )
        assert "sudo" in result_b["result"]["deny_patterns"]
        assert "chmod 777" in result_b["result"]["deny_patterns"]


@pytest.mark.asyncio
async def test_permissions_without_orchestrator_returns_defaults():
    """Without an orchestrator, permissions.get returns safe defaults.

    This is the fallback path when bridge state is not available.
    All clients share the same default values.
    """
    from miqi.runtime.permission_handlers import permissions_get_handler

    registry = ClientSessionRegistry()

    # Both clients get identical default state
    result_a = await permissions_get_handler("req-1", {}, "client-A", None, registry)
    result_b = await permissions_get_handler("req-2", {}, "client-B", None, registry)

    assert result_a["result"] == result_b["result"]
    assert result_a["result"]["permanent_allowlist"] == []
    assert result_a["result"]["deny_patterns"] == []
