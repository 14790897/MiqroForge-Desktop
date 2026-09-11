"""Tests for ReplayInspector — live-independent replay/debug views."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_stored_inspector_builds_thread_document(tmp_path):
    import time

    from miqi.runtime.history_runtime import HistoryItem
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_inspector import ReplayInspector
    from miqi.runtime.stored_runtime import StoredRuntimeReader
    from miqi.runtime.thread_runtime import ThreadRuntime

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    threads = ThreadRuntime(db, session_id="client-a:default")
    ledger = LedgerRuntime(db, session_id="client-a:default")
    await threads.initialize()
    await ledger.initialize()
    try:
        await threads.create_thread(title="T", thread_id="thread-1")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="turn_started")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="message", role="user", content="hello")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="assistant_delta", content="hi")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="turn_completed")
        # Seed matching history so integrity is ok
        reader = StoredRuntimeReader(db, client_id="client-a")
        await reader._write_history_items(
            "client-a:default", "thread-1",
            [HistoryItem("h1", "thread-1", "turn-1", "user", "hello", {}, time.time())],
        )

        inspector = ReplayInspector(db, client_id="client-a")
        document = await inspector.build_thread_document("thread-1")

        assert document["threadId"] == "thread-1"
        assert document["source"] == "stored"
        assert document["documentHash"].startswith("sha256:")
        assert document["turns"][0]["turn_id"] == "turn-1"
        assert document["integrity"]["ok"] is True
    finally:
        await ledger.close()
        await threads.close()


@pytest.mark.asyncio
async def test_integrity_detects_history_ledger_provider_message_mismatch(tmp_path):
    from miqi.runtime.history_runtime import HistoryItem
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_inspector import ReplayInspector
    from miqi.runtime.stored_runtime import StoredRuntimeReader
    from miqi.runtime.thread_runtime import ThreadRuntime

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    threads = ThreadRuntime(db, session_id="client-a:default")
    ledger = LedgerRuntime(db, session_id="client-a:default")
    await threads.initialize()
    await ledger.initialize()
    try:
        await threads.create_thread(title="T", thread_id="thread-1")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="message", role="user", content="ledger")
        reader = StoredRuntimeReader(db, client_id="client-a")
        await reader._write_history_items(
            "client-a:default",
            "thread-1",
            [HistoryItem("h1", "thread-1", "turn-1", "user", "history", {}, 1.0)],
        )

        inspector = ReplayInspector(db, client_id="client-a")
        report = await inspector.integrity_report("thread-1")

        assert report.ok is False
        assert any(check.name == "providerHistoryMatchesLedger" and not check.ok for check in report.checks)
    finally:
        await ledger.close()
        await threads.close()


@pytest.mark.asyncio
async def test_integrity_detects_duplicate_or_non_monotonic_seq(tmp_path):
    import aiosqlite

    from miqi.runtime.replay_inspector import ReplayInspector
    from miqi.runtime.stored_runtime import StoredRuntimeReader

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    reader = StoredRuntimeReader(db, client_id="client-a")
    await reader._ensure_schema()
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            """INSERT INTO runtime_threads
               (thread_id, session_id, title, status, created_at, updated_at, metadata_json)
               VALUES ('thread-1', 'client-a:default', 'T', 'active', 1, 1, '{}')"""
        )
        await conn.execute(
            """INSERT INTO runtime_ledger_items
               (item_id, session_id, thread_id, turn_id, seq, item_type, role, content, payload_json, created_at)
               VALUES ('i1', 'client-a:default', 'thread-1', 'turn-1', 2, 'message', 'user', 'hello', '{}', 1)"""
        )
        await conn.execute(
            """INSERT INTO runtime_ledger_items
               (item_id, session_id, thread_id, turn_id, seq, item_type, role, content, payload_json, created_at)
               VALUES ('i2', 'client-a:default', 'thread-1', 'turn-2', 2, 'message', 'user', 'again', '{}', 2)"""
        )
        await conn.commit()

    report = await ReplayInspector(db, client_id="client-a").integrity_report("thread-1")

    assert any(check.name == "ledgerSeqMonotonic" and not check.ok for check in report.checks)


@pytest.mark.asyncio
async def test_integrity_detects_dangling_tool_exec_and_approval(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_inspector import ReplayInspector
    from miqi.runtime.thread_runtime import ThreadRuntime

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    threads = ThreadRuntime(db, session_id="client-a:default")
    ledger = LedgerRuntime(db, session_id="client-a:default")
    await threads.initialize()
    await ledger.initialize()
    try:
        await threads.create_thread(title="T", thread_id="thread-1")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="turn_started")
        await ledger.append_item(
            thread_id="thread-1",
            turn_id="turn-1",
            item_type="tool_call_started",
            payload={"tool_call_id": "tc-1", "name": "exec"},
        )
        await ledger.append_item(
            thread_id="thread-1",
            turn_id="turn-1",
            item_type="exec_started",
            payload={"tool_call_id": "tc-1", "command": "sleep 10"},
        )
        await ledger.append_item(
            thread_id="thread-1",
            turn_id="turn-1",
            item_type="approval_requested",
            payload={"approval_id": "ap-1", "tool_call_id": "tc-1"},
        )

        report = await ReplayInspector(db, client_id="client-a").integrity_report("thread-1")

        dangling = next(check for check in report.checks if check.name == "noDanglingOperations")
        assert dangling.ok is False
        assert dangling.details["pendingTools"]
        assert dangling.details["pendingExecs"]
        assert dangling.details["pendingApprovals"]
    finally:
        await ledger.close()
        await threads.close()


# ── History-empty / ledger-nonempty detection ─────────────────────────────


@pytest.mark.asyncio
async def test_history_empty_ledger_nonempty_reports_mismatch(tmp_path):
    """When runtime_history_items is empty but ledger has messages,
    provider_messages_report must report historyMessages=[], matches=False."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_inspector import ReplayInspector
    from miqi.runtime.thread_runtime import ThreadRuntime

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    threads = ThreadRuntime(db, session_id="client-a:default")
    ledger = LedgerRuntime(db, session_id="client-a:default")
    await threads.initialize()
    await ledger.initialize()
    try:
        await threads.create_thread(title="T", thread_id="thread-1")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="message", role="user", content="ledger-only")
        # No history rows written

        inspector = ReplayInspector(db, client_id="client-a")
        report = await inspector.provider_messages_report("thread-1")

        assert report["historyMessages"] == []
        assert report["ledgerMessages"] == [{"role": "user", "content": "ledger-only"}]
        assert report["matches"] is False
        assert report["historyCount"] == 0
        assert report["ledgerCount"] == 1
    finally:
        await ledger.close()
        await threads.close()


@pytest.mark.asyncio
async def test_integrity_fails_when_history_empty_ledger_nonempty(tmp_path):
    """Integrity report must flag history-ledger mismatch when history is empty
    but ledger has messages.  ok must be False."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_inspector import ReplayInspector
    from miqi.runtime.thread_runtime import ThreadRuntime

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    threads = ThreadRuntime(db, session_id="client-a:default")
    ledger = LedgerRuntime(db, session_id="client-a:default")
    await threads.initialize()
    await ledger.initialize()
    try:
        await threads.create_thread(title="T", thread_id="thread-1")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="message", role="user", content="ledger-only")

        inspector = ReplayInspector(db, client_id="client-a")
        report = await inspector.integrity_report("thread-1")

        assert report.ok is False
        match_check = next(check for check in report.checks if check.name == "providerHistoryMatchesLedger")
        assert match_check.ok is False
    finally:
        await ledger.close()
        await threads.close()


@pytest.mark.asyncio
async def test_thread_document_empty_provider_messages_when_history_empty(tmp_path):
    """build_thread_document returns providerMessages=[] when runtime_history_items
    is empty (even if ledger has messages), and integrity.ok=False."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_inspector import ReplayInspector
    from miqi.runtime.thread_runtime import ThreadRuntime

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    threads = ThreadRuntime(db, session_id="client-a:default")
    ledger = LedgerRuntime(db, session_id="client-a:default")
    await threads.initialize()
    await ledger.initialize()
    try:
        await threads.create_thread(title="T", thread_id="thread-1")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="message", role="user", content="ledger-msg")

        inspector = ReplayInspector(db, client_id="client-a")
        doc = await inspector.build_thread_document("thread-1")

        assert doc["providerMessages"] == []
        assert doc["integrity"]["ok"] is False
    finally:
        await ledger.close()
        await threads.close()


# ── build_turn_response (debug/replay/turn) ───────────────────────────────


@pytest.mark.asyncio
async def test_build_turn_response_includes_metadata(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_inspector import ReplayInspector
    from miqi.runtime.thread_runtime import ThreadRuntime

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    threads = ThreadRuntime(db, session_id="client-a:default")
    ledger = LedgerRuntime(db, session_id="client-a:default")
    await threads.initialize()
    await ledger.initialize()
    try:
        await threads.create_thread(title="T", thread_id="thread-1")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="turn_started")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="message", role="user", content="hi")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="turn_completed")

        inspector = ReplayInspector(db, client_id="client-a")
        response = await inspector.build_turn_response("thread-1", "turn-1")

        assert response["threadId"] == "thread-1"
        assert response["turnId"] == "turn-1"
        assert response["sessionId"] == "client-a:default"
        assert response["source"] == "stored"
        assert response["timeline"]["turn_id"] == "turn-1"
        assert response["rawLedgerItems"] == []
    finally:
        await ledger.close()
        await threads.close()


@pytest.mark.asyncio
async def test_build_turn_response_include_raw_ledger(tmp_path):
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_inspector import ReplayInspector
    from miqi.runtime.thread_runtime import ThreadRuntime

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    threads = ThreadRuntime(db, session_id="client-a:default")
    ledger = LedgerRuntime(db, session_id="client-a:default")
    await threads.initialize()
    await ledger.initialize()
    try:
        await threads.create_thread(title="T", thread_id="thread-1")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="turn_started")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="message", role="user", content="hi")

        inspector = ReplayInspector(db, client_id="client-a")
        response = await inspector.build_turn_response("thread-1", "turn-1", include_raw_ledger=True)

        assert len(response["rawLedgerItems"]) == 2
        types = [r["itemType"] for r in response["rawLedgerItems"]]
        assert types == ["turn_started", "message"]
    finally:
        await ledger.close()
        await threads.close()


@pytest.mark.asyncio
async def test_build_turn_response_unknown_turn_null_timeline(tmp_path):
    """Unknown turn in an existing thread returns timeline=None with full metadata."""
    from miqi.runtime.ledger_runtime import LedgerRuntime
    from miqi.runtime.replay_inspector import ReplayInspector
    from miqi.runtime.thread_runtime import ThreadRuntime

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    threads = ThreadRuntime(db, session_id="client-a:default")
    ledger = LedgerRuntime(db, session_id="client-a:default")
    await threads.initialize()
    await ledger.initialize()
    try:
        await threads.create_thread(title="T", thread_id="thread-1")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-1", item_type="turn_started")

        inspector = ReplayInspector(db, client_id="client-a")
        response = await inspector.build_turn_response("thread-1", "nonexistent-turn")

        assert response["threadId"] == "thread-1"
        assert response["turnId"] == "nonexistent-turn"
        assert response["sessionId"] == "client-a:default"
        assert response["source"] == "stored"
        assert response["timeline"] is None
        assert response["rawLedgerItems"] == []
    finally:
        await ledger.close()
        await threads.close()
