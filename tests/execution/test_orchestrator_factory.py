"""Tests for miqi.execution.factory — create_default_orchestrator (Phase 48)."""

from unittest.mock import AsyncMock, MagicMock


def test_create_default_orchestrator_noop_emitter_when_none():
    """When event_emitter is None, a NoopEmitter is used instead of crashing."""
    from miqi.execution.factory import create_default_orchestrator

    orchestrator = create_default_orchestrator(
        tool_registry=None,
        event_emitter=None,
    )

    assert orchestrator is not None
    assert orchestrator.events is not None
    # NoopEmitter should not raise on emit
    import asyncio
    asyncio.run(orchestrator.events.emit({"type": "test"}))


def test_create_default_orchestrator_with_event_emitter():
    """When event_emitter is provided, it is used directly."""
    from miqi.execution.factory import create_default_orchestrator

    emitter = MagicMock()
    emitter.emit = AsyncMock()
    orchestrator = create_default_orchestrator(
        tool_registry=None,
        event_emitter=emitter,
    )

    assert orchestrator.events is emitter


def test_create_default_orchestrator_permanent_allowlist():
    """Permanent allowlist is passed through to PermissionEngine."""
    from miqi.execution.factory import create_default_orchestrator

    orchestrator = create_default_orchestrator(
        tool_registry=None,
        permanent_allowlist={"echo hello"},
    )

    assert "echo hello" in orchestrator.permissions.permanent_allowlist


def test_create_default_orchestrator_propagates_approval_channel_capability():
    """Frontends can opt into immediate fail-closed approval handling."""
    from miqi.execution.factory import create_default_orchestrator

    orchestrator = create_default_orchestrator(
        tool_registry=None,
        approval_channel_available=False,
    )

    assert orchestrator.approval_channel_available is False
