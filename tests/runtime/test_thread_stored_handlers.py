from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from miqi.config.schema import Config
from miqi.runtime.app_server import AppServer, ClientSessionRegistry
from miqi.runtime.ledger_runtime import LedgerRuntime
from miqi.runtime.thread_app_handlers import register_codex_thread_handlers
from miqi.runtime.thread_runtime import ThreadRuntime


async def _seed_thread(db_path, session_id="client-a:default", thread_id="thread-1"):
    threads = ThreadRuntime(db_path, session_id=session_id)
    ledger = LedgerRuntime(db_path, session_id=session_id)
    await threads.initialize()
    await ledger.initialize()
    await threads.create_thread(title="Stored thread", thread_id=thread_id)
    await ledger.append_item(thread_id=thread_id, turn_id="turn-1", item_type="turn_started")
    await ledger.append_item(thread_id=thread_id, turn_id="turn-1", item_type="message", role="user", content="hello")
    await ledger.append_item(thread_id=thread_id, turn_id="turn-1", item_type="assistant_delta", content="hi")
    await ledger.append_item(thread_id=thread_id, turn_id="turn-1", item_type="turn_completed")
    await ledger.close()
    await threads.close()


def _server(tmp_path):
    registry = ClientSessionRegistry()
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path)
    state = MagicMock()
    state.load_config.return_value = cfg
    registry.bridge_context["state"] = state
    server = AppServer(registry)
    register_codex_thread_handlers(server)
    return server


@pytest.mark.asyncio
async def test_thread_read_reads_stored_thread_without_live_session(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db)
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/read",
        {"threadId": "thread-1", "includeTurns": True},
        "client-a",
        None,
    )
    thread = response["result"]["thread"]
    assert thread["id"] == "thread-1"
    assert thread["status"] == {"type": "notLoaded"}
    assert thread["turns"][0]["items"][0]["type"] == "userMessage"


@pytest.mark.asyncio
async def test_thread_turns_list_reads_stored_turns_without_live_session(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db)
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/turns/list",
        {"threadId": "thread-1", "limit": 10, "sortDirection": "asc"},
        "client-a",
        None,
    )
    assert response["result"]["data"][0]["id"] == "turn-1"


@pytest.mark.asyncio
async def test_thread_list_pages_stored_threads(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db, thread_id="thread-1")
    await _seed_thread(db, thread_id="thread-2")
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/list", {"limit": 1}, "client-a", None,
    )
    assert len(response["result"]["data"]) == 1
    assert response["result"]["nextCursor"] is not None


@pytest.mark.asyncio
async def test_thread_list_finds_threads_with_bare_session_key(tmp_path):
    """Issue #490: frontend sends the loose, UN-namespaced session_key as
    camelCase ``sessionId`` (e.g. ``desktop:1739...``), but stored
    ``runtime_threads`` rows carry the namespaced id
    (``miqi-desktop:desktop:1739...`` from create_session).

    Before the fix, _thread_list passed the bare value straight to
    list_threads, whose exact-match filter returned [] → the resume path
    picked nothing → every chat.send minted a fresh orphaned thread.
    This pins that the bare session_key is namespaced under the client
    before filtering, so resume can find the prior thread.
    """
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db, session_id="client-a:desktop:1739", thread_id="thread-resume")
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/list",
        # Mirrors the FE/preload payload: session_key under camelCase sessionId.
        {"sessionId": "desktop:1739"},
        "client-a",
        None,
    )
    rows = response["result"]["data"]
    assert len(rows) == 1, f"expected the namespaced thread to be found, got {rows}"
    assert rows[0]["id"] == "thread-resume"


@pytest.mark.asyncio
async def test_thread_list_surfaces_turn_count_for_richness_resume(tmp_path):
    """Issue #490 (fragmented legacy sessions): the frontend resume now
    prefers the thread holding the MOST history over the merely
    most-recently-touched one. ``_thread_list`` must therefore surface a
    ``turnCount`` per thread reflecting the number of distinct persisted
    turns, so ``pickThreadToResume`` can rank by richness.

    The count is derived from ``runtime_history_items`` — the SAME table
    the model reloads on resume (task_runner.load_messages) — so the
    richness signal tracks what the model will actually see. Seeds three
    threads in one session with 3, 1, and 0 turns and asserts the emitted
    ``turnCount`` matches each, all under the same session_id (isolation
    preserved — the bare session_key still resolves).
    """
    import aiosqlite as _aiosqlite

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    session_id = "client-a:desktop:1789"

    async def _seed(thread_id, turn_ids):
        threads = ThreadRuntime(db, session_id=session_id)
        ledger = LedgerRuntime(db, session_id=session_id)
        await threads.initialize()
        await ledger.initialize()
        await threads.create_thread(title=thread_id, thread_id=thread_id)
        # Create the runtime_history_items table + write to it (the table the
        # _thread_list count reads from). Use a short-lived aiosqlite conn so
        # the schema matches what StoredRuntimeReader.load_history_items queries.
        db.parent.mkdir(parents=True, exist_ok=True)
        async with _aiosqlite.connect(str(db), timeout=30) as hdb:
            await hdb.execute(
                """CREATE TABLE IF NOT EXISTS runtime_history_items (
                    item_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )"""
            )
            for i, tid in enumerate(turn_ids):
                await hdb.execute(
                    "INSERT INTO runtime_history_items VALUES (?,?,?,?,?,?,?,?)",
                    (f"{thread_id}-{tid}-{i}", thread_id, session_id, tid, "user", "x", "{}", float(i)),
                )
            await hdb.commit()
        await ledger.close()
        await threads.close()

    await _seed("thread-rich", ["t1", "t2", "t3"])
    await _seed("thread-thin", ["s1"])
    await _seed("thread-empty", [])

    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/list",
        {"sessionId": "desktop:1789", "limit": 50, "sortDirection": "asc"},
        "client-a",
        None,
    )
    rows = {row["id"]: row for row in response["result"]["data"]}
    assert rows["thread-rich"]["turnCount"] == 3
    assert rows["thread-thin"]["turnCount"] == 1
    assert rows["thread-empty"]["turnCount"] == 0


@pytest.mark.asyncio
async def test_thread_read_finds_thread_with_bare_session_key(tmp_path):
    """Issue #490: the stored read path must also namespace a bare
    session_key, otherwise reopening a thread in a session not currently
    live fails to load its history."""
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db, session_id="client-a:desktop:1739", thread_id="thread-resume")
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/read",
        {"threadId": "thread-resume", "sessionId": "desktop:1739", "includeTurns": True},
        "client-a",
        None,
    )
    thread = response["result"]["thread"]
    assert thread["id"] == "thread-resume"


@pytest.mark.asyncio
async def test_thread_read_rejects_foreign_stored_thread(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db, session_id="client-b:default", thread_id="thread-b")
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/read", {"threadId": "thread-b"}, "client-a", None,
    )
    assert response["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_thread_list_leaves_unknown_namespaced_session_key_unmolested(tmp_path):
    """``_resolve_session_id_for_stored`` only namespaces THIS client's known
    bare keyspace markers (desktop's ``desktop:``/``default``). A value with an
    UNKNOWN prefix (e.g. a hypothetical ``cli:user``) is passed through
    unchanged so the downstream ownership check can reject it — it is NOT
    force-namespaced under the caller.

    Rationale: force-namespacing would mask ``thread/import``'s rejection of a
    foreign session_id (``client-b:default`` → ``client-a:client-b:default``
    passes the ownership check, so an import that should be UNAUTHORIZED would
    instead succeed). There is no client registry to disambiguate "this
    client's own bare key" from "a foreign already-namespaced id" for an
    unknown prefix, so isolation safety wins. Adding a new client's keyspace
    means registering its marker in ``_resolve_session_id_for_stored``.

    Here ``cli:user`` is unknown to client-a → returned unchanged → matches no
    stored row → empty list (safe no-op, no leak).
    """
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(
        db, session_id="client-a:cli:user", thread_id="thread-cli"
    )
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/list", {"sessionId": "cli:user"}, "client-a", None,
    )
    # Unknown prefix is not namespaced → no match → empty, no leak.
    rows = response["result"]["data"]
    assert rows == [], f"unknown-prefix key unexpectedly matched: {rows}"


@pytest.mark.asyncio
async def test_thread_list_foreign_namespaced_value_does_not_leak(tmp_path):
    """A foreign-prefixed session id (``client-b:default``) is passed through
    unchanged — NOT namespaced under the caller — so only THIS client's own
    rows can match. The foreign ``client-b:default`` row never leaks into
    client-a's results. Pins that namespacing does not weaken cross-client
    isolation, including for thread/import's foreign-session rejection.
    """
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    # A foreign client's row — must never be returned to client-a.
    await _seed_thread(
        db, session_id="client-b:default", thread_id="thread-foreign"
    )
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/list", {"sessionId": "client-b:default"}, "client-a", None,
    )
    rows = response["result"]["data"]
    assert rows == [], f"foreign thread leaked into client-a results: {rows}"


@pytest.mark.asyncio
async def test_thread_read_ambiguous_thread_requires_session_id(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db, session_id="client-a:one", thread_id="same")
    await _seed_thread(db, session_id="client-a:two", thread_id="same")
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/read", {"threadId": "same"}, "client-a", None,
    )
    assert response["code"] == "AMBIGUOUS_THREAD"


@pytest.mark.asyncio
async def test_thread_read_missing_thread_id_rejected(tmp_path):
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/read", {}, "client-a", None,
    )
    assert response["code"] == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_thread_turns_list_missing_thread_id_rejected(tmp_path):
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/turns/list", {}, "client-a", None,
    )
    assert response["code"] == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_thread_turns_items_list_still_unsupported(tmp_path):
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/turns/items/list", {"threadId": "x"}, "client-a", None,
    )
    assert response["code"] == "UNSUPPORTED_METHOD"


# ── Stored rollback and fork tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_rollback_works_on_stored_thread_without_live_session(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db)
    # Add a second turn directly
    ledger = LedgerRuntime(db, session_id="client-a:default")
    await ledger.initialize()
    try:
        await ledger.append_item(thread_id="thread-1", turn_id="turn-2", item_type="turn_started")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-2", item_type="message", role="user", content="drop")
        await ledger.append_item(thread_id="thread-1", turn_id="turn-2", item_type="turn_completed")
    finally:
        await ledger.close()
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/rollback",
        {"threadId": "thread-1", "dropLastTurns": 1},
        "client-a",
        None,
    )
    turns = response["result"]["thread"]["turns"]
    assert [turn["id"] for turn in turns] == ["turn-1"]


@pytest.mark.asyncio
async def test_thread_fork_copies_stored_thread_without_live_session(tmp_path):
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db, thread_id="source")
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/fork",
        {"threadId": "source", "title": "Forked", "excludeTurns": False},
        "client-a",
        None,
    )
    thread = response["result"]["thread"]
    assert thread["forkedFromId"] == "source"
    assert thread["turns"][0]["id"] == "turn-1"


@pytest.mark.asyncio
async def test_thread_rollback_missing_thread_id_rejected(tmp_path):
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/rollback", {"dropLastTurns": 1}, "client-a", None,
    )
    assert response["code"] == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_thread_fork_missing_thread_id_rejected(tmp_path):
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/fork", {}, "client-a", None,
    )
    assert response["code"] == "INVALID_PARAMS"


# ── Clean workspace (missing DB) tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_thread_list_returns_empty_on_clean_workspace(tmp_path):
    """thread/list on a workspace with no runtime.db returns []."""
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/list", {}, "client-a", None,
    )
    assert response["result"]["data"] == []
    assert response["result"]["nextCursor"] is None


@pytest.mark.asyncio
async def test_thread_read_returns_not_found_on_clean_workspace(tmp_path):
    """thread/read on a workspace with no runtime.db returns NOT_FOUND."""
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/read", {"threadId": "thread-1"}, "client-a", None,
    )
    assert response["code"] == "NOT_FOUND"


# ── Sort order tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thread_list_default_desc_returns_newest_first(tmp_path):
    """thread/list default sortDirection=desc returns newest thread first."""
    import aiosqlite
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    # Seed two threads with different updated_at
    await _seed_thread(db, thread_id="old")
    await _seed_thread(db, thread_id="new")
    # Manually set updated_at so ordering is deterministic
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            "UPDATE runtime_threads SET updated_at = 1000.0 WHERE thread_id = 'old'")
        await conn.execute(
            "UPDATE runtime_threads SET updated_at = 2000.0 WHERE thread_id = 'new'")
        await conn.commit()
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/list", {}, "client-a", None,
    )
    data = response["result"]["data"]
    assert len(data) == 2
    assert data[0]["id"] == "new"
    assert data[1]["id"] == "old"


@pytest.mark.asyncio
async def test_thread_list_asc_returns_oldest_first(tmp_path):
    """thread/list with sortDirection=asc returns oldest thread first."""
    import aiosqlite
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db, thread_id="old")
    await _seed_thread(db, thread_id="new")
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            "UPDATE runtime_threads SET updated_at = 1000.0 WHERE thread_id = 'old'")
        await conn.execute(
            "UPDATE runtime_threads SET updated_at = 2000.0 WHERE thread_id = 'new'")
        await conn.commit()
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/list", {"sortDirection": "asc"}, "client-a", None,
    )
    data = response["result"]["data"]
    assert data[0]["id"] == "old"
    assert data[1]["id"] == "new"


@pytest.mark.asyncio
async def test_thread_list_pagination_cursor_stable_with_desc(tmp_path):
    """Cursor-based pagination is stable in desc order."""
    import aiosqlite
    db = tmp_path / ".miqi-runtime" / "runtime.db"
    for tid in ["t1", "t2", "t3"]:
        await _seed_thread(db, thread_id=tid)
    async with aiosqlite.connect(str(db)) as conn:
        for i, tid in enumerate(["t1", "t2", "t3"], start=1):
            await conn.execute(
                "UPDATE runtime_threads SET updated_at = ? WHERE thread_id = ?",
                (float(i * 1000), tid))
        await conn.commit()
    server = _server(tmp_path)
    # Page 1 (desc → newest first = t3)
    p1 = await server.dispatch(
        "1", "thread/list", {"limit": 2}, "client-a", None,
    )
    assert [x["id"] for x in p1["result"]["data"]] == ["t3", "t2"]
    assert p1["result"]["nextCursor"] is not None
    # Page 2
    p2 = await server.dispatch(
        "2", "thread/list", {"limit": 2, "cursor": p1["result"]["nextCursor"]},
        "client-a", None,
    )
    assert [x["id"] for x in p2["result"]["data"]] == ["t1"]
    assert p2["result"]["nextCursor"] is None


@pytest.mark.asyncio
async def test_stored_fork_copies_history_rows(tmp_path):
    """Stored fork copies runtime_history_items so destination has provider messages."""
    import time

    from miqi.runtime.history_runtime import HistoryItem
    from miqi.runtime.stored_runtime import StoredRuntimeReader

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db, thread_id="src-fork")
    # Seed history for source thread
    reader = StoredRuntimeReader(db, client_id="client-a")
    await reader._write_history_items(
        "client-a:default", "src-fork",
        [HistoryItem(item_id="h1", thread_id="src-fork", turn_id="turn-1",
                     role="user", content="fork-src",
                     payload={}, created_at=time.time())],
    )
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/fork",
        {"threadId": "src-fork", "title": "Forked", "excludeTurns": False},
        "client-a", None,
    )
    child_id = response["result"]["thread"]["id"]
    msgs = await reader.load_provider_messages(
        await reader.resolve_thread(child_id),
    )
    assert len(msgs) >= 1
    assert msgs[0]["content"] == "fork-src"


@pytest.mark.asyncio
async def test_stored_rollback_deletes_history_for_removed_turns(tmp_path):
    """Stored rollback removes history items belonging to dropped turns."""
    import time

    from miqi.runtime.history_runtime import HistoryItem
    from miqi.runtime.stored_runtime import StoredRuntimeReader

    db = tmp_path / ".miqi-runtime" / "runtime.db"
    await _seed_thread(db, thread_id="rollback-hist")
    reader = StoredRuntimeReader(db, client_id="client-a")
    # Two turns worth of history
    await reader._write_history_items(
        "client-a:default", "rollback-hist",
        [
            HistoryItem(item_id="h1", thread_id="rollback-hist", turn_id="turn-1",
                        role="user", content="keep", payload={}, created_at=time.time()),
            HistoryItem(item_id="h2", thread_id="rollback-hist", turn_id="turn-2",
                        role="user", content="drop", payload={}, created_at=time.time()),
        ],
    )
    # Add a second turn to the ledger
    from miqi.runtime.ledger_runtime import LedgerRuntime
    ledger = LedgerRuntime(db, session_id="client-a:default")
    await ledger.initialize()
    try:
        await ledger.append_item(thread_id="rollback-hist", turn_id="turn-2", item_type="turn_started")
        await ledger.append_item(thread_id="rollback-hist", turn_id="turn-2", item_type="message", role="user", content="drop")
        await ledger.append_item(thread_id="rollback-hist", turn_id="turn-2", item_type="turn_completed")
    finally:
        await ledger.close()
    server = _server(tmp_path)
    response = await server.dispatch(
        "1", "thread/rollback",
        {"threadId": "rollback-hist", "dropLastTurns": 1},
        "client-a", None,
    )
    assert response["result"]["thread"]["turns"][0]["id"] == "turn-1"
    # History for turn-2 should be gone
    msgs = await reader.load_provider_messages(
        await reader.resolve_thread("rollback-hist"),
    )
    assert len(msgs) == 1
    assert msgs[0]["content"] == "keep"
