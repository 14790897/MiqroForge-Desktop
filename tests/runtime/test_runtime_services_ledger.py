"""Tests for ledger wiring into RuntimeServices and RuntimeSession (Phase 24)."""

import pytest


def test_runtime_services_creates_ledger_runtime(fake_config, fake_provider, tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.services import RuntimeServices

    services = RuntimeServices.from_config(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-ledger",
        workspace=tmp_path,
    )

    assert isinstance(services.ledger_runtime, LedgerRuntime)


@pytest.mark.asyncio
async def test_runtime_session_initializes_and_closes_ledger(fake_config, fake_provider, tmp_path):
    from miqi.runtime.session import RuntimeSession

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-ledger-session",
        workspace=tmp_path,
    )
    await runtime.start()
    assert runtime.services.ledger_runtime._db is not None

    await runtime.stop()
    assert runtime.services.ledger_runtime._db is None
