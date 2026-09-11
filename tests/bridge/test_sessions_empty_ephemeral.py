"""Empty sessions are ephemeral — not listed until the first message.

Validates the desktop-side contract behind "before the first question the
left sidebar shows no conversation":
- SessionManager.list_sessions(exclude_empty=True) hides sessions whose
  conversation file has no real message yet.
- sessions.list / sessions.list_archived handlers pass exclude_empty=True.
- sessions.get does not persist a brand-new empty session and garbage-collects
  stale empty folders left by older builds (which used to save on every get).
- sessions.get DOES persist an empty session that carries an explicit workspace
  — the user may switch directories before the first message, and that
  workspace metadata must survive across gets/restarts; the empty session is
  still hidden from the list until a real message arrives.
- sessions.get still persists sessions that actually have messages.
"""

import json
import tempfile
from pathlib import Path

import pytest

from miqi.runtime.app_server import ClientSessionRegistry
from miqi.session.manager import SessionManager, safe_filename


def _handler_sm(*, legacy_sessions_dir: Path | None = None) -> SessionManager:
    """SessionManager on the same workspace path the AppServer handlers use."""
    import miqi.bridge.server as bridge_module
    state = getattr(bridge_module, "_state", None)
    if state is None:
        pytest.skip("Bridge state not available")
    config = state.load_config()
    return SessionManager(config.workspace_path, legacy_sessions_dir=legacy_sessions_dir)


def _make_owned(sm: SessionManager, key: str, *, message: str | None, client: str = "A"):
    """Create a client-owned disk session; message=None leaves it empty."""
    s = sm.get_or_create(key, client_id=client)
    if message is not None:
        s.add_message("user", message)
    sm.save(s)
    sm.invalidate(key)


def _make_legacy_flat_owned(sm: SessionManager, key: str, *, client: str = "A") -> Path:
    """Simulate an old flat-storage build: sessions_dir/<safe_key>.jsonl.

    Pre-migration layouts wrote one JSONL file per session directly under
    sessions_dir (no per-session subdir).  Content is a single metadata line
    (no messages) → an owned EMPTY session in the old storage layout.
    """
    safe_key = safe_filename(key.replace(":", "_"))
    flat = sm.sessions_dir / f"{safe_key}.jsonl"
    flat.write_text(
        json.dumps(
            {
                "_type": "metadata",
                "metadata": {"owner_client_id": client},
                "owner_client_id": client,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return flat


def _archive(sm: SessionManager, key: str) -> None:
    marker = sm.get_session_dir(key) / ".archived"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def _cleanup(sm: SessionManager, keys) -> None:
    for key in keys:
        try:
            sm.delete(key)
        except Exception:
            pass


# ── SessionManager.list_sessions(exclude_empty=True) ─────────────────────────


def test_list_sessions_exclude_empty_keeps_full(tmp_path):
    sm = SessionManager(tmp_path)
    empty_key = "sm-empty-list"
    full_key = "sm-full-list"
    try:
        _make_owned(sm, empty_key, message=None)
        _make_owned(sm, full_key, message="first question")

        default = [s["key"] for s in sm.list_sessions(client_id="A")]
        assert empty_key in default and full_key in default, (
            "default list_sessions keeps empty sessions (backward compat)"
        )

        filtered = [s["key"] for s in sm.list_sessions(client_id="A", exclude_empty=True)]
        assert full_key in filtered
        assert empty_key not in filtered, "exclude_empty must hide the empty session"
    finally:
        _cleanup(sm, [empty_key, full_key])


def test_list_sessions_exclude_empty_applies_to_archived(tmp_path):
    sm = SessionManager(tmp_path)
    empty_key = "sm-empty-arch"
    full_key = "sm-full-arch"
    try:
        _make_owned(sm, empty_key, message=None)
        _make_owned(sm, full_key, message="archived question")
        _archive(sm, empty_key)
        _archive(sm, full_key)

        archived = [
            s["key"]
            for s in sm.list_sessions(include_archived=True, client_id="A", exclude_empty=True)
        ]
        assert full_key in archived
        assert empty_key not in archived
    finally:
        _cleanup(sm, [empty_key, full_key])


# ── sessions.list handler wiring ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_list_handler_excludes_empty_disk_sessions():
    from miqi.runtime.session_handlers import sessions_list_handler

    sm = _handler_sm()
    empty_key = "eempty-list-empty"
    full_key = "eempty-list-full"
    registry = ClientSessionRegistry()
    try:
        _make_owned(sm, empty_key, message=None)
        _make_owned(sm, full_key, message="visible question")

        result = await sessions_list_handler("req-1", {}, "A", None, registry)
        keys = [s["key"] for s in result["result"]["sessions"]]
        assert full_key in keys
        assert empty_key not in keys, "sessions.list must not surface empty sessions"
    finally:
        await registry.stop_all()
        _cleanup(sm, [empty_key, full_key])


@pytest.mark.asyncio
async def test_sessions_list_archived_handler_excludes_empty():
    from miqi.runtime.session_handlers import sessions_list_archived_handler

    sm = _handler_sm()
    empty_key = "eempty-arch-empty"
    full_key = "eempty-arch-full"
    registry = ClientSessionRegistry()
    try:
        _make_owned(sm, empty_key, message=None)
        _make_owned(sm, full_key, message="archived question")
        _archive(sm, empty_key)
        _archive(sm, full_key)

        result = await sessions_list_archived_handler("req-1", {}, "A", None, registry)
        keys = [s["key"] for s in result["result"]["sessions"]]
        assert full_key in keys
        assert empty_key not in keys
    finally:
        await registry.stop_all()
        _cleanup(sm, [empty_key, full_key])


# ── sessions.get gating ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_get_does_not_persist_brand_new_empty():
    from miqi.runtime.session_handlers import sessions_get_handler

    sm = _handler_sm()
    key = "eempty-get-fresh"
    registry = ClientSessionRegistry()
    try:
        result = await sessions_get_handler("req-1", {"session_key": key}, "A", None, registry)
        assert result["result"]["messages"] == []
        assert not sm.get_session_dir(key).exists(), (
            "opening an empty session must not create a disk folder"
        )
    finally:
        await registry.stop_all()
        _cleanup(sm, [key])


@pytest.mark.asyncio
async def test_sessions_get_garbage_collects_stale_empty_folder():
    from miqi.runtime.session_handlers import sessions_get_handler

    sm = _handler_sm()
    key = "eempty-get-stale"
    registry = ClientSessionRegistry()
    try:
        # Simulate an older build: it saved the empty session on open.
        _make_owned(sm, key, message=None)
        assert sm.get_session_dir(key).exists()

        result = await sessions_get_handler("req-1", {"session_key": key}, "A", None, registry)
        assert result["result"]["messages"] == []
        assert not sm.get_session_dir(key).exists(), (
            "loading a stale empty session must remove its disk folder"
        )
    finally:
        await registry.stop_all()
        _cleanup(sm, [key])


@pytest.mark.asyncio
async def test_sessions_get_garbage_collects_legacy_flat_empty_session():
    """Legacy flat .jsonl 空会话也要被 GC（不只在目录布局下判断存在性）。"""
    from miqi.runtime.session_handlers import sessions_get_handler

    sm = _handler_sm()
    key = "eempty-legacy-flat"
    registry = ClientSessionRegistry()
    try:
        flat = _make_legacy_flat_owned(sm, key)
        assert flat.exists()

        result = await sessions_get_handler("req-1", {"session_key": key}, "A", None, registry)
        assert result["result"]["messages"] == []
        assert not sm.get_session_dir(key).exists(), (
            "loading a stale legacy-flat empty session must remove its folder"
        )
        assert not flat.exists(), (
            "legacy flat source must not remain on disk after GC"
        )
    finally:
        await registry.stop_all()
        _cleanup(sm, [key])


@pytest.mark.asyncio
async def test_sessions_get_persists_empty_with_explicit_workspace():
    from miqi.runtime.session_handlers import sessions_get_handler

    sm = _handler_sm()
    key = "eempty-get-ws"
    registry = ClientSessionRegistry()
    ws = Path(tempfile.mkdtemp(prefix="miqi-e2e-ws-"))
    try:
        # 空会话但显式带 workspace → 落盘以保留 workspace 元数据(切目录先于首条消息)。
        result = await sessions_get_handler(
            "req-1", {"session_key": key, "workspace": str(ws)}, "A", None, registry
        )
        assert result["result"]["messages"] == []
        assert result["result"]["metadata"].get("workspace") == str(ws)
        assert sm.get_session_dir(key).exists(), (
            "empty session with explicit workspace must persist its metadata"
        )

        # 仍不进 exclude_empty 列表,直到真的有消息。
        listed = [s["key"] for s in sm.list_sessions(client_id="A", exclude_empty=True)]
        assert key not in listed

        # 首条消息写入后进列表,且 workspace 保留。
        s = sm.get_or_create(key, client_id="A")
        s.add_message("user", "第一个问题")
        sm.save(s)
        sm.invalidate(key)
        listed = [s["key"] for s in sm.list_sessions(client_id="A", exclude_empty=True)]
        assert key in listed
        detail = sm.get_or_create(key, client_id="A")
        assert detail.metadata.get("workspace") == str(ws)
    finally:
        await registry.stop_all()
        _cleanup(sm, [key])


@pytest.mark.asyncio
async def test_sessions_get_workspace_binding_survives_bare_reopen():
    """切目录后对空会话的裸重开不能 GC 掉已落盘的 workspace 绑定。

    真实序列（workspace E2E）：空会话先用带 workspace 的 get 落盘；随后某次
    不带 workspace 的 get（历史重载/列表刷新）若把它当残留删掉，workspace
    绑定就丢了，首条消息会落在一个无 workspace 的会话上。仅当空会话既无显式
    workspace、磁盘上也从未有过 workspace 元数据时，才当作旧版残留 GC。
    """
    from miqi.runtime.session_handlers import sessions_get_handler

    sm = _handler_sm()
    key = "eempty-get-ws-reopen"
    registry = ClientSessionRegistry()
    ws = Path(tempfile.mkdtemp(prefix="miqi-e2e-ws-"))
    try:
        # 带 workspace 的 get：空会话落盘以保留绑定。
        r1 = await sessions_get_handler(
            "req-1", {"session_key": key, "workspace": str(ws)}, "A", None, registry
        )
        assert r1["result"]["messages"] == []
        assert r1["result"]["metadata"].get("workspace") == str(ws)
        assert sm.get_session_dir(key).exists()

        # 裸重开（不带 workspace）：不能删——绑定必须保留。
        r2 = await sessions_get_handler("req-1", {"session_key": key}, "A", None, registry)
        assert sm.get_session_dir(key).exists(), (
            "bare reopen must not GC an empty session that carries a workspace binding"
        )
        assert r2["result"]["metadata"].get("workspace") == str(ws)

        # 首条消息写入后 workspace 仍保留（E2E 第 8 步断言的契约）。
        s = sm.get_or_create(key, client_id="A")
        s.add_message("user", "第一个问题")
        sm.save(s)
        sm.invalidate(key)
        detail = sm.get_or_create(key, client_id="A")
        assert detail.metadata.get("workspace") == str(ws)
    finally:
        await registry.stop_all()
        _cleanup(sm, [key])


@pytest.mark.asyncio
async def test_sessions_get_still_persists_messaged_session():
    from miqi.runtime.session_handlers import sessions_get_handler

    sm = _handler_sm()
    key = "eempty-get-full"
    registry = ClientSessionRegistry()
    try:
        _make_owned(sm, key, message="keep me")
        folder = sm.get_session_dir(key)
        assert folder.exists()

        result = await sessions_get_handler("req-1", {"session_key": key}, "A", None, registry)
        msgs = result["result"]["messages"]
        assert len(msgs) == 1 and msgs[0]["content"] == "keep me"
        assert folder.exists(), "a session with messages must stay persisted"

        # And the reloaded manager sees it via exclude_empty list.
        reloaded = [
            s["key"] for s in sm.list_sessions(client_id="A", exclude_empty=True)
        ]
        assert key in reloaded
    finally:
        await registry.stop_all()
        _cleanup(sm, [key])
