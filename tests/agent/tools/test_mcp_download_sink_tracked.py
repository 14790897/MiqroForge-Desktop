"""#983 缺口 2：MCP 下载产物落盘后必须进会话 tracked（任务附件面板可见）。

背景：#975 ``DownloadSink`` 把 binary artifact 交付到
``<ws>/sessions/<key>/files/.miqi/downloads/``，但**不写 tracked_files.json**
→ 产物只存在于磁盘，资产面板（``sessions.get_tracked_files``）看不到，用户
拿不到 #877 的「下载/另存为」入口。

修复：``materialize`` 在产物提交后调用 ``_persist_tracked_file``（与
create_pdf/docx 同机制）。落盘根与登记根同源（``_downloads_root_base``），
故条目键为 ``.miqi/downloads/<name>``。

面板读端两条链路（默认工作区布局，现网形态）：
- 存储读端 ``sessions.get_tracked_files`` → ``SessionManager(<ws>)
  .load_tracked_files(_session_files_dir_key(key))``；
- 文件读端 ``files.read(path, session_key)`` → 相对路径锚在
  ``<ws>/sessions/<key>/files``（``file_handlers._resolve_session_files_path``）。

**边界（既有读端行为，非本改动引入）**：自选工作区布局下产物落
``<custom>/.miqi/downloads``，而文件读端仍锚 ``<custom>/sessions/<key>/files``
→ 条目可见但按相对路径取不到字节（create_pdf 等文档工具同此）。本文件只断言
存储读端；文件读端的端到端回路见
``tests/runtime/test_file_handlers.py::test_get_tracked_files_reads_sink_delivered_artifact``。

判别性：去掉 ``materialize`` 里的 ``_track_delivered`` 调用后，本文件前 4 例
全红（见 PR 证据「变异验证」）。
"""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from miqi.agent.tools.filesystem import _session_files_dir_key
from miqi.agent.tools.mcp_download_sink import (
    DownloadPendingError,
    DownloadSha256MismatchError,
    DownloadSink,
)

# ── Helpers ────────────────────────────────────────────────────────────────

# 现网形态（两段 key，ChatConsole `desktop:${Date.now()}`）：文件读端的目录
# 派生（safe_filename(key.replace(":", "_"))）与写端 `_session_files_dir_key`
# 逐字相同，故本文件的相对路径断言与真实 handler 同源。
SESSION_KEY = "desktop:983downloads"


def _default_ws() -> Path:
    """``MIQI_HOME/workspace`` —— 默认工作区（会话 files 隔离生效的判别根）。"""
    from miqi.paths import get_miqi_home

    ws = Path(get_miqi_home()) / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _session_files_dir(ws: Path, key: str = SESSION_KEY) -> Path:
    return ws / "sessions" / _session_files_dir_key(key) / "files"


def _store_path(store_root: Path, key: str = SESSION_KEY) -> Path:
    return store_root / "sessions" / _session_files_dir_key(key) / "tracked_files.json"


def _read_tracked(path: Path) -> dict:
    assert path.exists(), f"tracked_files.json 不存在：{path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data.get("version") == 1
    return data.get("files", {})


def _panel_tracked(store_root: Path, key: str = SESSION_KEY) -> dict:
    """面板读取回路（逐字对齐 ``sessions.get_tracked_files``）：
    handler 先用 ``_session_files_dir_key`` 归一 key（#1003 finding ①），再
    ``SessionManager(<工作区>).load_tracked_files(<归一后 key>)``。"""
    from miqi.session.manager import SessionManager

    return SessionManager(store_root).load_tracked_files(_session_files_dir_key(key))


def _artifact_payload(data: bytes, name: str = "result.cube") -> str:
    return json.dumps(
        {
            "name": name,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_base64": base64.b64encode(data).decode(),
        },
        ensure_ascii=False,
    )


def _result_from_text(text: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        isError=False, structuredContent=None,
        content=[SimpleNamespace(text=text)],
    )


def _single_chunk_payload(
    piece: bytes,
    *,
    chunk_index: int,
    total_chunks: int,
    name: str = "chunked.cube",
    sha256: str | None = None,
    size_bytes: int | None = None,
) -> str:
    payload: dict = {
        "name": name,
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
        "content_base64": base64.b64encode(piece).decode(),
    }
    if sha256 is not None:
        payload["sha256"] = sha256
    if size_bytes is not None:
        payload["size_bytes"] = size_bytes
    return json.dumps(payload)


async def _materialize(sink: DownloadSink, result, *, key: str = SESSION_KEY, **kw):
    defaults = dict(
        session_key=key,
        server_name="miqroforge",
        tool_name="download_file",
        request_kwargs={"name": "result.cube"},
        turn_id="turn-1",
        tool_call_id="call-1",
    )
    defaults.update(kw)
    return await sink.materialize(result=result, **defaults)


# ── 正例：单包产物落会话 tracked 根 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_single_shot_artifact_lands_in_session_tracked_store():
    """单包下载：产物落会话 downloads 目录，条目落会话存储根（非孤儿路径）。"""
    ws = _default_ws()
    files_dir = _session_files_dir(ws)
    sink = DownloadSink(base_workspace=ws)
    data = b"cube-bytes-983"

    artifact = await _materialize(sink, _result_from_text(_artifact_payload(data)))

    assert artifact.path == files_dir / ".miqi" / "downloads" / "result.cube"
    assert artifact.path.read_bytes() == data

    tracked = _read_tracked(_store_path(ws))
    assert ".miqi/downloads/result.cube" in tracked, f"条目未落会话存储根：{sorted(tracked)}"
    assert tracked[".miqi/downloads/result.cube"]["op"] == "write"
    assert tracked[".miqi/downloads/result.cube"]["name"] == "result.cube"

    # 孤儿路径（会话 files 目录被当仓库根）不得出现
    assert not _store_path(files_dir).exists()

    # 面板读端（sessions.get_tracked_files 同源读端）必须读到
    assert ".miqi/downloads/result.cube" in _panel_tracked(ws)


@pytest.mark.asyncio
async def test_tracked_key_resolves_through_panel_read_path():
    """面板 ``files.read(path, session_key)`` 的解析语义（两段 key = 现网形态）：
    相对路径按 ``<ws>/sessions/<key>/files`` 拼 → 命中的正是产物本身
    （条目键必须与落盘根同源）。"""
    ws = _default_ws()
    files_dir = _session_files_dir(ws)
    sink = DownloadSink(base_workspace=ws)

    artifact = await _materialize(sink, _result_from_text(_artifact_payload(b"x")))

    key = next(iter(_read_tracked(_store_path(ws))))
    assert (files_dir / key).resolve() == artifact.path.resolve()


# ── 正例：分片续传「完成才登记」 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_chunked_transfer_tracked_only_after_completion():
    """形态乙：中间态（未交付）不登记；末片交付后才出现 tracked 条目。"""
    ws = _default_ws()
    sink = DownloadSink(base_workspace=ws)
    pieces = [b"part-0-", b"part-1"]
    full = b"".join(pieces)
    sha, size = hashlib.sha256(full).hexdigest(), len(full)

    first = _result_from_text(_single_chunk_payload(
        pieces[0], chunk_index=0, total_chunks=2, sha256=sha, size_bytes=size,
    ))
    with pytest.raises(DownloadPendingError):
        await _materialize(
            sink, first, request_kwargs={"name": "chunked.cube", "chunk_index": 0},
        )
    # 未交付 → 不得登记（面板里出现半截文件是数据完整性事故）
    assert not _store_path(ws).exists(), "未完成的传输被登记进 tracked"

    second = _result_from_text(_single_chunk_payload(
        pieces[1], chunk_index=1, total_chunks=2, sha256=sha, size_bytes=size,
    ))
    artifact = await _materialize(
        sink, second, request_kwargs={"name": "chunked.cube", "chunk_index": 1},
    )
    assert artifact.path.read_bytes() == full

    tracked = _read_tracked(_store_path(ws))
    assert ".miqi/downloads/chunked.cube" in tracked
    assert ".miqi/downloads/chunked.cube" in _panel_tracked(ws)


# ── 正例：复用既有文件（reuse_existing）仍登记 ───────────────────────────


@pytest.mark.asyncio
async def test_reuse_existing_artifact_is_tracked_again():
    """同身份同内容重试走 reuse_existing（不重写文件）——条目仍须在。"""
    ws = _default_ws()
    sink = DownloadSink(base_workspace=ws)
    payload = _artifact_payload(b"same-bytes")

    await _materialize(sink, _result_from_text(payload))
    store = _store_path(ws)
    store.unlink()  # 清空条目，验证第二次（reuse 分支）重新登记

    artifact = await _materialize(sink, _result_from_text(payload))

    assert artifact.path.read_bytes() == b"same-bytes"
    assert ".miqi/downloads/result.cube" in _read_tracked(store)


# ── 正例：自定义工作区（非默认根）────────────────────────────────────────


@pytest.mark.asyncio
async def test_custom_workspace_tracks_at_workspace_store_root(tmp_path):
    """自选项目目录：产物落 ``<custom>/.miqi/downloads``，条目落
    ``<custom>/sessions/<key>/tracked_files.json``——与存储读端
    （``SessionManager(config.workspace_path)``）同根同 key。

    **只断言存储读端**：该布局下文件读端仍锚 ``<custom>/sessions/<key>/files``
    （既有 ``_resolve_session_files_path`` 语义），按相对键取字节会落空；这是
    文档工具同样存在的既有边界，不在本次改动范围（见 PR「后续计划」）。
    """
    custom = tmp_path / "project"
    custom.mkdir()
    sink = DownloadSink(base_workspace=custom)

    artifact = await _materialize(sink, _result_from_text(_artifact_payload(b"c")))

    assert artifact.path == custom / ".miqi" / "downloads" / "result.cube"
    tracked = _read_tracked(_store_path(custom))
    assert ".miqi/downloads/result.cube" in tracked
    assert ".miqi/downloads/result.cube" in _panel_tracked(custom)
    # 不得把会话目录当成仓库根再嵌套一层（#1003 的孤儿形态）
    nested = custom / ".miqi" / "downloads" / "sessions"
    assert not nested.exists(), f"条目仍落嵌套孤儿路径：{nested}"


# ── 反例：未交付 / 登记失败 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failed_download_is_not_tracked():
    """校验失败（sha 不符）→ 文件未交付 → 不得留下 tracked 条目。"""
    ws = _default_ws()
    sink = DownloadSink(base_workspace=ws)
    payload = json.dumps({
        "name": "bad.cube",
        "size_bytes": 3,
        "sha256": "0" * 64,
        "content_base64": base64.b64encode(b"abc").decode(),
    })

    with pytest.raises(DownloadSha256MismatchError):
        await _materialize(sink, _result_from_text(payload))

    assert not _store_path(ws).exists()
    assert not (ws / "sessions" / _session_files_dir_key(SESSION_KEY)
                / "files" / ".miqi" / "downloads" / "bad.cube").exists()


@pytest.mark.asyncio
async def test_tracking_failure_does_not_fail_delivery(monkeypatch):
    """登记是旁路：``_persist_tracked_file`` 抛异常也不得让已交付的下载失败。"""
    ws = _default_ws()
    sink = DownloadSink(base_workspace=ws)

    def _boom(*args, **kwargs):
        raise RuntimeError("tracked store unavailable")

    monkeypatch.setattr(
        "miqi.agent.tools.filesystem._persist_tracked_file", _boom,
    )

    artifact = await _materialize(sink, _result_from_text(_artifact_payload(b"delivered")))

    assert artifact.path.read_bytes() == b"delivered"
    assert artifact.size_bytes == 9
