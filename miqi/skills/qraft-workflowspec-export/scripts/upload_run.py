#!/usr/bin/env python3
"""Qraft dataUpload 上传封装（#674 功能描述 4/5/6）。

用法：
    python upload_run.py <workflow.json> [--base-url URL] [--token-file PATH]
        [--retries N] [--json]

流程：
    1. 前置校验：文件存在、.json、≤5MB、JSON 可解析、含 document_kind 字段；
    2. 凭据：复用 auth.py 的 token 解析（token 文件优先 → env → 自管登录兜底）；
    3. 上传：POST /api/oauth2/dataUpload（Bearer + multipart），
       网络类错误自动重试（默认 3 次退避）；
    4. 结果分类：403 → IP 白名单；401 → token 过期；400 → 参数/业务错误；
       200 → 成功（实测 body 为纯文本 ok）；
    5. 输出：脱敏后的响应与下一步提示（--json 机器可读）。

Exit codes：0 上传成功；1 上传失败（分类错误）；2 前置校验失败/凭据不可用。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx


def _ensure_utf8_streams() -> None:
    """Windows 默认 stdout/stderr 编码为 cp1252 等本地代码页，打印中文会
    UnicodeEncodeError 崩溃。统一重配置为 UTF-8（Python 3.7+）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth import AuthError, resolve_token  # noqa: E402

DEFAULT_BASE_URL = os.environ.get("QRAFT_BASE_URL", "https://test.forge.miqroera.com/api")
MAX_FILE_BYTES = 5 * 1024 * 1024
DEFAULT_RETRIES = 3
RETRY_BACKOFF_S = [0.5, 1.0, 2.0]
ALLOWED_KINDS = ("workflow_definition", "workflow_run")


def log(msg: str) -> None:
    print(f"[qraft-upload] {msg}", file=sys.stderr)


class UploadError(Exception):
    def __init__(self, code: str, message: str, raw: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.raw = raw


def pre_check(file_path: Path) -> dict[str, Any]:
    """前置校验。返回解析后的 JSON；失败抛 UploadError（exit 2）。"""
    if not file_path.is_file():
        raise UploadError("PRECHECK_FAILED", f"文件不存在：{file_path}")
    if file_path.suffix.lower() != ".json":
        raise UploadError("PRECHECK_FAILED", "仅支持 .json 文件")
    size = file_path.stat().st_size
    if size > MAX_FILE_BYTES:
        limit_mb = MAX_FILE_BYTES / 1024 / 1024
        raise UploadError("PRECHECK_FAILED", f"文件 {size} 字节，超过 {limit_mb:.0f}MB 上限")
    try:
        doc = json.loads(file_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise UploadError("PRECHECK_FAILED", f"文件不是合法 JSON：{exc}") from exc
    kind = doc.get("document_kind")
    if kind not in ALLOWED_KINDS:
        raise UploadError(
            "PRECHECK_FAILED",
            f"缺少合法的 document_kind 字段（期望 workflow_definition / workflow_run，实际 {kind!r}）",
        )
    return doc


def classify_response(resp: httpx.Response, body_text: str) -> tuple[str, str]:
    """按实测常见问题分类。返回 (code, 用户可读提示)。"""
    status = resp.status_code
    if status == 403:
        return "IP_NOT_WHITELISTED", "上传失败（HTTP 403）：出口 IP 未加白，请联系 MiQroForge 管理员"
    if status == 401:
        return "TOKEN_EXPIRED", "上传失败（HTTP 401）：access_token 已失效，请到 设置 → MiQroForge 平台 重新登录"
    if 400 <= status < 500:
        message = ""
        try:
            data = resp.json()
            message = str(data.get("message") or data.get("msg") or "")
        except ValueError:
            message = body_text[:200]
        return "BAD_REQUEST", f"上传失败（HTTP {status}）：{message or '请求参数错误'}"
    if status >= 500:
        return "SERVER_ERROR", f"上传失败（HTTP {status}）：MiQroForge 服务端异常，请稍后重试"
    if 200 <= status < 300:
        # 实测成功时 body 为纯文本 ok；若返回 JSON 业务信封且 code != 200，
        # 是平台侧业务错误（如服务端数据库缺表），不能误报为成功。
        try:
            data = json.loads(body_text)
            if isinstance(data, dict) and data.get("code") not in (None, 200):
                inner = data.get("data")
                inner = inner if isinstance(inner, dict) else {}
                detail = inner.get("originalMessage") or inner.get("message") or data.get("msg") or ""
                return "SERVER_ERROR", f"上传失败：MiQroForge 返回业务错误（{detail or '未知错误'}），请联系 MiQroForge 管理员"
        except ValueError:
            pass
        return "OK", body_text.strip() or "ok"
    return "UNKNOWN", f"上传失败（HTTP {status}）：{body_text[:200]}"


def upload_file(
    base_url: str,
    file_path: Path,
    access_token: str,
    retries: int,
) -> tuple[str, str, int]:
    """执行上传。返回 (code, message, http_status)。网络错误重试。"""
    with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with file_path.open("rb") as fh:
                    resp = client.post(
                        f"{base_url}/oauth2/dataUpload",
                        headers={"Authorization": f"Bearer {access_token}"},
                        files={"file": (file_path.name, fh, "application/json")},
                    )
                if resp.status_code == 0:
                    raise httpx.TransportError("empty response (HTTP 000)")
                body_text = resp.text
                code, message = classify_response(resp, body_text)
                # dataUpload 为非幂等写入：收到任何 HTTP 响应即视为确定性结果，
                # 只对网络层失败（TransportError/Timeout，落在下方 except）重试，
                # 避免 5xx/业务错误重试造成重复落库。
                return code, message, resp.status_code
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_err = exc
                if attempt < retries:
                    backoff = RETRY_BACKOFF_S[attempt] if attempt < len(RETRY_BACKOFF_S) else 2.0
                    log(f"网络请求失败（{exc.__class__.__name__}），{backoff}s 后第 {attempt + 1} 次重试")
                    time.sleep(backoff)
        raise UploadError(
            "NETWORK_UNREACHABLE",
            f"网络请求失败（重试 {retries} 次后仍失败）：{last_err}",
        )


def main() -> int:
    # 先于 parse_args 重配置编码，避免 Windows cp1252 下 argparse 的中文
    # help/错误输出在 parse_args 内部崩溃。
    _ensure_utf8_streams()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="WorkflowRun/WorkflowDefinition JSON 文件")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token-file", default=None, help="token 文件路径（默认自动探测）")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    args = parser.parse_args()

    file_path = Path(args.file).resolve()

    try:
        doc = pre_check(file_path)
    except UploadError as exc:
        print(f"PRECHECK_FAILED: {exc.message}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "code": exc.code, "message": exc.message}, ensure_ascii=False))
        return 2

    try:
        cred = resolve_token(args.base_url, args.token_file)
    except AuthError as exc:
        print(f"[{exc.code}] {exc.message}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "code": exc.code, "message": exc.message}, ensure_ascii=False))
        return 2

    bearer = str(cred["accessToken"])
    log(
        f"开始上传 {file_path.name}（document_kind={doc.get('document_kind')}，"
        f"来源 {cred.get('source')}；token 已脱敏，不写入日志）"
    )

    try:
        code, message, http_status = upload_file(args.base_url, file_path, bearer, args.retries)
    except UploadError as exc:
        print(f"[{exc.code}] {exc.message}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "code": exc.code, "message": exc.message}, ensure_ascii=False))
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "ok": code == "OK",
                    "code": code,
                    "message": message,
                    "httpStatus": http_status,
                    "file": str(file_path),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(message)
    if code == "OK":
        log("上传成功，可在 MiQroForge 平台查看方案")
        return 0
    print(f"[{code}] {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
