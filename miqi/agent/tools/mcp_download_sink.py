"""MCP binary artifact 可信交付 —— Artifact Boundary（issue #975）。

背景
----
2026-09-08 事故：MCP ``download_file`` 的响应（含完整 ``content_base64``）
被当普通工具文本注入 LLM 上下文，上下文压缩中段截断后模型自行"补全解码"，
产出损坏文件并向用户谎报完成（P0 数据完整性）。服务端契约已修复
（响应含 ``size_bytes``/``sha256``/分片参数），本模块是客户端消费端闭环。

架构原则（v6.2 冻结基线，勿在实现时放宽）
----------------------------------------
- **Artifact Boundary**：LLM 永不接触原始二进制。``content_base64`` / raw
  bytes / raw response 只允许出现在 MCP SDK 响应、短生命周期内存、
  ``.staging/`` 临时文件与最终 artifact；**绝不进入** ctx.result、tool
  message、messages_delta、ledger、UI preview、logger。
- **身份模型**：``ArtifactIdentity``（幂等/最终命名/复用，首片+请求参数即可
  确定）与 ``TransferIdentity``（staging/并发锁/单次传输，可携带服务端
  request_id）分离。sha/size 是**校验谓词不是身份键**——形态乙分片下元数据
  可能末片才到，身份中途不得漂移。
- **Foreign-file ownership**：最终文件无匹配 sidecar 且 hash 不符 = 外来文件，
  绝不覆盖，走唯一名；同身份重试恒走同一 path，不制造 ``(1)`` 垃圾。
- **fail-closed**：任何契约异常（双源矛盾、缺内容、分片非法、校验失败）→
  丢弃整份，错误以结构化 JSON 返回，绝不回传内容、绝不让模型凭
  ``success=true`` 宣布交付。

服务端真实字段命名/嵌套是**样例冻结边界**：alias 解析集中在
``parse_mcp_result`` 一处，拿到真实响应后只改这里。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

# ── 限额（v6.2 §5.1）────────────────────────────────────────────────────────

# 最终文件累计字节上限。
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
# 单次 MCP result 内 base64 累计字符上限（decode 前先拒，防内存放大）。
MAX_RESPONSE_BASE64_CHARS = 16 * 1024 * 1024
# 单 chunk 的 base64 字符上限。v1 与单响应上限相等，后续可按协议拆小。
MAX_CHUNK_BASE64_CHARS = MAX_RESPONSE_BASE64_CHARS

# ── 下载工具分类（v6.2 §2）──────────────────────────────────────────────────

# 代码常量白名单——"是否 binary artifact endpoint" 是 runtime protocol
# semantics，不是用户偏好，不进用户 config（进 config = 允许把任意 MCP 工具
# 标成下载端点，扩大攻击面）。
DEFAULT_DOWNLOAD_TOOL_NAMES = frozenset({"download_file", "download_bulk"})

# per-server 精确匹配优先于全局名匹配。
_DOWNLOAD_TOOL_ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("miqroforge", "download_file"),
    ("miqroforge", "download_bulk"),
)

# 分类命中后追加到 wrapper description 的指引段（构造期拼好，随工具定义进模型）。
DOWNLOAD_TOOL_GUIDANCE = (
    "\n\n该工具返回的是二进制 artifact。"
    "下载结果只含文件摘要（path/size/sha256），内容不会出现在上下文里。"
    "若返回分片进度（如\"已接收 X/Y 片，请请求 chunk_index=N\"），"
    "必须继续调用同一工具请求下一片，不得中断、不得据此声称文件已交付。"
    "禁止 read_file 读取 artifact 内容后 write_file 重建文件；"
    "需要移动/复制时使用文件系统级 copy/move，并在交付后校验目标文件大小与 sha256。"
)


def is_download_tool(server_name: str, tool_name: str) -> bool:
    """构造期分类：``(server_name, tool_name)`` 精确 > 全局工具名 > 非下载。

    ``content_base64`` 不作为识别判据——MCP 生态里 media/embedding 类工具
    也可能返回 base64，误入下载路径会造成协议误判（只作第二重确认）。
    """
    if (server_name, tool_name) in _DOWNLOAD_TOOL_ALLOWLIST:
        return True
    return tool_name in DEFAULT_DOWNLOAD_TOOL_NAMES


# ── 错误语义（v6.2 §5.2）───────────────────────────────────────────────────


class DownloadError(Exception):
    """sink 内部错误基类。**只允许在 wrapper 内消化为文本**，绝不上抛到
    orchestrator（防 ``[Analyze the error above]`` 套壳与 UI 清洗污染语义）。"""

    code = "DOWNLOAD_ERROR"
    retryable = False

    def __init__(self, message: str | None = None):
        super().__init__(message or self.default_message())
        self.message = str(self.args[0])

    def default_message(self) -> str:
        raise NotImplementedError

    def to_model_text(self) -> str:
        """结构化错误 JSON。message 为丰富中文；无 traceback/repr/raw 内容。"""
        return json.dumps(
            {
                "type": "download_error",
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            },
            ensure_ascii=False,
        )


class DownloadServerError(DownloadError):
    code = "DOWNLOAD_SERVER_ERROR"
    retryable = True

    def default_message(self) -> str:
        return "下载失败：服务端明确返回错误。文件未交付，请检查远端文件后重新下载。"


class DownloadProtocolError(DownloadError):
    code = "DOWNLOAD_PROTOCOL_ERROR"
    retryable = False

    def default_message(self) -> str:
        return "下载失败：服务端响应不符合下载契约（数据缺失或自相矛盾）。文件未交付。"


class DownloadLimitError(DownloadError):
    code = "DOWNLOAD_LIMIT_ERROR"
    retryable = True

    def default_message(self) -> str:
        return (
            "下载失败：响应超过客户端单次下载限制。文件内容未交付。"
            "请使用服务端支持的分片下载方式重新请求。"
        )


class DownloadChunkError(DownloadError):
    code = "DOWNLOAD_CHUNK_ERROR"
    retryable = True

    def default_message(self) -> str:
        return "下载失败：分片协议异常（缺失/重复/顺序错误/并发冲突）。文件未交付，请重新下载。"


class DownloadBase64Error(DownloadError):
    code = "DOWNLOAD_BASE64_ERROR"
    retryable = True

    def default_message(self) -> str:
        return "下载失败：服务端返回的二进制编码非法或不完整。文件未交付。"


class DownloadSizeMismatchError(DownloadError):
    code = "DOWNLOAD_SIZE_MISMATCH"
    retryable = True

    def default_message(self) -> str:
        return (
            "下载失败：文件完整性校验未通过（大小不一致）。"
            "文件未交付，请重新下载，不要根据当前结果推断文件内容。"
        )


class DownloadSha256MismatchError(DownloadError):
    code = "DOWNLOAD_SHA256_MISMATCH"
    retryable = True

    def default_message(self) -> str:
        return (
            "下载失败：文件完整性校验未通过（SHA-256 不一致）。"
            "文件未交付，请重新下载，不要根据当前结果推断文件内容。"
        )


class DownloadPathError(DownloadError):
    code = "DOWNLOAD_PATH_ERROR"
    retryable = False

    def default_message(self) -> str:
        return "下载失败：服务端提供的文件名含非法路径。文件未交付。"


class DownloadIoError(DownloadError):
    code = "DOWNLOAD_IO_ERROR"
    retryable = True

    def default_message(self) -> str:
        return "下载失败：本地写入失败。文件未交付，请重试。"


# ── 数据结构（v6.2 §4/§7）──────────────────────────────────────────────────


def _canonical_args_hash(kwargs: dict[str, Any]) -> str:
    """业务参数 canonical JSON hash：runtime 注入键（``_`` 前缀）剔除后
    ``sort_keys`` 序列化再取 sha256 前 16 位。

    ``download_file(path=/a.cube)`` 与 ``download_file(path=/b.cube)``
    因此必然产生不同身份。
    """
    business = {k: v for k, v in kwargs.items() if not str(k).startswith("_")}
    try:
        raw = json.dumps(business, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # 极端不可序列化参数——字符串化兜底，身份仍稳定
        raw = json.dumps({k: str(v) for k, v in sorted(business.items())}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ArtifactIdentity:
    """幂等 / 最终命名 / 复用 的稳定键（v6.2 §4.1）。

    必须能在首片响应 + 请求参数上确定。``expected_sha256/expected_size``
    是校验谓词不是键成员——形态乙分片下元数据可能末片才到。
    """

    session_key: str
    server_name: str
    tool_name: str
    source_args_hash: str
    filename: str

    @property
    def artifact_key(self) -> str:
        """sidecar 归属校验用的稳定键。"""
        raw = "|".join(
            (self.session_key, self.server_name, self.tool_name,
             self.source_args_hash, self.filename)
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class TransferIdentity:
    """单次传输身份：staging 命名 / 并发锁 / chunk 状态（C3 起使用）。

    同一 ArtifactIdentity 可挂多个 TransferIdentity——失败重试携带新的
    服务端 request_id 时，最终 path 仍由 ArtifactIdentity 决定。
    """

    artifact: ArtifactIdentity
    request_id: str | None = None
    attempt: int = 1


@dataclass(frozen=True)
class ParsedChunk:
    chunk_index: int
    total_chunks: int | None
    content_base64: str
    success: bool | None = None


@dataclass(frozen=True)
class ParsedDownloadResponse:
    """适配层输出（统一内部结构）。真实字段命名/嵌套只在这个文件里出现。"""

    success: bool
    is_explicit_error: bool
    name: str | None
    size_bytes: int | None
    sha256: str | None
    request_id: str | None
    chunks: tuple[ParsedChunk, ...]
    raw_source: Literal["structuredContent", "content"]
    error_text: str | None = None


@dataclass(frozen=True)
class DownloadArtifact:
    identity: ArtifactIdentity
    path: Path
    size_bytes: int
    sha256: str
    request_id: str | None
    turn_id: str
    tool_call_id: str
    sha_origin: Literal["server"] = "server"

    def to_model_text(self) -> str:
        """模型可见摘要——**只允许** type/name/path/size_bytes/sha256。

        session/server/turn/tool_call/request_id 是内部追踪信息，进 sidecar
        不进摘要（模型上下文最小化 + 本地审计可追踪）。
        """
        return json.dumps(
            {
                "type": "download_artifact",
                "name": self.identity.filename,
                "path": str(self.path),
                "size_bytes": self.size_bytes,
                "sha256": self.sha256,
            },
            ensure_ascii=False,
        )


# ── 文件名净化与路径约束（v6.2 §4.4）───────────────────────────────────────

# Windows 保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）在文件系统层有特殊语义。
_RESERVED_WIN_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# 普通文件名合法化允许剔除的 Windows 非法字符（含控制符）。
_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"|?*\x00-\x1f]')


def sanitize_name(name: str) -> str:
    """服务端文件名净化（v6.2 §4.4）。

    含路径语义的输入（``/``、``\\``、盘符前缀 ``^[A-Za-z]:``、UNC、
    ``..``、全点号）**一律拒绝**（DownloadPathError），不静默净化继续执行
    ——静默改名会掩盖协议问题，且 NTFS 盘符/ADS 语义（``C:evil.exe``、
    ``a:b``）本身就是路径穿越面。其余只做普通文件名合法化（剔 Windows
    非法字符）；保留设备名加 ``_`` 前缀规避（属合法化，非路径问题）。
    """
    if not isinstance(name, str) or not name.strip():
        raise DownloadPathError("下载失败：服务端返回的文件名为空。")
    name = name.strip()
    if (
        "/" in name
        or "\\" in name
        or re.match(r"^[A-Za-z]:", name)
        or name in (".", "..")
        or set(name) <= {"."}
    ):
        raise DownloadPathError(
            "下载失败：服务端返回的文件名含路径语义，已拒绝（防路径穿越）。"
        )
    cleaned = _INVALID_FILENAME_CHARS_RE.sub("", name).strip()
    if not cleaned:
        raise DownloadPathError("下载失败：服务端返回的文件名净化后为空。")
    stem = cleaned.split(".")[0].upper()
    if stem in _RESERVED_WIN_NAMES:
        cleaned = "_" + cleaned
    return cleaned


def resolve_downloads_dir(base_workspace: Path, session_key: str) -> Path:
    """会话落盘根（v6.2 §R1）：复用 filesystem 的会话目录权威逻辑。

    默认 workspace → ``<ws>/sessions/<safe_key>/files/.miqi/downloads``
    （文件工具合法根内，模型可直接读/搬，不跨会话互见）；自选项目目录 /
    空 session_key → ``<ws>/.miqi/downloads``。**不在此文件自创目录算法**。
    """
    from miqi.agent.tools.filesystem import _session_files_dir_for_key

    session_files_dir = _session_files_dir_for_key(base_workspace, session_key or None)
    base = session_files_dir if session_files_dir is not None else base_workspace
    if base is None:
        raise DownloadPathError("下载失败：会话工作区不可用（workspace 为空）。")
    return base / ".miqi" / "downloads"


def _ensure_contained(root: Path, target: Path) -> Path:
    """落盘根硬约束：resolve 后必须在 download_root 内（防任意写盘）。"""
    resolved = target.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise DownloadPathError("下载失败：目标路径越出下载根目录，已拒绝。")
    return resolved


# ── sidecar 与 ownership（v6.2 §4.4/§7.3）───────────────────────────────────

_SIDECAR_SUFFIX = ".download.json"
_SIDECAR_SCHEMA_VERSION = 1


def _sidecar_path(final_path: Path) -> Path:
    return final_path.with_name(final_path.name + _SIDECAR_SUFFIX)


def _write_sidecar(final_path: Path, artifact: DownloadArtifact) -> None:
    """成功后写 sidecar：provenance + 完整性审计。永不写 base64/内容。"""
    sidecar = _sidecar_path(final_path)
    payload = {
        "schema_version": _SIDECAR_SCHEMA_VERSION,
        "type": "download_artifact",
        "artifact_key": artifact.identity.artifact_key,
        "name": artifact.identity.filename,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "server_name": artifact.identity.server_name,
        "tool_name": artifact.identity.tool_name,
        "request_id": artifact.request_id,
        "session_key": artifact.identity.session_key,
        "turn_id": artifact.turn_id,
        "tool_call_id": artifact.tool_call_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write(sidecar, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))


def _read_sidecar(final_path: Path) -> dict[str, Any] | None:
    """读 sidecar；缺失/损坏返回 None（损坏 sidecar = 失去归属证据 → foreign）。"""
    try:
        data = json.loads(_sidecar_path(final_path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write(target: Path, data: bytes) -> None:
    """原子写：.part → flush+fsync → os.replace（同卷原子改名）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    with open(part, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(part, target)


@dataclass(frozen=True)
class FinalPathPlan:
    """ownership 决策结果：最终路径 + 是否复用已落盘文件。"""

    final: Path
    reuse_existing: bool = False
    # reuse_existing=False 时 final 已可写（空闲名 / 本 sink 拥有的旧文件待替换 /
    # 唯一化后的新名）；.part → verify → os.replace 全部落到 final。
    owned_replace: bool = False


def _unique_name(downloads_dir: Path, name: str) -> str:
    """不同身份/外来文件撞名时的唯一化：``result.cube`` → ``result (1).cube``。"""
    stem, dot, ext = name.rpartition(".")
    base = stem if dot else name
    suffix = ("." + ext) if dot else ""
    index = 1
    while True:
        candidate = f"{base} ({index}){suffix}"
        if not (downloads_dir / candidate).exists():
            return candidate
        index += 1


def plan_final_path(
    downloads_dir: Path,
    identity: ArtifactIdentity,
    expected_size: int | None,
    expected_sha256: str | None,
) -> FinalPathPlan:
    """命名与 ownership 决策（v6.2 §4.4 树）。

    - final 不存在 → 空闲名（同名 zombie sidecar 无文件则顺手清掉）；
    - final 存在 + size/sha 匹配 → reuse（不重写）；无 sidecar 时由调用方补写；
    - final 存在 + 匹配 sidecar（本 sink 交付物，hash 不符 = 陈旧/上游变更）
      → 同 path 原子替换；
    - final 存在 + 无 sidecar / sidecar 归属他人 → **外来文件，绝不覆盖**，
      唯一化。
    """
    plain = _ensure_contained(downloads_dir, downloads_dir / identity.filename)

    if not plain.exists():
        # zombie sidecar（文件已被外部删除）不占名——清理后照常落盘。
        stale = _sidecar_path(plain)
        if stale.exists():
            try:
                stale.unlink()
            except OSError:
                pass
        return FinalPathPlan(final=plain)

    if expected_size is not None and expected_sha256 is not None:
        try:
            existing_size = plain.stat().st_size
            existing_sha = _file_sha256(plain)
        except OSError:
            existing_size, existing_sha = -1, ""
        if existing_size == expected_size and existing_sha == expected_sha256:
            # 内容完整一致 → 复用。sidecar 缺失由调用方按情况补写。
            return FinalPathPlan(final=plain, reuse_existing=True)

    sidecar = _read_sidecar(plain)
    owned = bool(sidecar and sidecar.get("artifact_key") == identity.artifact_key)
    if owned:
        # 本 sink 之前交付过同一身份 → 陈旧内容可替换（同 path，无 (1) 垃圾）。
        return FinalPathPlan(final=plain, owned_replace=True)

    # 外来文件（用户/agent 自放）或他身份占用 → 唯一名，绝不覆盖。
    return FinalPathPlan(final=_ensure_contained(
        downloads_dir, downloads_dir / _unique_name(downloads_dir, identity.filename)
    ))


# ── 响应适配层（样例冻结边界：真实字段命名只改这里）────────────────────────

_SIZE_KEYS = ("size_bytes", "size", "byte_size")
_SHA_KEYS = ("sha256", "sha", "hash")
_B64_KEYS = ("content_base64", "base64", "data_b64")


def _first_key(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _as_size(value: Any) -> int | None:
    """容忍 int / 数字字符串 / 浮点形态的 size 字段；畸形返回 None（由校验兜底）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _looks_like_artifact_dict(payload: Any) -> bool:
    return isinstance(payload, dict) and any(k in payload for k in _B64_KEYS)


def _looks_like_error_dict(payload: dict[str, Any]) -> bool:
    ok = _first_key(payload, ("success", "ok"))
    if isinstance(ok, bool) and not ok:
        return True
    return _first_key(payload, ("error", "message", "reason")) is not None


def _normalize_artifact_dict(
    payload: dict[str, Any],
    *,
    raw_source: Literal["structuredContent", "content"],
) -> ParsedDownloadResponse:
    """把单包 artifact/错误 dict 归一到 ParsedDownloadResponse。

    ``success=false`` / ``ok=false`` 显式失败 → is_explicit_error（含错误文本，
    截断 200 字符防内容回传）。其余字段缺失/畸形由 materialize 的 fail-closed
    规则兜底（C 类：success=true 缺内容 = DOWNLOAD_PROTOCOL_ERROR）。
    """
    success_flag = _first_key(payload, ("success", "ok"))
    if isinstance(success_flag, bool) and not success_flag:
        err = _first_key(payload, ("error", "message", "reason"))
        text = str(err)[:200] if err is not None else None
        return ParsedDownloadResponse(
            success=False, is_explicit_error=True,
            name=None, size_bytes=None, sha256=None, request_id=None,
            chunks=(), raw_source=raw_source, error_text=text,
        )
    b64 = _first_key(payload, _B64_KEYS)
    name = _first_key(payload, ("name", "suggested_filename", "filename", "file_name"))
    size = _as_size(_first_key(payload, _SIZE_KEYS))
    sha = _first_key(payload, _SHA_KEYS)
    request_id = _first_key(payload, ("request_id", "requestId", "transfer_id", "transferId"))
    chunks: tuple[ParsedChunk, ...] = ()
    if isinstance(b64, str) and b64:
        # 缺 content_base64（success=true 但无内容）→ chunks 留空，由 materialize
        # 的 C 类规则判 DOWNLOAD_PROTOCOL_ERROR——绝不把 None 当 "None" 解码。
        chunks = (ParsedChunk(chunk_index=0, total_chunks=1, content_base64=b64),)
    return ParsedDownloadResponse(
        success=True,
        is_explicit_error=False,
        name=str(name) if name is not None else None,
        size_bytes=size,
        sha256=str(sha).lower() if sha is not None else None,
        request_id=str(request_id) if request_id is not None else None,
        chunks=chunks,
        raw_source=raw_source,
    )


def _parse_json_payload(raw: str) -> ParsedDownloadResponse | None:
    """文本 JSON → ParsedDownloadResponse；不可解析返回 None（不抛——由调用方
    决定是协议错误还是 fallback 到另一个输入源）。"""
    if not raw or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if _looks_like_artifact_dict(payload):
        return _normalize_artifact_dict(payload, raw_source="content")
    if isinstance(payload, dict):
        # 显式错误 dict（无 content 但有 error/success=false）
        err = _first_key(payload, ("error", "message", "reason"))
        ok = _first_key(payload, ("success", "ok"))
        if err is not None or (isinstance(ok, bool) and not ok):
            return ParsedDownloadResponse(
                success=False, is_explicit_error=True,
                name=None, size_bytes=None, sha256=None, request_id=None,
                chunks=(), raw_source="content",
                error_text=str(err)[:200] if err is not None else None,
            )
    return None


def _text_from_blocks(blocks: Any) -> list[str]:
    """把 CallToolResult.content 的 TextContent 块提成文本列表（保块序）。"""
    out: list[str] = []
    for block in blocks or []:
        text = getattr(block, "text", None)
        if text is not None:
            out.append(text)
    return out


def parse_mcp_result(result: Any) -> ParsedDownloadResponse:
    """双源适配入口（v6.2 R2/§0.1-5）。

    顺序：① ``isError``（SDK 规范错误位）→ DOWNLOAD_SERVER_ERROR 语义；
    ② ``structuredContent`` 可解析出 artifact/error → canonical；
    ③ ``content`` TextContent fallback（可跨块拼接后整体 JSON 解析）。

    双源都解析出语义但**矛盾**（一成功一失败/描述不同）→ DownloadProtocolError
    ——双源矛盾时宁可不交付，绝不猜哪个是真的（P0 fail-closed）。
    """
    # ① isError —— 先于任何文本解析（服务端显式报错的权威信号）。
    if bool(getattr(result, "isError", False)):
        texts = _text_from_blocks(getattr(result, "content", None))
        text = "；".join(t for t in texts if t.strip())[:200] or None
        return ParsedDownloadResponse(
            success=False, is_explicit_error=True,
            name=None, size_bytes=None, sha256=None, request_id=None,
            chunks=(), raw_source="content", error_text=text,
        )

    # ② structuredContent canonical。
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # 双源都解析出语义但矛盾（一成功一失败/缺内容）→ fail-closed。
        texts = _text_from_blocks(getattr(result, "content", None))
        parsed_content = _parse_json_payload("\n".join(texts)) if texts else None

        if _looks_like_artifact_dict(structured) or _looks_like_error_dict(structured):
            parsed_structured = _normalize_artifact_dict(
                structured, raw_source="structuredContent"
            )
            if (
                parsed_content is not None
                and parsed_content.is_explicit_error != parsed_structured.is_explicit_error
            ):
                raise DownloadProtocolError(
                    "下载失败：structuredContent 与 content 语义矛盾（一成功一失败），"
                    "已按协议错误拒绝。文件未交付。"
                )
            return parsed_structured

    # ③ content fallback。
    texts = _text_from_blocks(getattr(result, "content", None))
    if not texts:
        raise DownloadProtocolError(
            "下载失败：响应既无 structuredContent 也无文本内容。文件未交付。"
        )
    joined = "\n".join(texts)
    parsed = _parse_json_payload(joined)
    if parsed is not None:
        return parsed
    # 跨块整体不可解析 → 逐块找 artifact JSON（块序语义待真实样例冻结）。
    for block_text in texts:
        per_block = _parse_json_payload(block_text)
        if per_block is not None:
            return per_block
    raise DownloadProtocolError(
        "下载失败：响应不是可识别的下载契约（缺文件名/大小/哈希/内容字段）。"
        "文件未交付。"
    )


# ── 落盘核心（单包路径先行；分片 assembler C3 接入）──────────────────────


class DownloadSink:
    """Artifact materialization 层：契约 → 校验 → 原子落盘 → 摘要。

    只负责 ``protocol -> artifact``；MCP call/billing/progress/timeout/LLM
    消息编排都在 wrapper 侧。wrapper 对每个下载类工具持有一个 sink（按
    base_workspace 构造，会话目录每次 execute 解析）。
    """

    def __init__(self, base_workspace: Path):
        self._base_workspace = Path(base_workspace)

    async def materialize(
        self,
        *,
        result: Any,
        session_key: str,
        server_name: str,
        tool_name: str,
        request_kwargs: dict[str, Any],
        turn_id: str,
        tool_call_id: str,
    ) -> DownloadArtifact:
        """单包 artifact 落盘（async 壳：文件 IO 放线程池，不阻塞事件循环）。"""
        return await asyncio.to_thread(
            self._materialize_sync,
            result=result,
            session_key=session_key,
            server_name=server_name,
            tool_name=tool_name,
            request_kwargs=request_kwargs,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
        )

    def _materialize_sync(
        self,
        *,
        result: Any,
        session_key: str,
        server_name: str,
        tool_name: str,
        request_kwargs: dict[str, Any],
        turn_id: str,
        tool_call_id: str,
    ) -> DownloadArtifact:
        try:
            return self._materialize_inner(
                result=result, session_key=session_key,
                server_name=server_name, tool_name=tool_name,
                request_kwargs=request_kwargs,
                turn_id=turn_id, tool_call_id=tool_call_id,
            )
        except DownloadError:
            raise  # 协议/校验类错误原样上抛（wrapper 消化为 JSON）
        except OSError as exc:
            # 目录创建/读写/fsync/rename/sidecar 等本地 IO 失败统一归 IO 错误。
            raise DownloadIoError(
                "下载失败：本地写入失败（磁盘/权限）。文件未交付，请重试。"
            ) from exc

    def _materialize_inner(
        self,
        *,
        result: Any,
        session_key: str,
        server_name: str,
        tool_name: str,
        request_kwargs: dict[str, Any],
        turn_id: str,
        tool_call_id: str,
    ) -> DownloadArtifact:
        downloads_dir = resolve_downloads_dir(self._base_workspace, session_key)
        downloads_dir.mkdir(parents=True, exist_ok=True)

        parsed = parse_mcp_result(result)
        if parsed.is_explicit_error:
            server_text = parsed.error_text or ""
            msg = (
                "下载失败：服务端明确返回错误。"
                + (f"原因：{server_text}。" if server_text else "")
                + "请检查远端文件后重新调用下载工具。"
            )
            raise DownloadServerError(msg)

        if not parsed.chunks:
            # 看似成功但没有内容 → 协议违例，绝不退回普通文本（C 类）。
            raise DownloadProtocolError(
                "下载失败：服务端标记成功但未返回文件内容。"
                "不得据此判断文件已交付。请重新下载。"
            )
        if len(parsed.chunks) != 1:
            # 分片（total_chunks > 1 / chunk_index > 0）由 C3 assembler 处理；
            # 未实现前遇到即 fail-closed，绝不静默取第一片。
            raise DownloadProtocolError(
                "下载失败：服务端返回了分片响应，当前通道暂不支持分片重组。"
                "文件未交付。"
            )

        chunk = parsed.chunks[0]
        if len(chunk.content_base64) > MAX_CHUNK_BASE64_CHARS:
            raise DownloadLimitError(
                "下载失败：单次响应超过客户端下载限制"
                f"（>{MAX_CHUNK_BASE64_CHARS} base64 字符）。"
                "文件内容未交付，请使用分片下载方式重新请求。"
            )

        filename = parsed.name or sanitize_name(
            str(request_kwargs.get("name") or request_kwargs.get("filename") or "download.bin")
        )
        filename = sanitize_name(filename)

        identity = ArtifactIdentity(
            session_key=session_key,
            server_name=server_name,
            tool_name=tool_name,
            source_args_hash=_canonical_args_hash(request_kwargs),
            filename=filename,
        )

        # decode（validate=True：非法输入 fail-closed，绝不宽容吞掉）
        try:
            decoded = base64.b64decode(chunk.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise DownloadBase64Error() from exc

        if len(decoded) > MAX_ARTIFACT_BYTES:
            raise DownloadLimitError(
                "下载失败：文件超过客户端大小上限（256 MiB）。文件未交付。"
            )

        # size/sha 是校验谓词不是身份键（v6.2 §4.3）——finalize 前求值。
        # 两者皆缺 = 无可交叉校验的完整性子段 → fail-closed（不信任裸内容）。
        if parsed.size_bytes is None and parsed.sha256 is None:
            raise DownloadProtocolError(
                "下载失败：响应缺少完整性字段（size_bytes/sha256 均缺失），"
                "无法校验。文件未交付。"
            )
        if parsed.size_bytes is not None and len(decoded) != parsed.size_bytes:
            raise DownloadSizeMismatchError(
                "下载失败：文件完整性校验未通过（大小不一致）。"
                f"期望 {parsed.size_bytes} bytes，实际 {len(decoded)} bytes。"
                "文件未交付，请重新下载。"
            )
        actual_sha = hashlib.sha256(decoded).hexdigest()
        if parsed.sha256 and actual_sha != parsed.sha256:
            raise DownloadSha256MismatchError(
                "下载失败：文件完整性校验未通过（SHA-256 不一致）。"
                f"期望 {parsed.sha256[:12]}... 实际 {actual_sha[:12]}...。"
                "文件未交付，请重新下载。"
            )

        # ownership + 命名决策 → 写盘。
        plan = plan_final_path(
            downloads_dir, identity,
            expected_size=parsed.size_bytes or len(decoded),
            expected_sha256=actual_sha,
        )
        if plan.reuse_existing:
            if _read_sidecar(plan.final) is None:
                # 内容一致复用 + sidecar 缺失：补写（provenance=本次断言）。
                _write_sidecar(plan.final, DownloadArtifact(
                    identity=identity, path=plan.final,
                    size_bytes=len(decoded), sha256=actual_sha,
                    request_id=parsed.request_id,
                    turn_id=turn_id, tool_call_id=tool_call_id,
                ))
            return DownloadArtifact(
                identity=identity, path=plan.final,
                size_bytes=len(decoded), sha256=actual_sha,
                request_id=parsed.request_id,
                turn_id=turn_id, tool_call_id=tool_call_id,
            )

        _atomic_write(plan.final, decoded)

        artifact = DownloadArtifact(
            identity=identity, path=plan.final,
            size_bytes=len(decoded), sha256=actual_sha,
            request_id=parsed.request_id,
            turn_id=turn_id, tool_call_id=tool_call_id,
        )
        # 文件已交付；sidecar 失败不撤销 artifact（降级为无归属，不抛）。
        try:
            _write_sidecar(plan.final, artifact)
        except OSError:
            pass
        return artifact
