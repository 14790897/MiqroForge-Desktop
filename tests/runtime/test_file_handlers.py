"""Tests for file artifact handlers — Phase 30.

Validates:
- Cross-client isolation: client A cannot access client B's session files
- Ownership enforcement: unowned legacy sessions return REQUIRES_CLAIM
- Bug fixes: _remove_tracked_file now defined, _reset_tracked_file_op uses client_id
- Snapshot/client-scoped path resolution
- files.tree workspace vs session-scoped
"""

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────


def _setup_session(session_key: str, client_id: str | None, *, set_owner: bool = True):
    """Create a session on disk using the bridge state's workspace.

    Returns (SessionManager, workspace_path).
    """
    import miqi.bridge.server as bridge_module

    state = getattr(bridge_module, "_state", None)
    config = state.load_config()
    from miqi.session.manager import SessionManager

    sm = SessionManager(config.workspace_path, legacy_sessions_dir=config.workspace_path / "_legacy_sessions")
    session = sm.get_or_create(session_key, client_id=client_id)
    if set_owner and client_id is not None:
        session.metadata["owner_client_id"] = client_id
    elif not set_owner:
        session.metadata.pop("owner_client_id", None)
    sm.save(session)
    return sm, config.workspace_path


def _ensure_session_file(workspace, session_key: str, filename: str, content: str = "data"):
    """Create a file in the session's files directory."""
    from miqi.utils.helpers import safe_filename

    safe_key = safe_filename(session_key.replace(":", "_"))
    files_dir = workspace / "sessions" / safe_key / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / filename).write_text(content, encoding="utf-8")
    return files_dir / filename


# ── files.tree ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_files_tree_workspace_only(fake_config, fake_provider, tmp_path):
    """files.tree returns workspace tree when no session_key is given."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.file_handlers import files_tree_handler

    registry = ClientSessionRegistry()

    result = await files_tree_handler(
        "req-1", {"path": "."}, "client-1", None, registry,
    )
    assert "result" in result
    assert "root" in result["result"]


@pytest.mark.asyncio
async def test_files_tree_session_scoped_requires_claim(fake_config, fake_provider, tmp_path):
    """files.tree with unowned session_key returns REQUIRES_CLAIM."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_tree_handler

    # Create an unowned session on disk (no owner_client_id)
    _setup_session("unowned-x-tree", None, set_owner=False)

    registry = ClientSessionRegistry()

    with pytest.raises(AppServerError) as exc_info:
        await files_tree_handler(
            "req-1", {"session_key": "unowned-x-tree"}, "client-1", None, registry,
        )
    assert exc_info.value.code == "REQUIRES_CLAIM"


# ── files.read ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_files_read_own_file_succeeds(fake_config, fake_provider, tmp_path):
    """files.read succeeds for a file in owned session scope."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.file_handlers import files_read_handler

    sm, ws = _setup_session("owned-reader", "client-1")
    _ensure_session_file(ws, "owned-reader", "hello.txt", "hello world")

    registry = ClientSessionRegistry()
    result = await files_read_handler(
        "req-1",
        {"path": "hello.txt", "session_key": "owned-reader"},
        "client-1", None, registry,
    )
    assert result["result"]["content"] == "hello world"


@pytest.mark.asyncio
async def test_files_read_cross_client_rejected(fake_config, fake_provider, tmp_path):
    """files.read by client-B on client-A's session returns UNAUTHORIZED."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_read_handler

    sm, ws = _setup_session("x-read-a", "client-A")
    _ensure_session_file(ws, "x-read-a", "secret.txt", "secret")

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as exc_info:
        await files_read_handler(
            "req-1",
            {"path": "secret.txt", "session_key": "x-read-a"},
            "client-B", None, registry,
        )
    assert exc_info.value.code == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_files_read_unowned_legacy_requires_claim(fake_config, fake_provider, tmp_path):
    """files.read on unowned legacy session returns REQUIRES_CLAIM."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_read_handler

    sm, ws = _setup_session("legacy-read-unowned", None, set_owner=False)
    _ensure_session_file(ws, "legacy-read-unowned", "old.txt", "old")

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as exc_info:
        await files_read_handler(
            "req-1",
            {"path": "old.txt", "session_key": "legacy-read-unowned"},
            "client-C", None, registry,
        )
    assert exc_info.value.code == "REQUIRES_CLAIM"


@pytest.mark.asyncio
async def test_files_read_missing_path(fake_config, fake_provider, tmp_path):
    """files.read rejects missing path parameter."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_read_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as exc_info:
        await files_read_handler("req-1", {}, "client-1", None, registry)
    assert exc_info.value.code == "INVALID_PARAMS"


# ── files.write ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_files_write_own_session_succeeds(fake_config, fake_provider, tmp_path):
    """files.write succeeds on owned session and updates tracked_files with client_id."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.file_handlers import files_write_handler

    sm, ws = _setup_session("write-own", "client-1")

    registry = ClientSessionRegistry()
    result = await files_write_handler(
        "req-1",
        {"path": "new-file.md", "content": "## hello", "session_key": "write-own"},
        "client-1", None, registry,
    )
    assert result["result"]["saved"] is True

    # Verify tracked_files was updated with ownership check (Bug A.3 fix)
    tracked = sm.load_tracked_files("write-own", client_id="client-1")
    assert "new-file.md" in tracked
    assert tracked["new-file.md"]["op"] == "write"


@pytest.mark.asyncio
async def test_files_write_cross_client_rejected(fake_config, fake_provider, tmp_path):
    """files.write by client-B on client-A's session returns UNAUTHORIZED."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_write_handler

    _setup_session("write-cross", "client-A")

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as exc_info:
        await files_write_handler(
            "req-1",
            {"path": "evil.md", "content": "evil", "session_key": "write-cross"},
            "client-B", None, registry,
        )
    assert exc_info.value.code == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_files_write_unowned_legacy_rejected(fake_config, fake_provider, tmp_path):
    """files.write on unowned legacy session returns REQUIRES_CLAIM (no auto-claim)."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_write_handler

    _setup_session("write-legacy-unowned", None, set_owner=False)

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as exc_info:
        await files_write_handler(
            "req-1",
            {"path": "test.txt", "content": "data", "session_key": "write-legacy-unowned"},
            "client-C", None, registry,
        )
    assert exc_info.value.code == "REQUIRES_CLAIM"


# ── files.delete ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_files_delete_cross_client_rejected(fake_config, fake_provider, tmp_path):
    """files.delete by client-B on client-A's session returns UNAUTHORIZED."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_delete_handler

    sm, ws = _setup_session("delete-cross", "client-A")
    _ensure_session_file(ws, "delete-cross", "delete-me.txt", "data")

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as exc_info:
        await files_delete_handler(
            "req-1",
            {"path": "delete-me.txt", "session_key": "delete-cross"},
            "client-B", None, registry,
        )
    assert exc_info.value.code == "UNAUTHORIZED"


# ── files.diff ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_files_diff_cross_client_rejected(fake_config, fake_provider, tmp_path):
    """files.diff by client-B on client-A's session returns UNAUTHORIZED."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_diff_handler

    _setup_session("diff-cross", "client-A")

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as exc_info:
        await files_diff_handler(
            "req-1",
            {"path": "test.txt", "session_key": "diff-cross"},
            "client-B", None, registry,
        )
    assert exc_info.value.code == "UNAUTHORIZED"


# ── files.revert ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_files_revert_cross_client_rejected(fake_config, fake_provider, tmp_path):
    """files.revert by client-B on client-A's session returns UNAUTHORIZED."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_revert_handler

    _setup_session("revert-cross", "client-A")

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as exc_info:
        await files_revert_handler(
            "req-1",
            {"path": "test.txt", "session_key": "revert-cross"},
            "client-B", None, registry,
        )
    assert exc_info.value.code == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_files_revert_uses_session_manager_not_undefined_function():
    """files.revert uses SessionManager.remove_tracked_file (Bug A.1 fix).

    The handler must NOT reference the previously undefined _remove_tracked_file
    symbol. It should use SessionManager.remove_tracked_file with client_id.
    """
    import inspect

    from miqi.runtime.file_handlers import files_revert_handler

    source = inspect.getsource(files_revert_handler)
    # The handler must not call bare _remove_tracked_file(...)
    assert "sm.remove_tracked_file" in source, (
        "files.revert handler should use SessionManager.remove_tracked_file with client_id"
    )


# ── files.accept ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_files_accept_updates_tracked_files_with_client_id(fake_config, fake_provider, tmp_path):
    """files.accept resets tracked_file op with client_id (Bug A.2 fix)."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.file_handlers import files_accept_handler

    sm, ws = _setup_session("accept-own", "client-1")
    sm.save_tracked_file("accept-own", "test.txt", op="write", client_id="client-1")

    registry = ClientSessionRegistry()
    result = await files_accept_handler(
        "req-1",
        {"path": "test.txt", "session_key": "accept-own"},
        "client-1", None, registry,
    )
    assert result["result"]["accepted"] is True

    tracked = sm.load_tracked_files("accept-own", client_id="client-1")
    assert tracked["test.txt"]["op"] == "read"


@pytest.mark.asyncio
async def test_files_accept_cross_client_rejected(fake_config, fake_provider, tmp_path):
    """files.accept by client-B on client-A's session returns UNAUTHORIZED."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_accept_handler

    _setup_session("accept-cross", "client-A")

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as exc_info:
        await files_accept_handler(
            "req-1",
            {"path": "test.txt", "session_key": "accept-cross"},
            "client-B", None, registry,
        )
    assert exc_info.value.code == "UNAUTHORIZED"


# ── SandboxManager client-scoped namespace ───────────────────────────────────


@pytest.mark.asyncio
async def test_sandbox_manager_client_scoped_keys():
    """Same session_key under different clients maps to different sandbox keys."""
    from pathlib import Path

    from miqi.sandbox.manager import SandboxManager

    manager = SandboxManager(workspace=Path("."), enabled=False)
    manager._initialized = True

    key_a = manager._sandbox_key("my-project", client_id="client-A")
    key_b = manager._sandbox_key("my-project", client_id="client-B")

    assert key_a != key_b, "Different clients must have different sandbox keys"
    assert key_a == "client-A:my-project"
    assert key_b == "client-B:my-project"

    # Legacy path: client_id=None falls back to raw session_key
    key_legacy = manager._sandbox_key("my-project", client_id=None)
    assert key_legacy == "my-project"


# ── _METHODS audit ───────────────────────────────────────────────────────────


def test_methods_no_files_handlers():
    """_METHODS must not contain any files.* handlers after Phase 30."""
    from miqi.bridge.server import _METHODS

    files_methods = [k for k in _METHODS if k.startswith("files.")]
    assert len(files_methods) == 0, (
        f"files.* handlers should be migrated to AppServer, got: {files_methods}"
    )


def test_appserver_has_all_file_handlers():
    """Handler module exports all 7 file handler functions."""
    from miqi.runtime import file_handlers

    expected = [
        "files_tree_handler",
        "files_read_handler",
        "files_write_handler",
        "files_delete_handler",
        "files_diff_handler",
        "files_revert_handler",
        "files_accept_handler",
    ]
    for name in expected:
        assert hasattr(file_handlers, name), f"Missing handler: {name}"
        handler = getattr(file_handlers, name)
        assert callable(handler), f"Handler {name} is not callable"


@pytest.mark.asyncio
async def test_files_read_image_returns_base64_and_mime(fake_config, fake_provider, tmp_path):
    """files.read on an image returns base64 + image mime — OCR 附件恢复链路 (#659)."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.file_handlers import files_read_handler

    sm, ws = _setup_session("img-reader", "client-1")
    files_dir = ws / "sessions" / "img-reader" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    # Minimal PNG: 8-byte signature + 16 zero bytes payload
    (files_dir / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    registry = ClientSessionRegistry()
    result = await files_read_handler(
        "req-img",
        {"path": "photo.png", "session_key": "img-reader"},
        "client-1", None, registry,
    )
    r = result["result"]
    assert r["is_binary"] is True
    assert r["mime_type"] == "image/png"
    assert r["data_base64"].startswith("iVBORw0KGgo")  # PNG magic bytes
    assert r["size"] == 24


@pytest.mark.asyncio
async def test_files_read_svg_returns_base64_and_mime(fake_config, fake_provider, tmp_path):
    """files.read on .svg 走二进制分支（data_base64 + image/svg+xml）。

    回归（CodeRabbit #761）：svg 同时属文本安全集与二进制可读集，
    文本分支先命中会返回纯文本 content，前端 [Image:] 内联展示拿不到 bytes。
    """
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.file_handlers import files_read_handler

    sm, ws = _setup_session("svg-reader", "client-1")
    files_dir = ws / "sessions" / "svg-reader" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    svg_body = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
    (files_dir / "step-graph.svg").write_text(svg_body, encoding="utf-8")

    registry = ClientSessionRegistry()
    result = await files_read_handler(
        "req-svg",
        {"path": "step-graph.svg", "session_key": "svg-reader"},
        "client-1", None, registry,
    )
    r = result["result"]
    assert r["is_binary"] is True
    assert r["mime_type"] == "image/svg+xml"
    assert r["data_base64"]  # base64 非空
    assert "content" not in r  # 不走文本分支


@pytest.mark.asyncio
async def test_files_read_svg_as_text_returns_content(fake_config, fake_provider, tmp_path):
    """files.read on .svg + as_text=true 走文本分支（#776）。

    svg 同时属文本安全集与二进制可读集，默认二进制（前端内联展示）；
    as_text=true 时显式请求纯文本，agent 可读 svg 源码。
    """
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.file_handlers import files_read_handler

    sm, ws = _setup_session("svg-text", "client-1")
    files_dir = ws / "sessions" / "svg-text" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    svg_body = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
    (files_dir / "step-graph.svg").write_text(svg_body, encoding="utf-8")

    registry = ClientSessionRegistry()
    result = await files_read_handler(
        "req-svg-text",
        {"path": "step-graph.svg", "session_key": "svg-text", "as_text": True},
        "client-1", None, registry,
    )
    r = result["result"]
    assert "data_base64" not in r  # 不走二进制分支
    assert r["content"] == svg_body  # 纯文本内容
    assert r["size"] == len(svg_body)


@pytest.mark.asyncio
async def test_files_read_image_jpg_mime(fake_config, fake_provider, tmp_path):
    """files.read on a .jpg maps to image/jpeg (#659)."""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.file_handlers import files_read_handler

    sm, ws = _setup_session("jpg-reader", "client-1")
    files_dir = ws / "sessions" / "jpg-reader" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "shot.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 8)

    registry = ClientSessionRegistry()
    result = await files_read_handler(
        "req-jpg",
        {"path": "shot.jpg", "session_key": "jpg-reader"},
        "client-1", None, registry,
    )
    r = result["result"]
    assert r["is_binary"] is True
    assert r["mime_type"] == "image/jpeg"
