"""MCP download sink 单包路径测试（issue #975 Artifact Boundary）。

覆盖（C1 范围）：
- 单包 materialize：decode → size/sha 校验 → 原子落盘 → sidecar → 摘要；
- 模型摘要只含 5 字段、错误为结构化 JSON、任何路径都不回传 base64/内容；
- ownership：同身份复用（补写 sidecar）/ 有 sidecar 原子替换 / foreign 唯一名；
- 文件名净化与路径穿越拒绝；限额先拒后解；size/sha/base64 fail-closed；
- structuredContent canonical / content fallback / 双源矛盾 fail-closed；
- 双源错误矩阵与 isError 信号。
"""

import base64
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from miqi.agent.tools import mcp_download_sink as sink_mod
from miqi.agent.tools.filesystem import _session_files_dir_key
from miqi.agent.tools.mcp_download_sink import (
    DownloadArtifact,
    DownloadBase64Error,
    DownloadIoError,
    DownloadLimitError,
    DownloadPathError,
    DownloadProtocolError,
    DownloadServerError,
    DownloadSha256MismatchError,
    DownloadSink,
    DownloadSizeMismatchError,
    is_download_tool,
    parse_mcp_result,
    resolve_downloads_dir,
    sanitize_name,
)

# ── Fixtures / helpers ──────────────────────────────────────────────────────

SESSION_KEY = "miqi-desktop:desktop:1786807046853"


def _env(tmp_path: Path, monkeypatch=None):
    """默认 workspace + 会话文件目录（与 filesystem 会话隔离同构）。

    默认 workspace 判定在 filesystem 内部按 ``get_miqi_home()/workspace``
    比较——tmp 环境永远不是"默认 workspace"，隔离会被正确跳过（= 自选项目
    目录语义）。需要会话隔离路径的测试传入 monkeypatch，把本 root 判为默认。
    """
    root = tmp_path / "ws"
    root.mkdir()
    if monkeypatch is not None:
        import miqi.agent.tools.filesystem as fs_mod

        monkeypatch.setattr(
            fs_mod,
            "_is_default_workspace",
            lambda path: path is not None and Path(path).resolve() == root.resolve(),
        )
    session_files = root / "sessions" / _session_files_dir_key(SESSION_KEY) / "files"
    session_files.mkdir(parents=True)
    sink = DownloadSink(base_workspace=root)
    return root, session_files, sink


def _text_block(text: str):
    return SimpleNamespace(text=text)


def _result_from_text(text: str, *, is_error: bool = False):
    return SimpleNamespace(
        isError=is_error, structuredContent=None, content=[_text_block(text)]
    )


def _artifact_payload(data: bytes, name: str = "result.cube"):
    return json.dumps(
        {
            "name": name,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_base64": base64.b64encode(data).decode(),
        },
        ensure_ascii=False,
    )


async def _materialize(sink: DownloadSink, result, root: Path, **kw):
    """sink.materialize 的便捷封装（默认 session_key / server / tool）。"""
    defaults = dict(
        session_key=SESSION_KEY,
        server_name="miqroforge",
        tool_name="download_file",
        request_kwargs={"name": "result.cube"},
        turn_id="turn-1",
        tool_call_id="call-1",
    )
    defaults.update(kw)
    return await sink.materialize(result=result, **defaults)


# ── 分类 ────────────────────────────────────────────────────────────────────


def test_is_download_tool_classification():
    # 精确 (server, tool) 白名单（#988 评审 P2a：名字约定不得升格为全局信任）
    assert is_download_tool("miqroforge", "download_file") is True
    assert is_download_tool("miqroforge", "download_bulk") is True
    # schema.DEFAULT_MCP_SERVERS 的默认键也覆盖（部署侧服务器名防漂移）
    assert is_download_tool("miqroforge-slurm", "download_file") is True
    assert is_download_tool("miqroforge-slurm", "download_bulk") is True
    # 未知 server 的同名工具**不**自动进入下载语义
    assert is_download_tool("other-server", "download_file") is False
    # 非下载 / 返回 base64 的媒体类工具绝不误入
    assert is_download_tool("miqroforge", "check_job_status") is False
    assert is_download_tool("miqroforge", "render_image") is False


def test_default_tool_names_constant():
    assert "download_file" in sink_mod.DEFAULT_DOWNLOAD_TOOL_NAMES
    assert "download_bulk" in sink_mod.DEFAULT_DOWNLOAD_TOOL_NAMES


# ── 文件名净化 / 路径拒绝 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "../../evil.exe",
        r"..\..\evil.exe",
        r"C:\Windows\System32\evil.exe",
        "/absolute/path/x",
        r"\\server\share\x",
        "a:b.exe",
        "..",
        ".",
        "...",
    ],
)
def test_sanitize_name_rejects_path_semantics(name):
    with pytest.raises(DownloadPathError):
        sanitize_name(name)


def test_sanitize_name_legalizes_windows_invalid_chars():
    assert sanitize_name('a<b>c:"d.txt') == "abcd.txt"
    assert sanitize_name("  spaced name.bin  ") == "spaced name.bin"


def test_sanitize_name_rejects_empty_and_reserved_device_prefix():
    with pytest.raises(DownloadPathError):
        sanitize_name("")
    with pytest.raises(DownloadPathError):
        sanitize_name("   ")
    assert sanitize_name("CON.txt") == "_CON.txt"
    assert sanitize_name("com1.dat") == "_com1.dat"


# ── 会话目录解析 ────────────────────────────────────────────────────────────


def test_resolve_downloads_dir_default_workspace(tmp_path, monkeypatch):
    root, session_files, _ = _env(tmp_path, monkeypatch)
    out = resolve_downloads_dir(root, SESSION_KEY)
    assert out == session_files / ".miqi" / "downloads"


def test_resolve_downloads_dir_custom_workspace(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    out = resolve_downloads_dir(root, SESSION_KEY)
    assert out == root / ".miqi" / "downloads"
    # 空 session_key 同样退回 base（现有 filesystem 隔离语义）
    out2 = resolve_downloads_dir(root, "")
    assert out2 == root / ".miqi" / "downloads"


# ── 单包成功路径 ────────────────────────────────────────────────────────────


async def test_single_shot_materializes_with_summary(tmp_path):
    root, _, sink = _env(tmp_path)
    data = os.urandom(2 * 1024 * 1024)  # decoded ≈ 2 MiB（远低于 16 MiB 门）
    artifact = await _materialize(sink, _result_from_text(_artifact_payload(data)), root)
    assert isinstance(artifact, DownloadArtifact)
    assert artifact.path.exists()
    assert artifact.path.stat().st_size == len(data)
    assert artifact.sha256 == hashlib.sha256(data).hexdigest()
    # 摘要只含 5 字段
    summary = json.loads(artifact.to_model_text())
    assert summary == {
        "type": "download_artifact",
        "name": "result.cube",
        "path": str(artifact.path),
        "size_bytes": len(data),
        "sha256": artifact.sha256,
    }
    # base64 不出现在摘要
    assert base64.b64encode(data).decode() not in artifact.to_model_text()
    # sidecar 存在且不含 base64/内容（CodeRabbit 06-48：原 or 短路恒真，收严）
    sidecar = artifact.path.with_name(artifact.path.name + ".download.json")
    assert sidecar.exists()
    sc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sc["artifact_key"] == artifact.identity.artifact_key
    assert sc["turn_id"] == "turn-1" and sc["tool_call_id"] == "call-1"
    sidecar_text = sidecar.read_text(encoding="utf-8")
    assert "base64" not in sidecar_text.lower()
    assert base64.b64encode(data).decode() not in sidecar_text


async def test_materialize_1_6_mib_regression(tmp_path):
    """事故路径：decoded ≈ 1.6 MiB 单包，base64 ≈ 2.1 MiB < 16 MiB 门。"""
    root, _, sink = _env(tmp_path)
    data = os.urandom(int(1.6 * 1024 * 1024))
    payload = _artifact_payload(data, name="Na_bvse.cube")
    assert len(base64.b64encode(data)) < sink_mod.MAX_RESPONSE_BASE64_CHARS
    artifact = await _materialize(
        sink, _result_from_text(payload), root,
        request_kwargs={"name": "Na_bvse.cube"},
    )
    assert artifact.path.name == "Na_bvse.cube"
    assert artifact.path.read_bytes() == data


async def test_structured_content_canonical_wins_over_render_text(tmp_path):
    root, _, sink = _env(tmp_path)
    data = b"cube-bytes-01"
    payload = json.loads(_artifact_payload(data))
    result = SimpleNamespace(
        isError=False,
        structuredContent=payload,
        content=[_text_block("download complete: result.cube")],  # 渲染文本，非协议
    )
    artifact = await _materialize(sink, result, root)
    assert artifact.path.read_bytes() == data
    assert artifact.path.name == "result.cube"


async def test_content_fallback_when_structured_missing(tmp_path):
    root, _, sink = _env(tmp_path)
    data = b"fallback-bytes"
    artifact = await _materialize(
        sink, _result_from_text(_artifact_payload(data, name="fallback.bin")), root
    )
    assert artifact.path.read_bytes() == data


def test_dual_source_conflict_fails_closed():
    data = b"x" * 16
    good = json.loads(_artifact_payload(data))
    err = {"success": False, "error": "file not found"}
    # structured 成功 + content 显式错误 → 协议错误（不猜哪个是真的）
    r = SimpleNamespace(
        isError=False, structuredContent=good,
        content=[_text_block(json.dumps(err))],
    )
    with pytest.raises(DownloadProtocolError):
        parse_mcp_result(r)
    # structured 显式错误 + content 成功 → 同样 fail-closed
    r2 = SimpleNamespace(
        isError=False, structuredContent=err,
        content=[_text_block(json.dumps(good))],
    )
    with pytest.raises(DownloadProtocolError):
        parse_mcp_result(r2)


def test_structured_error_surface_parse_level():
    err = {"success": False, "error": "quota exceeded"}
    r = SimpleNamespace(isError=False, structuredContent=err, content=[_text_block("render")])
    parsed = parse_mcp_result(r)
    assert parsed.is_explicit_error is True
    assert parsed.error_text == "quota exceeded"


async def test_structured_error_becomes_server_error_text(tmp_path):
    """structuredContent 显式错误 → 整条 materialize 路径返回 DOWNLOAD_SERVER_ERROR。"""
    root, _, sink = _env(tmp_path)
    err = {"success": False, "error": "quota exceeded"}
    r = SimpleNamespace(isError=False, structuredContent=err, content=[_text_block("render")])
    with pytest.raises(DownloadServerError) as ei:
        await _materialize(sink, r, root)
    text = json.loads(ei.value.to_model_text())
    assert text["code"] == "DOWNLOAD_SERVER_ERROR"
    assert "quota exceeded" in text["message"]
    assert text["retryable"] is True
    # 无内容回传
    assert "render" not in text["message"]


async def test_is_error_flag_full_pipeline(tmp_path):
    root, _, sink = _env(tmp_path)
    r = SimpleNamespace(
        isError=True, structuredContent=None,
        content=[_text_block("file not found on remote")],
    )
    with pytest.raises(DownloadServerError) as ei:
        await _materialize(sink, r, root)
    text = json.loads(ei.value.to_model_text())
    assert text["code"] == "DOWNLOAD_SERVER_ERROR"
    assert "file not found on remote" in text["message"]


# ── fail-closed：校验与协议违例 ─────────────────────────────────────────────


async def test_success_without_content_is_protocol_error(tmp_path):
    root, _, sink = _env(tmp_path)
    r = _result_from_text(json.dumps(
        {"success": True, "filename": "a.cube", "size_bytes": 100, "sha256": "ab" * 32}
    ))
    with pytest.raises(DownloadProtocolError) as ei:
        await _materialize(sink, r, root)
    err = json.loads(ei.value.to_model_text())
    assert err["code"] == "DOWNLOAD_PROTOCOL_ERROR"
    assert err["retryable"] is False
    assert not list((root / "sessions").glob("**/result.cube"))


async def test_size_mismatch_fails_closed_no_file(tmp_path):
    root, _, sink = _env(tmp_path)
    data = b"short"
    payload = json.loads(_artifact_payload(data))
    payload["size_bytes"] = len(data) + 5  # 篡改期望大小
    r = _result_from_text(json.dumps(payload))
    with pytest.raises(DownloadSizeMismatchError) as ei:
        await _materialize(sink, r, root)
    err = json.loads(ei.value.to_model_text())
    assert "大小不一致" in err["message"]
    assert not (root / "sessions").exists() or not list(
        (root / "sessions").glob("**/result.cube")
    )


async def test_sha_mismatch_fails_closed_no_file(tmp_path):
    root, _, sink = _env(tmp_path)
    data = b"integrity-check"
    payload = json.loads(_artifact_payload(data))
    payload["sha256"] = "ab" * 32  # 篡改一个字节的哈希
    r = _result_from_text(json.dumps(payload))
    with pytest.raises(DownloadSha256MismatchError) as ei:
        await _materialize(sink, r, root)
    err = json.loads(ei.value.to_model_text())
    assert err["code"] == "DOWNLOAD_SHA256_MISMATCH"
    # 错误消息不含内容、只含前缀
    assert "integrity-check" not in err["message"]
    assert not list((root / "sessions").glob("**/*.cube"))


async def test_invalid_base64_fails_closed(tmp_path):
    root, _, sink = _env(tmp_path)
    payload = json.loads(_artifact_payload(b"x" * 10))
    payload["content_base64"] = "!!!not-base64!!!"
    r = _result_from_text(json.dumps(payload))
    with pytest.raises(DownloadBase64Error):
        await _materialize(sink, r, root)


async def test_response_over_limit_rejected_before_decode(tmp_path):
    root, _, sink = _env(tmp_path)
    big_b64 = "A" * (sink_mod.MAX_CHUNK_BASE64_CHARS + 1)
    payload = {"name": "huge.bin", "content_base64": big_b64}
    r = _result_from_text(json.dumps(payload))
    with pytest.raises(DownloadLimitError):
        await _materialize(sink, r, root)


# ── ownership：复用 / 替换 / foreign 唯一名 ────────────────────────────────


async def test_ownership_reuse_same_content_backfills_sidecar(tmp_path):
    root, _, sink = _env(tmp_path)
    data = b"reusable-content"
    # 第一次成功
    a1 = await _materialize(sink, _result_from_text(_artifact_payload(data)), root)
    first = a1.path.read_bytes()
    assert first == data
    # 手动删除 sidecar 模拟"无归属但内容一致"（D 类）
    a1.path.with_name(a1.path.name + ".download.json").unlink()
    # 同身份再来一次 → 复用同一 path，不产生 (1)
    a2 = await _materialize(sink, _result_from_text(_artifact_payload(data)), root)
    assert a2.path == a1.path
    assert not list(root.glob("**/* (1).cube"))
    # sidecar 被补写
    assert a2.path.with_name(a2.path.name + ".download.json").exists()


async def test_ownership_owned_artifact_replaced_atomically_same_path(tmp_path):
    root, _, sink = _env(tmp_path)
    data_old = b"old-content-00000000000000000000"
    data_new = b"new-content-00000000000000000000"
    await _materialize(sink, _result_from_text(_artifact_payload(data_old)), root)
    # 同身份（同 source args + filename）但上游内容变更 → 原子替换同一 path
    a2 = await _materialize(sink, _result_from_text(_artifact_payload(data_new)), root)
    assert a2.path.name == "result.cube"
    assert a2.path.read_bytes() == data_new
    assert not list(root.glob("**/* (1).cube"))


async def test_ownership_foreign_file_never_overwritten(tmp_path):
    root, _, sink = _env(tmp_path)
    downloads = resolve_downloads_dir(root, SESSION_KEY)
    downloads.mkdir(parents=True)
    # 用户/agent 自己放的同名文件（无 sidecar）→ foreign
    (downloads / "result.cube").write_bytes(b"user-owned-precious-data")
    data = b"downloaded-content"
    artifact = await _materialize(sink, _result_from_text(_artifact_payload(data)), root)
    assert artifact.path.name == "result (1).cube"
    # 外来文件原封不动
    assert (downloads / "result.cube").read_bytes() == b"user-owned-precious-data"
    assert artifact.path.read_bytes() == data


async def test_ownership_same_content_different_identity_reuses(tmp_path):
    """内容完全一致（size+sha 匹配）→ 复用，身份不参与（最强的安全性检查）。"""
    root, _, sink = _env(tmp_path)
    data = b"some-artifact-bytes"
    a1 = await _materialize(sink, _result_from_text(_artifact_payload(data)), root)
    a2 = await _materialize(
        sink, _result_from_text(_artifact_payload(data)), root,
        request_kwargs={"name": "result.cube", "path": "/remote/other.cube"},
    )
    assert a2.path == a1.path
    assert not list(root.glob("**/* (1).cube"))


async def test_ownership_different_identity_and_content_unique(tmp_path):
    """同文件名 + 不同身份 + 内容不同 → 唯一名，原文件不动。"""
    root, _, sink = _env(tmp_path)
    data_a = b"artifact-bytes-aaaaaaaaaaaaaaaaaa"
    data_b = b"artifact-bytes-bbbbbbbbbbbbbbbbbb"
    a1 = await _materialize(sink, _result_from_text(_artifact_payload(data_a)), root)
    a2 = await _materialize(
        sink, _result_from_text(_artifact_payload(data_b, name="result.cube")), root,
        request_kwargs={"name": "result.cube", "path": "/remote/other.cube"},
    )
    assert a1.path.name == "result.cube"
    assert a2.path.name == "result (1).cube"
    assert a1.path.read_bytes() == data_a
    assert a2.path.read_bytes() == data_b


# ── 原子性与异常路径 ───────────────────────────────────────────────────────


def test_atomic_write_leaves_no_part_on_success(tmp_path):
    target = tmp_path / "out.bin"
    sink_mod._atomic_write(target, b"payload")
    assert target.read_bytes() == b"payload"
    assert not target.with_name(target.name + ".part").exists()


async def test_unwritable_download_dir_raises_io_error(tmp_path):
    """`.miqi` 被文件占位 → mkdir 失败 → OSError 统一归 DOWNLOAD_IO_ERROR。"""
    root = tmp_path / "ws"
    root.mkdir()
    (root / ".miqi").write_text("blocked", encoding="utf-8")  # 文件占位目录名
    sink = DownloadSink(base_workspace=root)
    data = b"io-fail-check"
    with pytest.raises(DownloadIoError) as ei:
        await _materialize(
            sink,
            _result_from_text(_artifact_payload(data, name="x.bin")),
            root,
            request_kwargs={"name": "x.bin"},
        )
    text = json.loads(ei.value.to_model_text())
    assert text["code"] == "DOWNLOAD_IO_ERROR"
    assert "io-fail-check" not in text["message"]


# ── 双源 / logger 泄漏快照 ─────────────────────────────────────────────────


def test_download_sink_logging_never_logs_payload(caplog):
    """关键路径成功 + 失败都不应向 logger 输出 base64/内容（纪律回归网）。"""
    import logging

    with caplog.at_level(logging.DEBUG):
        data = b"logger-sentinel-12345"
        try:
            parse_mcp_result(_result_from_text(_artifact_payload(data)))
        except Exception:
            pass
    joined = caplog.text
    assert "logger-sentinel-12345" not in joined
    assert base64.b64encode(data).decode() not in joined


def test_error_json_never_contains_raw_content():
    data = b"raw-secret-content-bytes"
    err = DownloadSizeMismatchError(
        f"期望 {999999} bytes，实际 {len(data)} bytes"
    )
    text = err.to_model_text()
    assert "raw-secret-content-bytes" not in text
    assert base64.b64encode(data).decode() not in text


# ── C3：分片组装（形态甲整包 / 形态乙跨调用续传 / fail-closed）─────────────


def _chunk_payload(
    pieces: list[bytes],
    *,
    declared_total: int | None = None,
    start_index: int = 0,
    name: str = "chunked.cube",
    per_item_total: bool = True,
    include_sha: bool = False,
    include_size: bool = False,
    totals: list[int | None] | None = None,
) -> str:
    """构造 chunks 数组响应（形态甲）。totals 可逐片覆盖（用于变更测试）。"""
    full = b"".join(pieces)
    items = []
    for i, piece in enumerate(pieces):
        item: dict = {
            "chunk_index": start_index + i,
            "content_base64": base64.b64encode(piece).decode(),
        }
        total = (totals[i] if totals else None)
        if total is not None:
            item["total_chunks"] = total
        elif per_item_total:
            item["total_chunks"] = declared_total if declared_total is not None else len(pieces)
        if include_sha:
            item["sha256"] = hashlib.sha256(full).hexdigest()
        if include_size:
            item["size_bytes"] = len(full)
        items.append(item)
    payload: dict = {"name": name, "chunks": items}
    if declared_total is not None:
        payload["total_chunks"] = declared_total
    if include_sha:
        payload["sha256"] = hashlib.sha256(full).hexdigest()
    if include_size:
        payload["size_bytes"] = len(full)
    return json.dumps(payload)


def _single_chunk_payload(
    piece: bytes,
    *,
    chunk_index: int,
    total_chunks: int,
    name: str = "chunked.cube",
    sha256: str | None = None,
    size_bytes: int | None = None,
    success: bool | None = None,
) -> str:
    """构造形态乙单响应（一次调用一片）。"""
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
    if success is not None:
        payload["success"] = success
    return json.dumps(payload)


async def test_form_a_chunk_array_single_response(tmp_path):
    """形态甲：一次响应含全部 3 片 → 直接组装完成，无中间态。"""
    root, _, sink = _env(tmp_path)
    pieces = [b"AAA", b"BBB", b"CCC"]
    full = b"".join(pieces)
    r = _result_from_text(_chunk_payload(pieces, include_sha=True, include_size=True))
    artifact = await _materialize(sink, r, root, request_kwargs={"name": "chunked.cube"})
    assert artifact.path.name == "chunked.cube"
    assert artifact.path.read_bytes() == full
    assert artifact.sha256 == hashlib.sha256(full).hexdigest()
    staging = resolve_downloads_dir(root, SESSION_KEY) / ".staging"
    assert not staging.exists() or not list(staging.glob("*"))


async def test_form_b_cross_call_assembly(tmp_path):
    """形态乙：模型带 chunk_index 三次调用 → pending → pending → 最终 artifact。"""
    root, _, sink = _env(tmp_path)
    pieces = [b"chunk-zero", b"chunk-one-", b"chunk-two!"]
    full = b"".join(pieces)
    sha = hashlib.sha256(full).hexdigest()

    call_ctx = dict(
        server_name="miqroforge", tool_name="download_file",
        session_key=SESSION_KEY, turn_id="t1", tool_call_id="c1",
    )

    # 第 0 片：不齐 → pending（不是错误）
    with pytest.raises(sink_mod.DownloadPendingError) as pe0:
        await sink.materialize(
            result=_result_from_text(_single_chunk_payload(
                pieces[0], chunk_index=0, total_chunks=3, sha256=sha,
                size_bytes=len(full), name="cube.cube")),
            request_kwargs={"name": "cube.cube", "chunk_index": 0},
            **call_ctx,
        )
    pend0 = json.loads(pe0.value.to_model_text())
    assert pend0["type"] == "download_pending"
    assert pend0["next_chunk_index"] == 1
    assert pend0["total_chunks"] == 3
    assert "不要中断" in pend0["message"]

    # 第 1 片：仍是 pending
    with pytest.raises(sink_mod.DownloadPendingError) as pe1:
        await sink.materialize(
            result=_result_from_text(_single_chunk_payload(
                pieces[1], chunk_index=1, total_chunks=3, sha256=sha,
                size_bytes=len(full), name="cube.cube")),
            request_kwargs={"name": "cube.cube", "chunk_index": 1},
            **call_ctx,
        )
    pend1 = json.loads(pe1.value.to_model_text())
    assert pend1["received_chunks"] == 2
    assert pend1["next_chunk_index"] == 2

    # 第 2 片：齐 → artifact；内容 = 三片拼接
    artifact = await sink.materialize(
        result=_result_from_text(_single_chunk_payload(
            pieces[2], chunk_index=2, total_chunks=3, sha256=sha,
            size_bytes=len(full), name="cube.cube")),
        request_kwargs={"name": "cube.cube", "chunk_index": 2},
        **call_ctx,
    )
    assert artifact.path.read_bytes() == full
    assert artifact.sha256 == sha
    # staging 无残留、无 (1) 垃圾
    staging = resolve_downloads_dir(root, SESSION_KEY) / ".staging"
    assert not staging.exists() or not list(staging.glob("*"))


async def test_chunk_index_param_excluded_from_identity(tmp_path):
    """chunk_index 是传输参数：不同调用 kwargs 必须产生同一 ArtifactIdentity。"""
    root, _, sink = _env(tmp_path)
    data = b"identity-check-data"
    r = _result_from_text(_artifact_payload(data, name="ident.cube"))
    a = await _materialize(sink, r, root, request_kwargs={"name": "ident.cube"})
    b = await _materialize(
        sink, r, root, request_kwargs={"name": "ident.cube", "chunk_index": 5},
    )
    assert a.identity.artifact_key == b.identity.artifact_key
    assert a.path == b.path  # 同一文件（幂等复用）


async def test_chunk_skip_index_fails_closed(tmp_path):
    root, _, sink = _env(tmp_path)
    pieces = [b"A", b"B", b"C"]
    with pytest.raises(sink_mod.DownloadError):
        await _materialize(
            sink, _result_from_text(_single_chunk_payload(
                pieces[0], chunk_index=0, total_chunks=3, name="s.cube")),
            root, request_kwargs={"name": "s.cube"},
        )
    with pytest.raises(sink_mod.DownloadChunkError) as ei:
        await _materialize(
            sink, _result_from_text(_single_chunk_payload(
                pieces[2], chunk_index=2, total_chunks=3, name="s.cube")),
            root, request_kwargs={"name": "s.cube"},
        )
    assert json.loads(ei.value.to_model_text())["code"] == "DOWNLOAD_CHUNK_ERROR"


async def test_chunk_duplicate_fails_closed(tmp_path):
    root, _, sink = _env(tmp_path)
    pieces = [b"A", b"B", b"C"]
    for idx in (0, 1, 1):  # 1 重复
        with pytest.raises(sink_mod.DownloadError):
            await _materialize(
                sink, _result_from_text(_single_chunk_payload(
                    pieces[idx], chunk_index=idx, total_chunks=3, name="d.cube")),
                root, request_kwargs={"name": "d.cube"},
            )
    # 无残留可拼接残片
    downloads = resolve_downloads_dir(root, SESSION_KEY)
    assert not (downloads / ".staging").exists() or not list((downloads / ".staging").glob("*"))


async def test_chunk_total_mutation_fails_closed(tmp_path):
    root, _, sink = _env(tmp_path)
    with pytest.raises(sink_mod.DownloadError):
        await _materialize(
            sink, _result_from_text(_single_chunk_payload(
                b"A", chunk_index=0, total_chunks=3, name="t.cube")),
            root, request_kwargs={"name": "t.cube"},
        )
    with pytest.raises(sink_mod.DownloadChunkError) as ei:  # total 3→4 变更
        await _materialize(
            sink, _result_from_text(_single_chunk_payload(
                b"B", chunk_index=1, total_chunks=4, name="t.cube")),
            root, request_kwargs={"name": "t.cube"},
        )
    assert json.loads(ei.value.to_model_text())["code"] == "DOWNLOAD_CHUNK_ERROR"


async def test_chunk_mid_transfer_success_false_fails_closed(tmp_path):
    root, _, sink = _env(tmp_path)
    pieces = [b"A", b"B", b"C"]
    # 先正常收第 0 片
    with pytest.raises(sink_mod.DownloadPendingError):
        await _materialize(
            sink, _result_from_text(_single_chunk_payload(
                pieces[0], chunk_index=0, total_chunks=3, name="f.cube")),
            root, request_kwargs={"name": "f.cube"},
        )
    # 中途 success=false → 服务端显式失败：整份丢弃（含在途 staging）
    with pytest.raises(sink_mod.DownloadServerError) as ei:
        await _materialize(
            sink, _result_from_text(_single_chunk_payload(
                pieces[1], chunk_index=1, total_chunks=3, name="f.cube",
                success=False)),
            root, request_kwargs={"name": "f.cube"},
        )
    assert json.loads(ei.value.to_model_text())["code"] == "DOWNLOAD_SERVER_ERROR"
    downloads = resolve_downloads_dir(root, SESSION_KEY)
    staging = downloads / ".staging"
    assert not staging.exists() or not list(staging.glob("*"))
    # 失败后无卡死：重新从头收第 0 片可以再次启动传输（self-healing）
    with pytest.raises(sink_mod.DownloadPendingError):
        await _materialize(
            sink, _result_from_text(_single_chunk_payload(
                pieces[0], chunk_index=0, total_chunks=3, name="f.cube")),
            root, request_kwargs={"name": "f.cube"},
        )


async def test_chunk_sha_continuity_mismatch_fails_closed(tmp_path):
    root, _, sink = _env(tmp_path)
    with pytest.raises(sink_mod.DownloadError):
        await _materialize(
            sink, _result_from_text(_single_chunk_payload(
                b"A", chunk_index=0, total_chunks=3, name="sc.cube",
                sha256="ab" * 32)),
            root, request_kwargs={"name": "sc.cube"},
        )
    with pytest.raises(sink_mod.DownloadChunkError) as ei:  # sha 中途变更
        await _materialize(
            sink, _result_from_text(_single_chunk_payload(
                b"B", chunk_index=1, total_chunks=3, name="sc.cube",
                sha256="cd" * 32)),
            root, request_kwargs={"name": "sc.cube"},
        )
    assert json.loads(ei.value.to_model_text())["code"] == "DOWNLOAD_CHUNK_ERROR"


async def test_staging_sweep_never_touches_final(tmp_path):
    """清扫只删 .staging/**，downloads 根下的 final artifact 原封不动。"""
    root, _, sink = _env(tmp_path)
    downloads = resolve_downloads_dir(root, SESSION_KEY)
    (downloads / ".staging").mkdir(parents=True)
    (downloads / ".staging" / "stale.part").write_bytes(b"stale")
    (downloads / ".staging" / "stale.json").write_text("{}", encoding="utf-8")
    genuine = downloads / "genuine.cube"
    genuine.write_bytes(b"keep-me")

    data = b"sweep-trigger"
    await _materialize(sink, _result_from_text(_artifact_payload(data)), root)

    assert not (downloads / ".staging" / "stale.part").exists()
    assert not (downloads / ".staging" / "stale.json").exists()
    assert genuine.read_bytes() == b"keep-me"


async def test_concurrent_same_identity_does_not_corrupt(tmp_path):
    """同身份并发（锁串行化）：两次同内容单包调用 → 单文件、内容一致。"""
    import asyncio

    root, _, sink = _env(tmp_path)
    data = os.urandom(64 * 1024)
    r = _result_from_text(_artifact_payload(data))

    async def _call():
        return await sink.materialize(
            result=r, session_key=SESSION_KEY, server_name="miqroforge",
            tool_name="download_file", request_kwargs={"name": "con.cube"},
            turn_id="t", tool_call_id="c",
        )

    a1, a2 = await asyncio.gather(_call(), _call())
    assert a1.path == a2.path
    assert a1.path.read_bytes() == data
    assert not list(root.glob("**/* (1).cube"))



# ── #988 评审回归：并发覆盖 / 分片元数据合法性 ─────────────────────────────


async def test_concurrent_different_identity_same_filename_no_overwrite(tmp_path):
    """评审 P1a 回归：不同 ArtifactIdentity + 同目标文件名并发 finalize。

    修复前（仅 artifact 级锁）两个身份可能同时看到 result.cube 空闲并双写，
    后者静默覆盖前者——与"foreign/异身份绝不覆盖"设计原则直接冲突。
    修复后（downloads-dir 级命名分配锁）：必得 result.cube + result (1).cube，
    内容各自完整、sidecar 各归其主。
    """
    import asyncio

    root, _, sink = _env(tmp_path)
    data_a = b"identity-A-content-aaaaaaaaaaaaaaa"
    data_b = b"identity-B-content-bbbbbbbbbbbbbbb"

    async def _dl(data: bytes, remote_path: str):
        return await sink.materialize(
            result=_result_from_text(_artifact_payload(data)),
            session_key=SESSION_KEY,
            server_name="miqroforge",
            tool_name="download_file",
            request_kwargs={"name": "result.cube", "path": remote_path},
            turn_id=f"turn-{remote_path[-1]}",
            tool_call_id="c",
        )

    a, b = await asyncio.gather(
        _dl(data_a, "/remote/a.cube"), _dl(data_b, "/remote/b.cube")
    )

    names = sorted(p.name for p in (a.path, b.path))
    assert names == sorted(["result.cube", "result (1).cube"])
    by_name = {p.name: p.read_bytes() for p in (a.path, b.path)}
    assert set(by_name.values()) == {data_a, data_b}  # 两份内容都在，无覆盖
    # sidecar 各归其主（文件名与 artifact_key 一一对应）
    for artifact in (a, b):
        sc = json.loads(
            artifact.path.with_name(artifact.path.name + ".download.json")
            .read_text(encoding="utf-8")
        )
        assert sc["artifact_key"] == artifact.identity.artifact_key
        assert sc["name"] == artifact.path.name


async def test_total_chunks_zero_rejected(tmp_path):
    """评审 P1b 回归：total_chunks=0 不得被当作"0>=0 已完成"的完整传输。"""
    root, _, sink = _env(tmp_path)
    payload = {
        "name": "z.cube",
        "chunk_index": 0,
        "total_chunks": 0,
        "content_base64": base64.b64encode(b"data").decode(),
    }
    with pytest.raises(sink_mod.DownloadError) as ei:
        await _materialize(sink, _result_from_text(json.dumps(payload)), root,
                           request_kwargs={"name": "z.cube"})
    assert json.loads(ei.value.to_model_text())["code"] in (
        "DOWNLOAD_CHUNK_ERROR", "DOWNLOAD_PROTOCOL_ERROR",
    )
    downloads = resolve_downloads_dir(root, SESSION_KEY)
    assert not (downloads / ".staging").exists() or not list((downloads / ".staging").glob("*"))


async def test_total_chunks_negative_rejected(tmp_path):
    """评审 P1b 回归：total_chunks=-1 必须 fail-closed。"""
    root, _, sink = _env(tmp_path)
    payload = {
        "name": "neg.cube",
        "chunk_index": 0,
        "total_chunks": -1,
        "content_base64": base64.b64encode(b"data").decode(),
    }
    with pytest.raises(sink_mod.DownloadError):
        await _materialize(sink, _result_from_text(json.dumps(payload)), root,
                           request_kwargs={"name": "neg.cube"})


async def test_chunk_index_out_of_bounds_rejected(tmp_path):
    """评审 P1b 回归：chunk_index >= total_chunks 的越界片必须 fail-closed。"""
    root, _, sink = _env(tmp_path)
    with pytest.raises(sink_mod.DownloadError):
        await _materialize(
            sink, _result_from_text(_single_chunk_payload(
                b"A", chunk_index=0, total_chunks=3, name="ob.cube")),
            root, request_kwargs={"name": "ob.cube"},
        )
    with pytest.raises(sink_mod.DownloadError):  # 3 >= 3 越界
        await _materialize(
            sink, _result_from_text(_single_chunk_payload(
                b"B", chunk_index=3, total_chunks=3, name="ob.cube")),
            root, request_kwargs={"name": "ob.cube"},
        )
