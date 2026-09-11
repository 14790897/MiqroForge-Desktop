"""Tests for cron handlers — Phase 35.6.

Validates cron.list, cron.create, cron.update, cron.delete,
cron.toggle, cron.run, and cron.runs migrated from bridge legacy
to AppServer async handlers.
"""

import pytest

from miqi.runtime.app_server import ClientSessionRegistry

# ── cron.list ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cron_list_returns_jobs():
    """cron.list should return jobs list."""
    from miqi.runtime.cron_handlers import cron_list_handler

    registry = ClientSessionRegistry()
    result = await cron_list_handler("req-1", {}, "client-1", None, registry)
    assert "jobs" in result["result"]
    assert isinstance(result["result"]["jobs"], list)


# ── cron.create ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cron_create_requires_name():
    """cron.create should reject empty name."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.cron_handlers import cron_create_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="name is required"):
        await cron_create_handler("req-1", {}, "client-1", None, registry)


@pytest.mark.asyncio
async def test_cron_create_invalid_schedule_kind():
    """cron.create should reject invalid schedule kind."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.cron_handlers import cron_create_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="Invalid schedule kind"):
        await cron_create_handler(
            "req-1", {"name": "test", "scheduleKind": "invalid"},
            "client-1", None, registry,
        )


@pytest.mark.asyncio
async def test_cron_create_at_requires_at_ms():
    """cron.create with 'at' schedule kind requires atMs."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.cron_handlers import cron_create_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="atMs is required"):
        await cron_create_handler(
            "req-1", {"name": "test", "scheduleKind": "at"},
            "client-1", None, registry,
        )


# ── cron.update ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cron_update_requires_job_id():
    """cron.update should reject empty jobId."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.cron_handlers import cron_update_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="jobId is required"):
        await cron_update_handler("req-1", {}, "client-1", None, registry)


@pytest.mark.asyncio
async def test_cron_update_not_found():
    """cron.update should raise for nonexistent job."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.cron_handlers import cron_update_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="Job not found"):
        await cron_update_handler(
            "req-1", {"jobId": "nonexistent-123"}, "client-1", None, registry,
        )


# ── cron.delete ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cron_delete_requires_job_id():
    """cron.delete should reject empty jobId."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.cron_handlers import cron_delete_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="jobId is required"):
        await cron_delete_handler("req-1", {}, "client-1", None, registry)


# ── cron.toggle ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cron_toggle_requires_job_id():
    """cron.toggle should reject empty jobId."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.cron_handlers import cron_toggle_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="jobId is required"):
        await cron_toggle_handler("req-1", {}, "client-1", None, registry)


# ── cron.run ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cron_run_requires_job_id():
    """cron.run should reject empty jobId."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.cron_handlers import cron_run_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="jobId is required"):
        await cron_run_handler("req-1", {}, "client-1", None, registry)


# ── cron.runs ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cron_runs_returns_list():
    """cron.runs should return runs list."""
    from miqi.runtime.cron_handlers import cron_runs_handler

    registry = ClientSessionRegistry()
    result = await cron_runs_handler("req-1", {}, "client-1", None, registry)
    assert "runs" in result["result"]
    assert isinstance(result["result"]["runs"], list)
