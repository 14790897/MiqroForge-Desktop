"""Tests for approval handlers — Phase 28.2.

Validates that approval listing, resolution, permanent management,
and history are properly scoped to client/session boundaries.
"""

import asyncio
import time

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────


async def _create_session_with_approval(registry, client_id, session_key, fake_config, fake_provider, tmp_path):
    """Create a RuntimeSession with a fake pending approval in its orchestrator."""
    session = await registry.create_session(
        client_id=client_id,
        session_key=session_key,
        config=fake_config,
        provider=fake_provider,
        workspace=tmp_path,
    )
    orchestrator = getattr(session.services, "orchestrator", None)
    if orchestrator is not None:
        # Inject a fake pending approval
        approval_id = f"test-turn:{session_key}-tool-1"
        future = asyncio.get_event_loop().create_future()
        orchestrator._pending_approvals[approval_id] = future
        orchestrator._approval_meta[approval_id] = {
            "approval_id": approval_id,
            "turn_id": "test-turn",
            "command": "rm -rf /test",
            "description": "Delete test directory",
            "details": "rm -rf /tmp/test",
            "allow_permanent": True,
            "created_at": time.time(),
        }
        # Store approval_id for cleanup in tests
        session._test_approval_id = approval_id
        session._test_approval_future = future
    return session


# ── approvals.list ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approvals_list_scoped_to_client_sessions(fake_config, fake_provider, tmp_path):
    """approvals.list returns only pending approvals from this client's sessions."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_list_handler

    registry = ClientSessionRegistry()

    try:
        # Client A: session with 1 pending approval
        await _create_session_with_approval(registry, "client-A", "session-a1", fake_config, fake_provider, tmp_path)

        # Client B: session with 2 pending approvals
        await _create_session_with_approval(registry, "client-B", "session-b1", fake_config, fake_provider, tmp_path)
        await _create_session_with_approval(registry, "client-B", "session-b2", fake_config, fake_provider, tmp_path)

        # Client A should see only 1 pending approval (from session-a1)
        result = await approvals_list_handler("req-1", {}, "client-A", None, registry)
        pending = result["result"]["pending"]
        pending_ids = result["result"]["pending_ids"]
        assert len(pending) == 1, f"Expected 1 pending for client-A, got {len(pending)}"
        assert "session-a1" in pending_ids[0]

        # Client B should see 2 pending approvals
        result = await approvals_list_handler("req-2", {}, "client-B", None, registry)
        pending = result["result"]["pending"]
        assert len(pending) == 2, f"Expected 2 pending for client-B, got {len(pending)}"
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_approvals_list_empty_when_no_sessions(fake_config, fake_provider, tmp_path):
    """approvals.list returns empty list when client has no sessions."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_list_handler

    registry = ClientSessionRegistry()

    result = await approvals_list_handler("req-1", {}, "unknown-client", None, registry)
    assert result["result"]["pending"] == []
    assert result["result"]["pending_ids"] == []


@pytest.mark.asyncio
async def test_approvals_list_includes_permanent_allowlist(fake_config, fake_provider, tmp_path):
    """approvals.list includes permanent allowlist metadata."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_list_handler

    registry = ClientSessionRegistry()

    result = await approvals_list_handler("req-1", {}, "client-1", None, registry)
    assert "permanent_allowlist" in result["result"]
    assert "permanent_entries" in result["result"]
    assert "enabled" in result["result"]
    assert "timeout" in result["result"]


@pytest.mark.asyncio
async def test_approvals_list_includes_approval_mode_from_profile(fake_config, fake_provider, tmp_path):
    """approvals.list surfaces the active session's approval_policy mode."""
    from pathlib import Path

    from miqi.execution.approval_policy import ApprovalMode, ApprovalPolicy
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_list_handler
    from miqi.runtime.permission_profile import PermissionProfile

    registry = ClientSessionRegistry()

    try:
        session = await registry.create_session(
            client_id="client-1",
            session_key="session-a",
            config=fake_config,
            provider=fake_provider,
            workspace=tmp_path,
        )
        profile = PermissionProfile(
            workspace=Path(tmp_path),
            approval_policy=ApprovalPolicy(mode=ApprovalMode.NEVER),
        )
        session.services.permission_profile = profile

        result = await approvals_list_handler("req-1", {}, "client-1", None, registry)
        assert "approval_mode" in result["result"]
        assert result["result"]["approval_mode"] == "never"
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_approvals_list_defaults_approval_mode_to_on_request(fake_config, fake_provider, tmp_path):
    """approvals.list defaults to 'on_request' when no approval_policy is available."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_list_handler

    registry = ClientSessionRegistry()

    try:
        await registry.create_session(
            client_id="client-1",
            session_key="session-a",
            config=fake_config,
            provider=fake_provider,
            workspace=tmp_path,
        )

        result = await approvals_list_handler("req-1", {}, "client-1", None, registry)
        assert "approval_mode" in result["result"]
        assert result["result"]["approval_mode"] == "on_request"
    finally:
        await registry.stop_all()


# ── approvals.resolve ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approvals_resolve_own_session_approval(fake_config, fake_provider, tmp_path):
    """approvals.resolve resolves an approval owned by the client's session."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_resolve_handler

    registry = ClientSessionRegistry()
    session = await _create_session_with_approval(registry, "client-A", "session-a", fake_config, fake_provider, tmp_path)
    approval_id = session._test_approval_id

    try:
        result = await approvals_resolve_handler(
            "req-1",
            {"approval_id": approval_id, "decision": "once"},
            "client-A", session.session_id, registry,
        )
        assert result["result"]["resolved"] is True
        assert result["result"]["approval_id"] == approval_id
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_approvals_resolve_rejects_cross_client(fake_config, fake_provider, tmp_path):
    """approvals.resolve returns UNAUTHORIZED for another client's approval."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_resolve_handler

    registry = ClientSessionRegistry()
    session = await _create_session_with_approval(registry, "client-A", "session-a", fake_config, fake_provider, tmp_path)
    approval_id = session._test_approval_id

    try:
        # Client B tries to resolve client A's approval
        with pytest.raises(AppServerError) as exc_info:
            await approvals_resolve_handler(
                "req-1",
                {"approval_id": approval_id, "decision": "once"},
                "client-B", None, registry,
            )
        assert exc_info.value.code == "UNAUTHORIZED"
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_approvals_resolve_invalid_decision(fake_config, fake_provider, tmp_path):
    """approvals.resolve rejects invalid decision values."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_resolve_handler

    registry = ClientSessionRegistry()

    with pytest.raises(AppServerError) as exc_info:
        await approvals_resolve_handler(
            "req-1",
            {"approval_id": "test-1", "decision": "invalid_choice"},
            "client-1", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_approvals_resolve_missing_approval_id(fake_config, fake_provider, tmp_path):
    """approvals.resolve requires approval_id."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_resolve_handler

    registry = ClientSessionRegistry()

    with pytest.raises(AppServerError) as exc_info:
        await approvals_resolve_handler(
            "req-1", {"decision": "once"}, "client-1", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"


# ── Phase 31.4: AppServer approvals.resolve terminal event tests ──────────


@pytest.mark.asyncio
async def test_approvals_resolve_allow_emits_resolved_event(fake_config, fake_provider, tmp_path):
    """Phase 31.4: AppServer approvals.resolve with 'once' emits
    ApprovalResolvedEvent through the runtime session's event emitter."""
    from miqi.protocol.events import ApprovalResolvedEvent
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_resolve_handler

    registry = ClientSessionRegistry()
    session = await _create_session_with_approval(registry, "client-A", "session-a", fake_config, fake_provider, tmp_path)
    approval_id = session._test_approval_id

    try:
        result = await approvals_resolve_handler(
            "req-1",
            {"approval_id": approval_id, "decision": "once"},
            "client-A", session.session_id, registry,
        )
        assert result["result"]["resolved"] is True
        assert result["result"]["decision"] == "once"

        # Verify ApprovalResolvedEvent was emitted into the event queue
        event = await asyncio.wait_for(session._events.get(), timeout=1)
        assert isinstance(event, ApprovalResolvedEvent), (
            f"Expected ApprovalResolvedEvent, got {type(event).__name__}"
        )
        assert event.approval_id == approval_id
        assert event.decision == "once"
        assert event.turn_id == "test-turn"
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_approvals_resolve_deny_emits_resolved_event(fake_config, fake_provider, tmp_path):
    """Phase 31.4: AppServer approvals.resolve with 'deny' emits
    ApprovalResolvedEvent with decision='deny'."""
    from miqi.protocol.events import ApprovalResolvedEvent
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_resolve_handler

    registry = ClientSessionRegistry()
    session = await _create_session_with_approval(registry, "client-A", "session-a", fake_config, fake_provider, tmp_path)
    approval_id = session._test_approval_id

    try:
        result = await approvals_resolve_handler(
            "req-1",
            {"approval_id": approval_id, "decision": "deny"},
            "client-A", session.session_id, registry,
        )
        assert result["result"]["resolved"] is True
        assert result["result"]["decision"] == "deny"

        # Verify ApprovalResolvedEvent was emitted
        event = await asyncio.wait_for(session._events.get(), timeout=1)
        assert isinstance(event, ApprovalResolvedEvent)
        assert event.decision == "deny"
        assert event.approval_id == approval_id
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_approvals_resolve_always_emits_resolved_event(fake_config, fake_provider, tmp_path):
    """Phase 31.4: AppServer approvals.resolve with 'always' emits
    ApprovalResolvedEvent and updates the permanent allowlist."""
    from miqi.protocol.events import ApprovalResolvedEvent
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_resolve_handler

    registry = ClientSessionRegistry()
    session = await _create_session_with_approval(registry, "client-A", "session-a", fake_config, fake_provider, tmp_path)
    approval_id = session._test_approval_id

    # Ensure permanent_allowlist is a real set for the test
    orchestrator = getattr(session.services, "orchestrator", None)
    assert orchestrator is not None
    orchestrator.permissions.permanent_allowlist = set()

    try:
        result = await approvals_resolve_handler(
            "req-1",
            {"approval_id": approval_id, "decision": "always"},
            "client-A", session.session_id, registry,
        )
        assert result["result"]["resolved"] is True
        assert result["result"]["decision"] == "always"

        # Verify ApprovalResolvedEvent was emitted
        event = await asyncio.wait_for(session._events.get(), timeout=1)
        assert isinstance(event, ApprovalResolvedEvent)
        assert event.decision == "always"
        assert event.approval_id == approval_id

        # Verify permanent allowlist was updated
        assert "Delete test directory" in orchestrator.permissions.permanent_allowlist
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_approvals_resolve_nonexistent_no_event(fake_config, fake_provider, tmp_path):
    """Phase 31.4: AppServer approvals.resolve for a nonexistent approval
    does NOT emit ApprovalResolvedEvent."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_resolve_handler

    registry = ClientSessionRegistry()
    session = await _create_session_with_approval(registry, "client-A", "session-a", fake_config, fake_provider, tmp_path)

    try:
        # Drain any existing events from session creation
        while not session._events.empty():
            session._events.get_nowait()

        with pytest.raises(AppServerError) as exc_info:
            await approvals_resolve_handler(
                "req-1",
                {"approval_id": "nonexistent-approval", "decision": "once"},
                "client-A", session.session_id, registry,
            )
        assert exc_info.value.code == "UNAUTHORIZED"

        # Verify no ApprovalResolvedEvent was emitted
        assert session._events.empty(), (
            "No events should be emitted for nonexistent approval resolve"
        )
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_approvals_resolve_invalid_decision_no_event(fake_config, fake_provider, tmp_path):
    """Phase 31.4: AppServer approvals.resolve with invalid decision
    does NOT emit ApprovalResolvedEvent."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_resolve_handler

    registry = ClientSessionRegistry()

    # Drain any events
    # No session needed — invalid decision is caught before session lookup

    with pytest.raises(AppServerError) as exc_info:
        await approvals_resolve_handler(
            "req-1",
            {"approval_id": "test-1", "decision": "bogus_decision"},
            "client-1", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_approvals_resolve_legacy_allow_mapped_to_once(fake_config, fake_provider, tmp_path):
    """Phase 31.4: legacy 'allow' decision is accepted and normalized to 'once'."""
    from miqi.protocol.events import ApprovalResolvedEvent
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_resolve_handler

    registry = ClientSessionRegistry()
    session = await _create_session_with_approval(registry, "client-A", "session-a", fake_config, fake_provider, tmp_path)
    approval_id = session._test_approval_id

    try:
        result = await approvals_resolve_handler(
            "req-1",
            {"approval_id": approval_id, "decision": "allow"},
            "client-A", session.session_id, registry,
        )
        assert result["result"]["resolved"] is True
        assert result["result"]["decision"] == "once", (
            "Legacy 'allow' should be normalized to 'once'"
        )

        # Verify ApprovalResolvedEvent was emitted with normalized decision
        event = await asyncio.wait_for(session._events.get(), timeout=1)
        assert isinstance(event, ApprovalResolvedEvent)
        assert event.decision == "once", (
            "ApprovalResolvedEvent should use normalized decision 'once', not 'allow'"
        )
    finally:
        await registry.stop_all()


@pytest.mark.asyncio
async def test_approvals_resolve_legacy_allow_permanent_mapped_to_always(fake_config, fake_provider, tmp_path):
    """Phase 31.4: legacy 'allow_permanent' decision is accepted and
    normalized to 'always'."""
    from miqi.protocol.events import ApprovalResolvedEvent
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_resolve_handler

    registry = ClientSessionRegistry()
    session = await _create_session_with_approval(registry, "client-A", "session-a", fake_config, fake_provider, tmp_path)
    approval_id = session._test_approval_id

    orchestrator = getattr(session.services, "orchestrator", None)
    assert orchestrator is not None
    orchestrator.permissions.permanent_allowlist = set()

    try:
        result = await approvals_resolve_handler(
            "req-1",
            {"approval_id": approval_id, "decision": "allow_permanent"},
            "client-A", session.session_id, registry,
        )
        assert result["result"]["resolved"] is True
        assert result["result"]["decision"] == "always", (
            "Legacy 'allow_permanent' should be normalized to 'always'"
        )

        # Verify ApprovalResolvedEvent was emitted with normalized decision
        event = await asyncio.wait_for(session._events.get(), timeout=1)
        assert isinstance(event, ApprovalResolvedEvent)
        assert event.decision == "always", (
            "ApprovalResolvedEvent should use normalized decision 'always', "
            "not 'allow_permanent'"
        )
    finally:
        await registry.stop_all()


# ── approvals.clear_permanent ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approvals_clear_permanent_removes_pattern(fake_config, fake_provider, tmp_path):
    """approvals.clear_permanent removes a specific pattern."""
    from miqi.agent.command_approval import approve_permanent, get_permanent_allowlist
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_clear_permanent_handler

    # Add a permanent approval
    approve_permanent("test-pattern-1")
    approve_permanent("test-pattern-2")
    assert "test-pattern-1" in get_permanent_allowlist()

    registry = ClientSessionRegistry()
    result = await approvals_clear_permanent_handler(
        "req-1", {"pattern": "test-pattern-1"}, "client-1", None, registry,
    )
    assert result["result"]["cleared"] is True
    assert "test-pattern-1" not in get_permanent_allowlist()
    assert "test-pattern-2" in get_permanent_allowlist()  # not affected

    # Cleanup
    await approvals_clear_permanent_handler("req-clean", {"pattern": "test-pattern-2"}, "client-1", None, registry)


@pytest.mark.asyncio
async def test_approvals_clear_permanent_clears_all(fake_config, fake_provider, tmp_path):
    """approvals.clear_permanent without pattern clears all."""
    from miqi.agent.command_approval import approve_permanent, get_permanent_allowlist
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_clear_permanent_handler

    approve_permanent("pattern-all-1")
    approve_permanent("pattern-all-2")

    registry = ClientSessionRegistry()
    result = await approvals_clear_permanent_handler(
        "req-1", {}, "client-1", None, registry,
    )
    assert result["result"]["cleared"] is True
    assert len(get_permanent_allowlist()) == 0


# ── approvals.add_permanent ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approvals_add_permanent_adds_pattern(fake_config, fake_provider, tmp_path):
    """approvals.add_permanent adds and persists a pattern."""
    from miqi.agent.command_approval import get_permanent_allowlist
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_add_permanent_handler

    registry = ClientSessionRegistry()
    result = await approvals_add_permanent_handler(
        "req-1", {"pattern": "new-pattern-xyz"}, "client-1", None, registry,
    )
    assert result["result"]["added"] is True
    assert result["result"]["pattern"] == "new-pattern-xyz"
    assert "new-pattern-xyz" in get_permanent_allowlist()

    # Cleanup
    from miqi.agent.command_approval import _lock, _permanent_added_at, _permanent_approved
    with _lock:
        _permanent_approved.discard("new-pattern-xyz")
        _permanent_added_at.pop("new-pattern-xyz", None)


@pytest.mark.asyncio
async def test_approvals_add_permanent_rejects_empty_pattern(fake_config, fake_provider, tmp_path):
    """approvals.add_permanent rejects empty pattern."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_add_permanent_handler

    registry = ClientSessionRegistry()

    with pytest.raises(AppServerError) as exc_info:
        await approvals_add_permanent_handler(
            "req-1", {"pattern": ""}, "client-1", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"


# ── approvals.history ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approvals_history_returns_data(fake_config, fake_provider, tmp_path):
    """approvals.history returns history data."""
    from miqi.agent.command_approval import add_approval_history
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.approval_handlers import approvals_history_handler

    # Add a history entry
    add_approval_history(
        pattern_key="test-pattern",
        description="test command",
        command="rm -rf /test",
        decision="once",
        session_key="client-1:session-1",
    )

    registry = ClientSessionRegistry()
    result = await approvals_history_handler("req-1", {"limit": 10}, "client-1", None, registry)
    assert "history" in result["result"]
    assert isinstance(result["result"]["history"], list)
