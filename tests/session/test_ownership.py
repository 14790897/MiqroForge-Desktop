"""Tests for SessionManager ownership model — Phase 29.

Validates:
- owner_client_id metadata schema
- Legacy session detection and claim semantics
- No auto-claim on first access (AppServer path)
- Cross-client isolation for disk sessions
- client_id=None backward compatibility
"""

import json
from pathlib import Path

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_session_manager(tmp_path: Path, **kwargs):
    """Create a fresh SessionManager for testing."""
    from miqi.session.manager import SessionManager
    return SessionManager(tmp_path, **kwargs)


def _write_legacy_session(sessions_dir: Path, key: str, messages: list | None = None):
    """Write a legacy session file WITHOUT owner_client_id."""
    safe_key = key.replace(":", "_")
    session_dir = sessions_dir / safe_key
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "conversation.jsonl"
    metadata = {
        "_type": "metadata",
        "key": key,
        "created_at": "2025-01-01T00:00:00",
        "updated_at": "2025-01-01T00:00:00",
        "metadata": {},
        "last_consolidated": 0,
    }
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(metadata, ensure_ascii=False) + "\n")
        if messages:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")


def _read_metadata_from_disk(sessions_dir: Path, key: str) -> dict | None:
    """Read the metadata line from a session file."""
    safe_key = key.replace(":", "_")
    path = sessions_dir / safe_key / "conversation.jsonl"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        first = f.readline().strip()
        if first:
            return json.loads(first)
    return None


# ── Ownership metadata schema ────────────────────────────────────────────────


def test_new_session_gets_owner_client_id(tmp_path):
    """New sessions created with client_id get owner_client_id in metadata."""
    sm = _make_session_manager(tmp_path)
    session = sm.get_or_create("my-session", client_id="client-A")
    session.add_message("user", "hello")
    sm.save(session)

    meta = _read_metadata_from_disk(tmp_path / "sessions", "my-session")
    assert meta is not None
    assert meta.get("owner_client_id") == "client-A"


def test_new_session_without_client_id_has_no_owner(tmp_path):
    """New sessions created without client_id get NO owner (backward compat)."""
    sm = _make_session_manager(tmp_path)
    session = sm.get_or_create("my-session")  # no client_id
    session.add_message("user", "hello")
    sm.save(session)

    meta = _read_metadata_from_disk(tmp_path / "sessions", "my-session")
    assert meta is not None
    assert meta.get("owner_client_id") is None


def test_get_owner_returns_none_for_unowned_session(tmp_path):
    """get_owner returns None for unowned sessions."""
    _write_legacy_session(tmp_path / "sessions", "legacy-session")
    sm = _make_session_manager(tmp_path)
    assert sm.get_owner("legacy-session") is None


def test_get_owner_returns_client_id_for_owned_session(tmp_path):
    """get_owner returns the owner_client_id for owned sessions."""
    sm = _make_session_manager(tmp_path)
    session = sm.get_or_create("owned-session", client_id="client-X")
    sm.save(session)
    assert sm.get_owner("owned-session") == "client-X"


def test_get_owner_returns_none_for_nonexistent_session(tmp_path):
    """get_owner returns None for sessions that don't exist."""
    sm = _make_session_manager(tmp_path)
    assert sm.get_owner("nonexistent") is None


# ── list_sessions ownership filtering ────────────────────────────────────────


def test_list_sessions_filters_by_ownership(tmp_path):
    """list_sessions with client_id excludes foreign-owned sessions."""
    sm = _make_session_manager(tmp_path)

    # Create sessions for different owners
    s1 = sm.get_or_create("session-A", client_id="client-A")
    s1.add_message("user", "A")
    sm.save(s1)

    s2 = sm.get_or_create("session-B", client_id="client-B")
    s2.add_message("user", "B")
    sm.save(s2)

    # Client A lists: should see session-A but not session-B
    result = sm.list_sessions(client_id="client-A")
    keys = [s["key"] for s in result]
    assert "session-A" in keys
    assert "session-B" not in keys

    owned = [s for s in result if s.get("ownership") == "owned"]
    assert len(owned) == 1
    assert owned[0]["key"] == "session-A"


def test_list_sessions_includes_unowned_with_flag(tmp_path):
    """Unowned legacy sessions are included with ownership='unowned'."""
    _write_legacy_session(tmp_path / "sessions", "legacy-1")
    sm = _make_session_manager(tmp_path)

    result = sm.list_sessions(client_id="client-A")
    unowned = [s for s in result if s.get("ownership") == "unowned"]
    assert len(unowned) == 1
    assert unowned[0]["key"] == "legacy-1"


def test_list_sessions_without_client_id_returns_all(tmp_path):
    """list_sessions without client_id returns all sessions (backward compat)."""
    sm = _make_session_manager(tmp_path)
    s1 = sm.get_or_create("s1", client_id="client-A")
    sm.save(s1)
    s2 = sm.get_or_create("s2", client_id="client-B")
    sm.save(s2)

    result = sm.list_sessions()
    keys = [s["key"] for s in result]
    assert "s1" in keys
    assert "s2" in keys
    # No ownership field when client_id is None
    for s in result:
        assert "ownership" not in s


# ── get_or_create ownership semantics ────────────────────────────────────────


def test_get_or_create_new_session_sets_owner(tmp_path):
    """get_or_create with client_id on a new session sets owner_client_id."""
    sm = _make_session_manager(tmp_path)
    session = sm.get_or_create("new-session", client_id="client-A")
    assert session.metadata.get("owner_client_id") == "client-A"


def test_get_or_create_existing_owned_by_same_client(tmp_path):
    """get_or_create returns session when owner matches."""
    sm = _make_session_manager(tmp_path)
    sm.get_or_create("my-session", client_id="client-A")
    # Second call with same client
    session = sm.get_or_create("my-session", client_id="client-A")
    assert session.key == "my-session"


def test_get_or_create_existing_owned_by_different_client(tmp_path):
    """get_or_create raises UNAUTHORIZED when owner doesn't match."""
    from miqi.session.manager import OwnershipError

    sm = _make_session_manager(tmp_path)
    sm.get_or_create("my-session", client_id="client-A")
    sm.save(sm._cache["my-session"])  # persist to disk
    sm.invalidate("my-session")

    with pytest.raises(OwnershipError) as exc_info:
        sm.get_or_create("my-session", client_id="client-B")
    assert exc_info.value.code == "UNAUTHORIZED"


def test_get_or_create_unowned_legacy_raises_requires_claim(tmp_path):
    """get_or_create with client_id on unowned legacy raises REQUIRES_CLAIM."""
    from miqi.session.manager import OwnershipError

    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    with pytest.raises(OwnershipError) as exc_info:
        sm.get_or_create("legacy", client_id="client-A")
    assert exc_info.value.code == "REQUIRES_CLAIM"


def test_get_or_create_unowned_legacy_does_not_auto_claim(tmp_path):
    """After REQUIRES_CLAIM error, the session remains unowned."""
    from miqi.session.manager import OwnershipError

    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    try:
        sm.get_or_create("legacy", client_id="client-A")
    except OwnershipError:
        pass

    # Session should still be unowned
    assert sm.get_owner("legacy") is None


def test_get_or_create_in_cache_checks_ownership(tmp_path):
    """Ownership is also checked for cached sessions."""
    from miqi.session.manager import OwnershipError

    sm = _make_session_manager(tmp_path)
    sm.get_or_create("cached", client_id="client-A")

    with pytest.raises(OwnershipError) as exc_info:
        sm.get_or_create("cached", client_id="client-B")
    assert exc_info.value.code == "UNAUTHORIZED"


# ── claim_session ────────────────────────────────────────────────────────────


def test_claim_session_succeeds_on_unowned(tmp_path):
    """claim_session claims an unowned legacy session."""
    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    claimed = sm.claim_session("legacy", "client-A")
    assert claimed is True
    assert sm.get_owner("legacy") == "client-A"

    # Verify it's written to disk
    meta = _read_metadata_from_disk(tmp_path / "sessions", "legacy")
    assert meta is not None
    assert meta.get("owner_client_id") == "client-A"


def test_claim_session_idempotent(tmp_path):
    """claim_session returns False when already claimed by same client."""
    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    claimed1 = sm.claim_session("legacy", "client-A")
    assert claimed1 is True

    claimed2 = sm.claim_session("legacy", "client-A")
    assert claimed2 is False  # Already claimed — idempotent


def test_claim_session_raises_on_foreign_owned(tmp_path):
    """claim_session raises UNAUTHORIZED when session is owned by other client."""
    from miqi.session.manager import OwnershipError

    sm = _make_session_manager(tmp_path)
    sm.get_or_create("owned", client_id="client-A")
    sm.save(sm._cache["owned"])
    sm.invalidate("owned")

    with pytest.raises(OwnershipError) as exc_info:
        sm.claim_session("owned", "client-B")
    assert exc_info.value.code == "UNAUTHORIZED"


def test_two_clients_racing_to_claim_same_legacy(tmp_path):
    """Two clients racing to claim the same unowned session: one wins."""
    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    # Client A claims
    assert sm.claim_session("legacy", "client-A") is True

    # Client B tries to claim — already owned by A
    from miqi.session.manager import OwnershipError
    with pytest.raises(OwnershipError) as exc_info:
        sm.claim_session("legacy", "client-B")
    assert exc_info.value.code == "UNAUTHORIZED"


def test_claim_session_nonexistent_session(tmp_path):
    """claim_session returns False for nonexistent sessions."""
    sm = _make_session_manager(tmp_path)
    claimed = sm.claim_session("nonexistent", "client-A")
    assert claimed is False


# ── delete ownership enforcement ─────────────────────────────────────────────


def test_delete_rejects_unowned_session(tmp_path):
    """delete with client_id raises REQUIRES_CLAIM for unowned sessions."""
    from miqi.session.manager import OwnershipError

    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    with pytest.raises(OwnershipError) as exc_info:
        sm.delete("legacy", client_id="client-A")
    assert exc_info.value.code == "REQUIRES_CLAIM"


def test_delete_rejects_foreign_owned_session(tmp_path):
    """delete with client_id raises UNAUTHORIZED for foreign-owned sessions."""
    from miqi.session.manager import OwnershipError

    sm = _make_session_manager(tmp_path)
    s = sm.get_or_create("owned-by-B", client_id="client-B")
    sm.save(s)
    sm.invalidate("owned-by-B")

    with pytest.raises(OwnershipError) as exc_info:
        sm.delete("owned-by-B", client_id="client-A")
    assert exc_info.value.code == "UNAUTHORIZED"


def test_delete_succeeds_for_owned_session(tmp_path):
    """delete succeeds when client owns the session."""
    sm = _make_session_manager(tmp_path)
    s = sm.get_or_create("mine", client_id="client-A")
    sm.save(s)

    deleted = sm.delete("mine", client_id="client-A")
    assert deleted is True
    assert sm.get_owner("mine") is None  # file is gone


def test_delete_succeeds_for_nonexistent_no_check(tmp_path):
    """delete without client_id doesn't check ownership (backward compat)."""
    sm = _make_session_manager(tmp_path)
    deleted = sm.delete("nonexistent")
    assert deleted is False  # Nothing to delete, but no error


# ── archive/unarchive ownership enforcement ──────────────────────────────────


def test_archive_rejects_unowned_session(tmp_path):
    """archive with client_id raises REQUIRES_CLAIM for unowned sessions."""
    from miqi.session.manager import OwnershipError

    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    with pytest.raises(OwnershipError) as exc_info:
        sm.archive("legacy", client_id="client-A")
    assert exc_info.value.code == "REQUIRES_CLAIM"


def test_archive_rejects_foreign_owned_session(tmp_path):
    """archive with client_id raises UNAUTHORIZED for foreign-owned sessions."""
    from miqi.session.manager import OwnershipError

    sm = _make_session_manager(tmp_path)
    s = sm.get_or_create("theirs", client_id="client-B")
    sm.save(s)
    sm.invalidate("theirs")

    with pytest.raises(OwnershipError) as exc_info:
        sm.archive("theirs", client_id="client-A")
    assert exc_info.value.code == "UNAUTHORIZED"


def test_unarchive_rejects_unowned_session(tmp_path):
    """unarchive with client_id raises REQUIRES_CLAIM for unowned sessions."""
    from miqi.session.manager import OwnershipError

    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)
    sm.archive("legacy")  # Archive without client_id to create the marker

    with pytest.raises(OwnershipError) as exc_info:
        sm.unarchive("legacy", client_id="client-A")
    assert exc_info.value.code == "REQUIRES_CLAIM"


def test_unarchive_rejects_foreign_owned_session(tmp_path):
    """unarchive with client_id raises UNAUTHORIZED for foreign-owned sessions."""
    from miqi.session.manager import OwnershipError

    sm = _make_session_manager(tmp_path)
    s = sm.get_or_create("theirs", client_id="client-B")
    sm.save(s)
    sm.archive("theirs", client_id="client-B")
    sm.invalidate("theirs")

    with pytest.raises(OwnershipError) as exc_info:
        sm.unarchive("theirs", client_id="client-A")
    assert exc_info.value.code == "UNAUTHORIZED"


# ── client_id=None bypass ────────────────────────────────────────────────────


def test_get_or_create_without_client_id_works_on_any_session(tmp_path):
    """client_id=None bypasses all ownership checks (CLI/AgentLoop path)."""
    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    # Should NOT raise — client_id=None skips ownership
    session = sm.get_or_create("legacy")  # no client_id
    assert session.key == "legacy"


def test_delete_without_client_id_works_on_any_session(tmp_path):
    """delete without client_id bypasses ownership checks."""
    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    deleted = sm.delete("legacy")  # no client_id
    assert deleted is True


def test_archive_without_client_id_works_on_any_session(tmp_path):
    """archive without client_id bypasses ownership checks."""
    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    sm.archive("legacy")  # no client_id — should not raise
    marker = tmp_path / "sessions" / "legacy" / ".archived"
    assert marker.exists()


# ── Two clients, same session_key = isolated ────────────────────────────────


def test_two_clients_cannot_share_same_session_key(tmp_path):
    """Two clients with the same session_key: second client gets UNAUTHORIZED.

    Session keys are unique on disk. Client A creates a session with a key,
    client B cannot also access that key — the disk session belongs to A.
    """
    from miqi.session.manager import OwnershipError

    sm = _make_session_manager(tmp_path)
    sm.get_or_create("shared-key", client_id="client-A")
    sm.save(sm._cache["shared-key"])
    sm.invalidate("shared-key")

    # Client B tries to use the same key — should fail
    with pytest.raises(OwnershipError) as exc_info:
        sm.get_or_create("shared-key", client_id="client-B")
    assert exc_info.value.code == "UNAUTHORIZED"

    # Client A's list should show the session
    result_a = sm.list_sessions(client_id="client-A")
    owned = [s for s in result_a if s.get("ownership") == "owned"]
    assert len(owned) == 1
    assert owned[0]["key"] == "shared-key"


# ── Tracked files ownership ──────────────────────────────────────────────────


def test_load_tracked_files_rejects_unowned(tmp_path):
    """load_tracked_files with client_id raises REQUIRES_CLAIM for unowned."""
    from miqi.session.manager import OwnershipError

    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    with pytest.raises(OwnershipError) as exc_info:
        sm.load_tracked_files("legacy", client_id="client-A")
    assert exc_info.value.code == "REQUIRES_CLAIM"


def test_clear_tracked_files_rejects_unowned(tmp_path):
    """clear_tracked_files with client_id raises REQUIRES_CLAIM for unowned."""
    from miqi.session.manager import OwnershipError

    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    with pytest.raises(OwnershipError) as exc_info:
        sm.clear_tracked_files("legacy", client_id="client-A")
    assert exc_info.value.code == "REQUIRES_CLAIM"


def test_tracked_files_without_client_id_works(tmp_path):
    """tracked_files without client_id bypasses ownership checks."""
    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    files = sm.load_tracked_files("legacy")  # no client_id
    assert files == {}


# ── save_tracked_file ownership ──────────────────────────────────────────────


def test_save_tracked_file_rejects_unowned(tmp_path):
    """save_tracked_file with client_id raises REQUIRES_CLAIM for unowned."""
    from miqi.session.manager import OwnershipError

    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    with pytest.raises(OwnershipError) as exc_info:
        sm.save_tracked_file("legacy", "/test/file.py", client_id="client-A")
    assert exc_info.value.code == "REQUIRES_CLAIM"


# ── reset_tracked_file_op ownership ──────────────────────────────────────────


def test_reset_tracked_file_op_rejects_unowned(tmp_path):
    """reset_tracked_file_op with client_id raises REQUIRES_CLAIM for unowned."""
    from miqi.session.manager import OwnershipError

    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    with pytest.raises(OwnershipError) as exc_info:
        sm.reset_tracked_file_op("legacy", "/test/file.py", client_id="client-A")
    assert exc_info.value.code == "REQUIRES_CLAIM"


# ── remove_tracked_file ownership ────────────────────────────────────────────


def test_remove_tracked_file_rejects_unowned(tmp_path):
    """remove_tracked_file with client_id raises REQUIRES_CLAIM for unowned."""
    from miqi.session.manager import OwnershipError

    _write_legacy_session(tmp_path / "sessions", "legacy")
    sm = _make_session_manager(tmp_path)

    with pytest.raises(OwnershipError) as exc_info:
        sm.remove_tracked_file("legacy", "/test/file.py", client_id="client-A")
    assert exc_info.value.code == "REQUIRES_CLAIM"


# ── OwnershipError codes ─────────────────────────────────────────────────────


def test_ownership_error_codes():
    """OwnershipError has correct default and custom codes."""
    from miqi.session.manager import OwnershipError

    e1 = OwnershipError("msg")
    assert e1.code == "UNAUTHORIZED"

    e2 = OwnershipError("msg", code="REQUIRES_CLAIM")
    assert e2.code == "REQUIRES_CLAIM"


# ── Save/compact preserves ownership ─────────────────────────────────────────


def test_save_writes_owner_client_id_to_disk(tmp_path):
    """save() includes owner_client_id in the metadata line."""
    sm = _make_session_manager(tmp_path)
    session = sm.get_or_create("test", client_id="owner-1")
    session.add_message("user", "hello")
    sm.save(session)

    path = tmp_path / "sessions" / "test" / "conversation.jsonl"
    with open(path, encoding="utf-8") as f:
        meta_line = json.loads(f.readline().strip())
    assert meta_line.get("_type") == "metadata"
    assert meta_line.get("owner_client_id") == "owner-1"


def test_compact_preserves_owner_client_id(tmp_path):
    """compact() preserves owner_client_id in the rewritten metadata line."""
    sm = _make_session_manager(
        tmp_path,
        compact_threshold_messages=2,
        compact_keep_messages=1,
    )
    session = sm.get_or_create("test", client_id="owner-1")
    session.add_message("user", "msg1")
    session.add_message("user", "msg2")
    sm.save(session)

    path = tmp_path / "sessions" / "test" / "conversation.jsonl"
    with open(path, encoding="utf-8") as f:
        meta_line = json.loads(f.readline().strip())
    assert meta_line.get("owner_client_id") == "owner-1"


# ── Legacy directory injection ─────────────────────────────────────────────────


def test_session_manager_uses_injected_legacy_directory(tmp_path):
    from miqi.session.manager import SessionManager

    workspace = tmp_path / "workspace"
    legacy = tmp_path / "legacy-sessions"
    manager = SessionManager(workspace, legacy_sessions_dir=legacy)

    assert manager.sessions_dir == workspace / "sessions"
    assert manager.legacy_sessions_dir == legacy


def test_new_session_never_writes_to_legacy_directory(tmp_path):
    from miqi.session.manager import SessionManager

    workspace = tmp_path / "workspace"
    legacy = tmp_path / "legacy-sessions"
    manager = SessionManager(workspace, legacy_sessions_dir=legacy)
    session = manager.get_or_create("cli:new")
    session.add_message("user", "hello")

    manager.save(session)

    assert (workspace / "sessions" / "cli_new" / "conversation.jsonl").is_file()
    assert not legacy.exists()
