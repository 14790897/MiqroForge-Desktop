"""Platform AI gateway — creds + endpoint constants for issue #922.

Qraft OAuth 登录后,Electron 主进程把 /oauth2/userinfo 下发的 AI 网关信息
(encryptedApiKey / aiGatewayStatus / configVersion)随 token 文件
`<workspace>/.qraft/token.json` 的 `aiGateway` 块同步到 Python。本模块负责
读取该块并判定是否应把 deepseek 模型调用路由到平台 AI 网关。

网关契约定稿于 apps/desktop/src/main/qraft/live.ai-gateway.test.ts:
  POST {origin}{GATEWAY_PREFIX}/v1/messages（origin 见 gateway_origin()）
  header X-Api-Key: <encryptedApiKey>
  Anthropic Messages 兼容(复用 AnthropicProvider)。

放 providers 下而非 kun_runtime:providers/factory 构造 provider 时读取,
避免 providers → kun_runtime 反向依赖。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

# 网关地址经环境变量注入。生产必须为 https 域名：encryptedApiKey 实为
# 明文腾讯云密钥（#923 实测），明文通道禁止承载密钥与聊天内容。
# 默认值仅为 test env 实测 IP（#923：平台尚未提供带证书的真实网关域名，
# https 裸 IP 证书不可用；生产域名 forge.miqroera.com 公网 NXDOMAIN）。
# 生产上线后平台经 QRAFT_GATEWAY_BASE 下发 https 域名，勿再回退 http。
_DEFAULT_TEST_ORIGIN = "http://118.25.115.164"
GATEWAY_PREFIX = os.environ.get("QRAFT_GATEWAY_PREFIX", "/miqroera-deepseek")
# 唯一经过实测的网关模型（issue #922；平台模型清单随 #893 后续下发）。
GATEWAY_MODEL = "deepseek-v4-flash"


def gateway_origin() -> str | None:
    """网关 origin（末尾斜杠已归一化）；配置非法返回 None，调用方回退直连。

    显式配置的 QRAFT_GATEWAY_BASE 必须为 https:// 开头，否则拒绝接受
    （CWE-319：明文通道不能承载密钥）。未配置时回退 test env 实测
    http IP（见模块注释），使测试环境仍可实测网关。
    """
    raw = os.environ.get("QRAFT_GATEWAY_BASE")
    if raw is None:
        return _DEFAULT_TEST_ORIGIN
    origin = raw.rstrip("/")
    if not origin.startswith("https://"):
        logger.warning(
            "QRAFT_GATEWAY_BASE 必须为 https:// 地址（明文通道禁止承载密钥），已忽略并回退直连：{}",
            raw,
        )
        return None
    return origin


def gateway_token_file(config: Any) -> Path:
    """Python 侧 token 文件路径(与 services._build_billing 同源)。"""
    return Path(config.workspace_path) / ".qraft" / "token.json"


def read_gateway_creds(token_file: Path) -> dict[str, Any] | None:
    """读取可用网关凭据:aiGateway 块存在、encryptedApiKey 非空、status=='active'。

    任一前提不满足(未登录/文件缺失损坏/网关未 active/无密钥)返回 None → 保持
    直连路径。损坏/缺失按"无凭据"静默处理,不抛给 provider 构造链。
    """
    try:
        if not token_file.is_file():
            return None
        data = json.loads(token_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    gateway = data.get("aiGateway")
    if not isinstance(gateway, dict):
        return None
    key = gateway.get("encryptedApiKey")
    if not (isinstance(key, str) and key) or gateway.get("status") != "active":
        return None
    return dict(gateway)
