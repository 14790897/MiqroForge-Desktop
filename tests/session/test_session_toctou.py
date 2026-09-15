"""Regression tests for #1050 — session ownership TOCTOU.

The defect: ownership validation happened *outside* the per-key lock, so a
session could be deleted and re-created under a new owner between the check and
the subsequent read/modify/write. A stale in-flight operation would then land on
the new owner's session (cross-client message write / data leak).

These tests pin two independent fixes:
1. the per-key lock is shared across SessionManager instances (class-level);
2. get_or_create re-validates ownership against disk on a cache hit, so a stale
   cached entry cannot resurrect an old owner's access after a flip.
"""

import json
import threading
import time

import pytest

from miqi.session.manager import OwnershipError, SessionManager


def _make(tmp_path):
    return SessionManager(tmp_path)


# ── Fix 1: the per-key lock is shared across instances ──────────────────────


def test_session_lock_shared_across_instances(tmp_path):
    """Two SessionManager instances on the same workspace share the same lock.

    Before #1050, _session_locks was instance-level, so the task_runner's
    instance and the handlers' instances each held independent per-key locks and
    never excluded each other — the exact window the TOCTOU exploited.
    """
    sm_a = _make(tmp_path)
    sm_b = _make(tmp_path)
    assert sm_a._get_session_lock("k") is sm_b._get_session_lock("k")


# ── Fix 2: cache-hit ownership re-validation against disk ───────────────────


def test_get_or_create_revalidates_owner_from_disk_cross_instance(tmp_path):
    """A stale cached entry cannot bypass a cross-instance owner flip.

    sm_a caches the session as owner A. sm_b (a different instance) deletes it
    and re-creates it under owner B. sm_a's next get_or_create must raise
    UNAUTHORIZED instead of returning the stale (still-owned-by-A) entry.
    """
    sm_a = _make(tmp_path)
    sm_b = _make(tmp_path)

    # A creates and persists the session, leaving it in sm_a's cache.
    a = sm_a.get_or_create("shared", client_id="client-A")
    a.add_message("user", "seed")
    sm_a.save(a)

    # B deletes + re-creates + persists under owner B.
    sm_b.delete("shared", client_id="client-A")
    b = sm_b.get_or_create("shared", client_id="client-B")
    b.add_message("user", "B-secret")
    sm_b.save(b)

    # sm_a's cache still says owner A. Re-validation against disk must reject A.
    with pytest.raises(OwnershipError) as exc_info:
        sm_a.get_or_create("shared", client_id="client-A")
    assert exc_info.value.code == "UNAUTHORIZED"


# ── Atomic message-write vs delete + re-create (single-window) ──────────────


def test_message_write_atomic_against_delete_recreate(tmp_path):
    """A validate→mutate→save sequence holding the shared lock cannot be
    interleaved by a delete + re-create on another instance.

    Mirrors _save_to_session_manager: the caller holds the per-key lock across
    get_or_create → add_message → save. A concurrent delete+re-create under a new
    owner must block until the write completes, so the write can never land on
    the new owner's session.
    """
    sm_a = _make(tmp_path)
    sm_b = _make(tmp_path)

    # Seed: A owns session "k" on disk.
    seed = sm_a.get_or_create("k", client_id="client-A")
    seed.add_message("user", "seed")
    sm_a.save(seed)

    entered = threading.Event()
    release = threading.Event()
    state: dict = {}

    def writer():
        with sm_a._get_session_lock("k"):
            session = sm_a.get_or_create("k", client_id="client-A")
            session.add_message("user", "A-injected")
            entered.set()
            release.wait(timeout=5)
            sm_a.save(session)
        state["writer_done"] = True

    def flipper():
        sm_b.delete("k", client_id="client-A")
        fresh = sm_b.get_or_create("k", client_id="client-B")
        fresh.add_message("user", "B-secret")
        sm_b.save(fresh)
        state["flip_done"] = True

    t_writer = threading.Thread(target=writer)
    t_writer.start()
    assert entered.wait(timeout=5), "writer never entered the locked section"

    t_flip = threading.Thread(target=flipper)
    t_flip.start()

    # The flip must block on the shared lock until the writer releases it. A
    # short grace period is enough: if the lock were NOT shared the flip would
    # complete immediately and "flip_done" would be set here.
    time.sleep(0.3)
    assert "flip_done" not in state, "delete+re-create interleaved inside the write lock"

    release.set()
    t_writer.join(timeout=5)
    t_flip.join(timeout=5)

    assert state.get("writer_done") is True
    assert state.get("flip_done") is True

    # The flip happened *after* A's write, so the final owner is B and A's
    # injected message was deleted along with A's session.
    assert sm_b.get_owner("k") == "client-B"
    b_session = sm_b.get_or_create("k", client_id="client-B")
    contents = [m["content"] for m in b_session.messages]
    assert "B-secret" in contents
    assert "A-injected" not in contents


def test_read_owner_scans_past_leading_message_lines(tmp_path):
    """_read_owner finds the owner even when a legacy file leads with a message.

    #1050 made get_or_create re-check ownership against disk via _read_owner.
    That method must not misread a legacy file (metadata not on the first line)
    as unowned — that would let claim_session treat an owned session as
    claimable. It should scan past leading message lines to the metadata line.
    """
    sessions_dir = tmp_path / "sessions" / "legacy"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / "conversation.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "role": "user", "content": "hi", "timestamp": "2025-01-01T00:00:00",
        }) + "\n")
        f.write(json.dumps({
            "_type": "metadata",
            "key": "legacy",
            "owner_client_id": "client-A",
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
            "metadata": {},
            "last_consolidated": 0,
        }) + "\n")

    sm = _make(tmp_path)
    assert sm.get_owner("legacy") == "client-A"


def test_stale_cache_cannot_authorize_existing_unowned_session(tmp_path):
    """An existing-but-unowned disk session must raise REQUIRES_CLAIM, not be
    authorized by a stale cached owner.

    get_or_create's cache-hit re-check reads the owner from disk. When the disk
    session exists but is unowned, a stale cached entry (owner A) must NOT be
    used to authorize A — that would bypass the REQUIRES_CLAIM boundary and let
    a later save() persist A onto an unowned session.
    """
    sm_a = _make(tmp_path)

    # A creates + persists, leaving owner=A in sm_a's cache.
    a = sm_a.get_or_create("k", client_id="client-A")
    a.add_message("user", "seed")
    sm_a.save(a)

    # Another actor rewrites the disk session WITHOUT an owner (legacy).
    path = tmp_path / "sessions" / "k" / "conversation.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    rewritten = []
    for ln in lines:
        obj = json.loads(ln)
        if obj.get("_type") == "metadata":
            obj.pop("owner_client_id", None)
        rewritten.append(json.dumps(obj, ensure_ascii=False))
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    # sm_a's cache still says owner=A, but disk is now unowned. Must not fall
    # back to the cache: raise REQUIRES_CLAIM instead of authorizing A.
    with pytest.raises(OwnershipError) as exc_info:
        sm_a.get_or_create("k", client_id="client-A")
    assert exc_info.value.code == "REQUIRES_CLAIM"
