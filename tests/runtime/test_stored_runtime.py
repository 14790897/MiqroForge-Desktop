from __future__ import annotations

import pytest

from miqi.runtime.ledger_runtime import LedgerRuntime
from miqi.runtime.stored_runtime import (
    StoredRuntimeReader,
    StoredThreadAmbiguousError,
    StoredThreadNotFoundError,
    StoredThreadUnauthorizedError,
    session_belongs_to_client,
)
from miqi.runtime.thread_runtime import ThreadRuntime


@pytest.mark.asyncio
async def test_session_belongs_to_client():
    assert session_belongs_to_client("client-a:default", "client-a")
    assert session_belongs_to_client("client-a:project", "client-a")
    assert session_belongs_to_client("client-a", "client-a")
    assert not session_belongs_to_client("client-10:default", "client-1")
    assert not session_belongs_to_client("other:default", "client-1")


@pytest.mark.asyncio
async def test_stored_reader_lists_only_client_owned_threads(tmp_path):
    db = tmp_path / "runtime.db"
    a = ThreadRuntime(db, session_id="client-a:default")
    b = ThreadRuntime(db, session_id="client-b:default")
    await a.initialize()
    await b.initialize()
    try:
        await a.create_thread(title="A", thread_id="thread-a")
        await b.create_thread(title="B", thread_id="thread-b")
        reader = StoredRuntimeReader(db, client_id="client-a")
        threads = await reader.list_threads(include_archived=True)
        assert [t.thread_id for t in threads] == ["thread-a"]
    finally:
        await a.close()
        await b.close()


@pytest.mark.asyncio
async def test_stored_reader_resolves_thread_by_session_id(tmp_path):
    db = tmp_path / "runtime.db"
    runtime = ThreadRuntime(db, session_id="client-a:project")
    await runtime.initialize()
    try:
        await runtime.create_thread(title="Project", thread_id="same")
        reader = StoredRuntimeReader(db, client_id="client-a")
        thread = await reader.resolve_thread("same", session_id="client-a:project")
        assert thread.thread_id == "same"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_stored_reader_rejects_foreign_session_id(tmp_path):
    reader = StoredRuntimeReader(tmp_path / "nonexistent.db", client_id="client-a")
    with pytest.raises(StoredThreadUnauthorizedError):
        await reader.resolve_thread("thread-x", session_id="client-b:default")


@pytest.mark.asyncio
async def test_stored_reader_ambiguous_thread_requires_session_id(tmp_path):
    db = tmp_path / "runtime.db"
    a1 = ThreadRuntime(db, session_id="client-a:one")
    a2 = ThreadRuntime(db, session_id="client-a:two")
    await a1.initialize()
    await a2.initialize()
    try:
        await a1.create_thread(title="One", thread_id="same")
        await a2.create_thread(title="Two", thread_id="same")
        reader = StoredRuntimeReader(db, client_id="client-a")
        with pytest.raises(StoredThreadAmbiguousError):
            await reader.resolve_thread("same")
    finally:
        await a1.close()
        await a2.close()


@pytest.mark.asyncio
async def test_stored_reader_not_found_for_missing_thread(tmp_path):
    db = tmp_path / "runtime.db"
    runtime = ThreadRuntime(db, session_id="client-a:default")
    await runtime.initialize()
    try:
        reader = StoredRuntimeReader(db, client_id="client-a")
        with pytest.raises(StoredThreadNotFoundError):
            await reader.resolve_thread("nonexistent")
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_stored_reader_loads_rollback_effective_ledger_items(tmp_path):
    db = tmp_path / "runtime.db"
    threads = ThreadRuntime(db, session_id="client-a:default")
    ledger = LedgerRuntime(db, session_id="client-a:default")
    await threads.initialize()
    await ledger.initialize()
    try:
        await threads.create_thread(title="T", thread_id="thread-1")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="message", role="user", content="keep")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-2", item_type="message", role="user", content="drop")
        await ledger.append_rollback_marker("thread-1", drop_last_turns=1)
        reader = StoredRuntimeReader(db, client_id="client-a")
        bundle = await reader.load_bundle("thread-1")
        assert [item.content for item in bundle.ledger_items if item.role == "user"] == ["keep"]
    finally:
        await ledger.close()
        await threads.close()
