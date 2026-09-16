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
    """Create a file in the session's files directory.

    目录名走公共派生（#1014）：这里模拟的是**当前**写侧会创建的目录，
    三段 namespaced key 下 raw 公式会建到 handler 找不到的地方。
    """
    from miqi.session.session_keys import session_files_dir_key

    files_dir = workspace / "sessions" / session_files_dir_key(session_key) / "files"
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


# ── sessions.get_tracked_files / clear_tracked_files（#1003 finding ①）───────


@pytest.mark.asyncio
async def test_get_tracked_files_namespaced_key_reads_write_path_store(tmp_path):
    """三段 namespaced key：读端必须与写端解析到同一目录。

    写端 ``_persist_tracked_file`` 按 ``_session_files_dir_key`` 落
    ``sessions/desktop_983namespaced/tracked_files.json``；读端（handler）若不
    归一就会读 ``sessions/miqi-desktop_desktop_983namespaced/`` → 空。
    """
    from miqi.agent.tools.filesystem import _persist_tracked_file, _session_files_dir_key
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_tracked_files_handler

    key = "miqi-desktop:desktop:983namespaced"
    derived = _session_files_dir_key(key)
    assert derived == "desktop_983namespaced"
    assert derived != key.replace(":", "_")  # 三段 key 才会分叉

    # 归属记录落在派生目录（sessions/desktop_983namespaced/conversation.jsonl）
    sm, ws = _setup_session("desktop:983namespaced", "client-A")
    files_dir = ws / "sessions" / derived / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    target = files_dir / "ns.md"
    target.write_text("x", encoding="utf-8")
    _persist_tracked_file(files_dir, target, op="write", session_key=key)

    registry = ClientSessionRegistry()
    result = await sessions_get_tracked_files_handler(
        "req-1", {"session_key": key}, "client-A", None, registry,
    )
    paths = {item["path"] for item in result["result"]["tracked_files"]}
    assert "ns.md" in paths, paths

    # 归一不削弱 ownership：同一 namespaced key 换 client 仍被拒。
    # 精确到 UNAUTHORIZED：会话归属元数据已由 _setup_session(client-A) 落在归一
    # 后的同一个会话目录里，读到 REQUIRES_CLAIM 只会意味着「目录解析错到了没有
    # ownership 元数据的地方」，是回归而不是可接受分支（CodeRabbit #1003）。
    with pytest.raises(AppServerError) as exc_info:
        await sessions_get_tracked_files_handler(
            "req-2", {"session_key": key}, "client-B", None, registry,
        )
    assert exc_info.value.code == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_get_tracked_files_two_segment_key_behavior_unchanged(tmp_path):
    """两段 key（现网唯一形态）：归一为恒等，读端行为逐字不变。"""
    from miqi.agent.tools.filesystem import _persist_tracked_file, _session_files_dir_key
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_tracked_files_handler

    key = "desktop:983twoseg"
    assert _session_files_dir_key(key) == key.replace(":", "_")  # 归一恒等

    sm, ws = _setup_session(key, "client-A")
    files_dir = ws / "sessions" / _session_files_dir_key(key) / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    target = files_dir / "two.md"
    target.write_text("x", encoding="utf-8")
    _persist_tracked_file(files_dir, target, op="write", session_key=key)

    registry = ClientSessionRegistry()
    result = await sessions_get_tracked_files_handler(
        "req-1", {"session_key": key}, "client-A", None, registry,
    )
    paths = {item["path"] for item in result["result"]["tracked_files"]}
    assert "two.md" in paths, paths


@pytest.mark.asyncio
async def test_clear_tracked_files_namespaced_key_clears_write_path_store(tmp_path):
    """三段 namespaced key 的 clear 必须删到写端落盘的那份 tracked_files.json。"""
    from miqi.agent.tools.filesystem import _persist_tracked_file, _session_files_dir_key
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_clear_tracked_files_handler

    key = "miqi-desktop:desktop:983clear"
    sm, ws = _setup_session("desktop:983clear", "client-A")
    files_dir = ws / "sessions" / _session_files_dir_key(key) / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    target = files_dir / "clr.md"
    target.write_text("x", encoding="utf-8")
    _persist_tracked_file(files_dir, target, op="write", session_key=key)
    store = ws / "sessions" / _session_files_dir_key(key) / "tracked_files.json"
    assert store.exists()

    registry = ClientSessionRegistry()
    result = await sessions_clear_tracked_files_handler(
        "req-1", {"session_key": key}, "client-A", None, registry,
    )
    assert result["result"]["cleared"] is True
    assert not store.exists(), f"clear 未删到写端落盘的文件：{store}"


# ── #1061：文件夹绑定会话的资产读/清（对话历史可以为空）─────────────────────


def _make_folder_session(folder, key: str, client_id: str, asset: str | None = None):
    """文件夹工区里建一个**零消息**会话；asset 非空时同时登记一条资产。

    资产由运行时的写端落在会话自己的工区，与有没有对话历史无关——这正是
    「按消息行判定会话是否存在」会踩空的地方。
    """
    from miqi.agent.tools.filesystem import _persist_tracked_file, _session_files_dir_key
    from miqi.session.manager import SessionManager

    folder_sm = SessionManager(folder)
    session = folder_sm.get_or_create(key, client_id=client_id)
    session.metadata["owner_client_id"] = client_id
    folder_sm.save(session)
    folder_sm.invalidate(key)
    if asset is not None:
        files_dir = folder / "sessions" / _session_files_dir_key(key) / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        target = files_dir / asset
        target.write_text("payload", encoding="utf-8")
        _persist_tracked_file(folder, target, op="write", session_key=key)
    return folder_sm


@pytest.mark.asyncio
async def test_get_tracked_files_reads_message_less_folder_ledger(tmp_path):
    """#1061：有资产、零消息的文件夹会话，资产面板必须读到会话自己的账本。

    app-home 对该会话没有 stub，唯一能指出文件夹根的线索是活跃 runtime 的
    workspace。若解析沿用「文件夹副本必须有消息行」（读对话处需要它来判定历史
    权威），这类会话会被判为不存在，回落到 app-home 的空账本 → 面板恒为空。
    """
    from types import SimpleNamespace

    from miqi.agent.tools.filesystem import _session_files_dir_key
    from miqi.runtime.session_handlers import sessions_get_tracked_files_handler

    key = "desktop:1061-get-assets"
    folder = tmp_path / "folder-ws"
    folder.mkdir()
    _make_folder_session(folder, key, "client-A", asset="asset.txt")

    runtime = SimpleNamespace(services=SimpleNamespace(workspace=folder))

    async def _get_session(cid, sid):
        return runtime

    out = await sessions_get_tracked_files_handler(
        "req-1", {"session_key": key}, "client-A", None,
        SimpleNamespace(get_session=_get_session),
    )
    paths = {item["path"] for item in out["result"]["tracked_files"]}
    expected = f"sessions/{_session_files_dir_key(key)}/files/asset.txt"
    assert expected in paths, paths


@pytest.mark.asyncio
async def test_clear_tracked_files_clears_message_less_folder_ledger(tmp_path):
    """#1061：零消息的文件夹会话，「清空资产」必须删到它自己那份账本。

    否则清理落在 app-home 的空账本上（无操作）而文件夹那份仍在——下次读回来
    资产照旧，表现为「清理不掉」。
    """
    from types import SimpleNamespace

    from miqi.agent.tools.filesystem import _session_files_dir_key
    from miqi.runtime.session_handlers import sessions_clear_tracked_files_handler

    key = "desktop:1061-clear-assets"
    folder = tmp_path / "folder-ws"
    folder.mkdir()
    _make_folder_session(folder, key, "client-A", asset="gone.txt")
    store = folder / "sessions" / _session_files_dir_key(key) / "tracked_files.json"
    assert store.exists()

    runtime = SimpleNamespace(services=SimpleNamespace(workspace=folder))

    async def _get_session(cid, sid):
        return runtime

    out = await sessions_clear_tracked_files_handler(
        "req-1", {"session_key": key}, "client-A", None,
        SimpleNamespace(get_session=_get_session),
    )
    assert out["result"]["cleared"] is True
    assert not store.exists(), f"clear 未删到文件夹工区的账本：{store}"


@pytest.mark.asyncio
async def test_get_tracked_files_reads_folder_ledger_via_anchor_without_runtime(tmp_path):
    """重启后无 runtime：靠其它会话的 workspace 绑定仍能定位文件夹账本。"""
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_tracked_files_handler

    key = "desktop:1061-cold-assets"
    anchor = "desktop:1061-cold-anchor"
    folder = tmp_path / "folder-ws"
    folder.mkdir()
    _make_folder_session(folder, key, "client-A", asset="cold.txt")

    # 另一个会话在 app-home 声明了该文件夹 → 冷启动时仍能枚举到这个根。
    sm, _ws = _setup_session(anchor, "client-A")
    stub = sm.get_or_create(anchor, client_id="client-A", workspace=folder)
    sm.save(stub)
    sm.invalidate(anchor)

    try:
        out = await sessions_get_tracked_files_handler(
            "req-1", {"session_key": key}, "client-A", None, ClientSessionRegistry(),
        )
        paths = {item["path"] for item in out["result"]["tracked_files"]}
        assert any(p.endswith("cold.txt") for p in paths), paths
    finally:
        sm.delete(anchor, client_id="client-A")


def _shadowed_rebound_session(tmp_path, key: str, *, asset: str):
    """同一个 key 在两个文件夹各留一份副本：旧文件夹 + 当前绑定所在文件夹。

    旧文件夹那份零消息、无账本——用户用工作区选择器把会话改绑到新文件夹后，
    它就留在磁盘上了。再让旧根对应的 app-home stub 带上更晚的 ``updated_at``，
    把扫描顺序固定成「旧根在前」：用例因此是在证明解析没有退化成「谁先扫到算
    谁」，而不是碰运气通过。
    """
    from datetime import timedelta
    from pathlib import Path

    stale = tmp_path / "stale-ws"
    live = tmp_path / "live-ws"
    stale.mkdir()
    live.mkdir()
    _make_folder_session(stale, key, "client-A")
    _make_folder_session(live, key, "client-A", asset=asset)

    sm, _ws = _setup_session(key, "client-A")
    sm.save(sm.get_or_create(key, client_id="client-A", workspace=live))
    anchor = "desktop:1061-shadow-anchor"
    anchor_stub = sm.get_or_create(anchor, client_id="client-A", workspace=stale)
    anchor_stub.updated_at = anchor_stub.updated_at + timedelta(days=1)
    sm.save(anchor_stub)
    sm.invalidate(key)
    sm.invalidate(anchor)

    order = [
        str(Path(p).expanduser().resolve())
        for p in sm.list_bound_workspaces(client_id="client-A", include_archived=True)
    ]
    assert order.index(str(stale.resolve())) < order.index(str(live.resolve())), order
    return stale, live, sm, anchor


@pytest.mark.asyncio
async def test_get_tracked_files_ignores_stale_copy_of_rebound_session(tmp_path):
    """#1061 复审：改绑后旧文件夹里的零消息副本不得遮蔽真账本。

    一份零消息的副本本身说明不了它是不是本会话的账本所在：同一个 key 可以因为
    改绑而在旧文件夹留下一份。扫描按 stub 的 updated_at 倒序走，旧副本排在前，
    若解析只看「会话副本存在」就选它，资产面板读回空列表。扫描候选必须真的持有
    tracked_files.json。
    """
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_get_tracked_files_handler

    key = "desktop:1061-shadow-read"
    stale, live, sm, anchor = _shadowed_rebound_session(tmp_path, key, asset="real.txt")
    try:
        out = await sessions_get_tracked_files_handler(
            "req-1", {"session_key": key}, "client-A", None, ClientSessionRegistry(),
        )
        paths = {item["path"] for item in out["result"]["tracked_files"]}
        assert any(p.endswith("real.txt") for p in paths), paths
    finally:
        sm.delete(key, client_id="client-A")
        sm.delete(anchor, client_id="client-A")


@pytest.mark.asyncio
async def test_clear_tracked_files_ignores_stale_copy_of_rebound_session(tmp_path):
    """#1061 复审：清理同样要落在真账本上——清错文件夹比读空更糟（文件真没了）。

    旧副本没有账本，「清空资产」落在它身上等于什么都没清：真账本原样留在当前
    文件夹，下次读回来资产照旧，表现为「清理不掉」。
    """
    from miqi.agent.tools.filesystem import _session_files_dir_key
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import sessions_clear_tracked_files_handler

    key = "desktop:1061-shadow-clear"
    stale, live, sm, anchor = _shadowed_rebound_session(tmp_path, key, asset="real.txt")
    store = live / "sessions" / _session_files_dir_key(key) / "tracked_files.json"
    assert store.exists()
    try:
        out = await sessions_clear_tracked_files_handler(
            "req-1", {"session_key": key}, "client-A", None, ClientSessionRegistry(),
        )
        assert out["result"]["cleared"] is True
        assert not store.exists(), f"clear 没落到真账本：{store}"
    finally:
        sm.delete(key, client_id="client-A")
        sm.delete(anchor, client_id="client-A")


def _rebound_without_binding(tmp_path, key: str, *, asset: str):
    """同上，但 app-home 对 key 没有 stub——冷启动没有 binding 可作种子，只能扫描。

    旧文件夹的 stub 带更晚的 ``updated_at``，扫描顺序里它排第一：拿到真账本只能
    靠「候选必须自己持有 tracked_files.json」，靠不了顺序。
    """
    from datetime import timedelta
    from pathlib import Path

    stale = tmp_path / "stale-ws"
    live = tmp_path / "live-ws"
    stale.mkdir()
    live.mkdir()
    _make_folder_session(stale, key, "client-A")
    _make_folder_session(live, key, "client-A", asset=asset)

    anchor_a = "desktop:1061-scan-anchor-a"
    anchor_b = "desktop:1061-scan-anchor-b"
    sm, _ws = _setup_session(anchor_a, "client-A")
    a_stub = sm.get_or_create(anchor_a, client_id="client-A", workspace=stale)
    a_stub.updated_at = a_stub.updated_at + timedelta(days=1)
    sm.save(a_stub)
    sm.save(sm.get_or_create(anchor_b, client_id="client-A", workspace=live))
    sm.invalidate(anchor_a)
    sm.invalidate(anchor_b)

    order = [
        str(Path(p).expanduser().resolve())
        for p in sm.list_bound_workspaces(client_id="client-A", include_archived=True)
    ]
    assert order.index(str(stale.resolve())) < order.index(str(live.resolve())), order
    return stale, live, sm, anchor_a, anchor_b


@pytest.mark.asyncio
async def test_tracked_files_resolve_ledger_owner_without_binding(tmp_path):
    """评审要求的场景：同 key 在 A 有空副本且无账本、B 有真账本，冷启动无 runtime。

    app-home 对本会话没有 stub，没有 binding 可作种子，解析只能走扫描；而扫描按
    stub 的 updated_at 倒序，A 排在前。副本存在说明不了它持有账本——读和清都必须
    落在 B 上，否则读回空列表、清理清错文件夹。
    """
    from miqi.agent.tools.filesystem import _session_files_dir_key
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.session_handlers import (
        sessions_clear_tracked_files_handler,
        sessions_get_tracked_files_handler,
    )

    key = "desktop:1061-scan-ledger"
    stale, live, sm, anchor_a, anchor_b = _rebound_without_binding(
        tmp_path, key, asset="real.txt"
    )
    store = live / "sessions" / _session_files_dir_key(key) / "tracked_files.json"
    assert store.exists()
    try:
        out = await sessions_get_tracked_files_handler(
            "req-1", {"session_key": key}, "client-A", None, ClientSessionRegistry(),
        )
        paths = {item["path"] for item in out["result"]["tracked_files"]}
        assert any(p.endswith("real.txt") for p in paths), paths

        cleared = await sessions_clear_tracked_files_handler(
            "req-1", {"session_key": key}, "client-A", None, ClientSessionRegistry(),
        )
        assert cleared["result"]["cleared"] is True
        assert not store.exists(), f"clear 没落到真账本：{store}"
    finally:
        sm.delete(anchor_a, client_id="client-A")
        sm.delete(anchor_b, client_id="client-A")


# ── #983 缺口 2：DownloadSink 产物进 tracked（面板读端回路）────────────────


@pytest.mark.asyncio
async def test_get_tracked_files_reads_sink_delivered_artifact(tmp_path):
    """``DownloadSink`` 交付的 MCP 下载产物必须出现在面板读端（真实 handler）。

    写端：sink 落盘 → ``_persist_tracked_file``（与 create_pdf 同机制）。
    读端两条真实链路：
    - ``sessions.get_tracked_files``（#1003 finding ① 归一后）读到条目；
    - ``files.read(path, session_key, as_binary)`` 按条目键取回字节
      （面板「下载/另存为」走的就是这条，#877）。

    产物名用 ``.pdf``：``files.read`` 只服务文本安全/可预览/可二进制读的
    后缀集，非白名单后缀（如 ``.cube``）会在读取层被拒（既有读端门，见 PR
    「后续计划」）——用白名单内的后缀才能证明端到端回路成立。
    """
    import base64
    import hashlib
    import json
    from types import SimpleNamespace

    from miqi.agent.tools.mcp_download_sink import DownloadSink
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.file_handlers import files_read_handler
    from miqi.runtime.session_handlers import sessions_get_tracked_files_handler

    key = "desktop:983downloads"
    sm, ws = _setup_session(key, "client-A")
    data = b"%PDF-1.4 artifact-bytes-983"
    payload = json.dumps({
        "name": "report.pdf",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "content_base64": base64.b64encode(data).decode(),
    })
    result = SimpleNamespace(
        isError=False, structuredContent=None,
        content=[SimpleNamespace(text=payload)],
    )

    sink = DownloadSink(base_workspace=ws)
    artifact = await sink.materialize(
        result=result,
        session_key=key,
        server_name="miqroforge",
        tool_name="download_file",
        request_kwargs={"name": "report.pdf"},
        turn_id="turn-1",
        tool_call_id="call-1",
    )
    assert artifact.path.read_bytes() == data

    registry = ClientSessionRegistry()
    out = await sessions_get_tracked_files_handler(
        "req-1", {"session_key": key}, "client-A", None, registry,
    )
    paths = {item["path"] for item in out["result"]["tracked_files"]}
    assert ".miqi/downloads/report.pdf" in paths, paths

    # 面板「下载/另存为」链路：条目键 → files.read 取回原字节
    read = await files_read_handler(
        "req-2",
        {"path": ".miqi/downloads/report.pdf", "session_key": key, "as_binary": True},
        "client-A", None, registry,
    )
    assert base64.b64decode(read["result"]["data_base64"]) == data
    assert read["result"]["size"] == len(data)


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


# ── session containment (#1051) ──────────────────────────────────────────────
#
# Path containment used to be enforced at WORKSPACE granularity: the
# session-scoped branch resolved against the caller's session directory but
# then accepted anything under `<ws>/`, and the workspace-scoped branch ran
# with no ownership check at all when `session_key` was omitted.  Because every
# session lives at `<ws>/sessions/<key>/`, both let a caller reach a SIBLING
# session — `../../<other>/conversation.jsonl` with the caller's own key, or a
# plain `sessions/<other>/conversation.jsonl` with no key at all.  Rewriting
# that file's `owner_client_id` metadata line reverses session ownership.
#
# These tests pin the boundary: session-scoped access stays inside the
# caller's own session directory, workspace-scoped access never enters
# `sessions/`, and both benign flows keep working.


def _session_dir(ws, session_key: str):
    from miqi.session.session_keys import session_files_dir_key

    return ws / "sessions" / session_files_dir_key(session_key)


def _victim_conversation(ws, session_key: str):
    return _session_dir(ws, session_key) / "conversation.jsonl"


@pytest.mark.asyncio
async def test_files_write_rejects_traversal_into_sibling_session(
    fake_config, fake_provider, tmp_path,
):
    """#1051: `..` from an owned session must not overwrite a sibling session."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_write_handler
    from miqi.session.session_keys import session_files_dir_key

    _, ws = _setup_session("atk-1051", "client-A")
    _setup_session("vic-1051", "client-B")
    victim = _victim_conversation(ws, "vic-1051")
    before = victim.read_text(encoding="utf-8")

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as exc_info:
        await files_write_handler(
            "req-1051-w",
            {
                "path": "../../%s/conversation.jsonl" % session_files_dir_key("vic-1051"),
                "content": '{"_type":"metadata","owner_client_id":"client-A"}\n',
                "session_key": "atk-1051",
            },
            "client-A", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"
    # The point of the bug: the victim's file — and with it its ownership —
    # must be untouched.
    assert victim.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_files_read_and_delete_reject_traversal_into_sibling_session(
    fake_config, fake_provider, tmp_path,
):
    """#1051: the same escape via files.read and files.delete."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_delete_handler, files_read_handler
    from miqi.session.session_keys import session_files_dir_key

    _, ws = _setup_session("atk-1051-rd", "client-A")
    _setup_session("vic-1051-rd", "client-B")
    victim = _victim_conversation(ws, "vic-1051-rd")
    escape = "../../%s/conversation.jsonl" % session_files_dir_key("vic-1051-rd")

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as read_exc:
        await files_read_handler(
            "req-1051-r", {"path": escape, "session_key": "atk-1051-rd"},
            "client-A", None, registry,
        )
    assert read_exc.value.code == "INVALID_PARAMS"

    with pytest.raises(AppServerError) as del_exc:
        await files_delete_handler(
            "req-1051-d", {"path": escape, "session_key": "atk-1051-rd"},
            "client-A", None, registry,
        )
    assert del_exc.value.code == "INVALID_PARAMS"
    assert victim.exists()


@pytest.mark.asyncio
async def test_files_write_rejects_sessions_path_without_session_key(
    fake_config, fake_provider, tmp_path,
):
    """#1051: omitting session_key must not grant unlimited access to sessions/."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_write_handler
    from miqi.session.session_keys import session_files_dir_key

    _, ws = _setup_session("atk-1051-ns", "client-A")
    _setup_session("vic-1051-ns", "client-B")
    victim = _victim_conversation(ws, "vic-1051-ns")
    before = victim.read_text(encoding="utf-8")

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as exc_info:
        await files_write_handler(
            "req-1051-ns",
            {
                "path": "sessions/%s/conversation.jsonl" % session_files_dir_key("vic-1051-ns"),
                "content": '{"_type":"metadata","owner_client_id":"client-A"}\n',
            },
            "client-A", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"
    assert victim.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_files_write_rejects_sessions_path_naming_foreign_session(
    fake_config, fake_provider, tmp_path,
):
    """#1051: naming another session while holding a session_key is rejected.

    A workspace-relative path into `sessions/` is only re-rooted into the
    caller's files dir when it is the caller's OWN session; anything else must
    fail rather than silently land somewhere else.
    """
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_write_handler
    from miqi.session.session_keys import session_files_dir_key

    _setup_session("atk-1051-fk", "client-A")
    _setup_session("vic-1051-fk", "client-B")

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as exc_info:
        await files_write_handler(
            "req-1051-fk",
            {
                "path": "sessions/%s/conversation.jsonl" % session_files_dir_key("vic-1051-fk"),
                "content": "PWNED",
                "session_key": "atk-1051-fk",
            },
            "client-A", None, registry,
        )
    assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_files_write_rejects_absolute_and_sandbox_paths_into_sibling_session(
    fake_config, fake_provider, tmp_path,
):
    """#1051: absolute (incl. Windows drive-letter) and sandbox-prefixed forms.

    The Windows form used to skip absolute-path normalisation entirely, and the
    sandbox-prefix strip is a prefix strip rather than a sanitiser, so neither
    may bypass the session boundary.
    """
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_write_handler

    _, ws = _setup_session("atk-1051-abs", "client-A")
    _setup_session("vic-1051-abs", "client-B")
    victim = _victim_conversation(ws, "vic-1051-abs")
    before = victim.read_text(encoding="utf-8")

    registry = ClientSessionRegistry()
    escapes = [
        str(victim),
        str(victim).replace("\\", "/"),
        "/home/miqi/workspace/../../%s/conversation.jsonl"
        % _session_dir(ws, "vic-1051-abs").name,
    ]
    for escape in escapes:
        with pytest.raises(AppServerError) as exc_info:
            await files_write_handler(
                "req-1051-abs",
                {"path": escape, "content": "PWNED", "session_key": "atk-1051-abs"},
                "client-A", None, registry,
            )
        assert exc_info.value.code == "INVALID_PARAMS", escape
    assert victim.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_files_write_still_rejects_workspace_escape_without_session_key(
    fake_config, fake_provider, tmp_path,
):
    """#1051 control: escaping the workspace entirely is still rejected."""
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_write_handler

    _setup_session("atk-1051-ctl", "client-A")
    registry = ClientSessionRegistry()

    for session_key in (None, "atk-1051-ctl"):
        with pytest.raises(AppServerError) as exc_info:
            await files_write_handler(
                "req-1051-ctl",
                {
                    "path": "../../../../outside-1051.txt",
                    "content": "x",
                    "session_key": session_key,
                },
                "client-A", None, registry,
            )
        assert exc_info.value.code == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_files_benign_workspace_and_session_writes_still_work(
    fake_config, fake_provider, tmp_path,
):
    """#1051 control: the tightening must not break the legitimate flows.

    - workspace file with no session_key (the workspace editor)
    - session-relative file with a session_key
    - the desktop's workspace-relative path into the caller's OWN session
    """
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.file_handlers import files_read_handler, files_write_handler

    _, ws = _setup_session("atk-1051-ok", "client-A")
    registry = ClientSessionRegistry()

    ws_file = await files_write_handler(
        "req-ok-1", {"path": "editor-note-1051.md", "content": "hello"},
        "client-A", None, registry,
    )
    assert ws_file["result"]["saved"] is True
    assert (ws / "editor-note-1051.md").read_text(encoding="utf-8") == "hello"

    own = await files_write_handler(
        "req-ok-2",
        {"path": "in-session-1051.md", "content": "hi", "session_key": "atk-1051-ok"},
        "client-A", None, registry,
    )
    assert own["result"]["saved"] is True
    assert (_session_dir(ws, "atk-1051-ok") / "files" / "in-session-1051.md").exists()

    # The desktop sends the full workspace-relative path into its own session.
    read_back = await files_read_handler(
        "req-ok-3",
        {
            "path": "sessions/%s/files/in-session-1051.md"
            % _session_dir(ws, "atk-1051-ok").name,
            "session_key": "atk-1051-ok",
        },
        "client-A", None, registry,
    )
    assert read_back["result"]["content"] == "hi"


@pytest.mark.asyncio
async def test_files_diff_resolves_own_session_full_path(
    fake_config, fake_provider, tmp_path,
):
    """#1051: a full session path + session_key resolves to the real file.

    It used to be re-joined onto the session dir, yielding a nested path that
    no writer ever created, so diff/revert never found the file they had just
    written.
    """
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.file_handlers import files_diff_handler, files_write_handler

    _, ws = _setup_session("atk-1051-diff", "client-A")
    registry = ClientSessionRegistry()
    full = "sessions/%s/files/diffed-1051.md" % _session_dir(ws, "atk-1051-diff").name

    await files_write_handler(
        "req-diff-1", {"path": full, "content": "v1", "session_key": "atk-1051-diff"},
        "client-A", None, registry,
    )
    assert (_session_dir(ws, "atk-1051-diff") / "files" / "diffed-1051.md").exists()

    diff = await files_diff_handler(
        "req-diff-2", {"path": full, "session_key": "atk-1051-diff"},
        "client-A", None, registry,
    )
    # The discriminator: before the fix this resolved to a nested path inside
    # the session dir, so the handler read nothing back (`current_content`
    # None).  Now it finds the file the write actually created.
    assert diff["result"]["current_content"] == "v1"


@pytest.mark.asyncio
async def test_files_tree_workspace_hides_sessions_dir(
    fake_config, fake_provider, tmp_path,
):
    """#1051: the workspace tree must not enumerate the sessions subtree.

    It exposed every sibling session's directory name and conversation.jsonl
    path, and the editor it feeds cannot write those files any more.
    """
    from miqi.runtime.app_server import ClientSessionRegistry
    from miqi.runtime.file_handlers import files_tree_handler

    _setup_session("atk-1051-tree", "client-A")
    _setup_session("vic-1051-tree", "client-B")
    registry = ClientSessionRegistry()

    result = await files_tree_handler("req-1051-tree", {}, "client-A", None, registry)
    names = [child["name"] for child in result["result"]["root"]["children"]]
    assert "sessions" not in names


@pytest.mark.asyncio
async def test_files_reject_colliding_session_key_into_sibling_session(
    fake_config, fake_provider, tmp_path,
):
    """#1051: session keys are not injective — a colliding key must not address
    a sibling session's directory.

    ``session_files_dir_key`` drops the leading segment for 3+ segment keys, so
    ``z:desktop:vic`` derives the same directory as ``desktop:vic``.  When the
    victim's ``conversation.jsonl`` is absent (a session directory that exists
    with files but no persisted session), ownership is unreadable — so the file
    boundary must refuse the key rather than hand out the directory.
    """
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_read_handler, files_write_handler
    from miqi.session.session_keys import session_files_dir_key

    _, ws = _setup_session("desktop:vic", "client-B")
    victim_dir = ws / "sessions" / session_files_dir_key("desktop:vic")
    # Session directory with content but no persisted session: the shape in
    # which the ownership check used to be inconclusive.
    (victim_dir / "conversation.jsonl").unlink()
    (victim_dir / "files").mkdir(parents=True, exist_ok=True)
    (victim_dir / "files" / "secret.md").write_text("VICTIM SECRET", encoding="utf-8")

    registry = ClientSessionRegistry()
    for colliding in ("z:desktop:vic", "client-B:desktop:vic"):
        assert session_files_dir_key(colliding) == session_files_dir_key("desktop:vic")

        with pytest.raises(AppServerError) as read_exc:
            await files_read_handler(
                "req-collide-r",
                {"path": "secret.md", "session_key": colliding},
                "client-A", None, registry,
            )
        assert read_exc.value.code == "REQUIRES_CLAIM", colliding

        with pytest.raises(AppServerError) as write_exc:
            await files_write_handler(
                "req-collide-w",
                {"path": "pwn.md", "content": "PWNED", "session_key": colliding},
                "client-A", None, registry,
            )
        assert write_exc.value.code == "REQUIRES_CLAIM", colliding

    assert (victim_dir / "files" / "secret.md").read_text(encoding="utf-8") == "VICTIM SECRET"
    assert not (victim_dir / "files" / "pwn.md").exists()


@pytest.mark.asyncio
async def test_files_reject_session_key_with_unsafe_derived_dir(
    fake_config, fake_provider, tmp_path,
):
    """#1051: derivations that are not a single safe path segment are rejected.

    ``.``/``..`` derive themselves and would move the session root out of
    ``<ws>/sessions/`` (silently disabling isolation); a name ending in a dot
    cannot be created on Windows and used to surface as an internal error.
    """
    from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
    from miqi.runtime.file_handlers import files_write_handler

    _setup_session("atk-unsafe", "client-A")
    registry = ClientSessionRegistry()

    for bad_key in ("..", ".", "a:b:.."):
        with pytest.raises(AppServerError) as exc_info:
            await files_write_handler(
                "req-unsafe",
                {"path": "x.md", "content": "x", "session_key": bad_key},
                "client-A", None, registry,
            )
        assert exc_info.value.code == "INVALID_PARAMS", bad_key
