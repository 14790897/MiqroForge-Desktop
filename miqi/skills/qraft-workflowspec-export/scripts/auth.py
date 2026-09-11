#!/usr/bin/env python3
"""Qraft OAuth2 凭据解析（#674 功能描述 3）。

优先读取 MiQroForge Desktop 登录态提供的 token 文件（#747 token 通道）：
    <workspace>/.qraft/token.json  →  {"accessToken": ..., "expiresAt": <epoch 毫秒>}
存在且未临期（expiresAt - now > 5min）直接使用；否则按序降级：

1. QRAFT_ACCESS_TOKEN 环境变量（无有效期，视为可用）；
2. QRAFT_PHONE + QRAFT_PASSWORD 环境变量 → 自管凭据流程兜底
   （RSA 加密登录 → authorize → doConfirm → 换 token，测试阶段）；
3. 都没有 → 提示用户到「设置 → MiQroForge 平台」完成登录（#728 内置登录）。

凭据脱敏：stderr 日志不写入任何凭据（含片段、密码、手机号）。
注意：stdout（含 --json 的 accessToken 字段）返回完整 token 供脚本组合消费，
调用方不得把 stdout 写入日志或会话记录。

Usage:
    python auth.py token [--base-url URL] [--token-file PATH] [--json]

Exit codes:
    0  拿到可用 access token
    1  未登录 / 授权流程失败（stderr 给出分类指引）
    2  参数/环境配置错误
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx


def _ensure_utf8_streams() -> None:
    """Windows 默认 stdout/stderr 编码为 cp1252 等本地代码页，打印中文会
    UnicodeEncodeError 崩溃。统一重配置为 UTF-8（Python 3.7+）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

# 到期前多少毫秒视为"临期"（与 docs/frontend/qraft-oauth2-login.md 第 6 节一致）
EXPIRY_SKEW_MS = 5 * 60 * 1000
DEFAULT_BASE_URL = "https://test.forge.miqroera.com/api"
DEFAULT_TIMEOUT_S = 15
RETRIES = 3
RETRY_BACKOFF_S = [0.5, 1.0, 2.0]

PUBLIC_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN PUBLIC KEY-----[\sA-Za-z0-9+/=]+-----END PUBLIC KEY-----"
)


def mask_secret(value: str | None, head: int = 4, tail: int = 4) -> str:
    if not value:
        return "(empty)"
    if len(value) <= head + tail:
        return "*" * len(value)
    suffix = value[-tail:] if tail > 0 else ""
    return f"{value[:head]}…{suffix}"


def log(msg: str) -> None:
    print(f"[qraft-auth] {msg}", file=sys.stderr)


def retry_http(fn):
    """对瞬时网络错误重试（连接失败/超时/空响应），业务 4xx/5xx 不重试。

    重试耗尽抛 AuthError("NETWORK_UNREACHABLE")，保证 CLI 侧始终拿到
    分类错误而非裸 TransportError/traceback。
    """
    last_err: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            resp = fn()
            # 实测出口线路偶发抖动（curl 表现为 HTTP 000）
            if resp is None or resp.status_code == 0:
                raise httpx.TransportError("empty response (HTTP 000)")
            return resp
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_err = exc
            if attempt < RETRIES:
                backoff = RETRY_BACKOFF_S[attempt] if attempt < len(RETRY_BACKOFF_S) else 2.0
                log(f"请求失败（{exc.__class__.__name__}），{backoff}s 后第 {attempt + 1} 次重试")
                time.sleep(backoff)
    raise AuthError(
        "NETWORK_UNREACHABLE",
        f"网络请求失败（重试 {RETRIES} 次后仍失败）：{last_err}",
    )


class AuthError(Exception):
    """带稳定错误码的凭据错误，供 SKILL/agent 分类展示。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── token 文件（#747 通道） ────────────────────────────────────────────


def _default_home_workspace() -> Path | None:
    """经 miqi.paths 解析默认 home 的 workspace（遵守 MIQI_HOME 策略，
    不直接构造 ~/.miqi 路径）。skill 脚本在 MiQi 环境内运行时可用；
    独立运行时 ImportError 则跳过该候选。"""
    try:
        from miqi.paths import get_miqi_home

        return get_miqi_home() / "workspace"
    except ImportError:
        return None


def candidate_token_files() -> list[Path]:
    """按优先级返回可能存在的 token 文件位置（arg/env 由调用方先行处理）。

    覆盖三种运行形态：沙箱 cwd、MIQI_HOME 注入、默认 home 的 workspace
    （桌面端 #747 默认写入位置，经 miqi.paths 解析）。
    """
    candidates: list[Path] = [Path.cwd() / ".qraft" / "token.json"]
    miqi_home = os.environ.get("MIQI_HOME", "").strip()
    if miqi_home:
        candidates.append(Path(miqi_home) / "workspace" / ".qraft" / "token.json")
    default_ws = _default_home_workspace()
    if default_ws:
        candidates.append(default_ws / ".qraft" / "token.json")
    return candidates


def read_token_file(path: Path) -> dict[str, Any] | None:
    """读取 token 文件；格式非法/过期返回 None。"""
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        tok = str(data.get("accessToken", "")).strip()
        expires_at = data.get("expiresAt")
        if not tok:
            return None
        if not isinstance(expires_at, (int, float)) or expires_at - time.time() * 1000 <= EXPIRY_SKEW_MS:
            log("token 文件已过期或缺少 expiresAt（状态记录，值不写入日志）")
            return None
        return {"accessToken": tok, "expiresAt": int(expires_at)}
    except (OSError, ValueError) as exc:
        log(f"token 文件读取失败（{path}）：{exc}")
        return None


# ── 自管凭据兜底（RSA 登录 → 授权 → 换 token） ─────────────────────────


def _origin(base_url: str) -> str:
    u = httpx.URL(base_url)
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{u.host}{port}"


def fetch_public_key(client: httpx.Client, base_url: str) -> str:
    """从登录页 era-index-*.js bundle 动态提取 RSA 公钥（避免硬编码随发版失效）。"""
    origin = _origin(base_url)
    login_page = retry_http(
        lambda: client.get(f"{origin}/login", timeout=DEFAULT_TIMEOUT_S)
    )
    if login_page.status_code == 403:
        raise AuthError("IP_NOT_WHITELISTED", "出口 IP 未加白，请联系 MiQroForge 管理员")
    bundle_urls = re.findall(
        r'<script\b[^>]*\bsrc=["\']([^"\']+)["\'][^>]*>', login_page.text
    )
    bundle_urls = [u for u in bundle_urls if re.search(r"era-index-[\w.-]*\.js$", u.split("?")[0].split("/")[-1], re.I)]
    for rel in bundle_urls:
        url = urljoin(f"{origin}/", rel)
        bundle = retry_http(lambda: client.get(url, timeout=DEFAULT_TIMEOUT_S))
        # bundle 中公钥可能以字符串字面量形式存在（换行转义为 \n），先归一化
        normalized = bundle.text.replace("\\n", "\n").replace("\\r", "\r")
        match = PUBLIC_KEY_BLOCK_RE.search(normalized)
        if match:
            log("RSA 公钥提取成功（era-index bundle）")
            return match.group(0)
    raise AuthError("PUBLIC_KEY_EXTRACT_FAILED", "登录页 bundle 中未找到 RSA 公钥")


def rsa_encrypt(password: str, public_key_pem: str) -> str:
    """PKCS#1 v1.5 填充 RSA 加密（与 JSEncrypt 默认一致），结果 Base64。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    encrypted = key.encrypt(password.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode("ascii")


def platform_login(
    client: httpx.Client, base_url: str, phone: str, password: str
) -> str:
    """POST /portal/auth/login → Authorization cookie（OAuth 依赖 cookie）。"""
    public_key = fetch_public_key(client, base_url)
    encrypted = rsa_encrypt(password, public_key)
    resp = retry_http(
        lambda: client.post(
            f"{base_url}/portal/auth/login",
            json={"phone": phone, "password": encrypted},
            timeout=DEFAULT_TIMEOUT_S,
        )
    )
    if resp.status_code == 403:
        raise AuthError("IP_NOT_WHITELISTED", "出口 IP 未加白，请联系 MiQroForge 管理员")
    cookie = resp.headers.get("set-cookie", "")
    match = re.search(r"Authorization=([^;]+)", cookie)
    if not match:
        data = _try_json(resp)
        message = data.get("message") or data.get("msg") or "未知错误"
        raise AuthError("LOGIN_FAILED", f"登录失败：{message}")
    log("平台登录成功（手机号已脱敏，不写入日志）")
    return match.group(1).strip()


def authorize_flow(
    client: httpx.Client, base_url: str, cookie: str, redirect_uri: str
) -> str:
    """GET authorize →（未确认时）doConfirm → 再 GET authorize 取 302 Location 中的 code。

    实测要点：不传 state（传了报"多次请求的 state 不可重复"）；
    redirect_uri 必填；授权页 accept=1 按钮修复前必须走 doConfirm。
    """
    params = (
        "response_type=code"
        f"&client_id={_urlenc(os.environ.get('QRAFT_CLIENT_ID', 'miqi'))}"
        "&scope=openid,userinfo,oidc"
        f"&redirect_uri={_urlenc(redirect_uri)}"
    )
    headers = {"Cookie": f"Authorization={cookie}"}
    authorize_url = f"{base_url}/oauth2/authorize?{params}"

    resp = retry_http(lambda: client.get(authorize_url, headers=headers, timeout=DEFAULT_TIMEOUT_S))
    if resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("location", "")
        if re.search(r"/login(?:\?|$)", location):
            raise AuthError("SESSION_EXPIRED", "登录态已失效，请重新登录")
        code = _code_from_url(location)
        if code:
            return code
    elif resp.status_code == 200:
        do_confirm = (
            f"{base_url}/oauth2/doConfirm?client_id={_urlenc(os.environ.get('QRAFT_CLIENT_ID', 'miqi'))}"
            f"&scope=openid,userinfo,oidc&redirect_uri={_urlenc(redirect_uri)}"
        )
        retry_http(lambda: client.post(do_confirm, headers=headers, timeout=DEFAULT_TIMEOUT_S))
        resp = retry_http(lambda: client.get(authorize_url, headers=headers, timeout=DEFAULT_TIMEOUT_S))
        location = resp.headers.get("location", "")
        code = _code_from_url(location)
        if code:
            return code
    raise AuthError("AUTHORIZE_FAILED", "授权流程失败：未能获取授权码")


def _urlenc(value: str) -> str:
    import urllib.parse

    return urllib.parse.quote(value, safe="")


def _code_from_url(location: str) -> str | None:
    try:
        import urllib.parse

        parsed = urllib.parse.urlparse(location)
        return urllib.parse.parse_qs(parsed.query).get("code", [None])[0]
    except (ValueError, AttributeError):
        return None


def _try_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except ValueError:
        return {}


def exchange_token(
    client: httpx.Client, base_url: str, code: str, redirect_uri: str
) -> dict[str, Any]:
    # 测试阶段开箱即用：硬编码默认值，QRAFT_CLIENT_SECRET 环境变量可覆盖
    #（转正式环境接入前移除默认值，改回显式要求）。
    client_secret = os.environ.get("QRAFT_CLIENT_SECRET", "miqi123456").strip()
    resp = retry_http(
        lambda: client.post(
            f"{base_url}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": os.environ.get("QRAFT_CLIENT_ID", "miqi"),
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            timeout=DEFAULT_TIMEOUT_S,
        )
    )
    data = _try_json(resp)
    if data.get("code") != 200 or not data.get("access_token"):
        message = data.get("message") or data.get("msg") or f"HTTP {resp.status_code}"
        raise AuthError("TOKEN_EXCHANGE_FAILED", f"换取 token 失败：{message}")
    expires_in = int(data.get("expires_in") or 7199)
    return {
        "accessToken": str(data["access_token"]),
        "expiresAt": int(time.time() * 1000) + expires_in * 1000,
    }


def self_managed_login(base_url: str, phone: str, password: str) -> dict[str, Any]:
    with httpx.Client(follow_redirects=False) as client:
        cookie = platform_login(client, base_url, phone, password)
        redirect_uri = os.environ.get("QRAFT_REDIRECT_URI", "http://localhost:38000/callback")
        code = authorize_flow(client, base_url, cookie, redirect_uri)
        log("获取授权码成功（code 已脱敏，不写入日志）")
        return exchange_token(client, base_url, code, redirect_uri)


# ── 主入口：按优先级解析可用 token ──────────────────────────────────────


def resolve_token(base_url: str, token_file_arg: str | None) -> dict[str, Any]:
    """返回 {"accessToken", "expiresAt"?, "source"}；全不可用抛 AuthError。"""
    if token_file_arg:
        candidates = [Path(token_file_arg)]
    elif os.environ.get("QRAFT_TOKEN_FILE"):
        candidates = [Path(os.environ["QRAFT_TOKEN_FILE"])]
    else:
        candidates = candidate_token_files()

    for path in candidates:
        data = read_token_file(path)
        if data:
            return {**data, "source": f"token_file:{path}"}

    env_tok = os.environ.get("QRAFT_ACCESS_TOKEN", "").strip()
    if env_tok:
        log("使用 QRAFT_ACCESS_TOKEN 环境变量（无有效期，视为可用）")
        return {"accessToken": env_tok, "source": "env:QRAFT_ACCESS_TOKEN"}

    phone = os.environ.get("QRAFT_PHONE", "").strip()
    password = os.environ.get("QRAFT_PASSWORD", "")
    if phone and password:
        log("token 文件不可用，使用环境变量凭据走自管登录兜底")
        data = self_managed_login(base_url, phone, password)
        return {**data, "source": "self_login"}

    raise AuthError(
        "NOT_LOGGED_IN",
        "未找到可用的 MiQroForge 登录态：请到 MiQroForge 设置 → MiQroForge 平台 完成登录"
        "（登录后自动生成 token 文件），或配置 QRAFT_ACCESS_TOKEN / QRAFT_PHONE+QRAFT_PASSWORD 环境变量",
    )


def main() -> int:
    # 先于 parse_args 重配置编码，避免 Windows cp1252 下 argparse 的中文
    # help/错误输出在 parse_args 内部崩溃。
    _ensure_utf8_streams()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="token", choices=["token"], help="取 token（默认）")
    parser.add_argument("--base-url", default=os.environ.get("QRAFT_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--token-file", default=None, help="token 文件路径（默认自动探测 workspace/.qraft/token.json）")
    parser.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    parser.add_argument(
        "--no-token",
        action="store_true",
        help="仅输出登录态检查结果（{ok, source, expiresAt}），不含 accessToken —— "
        "供 agent/SKILL 检查登录状态时使用，避免完整 token 进入工具输出与日志",
    )
    args = parser.parse_args()

    try:
        data = resolve_token(args.base_url, args.token_file)
    except AuthError as exc:
        if args.json:
            print(json.dumps({"ok": False, "code": exc.code, "message": exc.message}, ensure_ascii=False))
        else:
            print(f"[{exc.code}] {exc.message}", file=sys.stderr)
        return 1

    tok = str(data["accessToken"])
    if args.json:
        payload = {
            "ok": True,
            "expiresAt": data.get("expiresAt"),
            "source": data.get("source"),
            "baseUrl": args.base_url,
        }
        if not args.no_token:
            # 完整 token 仅供脚本组合消费；agent/SKILL 用 --no-token 检查状态，
            # 避免凭据进入工具输出与日志。
            payload["accessToken"] = tok
        print(json.dumps(payload, ensure_ascii=False))
    elif args.no_token:
        print(json.dumps({"ok": True, "source": data.get("source")}, ensure_ascii=False))
    else:
        print(tok)
    log(f"token 就绪（来源 {data.get('source')}，值不写入日志）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
