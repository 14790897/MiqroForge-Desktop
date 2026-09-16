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


@pytest.mark.asyncio
async def test_sessions_get_reads_workspace_rooted_conversation():
    """自定义-workspace 会话:默认根只剩空壳,真对话在 <workspace>/sessions 下。

    任务 runner 把 JSONL 镜像写在 SessionManager(会话 workspace)
    (task_runner._save_to_session_manager),而 sessions.get 从全局 config
    workspace_path(默认根)读。当默认根为空、但 metadata 声明了非默认 workspace、
    且该 workspace 根下确有真对话时,get 必须改从 workspace 根读——否则切回/重启
    会把已完成的问答塌成空(修复:切换会话后回复只剩思考过程)。
    """
    import shutil

    from miqi.runtime.session_handlers import sessions_get_handler

    sm = _handler_sm()
    key = "eempty-ws-rooted"
    ws = Path(tempfile.mkdtemp(prefix="miqi-ws-root-"))
    ws_sm = SessionManager(ws)
    registry = ClientSessionRegistry()
    try:
        # 默认根:只落一个带 workspace 的空壳(模拟 chat.send 只持久化 metadata)。
        stub = sm.get_or_create(key, client_id="A", workspace=ws)
        sm.save(stub)
        sm.invalidate(key)
        assert sm.get_session_dir(key).exists()

        # 真实对话写在 workspace 根(simulate task_runner JSONL mirror)。
        real = ws_sm.get_or_create(key, client_id="A")
        real.add_message("user", "问句")
        real.add_message("assistant", "完整回答")
        ws_sm.save(real)
        ws_sm.invalidate(key)

        result = await sessions_get_handler("req-1", {"session_key": key}, "A", None, registry)
        msgs = result["result"]["messages"]
        assert [m["content"] for m in msgs] == ["问句", "完整回答"], (
            "get 必须从 workspace 根读到真实对话,而非默认根的空壳"
        )
        # redirect 后 disk_session 是真对话那份(metadata 里没有 workspace)，
        # 解析结果走 handler 的顶层 workspace 字段。
        assert result["result"]["metadata"].get("workspace") is None
        assert result["result"]["workspace"] == str(ws), (
            "detail 需报告会话的 workspace 绑定(顶层字段)"
        )
    finally:
        await registry.stop_all()
        _cleanup(sm, [key])
        _cleanup(ws_sm, [key])
        shutil.rmtree(ws, ignore_errors=True)


@pytest.mark.asyncio
async def test_sessions_get_default_session_unaffected_by_redirect():
    """无 workspace 绑定的普通会话不受 redirect 影响(仍正常从默认根读)。"""
    from miqi.runtime.session_handlers import sessions_get_handler

    sm = _handler_sm()
    key = "eempty-ws-absent"
    registry = ClientSessionRegistry()
    try:
        _make_owned(sm, key, message="ordinary turn")
        result = await sessions_get_handler("req-1", {"session_key": key}, "A", None, registry)
        assert [m["content"] for m in result["result"]["messages"]] == ["ordinary turn"]
    finally:
        await registry.stop_all()
        _cleanup(sm, [key])


@pytest.mark.asyncio
async def test_sessions_get_reads_active_runtime_workspace_without_stub():
    """运行中、且从未在 app 根登记 binding 的绑定会话:直接读 runtime 的 workspace 根。

    场景:workspace 窗口(自身数据根=文件夹)创建的会话,在默认窗口侧没有 app 根 stub。
    sessions.get 裸调用时若 registry 里该 runtime 活跃,从其 services.workspace 读。
    """
    import shutil
    from types import SimpleNamespace

    from miqi.runtime.session_handlers import sessions_get_handler

    ws = Path(tempfile.mkdtemp(prefix="miqi-ws-active-"))
    ws_sm = SessionManager(ws)
    key = "eempty-ws-active"
    try:
        # 真对话写在 workspace 根;app 根不写任何 stub。
        real = ws_sm.get_or_create(key, client_id="A")
        real.add_message("user", "活跃问句")
        real.add_message("assistant", "活跃回复")
        ws_sm.save(real)
        ws_sm.invalidate(key)
        assert not (Path(_handler_sm().sessions_dir) / safe_filename(key.replace(":", "_"))).exists()

        async def _no_snaps():
            return []

        rt_services = SimpleNamespace(
            workspace=ws,
            agent_control=SimpleNamespace(_agents={}),
            history_runtime=SimpleNamespace(get_interrupted_snapshots=_no_snaps),
        )
        runtime = SimpleNamespace(services=rt_services)

        async def _get_session(cid, sid):
            return runtime

        fake_reg = SimpleNamespace(get_session=_get_session)

        result = await sessions_get_handler("req-1", {"session_key": key}, "A", None, fake_reg)
        msgs = result["result"]["messages"]
        assert [m["content"] for m in msgs] == ["活跃问句", "活跃回复"], (
            "活跃绑定会话必须从 runtime 的 workspace 根读到真对话"
        )
        assert Path(result["result"]["workspace"]) == ws, (
            "返回 detail 需报告会话的 workspace 绑定"
        )
    finally:
        _cleanup(ws_sm, [key])
        shutil.rmtree(ws, ignore_errors=True)


@pytest.mark.asyncio
async def test_sessions_get_heals_cold_stubless_folder_session():
    """重启后、从未在 app-home 登记 binding 的文件夹会话：真对话在文件夹根，但
    app-home 无 stub → 裸 get 无从得知 workspace。须经"其它绑定会话声明的
    recent workspace"找到真对话并回填 binding——否则重启后切回该会话仍为空
    （前端只渲染瞬时思考行、输入卡在生成中）。"""
    import shutil

    from miqi.runtime.session_handlers import sessions_get_handler

    sm = _handler_sm()
    orphan = "eempty-ws-orphan"
    anchor = "eempty-ws-anchor"
    ws = Path(tempfile.mkdtemp(prefix="miqi-ws-orphan-"))
    ws_sm = SessionManager(ws)
    registry = ClientSessionRegistry()
    try:
        # 真对话写在文件夹根；app-home 对该 orphan 没有任何 stub。
        real = ws_sm.get_or_create(orphan, client_id="A")
        real.add_message("user", "文件夹里的问题")
        real.add_message("assistant", "文件夹里的回答")
        ws_sm.save(real)
        ws_sm.invalidate(orphan)
        assert not sm.get_session_dir(orphan).exists()

        # 锚点：app-home 已有另一会话声明了该文件夹 → recent-workspaces 能枚举到它。
        anchor_stub = sm.get_or_create(anchor, client_id="A", workspace=ws)
        sm.save(anchor_stub)
        sm.invalidate(anchor)

        result = await sessions_get_handler(
            "req-1", {"session_key": orphan}, "A", None, registry
        )
        assert [m["content"] for m in result["result"]["messages"]] == [
            "文件夹里的问题", "文件夹里的回答",
        ], "冷启动的 stub-less 文件夹会话必须从 workspace 根读到真对话"
        assert Path(result["result"]["workspace"]) == ws

        # 回填的 binding 让后续裸 get（runtime 停止/重启后）无需再发现即可解析。
        healed = sm.get_or_create(orphan, client_id="A")
        assert healed.metadata.get("workspace") == str(ws)
    finally:
        await registry.stop_all()
        _cleanup(sm, [orphan, anchor])
        _cleanup(ws_sm, [orphan])
        shutil.rmtree(ws, ignore_errors=True)


@pytest.mark.asyncio
async def test_sessions_list_resolves_folder_bound_sessions_from_workspace():
    """sessions.list 需按 workspace 根判定空态/取标题，而不是只看 app-home stub。

    文件夹会话的 app-home 只是空 stub（仅 metadata.workspace），真对话在文件夹
    根。若沿用 exclude_empty 只看 app-home 文件 → 重启后文件夹会话整体从左侧消失。
    """
    import shutil

    from miqi.runtime.session_handlers import sessions_list_handler

    sm = _handler_sm()
    full_key = "eempty-list-folder-full"
    empty_key = "eempty-list-folder-empty"
    ws = Path(tempfile.mkdtemp(prefix="miqi-list-folder-"))
    ws_sm = SessionManager(ws)
    registry = ClientSessionRegistry()
    try:
        # app-home：两个都是空 stub（仅 metadata.workspace）。
        for k in (full_key, empty_key):
            stub = sm.get_or_create(k, client_id="A", workspace=ws)
            sm.save(stub)
            sm.invalidate(k)

        # 文件夹根：full 有真对话，empty 没有。
        real = ws_sm.get_or_create(full_key, client_id="A")
        real.add_message("user", "首问")
        ws_sm.save(real)
        ws_sm.invalidate(full_key)
        ws_sm.save(ws_sm.get_or_create(empty_key, client_id="A"))
        ws_sm.invalidate(empty_key)

        result = await sessions_list_handler("req-1", {}, "A", None, registry)
        keys = [s["key"] for s in result["result"]["sessions"]]
        assert full_key in keys, "文件夹会话（stub 空、真对话在 workspace）必须出现在列表"
        assert empty_key not in keys, "文件夹根也没有真对话 → 仍按临时空会话隐藏"
        entry = next(s for s in result["result"]["sessions"] if s["key"] == full_key)
        assert entry["title"] == "首问", "标题应取自 workspace 根的真对话首条 user 消息"
    finally:
        await registry.stop_all()
        _cleanup(sm, [full_key, empty_key])
        _cleanup(ws_sm, [full_key, empty_key])
        shutil.rmtree(ws, ignore_errors=True)


@pytest.mark.asyncio
async def test_sessions_get_ignores_message_less_folder_copy():
    """无消息的文件夹副本不是历史权威——读对话这条路径不接受它。

    #1061 让资产面板认「零消息的文件夹副本」（资产与历史无关），但那只适用于
    tracked files 那条解析（`_find_ledger_root`）。读对话的 `_find_folder_session`
    若一并放宽，一份空副本就会被当成会话历史——这里把这条规则钉住。
    """
    import shutil

    from miqi.runtime.session_handlers import sessions_get_handler

    sm = _handler_sm()
    key = "eempty-ws-msgless"
    anchor = "eempty-ws-msgless-anchor"
    ws = Path(tempfile.mkdtemp(prefix="miqi-ws-msgless-"))
    ws_sm = SessionManager(ws)
    registry = ClientSessionRegistry()
    try:
        # 文件夹根只有一份零消息的副本。
        ws_sm.save(ws_sm.get_or_create(key, client_id="A"))
        ws_sm.invalidate(key)

        # 锚点：让该 workspace 可被发现，否则连扫描都到不了这个根。
        anchor_stub = sm.get_or_create(anchor, client_id="A", workspace=ws)
        sm.save(anchor_stub)
        sm.invalidate(anchor)

        result = await sessions_get_handler(
            "req-1", {"session_key": key}, "A", None, registry
        )
        assert result["result"]["messages"] == [], (
            "无消息的文件夹副本不得被当作会话历史读回"
        )
    finally:
        await registry.stop_all()
        _cleanup(sm, [key, anchor])
        _cleanup(ws_sm, [key])
        shutil.rmtree(ws, ignore_errors=True)


@pytest.mark.asyncio
async def test_create_session_registers_folder_binding_in_app_home(monkeypatch):
    """运行时在文件夹根诞生时，app-home 索引须登记 key→workspace binding。

    未来会话的持久保证：create_session 时 workspace != config 默认根 → 写 app-home
    stub（metadata.workspace），使 runtime 停止/重启后 get/list 都能解析真对话。
    """
    import shutil

    import miqi.bridge.server as bridge_module
    import miqi.runtime.session as sess_mod
    from miqi.runtime import app_server as app_server_mod
    from miqi.runtime.session_handlers import _get_session_manager

    sm = _get_session_manager()
    state = getattr(bridge_module, "_state", None)
    if state is None:
        pytest.skip("Bridge state not available")
    config = state.load_config()
    key = "eempty-create-ws"
    ws = Path(tempfile.mkdtemp(prefix="miqi-create-ws-"))
    registry = app_server_mod.ClientSessionRegistry()

    class _FakeRuntime:
        async def start(self) -> None:
            pass

    monkeypatch.setattr(
        sess_mod,
        "RuntimeSession",
        type("_FakeRuntimeSession", (), {"create": staticmethod(lambda **kw: _FakeRuntime())}),
    )
    try:
        await registry.create_session(
            client_id="A", session_key=key, config=config, provider=None, workspace=ws
        )
        bound = sm.get_or_create(key, client_id="A")
        assert bound.metadata.get("workspace") == str(ws), (
            "文件夹运行时创建后 app-home 必须出现 workspace binding"
        )
    finally:
        _cleanup(sm, [key])
        shutil.rmtree(ws, ignore_errors=True)


async def test_create_session_rolls_back_when_binding_write_fails(monkeypatch):
    """binding 落盘失败必须回滚，不得留下"进程内可用、重启即丢"的会话。

    runtime 已启动、app-home 索引写不进去（I/O、权限、磁盘满、stub 冲突）时，
    若把写失败吞掉并照样返回成功：进程内会话一切正常、E2E 也照绿，直到 runtime
    停止或应用重启才暴露——folder 里的对话仍在磁盘，但指向它的 binding 从未落盘，
    也没有活跃 runtime 可作 seed，入口彻底丢失（评审阻塞项）。此处钉住写失败的
    三条可观测结果：抛 AppServerError、runtime 已回滚、registry 里不留残留。
    """
    import shutil

    import miqi.bridge.server as bridge_module
    import miqi.runtime.session as sess_mod
    from miqi.runtime import app_server as app_server_mod
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.session_handlers import _get_session_manager

    sm = _get_session_manager()
    state = getattr(bridge_module, "_state", None)
    if state is None:
        pytest.skip("Bridge state not available")
    config = state.load_config()
    key = "eempty-binding-write-fail"
    ws = Path(tempfile.mkdtemp(prefix="miqi-binding-fail-"))
    registry = app_server_mod.ClientSessionRegistry()

    stopped = []

    class _FakeRuntime:
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            stopped.append(True)

    monkeypatch.setattr(
        sess_mod,
        "RuntimeSession",
        type("_FakeRuntimeSession", (), {"create": staticmethod(lambda **kw: _FakeRuntime())}),
    )

    def _failing_save(self, session):
        raise OSError("disk full")

    monkeypatch.setattr(SessionManager, "save", _failing_save)
    try:
        with pytest.raises(AppServerError):
            await registry.create_session(
                client_id="A", session_key=key, config=config, provider=None, workspace=ws
            )
        assert stopped == [True], "binding 写失败必须回滚已启动的 runtime"
        assert not registry.session_exists(f"A:{key}"), (
            "binding 未落盘时不得把 session 留在 registry 里——那正是重启即丢的状态"
        )
        assert await registry.get_session("A", f"A:{key}") is None
    finally:
        monkeypatch.undo()
        _cleanup(sm, [key])
        shutil.rmtree(ws, ignore_errors=True)
