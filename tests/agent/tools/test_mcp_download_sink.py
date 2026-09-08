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
    assert is_download_tool("miqroforge", "download_file") is True
    assert is_download_tool("miqroforge", "download_bulk") is True
    # 全局名匹配（未入 per-server 白名单的服务器也识别）
    assert is_download_tool("other-server", "download_file") is True
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
    # sidecar 存在且不含 base64
    sidecar = artifact.path.with_name(artifact.path.name + ".download.json")
    assert sidecar.exists()
    sc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sc["artifact_key"] == artifact.identity.artifact_key
    assert sc["turn_id"] == "turn-1" and sc["tool_call_id"] == "call-1"
    assert "base64" not in sidecar.read_text(encoding="utf-8").lower() or sc.get("content_base64") is None


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
