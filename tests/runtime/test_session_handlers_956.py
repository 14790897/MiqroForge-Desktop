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
