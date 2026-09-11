"""Tests for the shared experimental API gate (Phase 46).

Covers require_experimental_api() behavior:
- AppServer client capability experimental_api=True passes
- Per-request experimentalApi: true passes
- registry.bridge_context["experimental_api_enabled"] = True passes
- Missing all gates raises EXPERIMENTAL_API_REQUIRED
- process/spawn gate behavior is preserved through existing tests
"""

from __future__ import annotations

import pytest

from miqi.runtime.app_server import (
    AppServer,
    AppServerError,
    ClientCapabilities,
    ClientSessionRegistry,
)
from miqi.runtime.experimental_api import require_experimental_api

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_registry(*, app_server=None, experimental_enabled=None):
    """Create a registry with optional AppServer and experimental flag."""
    registry = ClientSessionRegistry()
    ctx: dict = {}
    if app_server is not None:
        ctx["app_server"] = app_server
    if experimental_enabled is not None:
        ctx["experimental_api_enabled"] = experimental_enabled
    registry.bridge_context = ctx
    return registry


# ── Tests ────────────────────────────────────────────────────────────────────


class TestRequireExperimentalApi:
    """Gate behavior for require_experimental_api()."""

    def test_client_capability_experimental_true_passes(self):
        """Client with experimental_api=True capability passes the gate."""
        server = AppServer(ClientSessionRegistry())
        server.set_client_capabilities(
            "client-1",
            ClientCapabilities(experimental_api=True),
        )
        registry = _make_registry(app_server=server)

        # Should not raise
        require_experimental_api({}, registry, "client-1", "test/method")

    def test_per_request_flag_passes(self):
        """params.experimentalApi=True passes the gate."""
        registry = _make_registry()

        # Should not raise
        require_experimental_api(
            {"experimentalApi": True}, registry, "client-1", "test/method",
        )

    def test_bridge_context_flag_passes(self):
        """registry.bridge_context["experimental_api_enabled"]=True passes the gate."""
        registry = _make_registry(experimental_enabled=True)

        # Should not raise
        require_experimental_api({}, registry, "client-1", "test/method")

    def test_missing_all_gates_raises(self):
        """No capability, no param flag, no context flag → EXPERIMENTAL_API_REQUIRED."""
        registry = _make_registry()

        with pytest.raises(AppServerError) as exc_info:
            require_experimental_api({}, registry, "client-1", "test/method")

        assert exc_info.value.code == "EXPERIMENTAL_API_REQUIRED"
        assert "test/method" in exc_info.value.message
        assert "experimentalApi" in exc_info.value.message

    def test_client_without_experimental_capability_fails(self):
        """Client with experimental_api=False fails the gate."""
        server = AppServer(ClientSessionRegistry())
        server.set_client_capabilities(
            "client-1",
            ClientCapabilities(experimental_api=False),
        )
        registry = _make_registry(app_server=server)

        with pytest.raises(AppServerError) as exc_info:
            require_experimental_api({}, registry, "client-1", "test/method")

        assert exc_info.value.code == "EXPERIMENTAL_API_REQUIRED"

    def test_different_client_id_does_not_use_capability(self):
        """Capability check is per-client; different client_id must provide per-request flag."""
        server = AppServer(ClientSessionRegistry())
        server.set_client_capabilities(
            "client-1",
            ClientCapabilities(experimental_api=True),
        )
        registry = _make_registry(app_server=server)

        # client-2 has no capability set
        with pytest.raises(AppServerError) as exc_info:
            require_experimental_api({}, registry, "client-2", "test/method")

        assert exc_info.value.code == "EXPERIMENTAL_API_REQUIRED"

    def test_no_app_server_in_context_checks_other_gates(self):
        """When app_server is None in context, falls through to other checks."""
        registry = _make_registry(app_server=None)

        # Should fail (no gates set)
        with pytest.raises(AppServerError):
            require_experimental_api({}, registry, "client-1", "test/method")

    def test_params_experimental_api_not_true_bool_fails(self):
        """experimentalApi must be exactly True, not truthy."""
        registry = _make_registry()

        with pytest.raises(AppServerError):
            require_experimental_api(
                {"experimentalApi": "true"}, registry, "client-1", "test/method",
            )

        with pytest.raises(AppServerError):
            require_experimental_api(
                {"experimentalApi": 1}, registry, "client-1", "test/method",
            )
