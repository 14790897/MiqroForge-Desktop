from __future__ import annotations

import pytest

from tests.runtime.test_thread_stored_handlers import _seed_thread, _server


@pytest.mark.asyncio
async def test_thread_export_returns_versioned_document(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db)
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/export", {"threadId": "thread-1"}, "client-a", None,
    )
    doc = response["result"]["document"]
    assert doc["version"] == 1
    assert doc["thread"]["thread_id"] == "thread-1"
    assert any(item["itemType"] == "message" for item in doc["ledgerItems"])


@pytest.mark.asyncio
async def test_thread_export_rejects_foreign_thread(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db, session_id="client-b:default", thread_id="thread-b")
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/export", {"threadId": "thread-b"}, "client-a", None,
    )
    assert response["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_thread_export_missing_thread_id_rejected(tmp_path):
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/export", {}, "client-a", None,
    )
    assert response["code"] == "INVALID_PARAMS"


# ── Import tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_import_round_trips_exported_document(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db, thread_id="source")
    server = _server(tmp_path)
    exported = await server.dispatch(
        "1", "thread/export", {"threadId": "source"}, "client-a", None,
    )
    doc = exported["result"]["document"]
    imported = await server.dispatch(
        "2", "thread/import",
        {"document": doc, "threadId": "imported", "includeTurns": True},
        "client-a",
        None,
    )
    thread = imported["result"]["thread"]
    assert thread["id"] == "imported"
    assert thread["turns"][0]["id"] == "turn-1"


@pytest.mark.asyncio
async def test_thread_import_rejects_foreign_session_id(tmp_path):
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/import",
        {"sessionId": "client-b:default", "document": {"version": 1, "thread": {"thread_id": "x"}, "ledgerItems": []}},
        "client-a",
        None,
    )
    assert response["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_thread_import_rejects_bad_version(tmp_path):
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/import",
        {"document": {"version": 999, "thread": {"thread_id": "x"}, "ledgerItems": []}},
        "client-a",
        None,
    )
    assert response["code"] == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_thread_import_missing_document_rejected(tmp_path):
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/import", {}, "client-a", None,
    )
    assert response["code"] == "INVALID_PARAMS"


# ── Clean workspace tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_import_creates_db_and_table_on_clean_workspace(tmp_path):
    """thread/import on clean workspace creates runtime.db and tables, then thread/read works."""
    server = _server(tmp_path)
    document = {
        "version": 1,
        "thread": {
            "thread_id": "imported-clean",
            "title": "From clean workspace",
            "status": "active",
            "created_at": 1000.0,
            "updated_at": 1000.0,
        },
        "ledgerItems": [
            {
                "itemType": "turn_started",
                "turnId": "turn-1",
                "seq": 1,
                "role": None,
                "content": "",
                "payload": {},
                "createdAt": 1000.0,
            },
            {
                "itemType": "message",
                "turnId": "turn-1",
                "seq": 2,
                "role": "user",
                "content": "hello from clean",
                "payload": {},
                "createdAt": 1000.1,
            },
        ],
        "providerMessages": [],
    }
    # Import should create the DB and tables
    imported = await server.dispatch(
        "1", "thread/import",
        {"document": document, "includeTurns": True},
        "client-a",
        None,
    )
    assert imported["result"]["thread"]["id"] == "imported-clean"
    # Verify the DB file was created
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    assert db.exists()
    # thread/read should now find it
    read = await server.dispatch(
        "2", "thread/read",
        {"threadId": "imported-clean", "includeTurns": True},
        "client-a",
        None,
    )
    assert read["result"]["thread"]["id"] == "imported-clean"


# ── History (provider-visible messages) tests ─────────────────────────────


@pytest.mark.asyncio
async def test_export_includes_provider_messages(tmp_path):
    """thread/export includes non-empty providerMessages from stored history."""
    from miqi.runtime.history_runtime import HistoryItem
    from miqi.runtime.stored_runtime import StoredRuntimeReader

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db, thread_id="export-me")
    # Write some history items directly
    import time
    reader = StoredRuntimeReader(db, client_id="client-a")
    await reader._ensure_schema()
    await reader._write_history_items(
        "client-a:default", "export-me",
        [
            HistoryItem(item_id="h1", thread_id="export-me", turn_id="turn-1",
                        role="user", content="hello export",
                        payload={}, created_at=time.time()),
            HistoryItem(item_id="h2", thread_id="export-me", turn_id="turn-1",
                        role="assistant", content="hi there",
                        payload={"message_fields": {"name": "claude"}},
                        created_at=time.time()),
        ],
    )
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/export", {"threadId": "export-me"}, "client-a", None,
    )
    doc = response["result"]["document"]
    assert len(doc["providerMessages"]) == 2
    assert doc["providerMessages"][0]["role"] == "user"
    assert doc["providerMessages"][1]["role"] == "assistant"
    assert doc["providerMessages"][1]["name"] == "claude"


@pytest.mark.asyncio
async def test_import_writes_provider_visible_history(tmp_path):
    """Import creates runtime_history_items readable via StoredRuntimeReader."""
    from miqi.runtime.stored_runtime import StoredRuntimeReader

    server = _server(tmp_path)
    document = {
        "version": 1,
        "thread": {"thread_id": "hist-1", "title": "H", "status": "active",
                    "created_at": 1.0, "updated_at": 1.0},
        "ledgerItems": [
            {"itemType": "message", "turnId": "turn-1", "seq": 1, "role": "user",
             "content": "p1", "payload": {}, "createdAt": 1.0},
            {"itemType": "message", "turnId": "turn-1", "seq": 2, "role": "assistant",
             "content": "p2", "payload": {}, "createdAt": 1.1},
        ],
        "providerMessages": [],
    }
    await server.dispatch(
        "1", "thread/import", {"document": document, "includeTurns": True},
        "client-a", None,
    )
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    reader = StoredRuntimeReader(db, client_id="client-a")
    msgs = await reader.load_provider_messages(
        await reader.resolve_thread("hist-1"),
    )
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "p1"


@pytest.mark.asyncio
async def test_import_overwrite_deletes_stale_history(tmp_path):
    """Overwrite import removes old runtime_history_items so no stale context remains."""
    import time

    from miqi.runtime.history_runtime import HistoryItem
    from miqi.runtime.stored_runtime import StoredRuntimeReader

    server = _server(tmp_path)
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    reader = StoredRuntimeReader(db, client_id="client-a")
    await reader._ensure_schema()
    await reader._write_history_items(
        "client-a:default", "overwrite-me",
        [HistoryItem(item_id="old-h", thread_id="overwrite-me", turn_id="old",
                     role="user", content="stale", payload={}, created_at=time.time())],
    )
    document = {
        "version": 1,
        "thread": {"thread_id": "overwrite-me", "title": "OW", "status": "active",
                    "created_at": 1.0, "updated_at": 1.0},
        "ledgerItems": [
            {"itemType": "message", "turnId": "turn-1", "seq": 1, "role": "user",
             "content": "fresh", "payload": {}, "createdAt": 1.0},
        ],
    }
    # First import (no overwrite) should fail because thread already exists
    # ...but our setup seeded history items without a thread row, so let's create one
    import aiosqlite
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            """INSERT INTO runtime_threads (thread_id, session_id, title, status, created_at, updated_at, metadata_json)
               VALUES ('overwrite-me','client-a:default','OW','active',1.0,1.0,'{}')""")
        await conn.commit()

    # Now import with overwrite
    resp = await server.dispatch(
        "1", "thread/import",
        {"document": document, "overwrite": True, "includeTurns": True},
        "client-a", None,
    )
    assert resp["result"]["thread"]["id"] == "overwrite-me"
    # Stale history item should be gone; only fresh one should remain
    msgs = await reader.load_provider_messages(
        await reader.resolve_thread("overwrite-me"),
    )
    assert len(msgs) == 1
    assert msgs[0]["content"] == "fresh"


# ── Dedup: export → import round-trip MUST NOT duplicate provider history ──


@pytest.mark.asyncio
async def test_import_round_trip_does_not_duplicate_provider_history(tmp_path):
    """Export→import round-trip: provider history count equals ledger message count (no dup)."""
    import time

    from miqi.runtime.history_runtime import HistoryItem
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.stored_runtime import StoredRuntimeReader

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db, thread_id="src")
    # Add a second message-type ledger item so ledger has 2 messages
    ledger = LedgerRuntime(db, session_id="client-a:default")
    await ledger.initialize()
    try:
        await ledger.append_item(thread_id="src", turn_id="turn-1",
                                 item_type="message", role="assistant", content="hi")
    finally:
        await ledger.close()
    # Seed 2 history items for source (same content as ledger messages)
    reader = StoredRuntimeReader(db, client_id="client-a")
    await reader._write_history_items(
        "client-a:default", "src",
        [
            HistoryItem(item_id="h1", thread_id="src", turn_id="turn-1",
                        role="user", content="hello", payload={}, created_at=time.time()),
            HistoryItem(item_id="h2", thread_id="src", turn_id="turn-1",
                        role="assistant", content="hi", payload={}, created_at=time.time()),
        ],
    )
    # Verify source has 2 provider messages
    src_msgs = await reader.load_provider_messages(
        await reader.resolve_thread("src"),
    )
    assert len(src_msgs) == 2

    # Export then import
    server = _server(tmp_path)
    exported = await server.dispatch(
        "1", "thread/export", {"threadId": "src"}, "client-a", None,
    )
    doc = exported["result"]["document"]
    # Document must have both ledgerItems (2 message-type) and providerMessages (2)
    msg_ledger = [i for i in doc["ledgerItems"] if i["itemType"] == "message"]
    assert len(msg_ledger) == 2
    assert len(doc["providerMessages"]) == 2

    imported = await server.dispatch(
        "2", "thread/import",
        {"document": doc, "threadId": "roundtrip", "includeTurns": True},
        "client-a", None,
    )
    assert imported["result"]["thread"]["id"] == "roundtrip"
    # Provider history MUST NOT be duplicated — count = ledger message count (2),
    # NOT ledger (2) + providerMessages (2) = 4
    imported_msgs = await reader.load_provider_messages(
        await reader.resolve_thread("roundtrip"),
    )
    assert len(imported_msgs) == 2, (
        f"Expected 2 (ledger only, no dup), got {len(imported_msgs)}: {imported_msgs}"
    )


@pytest.mark.asyncio
async def test_import_uses_provider_messages_fallback_when_no_ledger_messages(tmp_path):
    """When import doc has no message-type ledgerItems, providerMessages is the fallback."""
    from miqi.runtime.stored_runtime import StoredRuntimeReader

    server = _server(tmp_path)
    document = {
        "version": 1,
        "thread": {"thread_id": "fallback-1", "title": "FB", "status": "active",
                    "created_at": 1.0, "updated_at": 1.0},
        "ledgerItems": [
            # Only turn_started / turn_completed — no message-type items
            {"itemType": "turn_started", "turnId": "turn-1", "seq": 1, "role": None,
             "content": "", "payload": {}, "createdAt": 1.0},
            {"itemType": "turn_completed", "turnId": "turn-1", "seq": 2, "role": None,
             "content": "", "payload": {}, "createdAt": 1.1},
        ],
        "providerMessages": [
            {"role": "user", "content": "fallback-msg"},
            {"role": "assistant", "content": "fallback-reply"},
        ],
    }
    resp = await server.dispatch(
        "1", "thread/import",
        {"document": document, "threadId": "fallback-1", "includeTurns": True},
        "client-a", None,
    )
    assert resp["result"]["thread"]["id"] == "fallback-1"
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    reader = StoredRuntimeReader(db, client_id="client-a")
    msgs = await reader.load_provider_messages(
        await reader.resolve_thread("fallback-1"),
    )
    assert len(msgs) == 2
    # Order is nondeterministic — items are created with the same timestamp
    # and sorted by UUID as tiebreaker.  Check presence, not order.
    contents = {m["content"] for m in msgs}
    assert contents == {"fallback-msg", "fallback-reply"}


@pytest.mark.asyncio
async def test_import_ledger_wins_over_provider_messages_when_both_present(tmp_path):
    """When both ledger messages and providerMessages exist, ledger is the sole source."""
    from miqi.runtime.stored_runtime import StoredRuntimeReader

    server = _server(tmp_path)
    document = {
        "version": 1,
        "thread": {"thread_id": "both-1", "title": "B", "status": "active",
                    "created_at": 1.0, "updated_at": 1.0},
        "ledgerItems": [
            {"itemType": "message", "turnId": "turn-1", "seq": 1, "role": "user",
             "content": "from-ledger", "payload": {}, "createdAt": 1.0},
            {"itemType": "message", "turnId": "turn-1", "seq": 2, "role": "assistant",
             "content": "ledger-reply", "payload": {}, "createdAt": 1.1},
        ],
        "providerMessages": [
            {"role": "user", "content": "different-from-provider"},
            {"role": "assistant", "content": "also-different"},
        ],
    }
    await server.dispatch(
        "1", "thread/import",
        {"document": document, "threadId": "both-1", "includeTurns": True},
        "client-a", None,
    )
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    reader = StoredRuntimeReader(db, client_id="client-a")
    msgs = await reader.load_provider_messages(
        await reader.resolve_thread("both-1"),
    )
    # Must be exactly 2 (ledger messages only), NOT 4 (ledger + provider)
    assert len(msgs) == 2, f"Expected 2 (ledger only), got {len(msgs)}"
    assert msgs[0]["content"] == "from-ledger"
    assert msgs[1]["content"] == "ledger-reply"


@pytest.mark.asyncio
async def test_import_overwrite_does_not_duplicate_provider_history(tmp_path):
    """Overwrite import on a thread that already has history must not duplicate."""
    import time

    import aiosqlite

    from miqi.runtime.history_runtime import HistoryItem
    from miqi.runtime.stored_runtime import StoredRuntimeReader

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    reader = StoredRuntimeReader(db, client_id="client-a")
    await reader._ensure_schema()
    # Pre-seed a thread with history
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            """INSERT INTO runtime_threads (thread_id, session_id, title, status, created_at, updated_at, metadata_json)
               VALUES ('ow-1','client-a:default','OW','active',1.0,1.0,'{}')""")
        await conn.commit()
    await reader._write_history_items(
        "client-a:default", "ow-1",
        [HistoryItem(item_id="old-h", thread_id="ow-1", turn_id="old",
                     role="user", content="stale", payload={}, created_at=time.time())],
    )
    # Export a source thread with 2 message-type ledger items + 2 history msgs
    from miqi.runtime.ledger_runtime import LedgerRuntime
    await _seed_thread(db, thread_id="src-ow")
    # Add second message-type ledger item (ledger now has: hello, extra-msg)
    ledger2 = LedgerRuntime(db, session_id="client-a:default")
    await ledger2.initialize()
    try:
        await ledger2.append_item(thread_id="src-ow", turn_id="turn-1",
                                  item_type="message", role="assistant", content="extra-msg")
    finally:
        await ledger2.close()
    # History items match ledger content (same as what export→import round-trip would produce)
    await reader._write_history_items(
        "client-a:default", "src-ow",
        [
            HistoryItem(item_id="h1", thread_id="src-ow", turn_id="turn-1",
                        role="user", content="hello", payload={}, created_at=time.time()),
            HistoryItem(item_id="h2", thread_id="src-ow", turn_id="turn-1",
                        role="assistant", content="extra-msg", payload={}, created_at=time.time()),
        ],
    )
    server = _server(tmp_path)
    exported = await server.dispatch(
        "1", "thread/export", {"threadId": "src-ow"}, "client-a", None,
    )
    doc = exported["result"]["document"]

    # Import with overwrite into ow-1
    resp = await server.dispatch(
        "2", "thread/import",
        {"document": doc, "threadId": "ow-1", "overwrite": True, "includeTurns": True},
        "client-a", None,
    )
    assert resp["result"]["thread"]["id"] == "ow-1"
    msgs = await reader.load_provider_messages(
        await reader.resolve_thread("ow-1"),
    )
    # Must be exactly 2 — no stale, no duplicate from providerMessages in doc
    assert len(msgs) == 2, f"Expected 2 after overwrite, got {len(msgs)}"
    contents = {m["content"] for m in msgs}
    assert "stale" not in contents
    assert "hello" in contents
    assert "extra-msg" in contents
