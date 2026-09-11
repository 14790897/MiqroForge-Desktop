"""Tests for #956 — folder-bound session resolution.

Folder-bound sessions write their real conversation under the bound workspace
root while the app-home root keeps only a stub.  These tests cover the
read-side resolution (sessions.get / sessions.list), ownership isolation, and
the mutation handlers' folder-copy cleanup, on top of the #918 exclude_empty
empty-session filtering.
"""

import pytest


def _install_app_home(monkeypatch, app_home):
    """Point the bridge state's config at a per-test app-home workspace."""
    from unittest.mock import MagicMock

    from miqi.bridge import server as bridge_module
    from miqi.config.schema import Config

    config = Config()
    config.agents.defaults.workspace = str(app_home)
    state = MagicMock()
    state.load_config.return_value = config
    monkeypatch.setattr(bridge_module, "_state", state)
    return config


def _write_folder_session(folder_root, key, client_id):
    """Write a session conversation under a folder root (write-side mirror)."""
    from miqi.session.manager import SessionManager

    sm = SessionManager(folder_root)
    session = sm.get_or_create(key, client_id=client_id)
    session.add_message("user", "folder question")
    session.add_message("assistant", "folder answer")
    sm.save(session)
    return sm


def _write_app_home_stub(app_home, key, client_id, workspace=None):
    """Write an app-home stub for a session (optionally with a workspace binding)."""
    from miqi.session.manager import SessionManager

    sm = SessionManager(app_home)
    session = sm.get_or_create(key, client_id=client_id, workspace=workspace)
    sm.save(session)
    return sm


def _write_app_home_stub_at(app_home, key, client_id, workspace, updated_at):
    """Write an app-home binding stub stamped with an explicit updated_at.

    list_sessions orders by updated_at, so a test that needs a particular
    recency ordering has to pin it on the metadata line directly.
    """
    import json

    sm = _write_app_home_stub(app_home, key, client_id, workspace)
    path = sm.get_session_dir(key) / "conversation.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    meta["updated_at"] = updated_at
    lines[0] = json.dumps(meta, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sm


def test_load_existing_does_not_migrate_legacy_flat_file(tmp_path):
    """load_existing must not migrate a legacy flat session file (#956 review).

    _find_folder_session probes candidate workspace roots via load_existing;
    if that mutated legacy files, a scan could silently move a session into
    the wrong root.  load_existing must leave flat .jsonl files untouched.
    """
    from miqi.session.manager import SessionManager

    sm = SessionManager(tmp_path)
    key = "legacy-flat"
    safe_key = "legacy-flat"  # no colon → dir/file name unchanged
    flat = sm.sessions_dir / f"{safe_key}.jsonl"
    flat.write_text(
        '{"_type": "metadata", "metadata": {}, "owner_client_id": "client-1"}\n'
        '{"role": "user", "content": "hi", "timestamp": "2026-01-01T00:00:00"}\n',
        encoding="utf-8",
    )

    # Probing must not migrate: the directory-based path does not exist yet,
    # so load_existing returns None and leaves the flat file alone.
    assert sm.load_existing(key) is None
    assert flat.exists()
    assert not (sm.sessions_dir / safe_key).exists()


# ── sessions.get ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_get_resolves_folder_session_via_metadata(monkeypatch, tmp_path):
    """sessions.get returns the folder-root copy for a workspace-bound stub."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_handler

    app_home = tmp_path / "app-home"
    folder_root = tmp_path / "task-folder"
    app_home.mkdir(parents=True)
    folder_root.mkdir(parents=True)
    _install_app_home(monkeypatch, app_home)

    # Write side: real conversation lives under the bound folder root
    _write_folder_session(folder_root, "folder-session", "client-1")
    # Read side: app-home keeps a binding-only stub (empty + workspace metadata)
    _write_app_home_stub(app_home, "folder-session", "client-1", folder_root)

    registry = ClientSessionRegistry()
    result = await sessions_get_handler(
        "req-1", {"session_key": "folder-session"}, "client-1", None, registry,
    )
    r = result["result"]
    assert r["workspace"] == str(folder_root)
    contents = [m.get("content") for m in r["messages"]]
    assert "folder question" in contents
    assert "folder answer" in contents


@pytest.mark.asyncio
async def test_sessions_get_folder_fallback_via_recent_workspace(monkeypatch, tmp_path):
    """A binding-less folder session is found by scanning known workspace roots."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_handler

    app_home = tmp_path / "app-home"
    folder_root = tmp_path / "task-folder"
    app_home.mkdir(parents=True)
    folder_root.mkdir(parents=True)
    _install_app_home(monkeypatch, app_home)

    # No app-home stub for folder-session — only another session's metadata
    # records the folder root (legacy registration path).
    _write_folder_session(folder_root, "folder-session", "client-1")
    _write_app_home_stub(app_home, "other-session", "client-1", folder_root)

    registry = ClientSessionRegistry()
    result = await sessions_get_handler(
        "req-1", {"session_key": "folder-session"}, "client-1", None, registry,
    )
    r = result["result"]
    assert r["workspace"] == str(folder_root)
    assert any(m.get("content") == "folder answer" for m in r["messages"])


@pytest.mark.asyncio
async def test_sessions_get_does_not_adopt_other_clients_folder(monkeypatch, tmp_path):
    """A folder copy owned by another client is never adopted or leaked."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_handler

    app_home = tmp_path / "app-home"
    folder_root = tmp_path / "task-folder"
    app_home.mkdir(parents=True)
    folder_root.mkdir(parents=True)
    _install_app_home(monkeypatch, app_home)

    _write_folder_session(folder_root, "folder-session", "client-2")
    _write_app_home_stub(app_home, "folder-session", "client-1", folder_root)

    registry = ClientSessionRegistry()
    result = await sessions_get_handler(
        "req-1", {"session_key": "folder-session"}, "client-1", None, registry,
    )
    r = result["result"]
    assert not any(m.get("content") == "folder answer" for m in r["messages"])


@pytest.mark.asyncio
async def test_sessions_get_unowned_stub_does_not_crash(monkeypatch, tmp_path):
    """REQUIRES_CLAIM fallback path must not crash on folder-resolution vars."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_handler
    from miqi.session.manager import SessionManager

    app_home = tmp_path / "app-home"
    app_home.mkdir(parents=True)
    _install_app_home(monkeypatch, app_home)

    # Legacy unowned session (created without client_id → no owner)
    sm = SessionManager(app_home)
    legacy = sm.get_or_create("legacy-session")
    legacy.add_message("user", "legacy hello")
    sm.save(legacy)

    registry = ClientSessionRegistry()
    result = await sessions_get_handler(
        "req-1", {"session_key": "legacy-session"}, "client-1", None, registry,
    )
    r = result["result"]
    assert r["ownership"] == "unowned"
    assert any(m.get("content") == "legacy hello" for m in r["messages"])
    assert r["workspace"] is None


# ── sessions.list ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_list_surfaces_folder_sessions(monkeypatch, tmp_path):
    """sessions.list surfaces folder sessions hidden by exclude_empty."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_list_handler

    app_home = tmp_path / "app-home"
    folder_root = tmp_path / "task-folder"
    app_home.mkdir(parents=True)
    folder_root.mkdir(parents=True)
    _install_app_home(monkeypatch, app_home)

    _write_folder_session(folder_root, "folder-session", "client-1")
    # Empty app-home stub with a workspace binding (exclude_empty hides it)
    _write_app_home_stub(app_home, "folder-session", "client-1", folder_root)

    registry = ClientSessionRegistry()
    result = await sessions_list_handler("req-1", {}, "client-1", None, registry)
    sessions = result["result"]["sessions"]
    folder_entries = [s for s in sessions if s.get("key") == "folder-session"]
    assert len(folder_entries) == 1
    entry = folder_entries[0]
    assert entry["workspace"] == str(folder_root)
    # Title derived from the folder copy's first user message, not the key
    assert entry["title"] and entry["title"] != "folder-session"
    assert entry["status"] == "inactive"


@pytest.mark.asyncio
async def test_sessions_list_no_duplicate_for_active_folder_session(
    monkeypatch, tmp_path, fake_config, fake_provider,
):
    """An active folder-bound session appears exactly once in the list."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_list_handler

    app_home = tmp_path / "app-home"
    folder_root = tmp_path / "task-folder"
    app_home.mkdir(parents=True)
    folder_root.mkdir(parents=True)
    _install_app_home(monkeypatch, app_home)

    _write_folder_session(folder_root, "folder-session", "client-1")
    _write_app_home_stub(app_home, "folder-session", "client-1", folder_root)

    registry = ClientSessionRegistry()
    try:
        await registry.create_session(
            client_id="client-1",
            session_key="folder-session",
            config=fake_config,
            provider=fake_provider,
            workspace=folder_root,
        )
        result = await sessions_list_handler("req-1", {}, "client-1", None, registry)
        entries = [
            s for s in result["result"]["sessions"] if s.get("key") == "folder-session"
        ]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["status"] == "running"
        # Title resolved from the folder copy, not the generic active-loop key
        assert entry["title"] and entry["title"] != "folder-session"
    finally:
        await registry.stop_all()


# ── sessions.delete ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_delete_removes_folder_copy(monkeypatch, tmp_path):
    """sessions.delete removes the folder-root copy so it cannot resurrect."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_delete_handler, sessions_list_handler

    app_home = tmp_path / "app-home"
    folder_root = tmp_path / "task-folder"
    app_home.mkdir(parents=True)
    folder_root.mkdir(parents=True)
    _install_app_home(monkeypatch, app_home)

    folder_sm = _write_folder_session(folder_root, "folder-session", "client-1")
    _write_app_home_stub(app_home, "folder-session", "client-1", folder_root)

    registry = ClientSessionRegistry()
    result = await sessions_delete_handler(
        "req-1", {"session_key": "folder-session"}, "client-1", None, registry,
    )
    assert result["result"]["deleted"] is True
    # Both copies gone — the folder scan cannot resurrect the session
    assert folder_sm.load_existing("folder-session") is None
    list_result = await sessions_list_handler("req-1", {}, "client-1", None, registry)
    assert not [
        s for s in list_result["result"]["sessions"] if s.get("key") == "folder-session"
    ]


# ── sessions.archive / sessions.list_archived ──────────────────────────────


@pytest.mark.asyncio
async def test_sessions_archive_reaches_folder_copy(monkeypatch, tmp_path):
    """sessions.archive archives the folder-root copy and keeps it unarchivable.

    sessions.archive marks the app-home stub archived *before* it locates the
    folder copy, so root discovery must not be gated on archive state: an
    active-only recent-workspace scan drops the stub, the folder copy is never
    found, and it stays invisible to sessions.list_archived — leaving
    sessions.unarchive no way to reach it (#956).
    """
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import (
        sessions_archive_handler,
        sessions_list_archived_handler,
        sessions_list_handler,
    )

    app_home = tmp_path / "app-home"
    folder_root = tmp_path / "task-folder"
    app_home.mkdir(parents=True)
    folder_root.mkdir(parents=True)
    _install_app_home(monkeypatch, app_home)

    _write_folder_session(folder_root, "folder-session", "client-1")
    _write_app_home_stub(app_home, "folder-session", "client-1", folder_root)

    registry = ClientSessionRegistry()
    result = await sessions_archive_handler(
        "req-1", {"session_key": "folder-session"}, "client-1", None, registry,
    )
    assert result["result"]["archived"] is True

    # Archived under both roots: the folder scan must not surface it in the
    # active list...
    listed = await sessions_list_handler("req-1", {}, "client-1", None, registry)
    assert not [
        s for s in listed["result"]["sessions"] if s.get("key") == "folder-session"
    ]

    # ...and the archive must still find it, or unarchive has no way to reach
    # the copy.  This is the assertion the ordering bug breaks.
    archived = await sessions_list_archived_handler(
        "req-1", {}, "client-1", None, registry,
    )
    assert [
        s for s in archived["result"]["sessions"] if s.get("key") == "folder-session"
    ]


# ── candidate root discovery is uncapped ───────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_list_finds_folder_session_beyond_recent_window(
    monkeypatch, tmp_path
):
    """A folder session older than any recent-workspace window still surfaces.

    Candidate roots used to come from a capped recent-workspace list.  Once a
    user had more workspaces than the cap, an older folder session kept its
    conversation on disk but stopped being scanned: it disappeared from the
    sidebar after a restart, and a bare sessions.get found nothing.
    """
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_list_handler

    app_home = tmp_path / "app-home"
    app_home.mkdir(parents=True)
    _install_app_home(monkeypatch, app_home)

    # Newer sessions bound to distinct workspaces — enough to overflow any
    # recent-N window.
    for i in range(30):
        ws = tmp_path / f"ws-{i:02d}"
        ws.mkdir()
        _write_app_home_stub_at(
            app_home, f"recent-{i:02d}", "client-1", ws,
            f"2026-02-01T00:00:{i:02d}+00:00",
        )

    # The session under test holds the oldest binding in the store.
    target_root = tmp_path / "target-folder"
    target_root.mkdir()
    _write_folder_session(target_root, "target-session", "client-1")
    _write_app_home_stub_at(
        app_home, "target-session", "client-1", target_root,
        "2026-01-01T00:00:00+00:00",
    )

    registry = ClientSessionRegistry()
    listed = await sessions_list_handler("req-1", {}, "client-1", None, registry)
    keys = [s.get("key") for s in listed["result"]["sessions"]]
    assert "target-session" in keys


@pytest.mark.asyncio
async def test_sessions_get_unowned_folder_copy_reports_unowned(monkeypatch, tmp_path):
    """An unowned folder copy follows the legacy REQUIRES_CLAIM contract.

    The app-home legacy path reads the history but reports ownership
    "unowned" and never auto-claims it.  The folder fallback used to adopt
    the copy as an ordinary owned session, so the same session answered
    "unowned" from the app-home root and "owned" from the folder root.
    """
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_handler
    from miqi.session.manager import SessionManager

    app_home = tmp_path / "app-home"
    folder_root = tmp_path / "task-folder"
    app_home.mkdir(parents=True)
    folder_root.mkdir(parents=True)
    _install_app_home(monkeypatch, app_home)

    # Legacy folder copy: written without a client_id, so no owner_client_id.
    legacy_sm = SessionManager(folder_root)
    legacy = legacy_sm.get_or_create("legacy-folder-session")
    legacy.add_message("user", "legacy folder question")
    legacy.add_message("assistant", "legacy folder answer")
    legacy_sm.save(legacy)

    registry = ClientSessionRegistry()
    result = await sessions_get_handler(
        "req-1",
        {"session_key": "legacy-folder-session", "workspace": str(folder_root)},
        "client-1",
        None,
        registry,
    )
    r = result["result"]
    assert r["ownership"] == "unowned"
    assert any(m.get("content") == "legacy folder question" for m in r["messages"])
