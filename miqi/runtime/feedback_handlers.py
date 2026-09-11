"""Feedback handlers for AppServer dispatch.

Submits user feedback + collected logs to Feishu Bitable and stores
a local backup in memory/FEEDBACK.jsonl.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests
from loguru import logger

from miqi.config.schema import FeedbackConfig
from miqi.runtime.app_server import AppServerError

# Feishu Bitable payload limits.
MAX_LOG_BYTES = 196_608  # Bitable text-field limit
NOTICE_BUDGET = 300  # reserve bytes for skipped/unreadable notices and separators
# Per-cell cap with safety margin: JSON escaping of backslashes (Windows
# paths) and other special chars can inflate the payload by up to 2x.
MAX_CELL_BYTES = 88_000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_workspace_path() -> Path:
    """Resolve workspace path from bridge state config."""
    import miqi.bridge.server as bridge_module

    state = getattr(bridge_module, "_state", None)
    if state is None:
        raise AppServerError("Bridge state not available", code="INTERNAL")
    config = state.load_config()
    return config.workspace_path


def _get_feedback_file() -> Path:
    """Return path to local feedback backup JSONL file."""
    return _get_workspace_path() / "memory" / "FEEDBACK.jsonl"


def _ensure_memory_dir() -> None:
    """Ensure the memory directory exists."""
    memory_dir = _get_workspace_path() / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)


def _collect_all_logs(log_dir: Path, max_age_days: int = 7) -> str:
    """Read files under workspace/logs/ recursively, limited to those
    modified within *max_age_days*, and return concatenated text.

    Individual files are capped at 50k chars (tail end).  The combined payload
    is capped at 196,608 UTF-8 bytes to fit within Feishu Bitable
    text-field limits.  Newest files are preserved first.
    """
    if not log_dir.exists():
        return "[日志目录不存在]"

    _date_re = re.compile(r"(\d{4}-\d{2}-\d{2})")
    parts: list[str] = []
    recent_count = 0
    skipped_count = 0
    unreadable_count = 0

    def _file_sort_key(p: Path) -> float:
        m = _date_re.search(p.name)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y-%m-%d").timestamp()
            except ValueError:
                pass
        try:
            return os.path.getmtime(str(p))
        except OSError:
            return 0.0

    def _file_age_days(p: Path) -> float | None:
        m = _date_re.search(p.name)
        if m:
            try:
                file_date = date.fromisoformat(m.group(1))
                return (date.today() - file_date).days
            except ValueError:
                pass
        try:
            mtime = os.path.getmtime(str(p))
            return (time.time() - mtime) / 86400
        except OSError:
            return None

    for f in sorted(log_dir.rglob("*"), key=_file_sort_key, reverse=True):
        if not f.is_file():
            continue
        age = _file_age_days(f)
        if age is None:
            unreadable_count += 1
            continue
        if age > max_age_days:
            skipped_count += 1
            continue
        recent_count += 1
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            max_chars = 50_000
            if len(content) > max_chars:
                content = content[-max_chars:]
            rel = f.relative_to(log_dir)
            parts.append(f"=== {rel} ===\n{content}")
        except Exception as exc:
            parts.append(f"=== {f.name} === [读取失败: {exc}]")

    final_parts: list[str] = []
    total_bytes = NOTICE_BUDGET  # reserve for notices prepended later
    for part in parts:
        part_bytes = len(part.encode("utf-8"))
        if total_bytes + part_bytes > MAX_LOG_BYTES:
            remaining = MAX_LOG_BYTES - total_bytes
            marker = "\n...(截断: 超出总大小限制)"
            marker_bytes = len(marker.encode("utf-8"))
            if remaining > marker_bytes:
                tail = part.encode("utf-8")[:remaining - marker_bytes].decode("utf-8", errors="ignore")
                final_parts.append(tail + marker)
            break
        final_parts.append(part)
        total_bytes += part_bytes

    if not final_parts:
        return f"[最近{max_age_days}天无日志文件（跳过{skipped_count}个旧文件）]"

    if skipped_count:
        final_parts.insert(0, f"（跳过了 {skipped_count} 个超过 {max_age_days} 天的旧日志文件）\n")
    if unreadable_count:
        final_parts.insert(1 if skipped_count else 0, f"（{unreadable_count} 个文件无法读取修改时间）\n")

    combined = "\n\n".join(final_parts)

    return combined


def _collect_system_info() -> dict[str, str]:
    """Collect basic system / environment info."""
    info: dict[str, str] = {
        "os": platform.system(),
        "os_version": platform.release(),
        "machine": platform.machine(),
        "python_version": sys.version.split()[0],
    }
    # Try WSL check
    try:
        import os as _os
        import subprocess
        r = subprocess.run(
            ["wsl", "--list", "--verbose"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if _os.name == "nt" else 0,  # noqa: S603
        )
        info["wsl_status"] = r.stdout.strip() or "[未安装或无权限]"
    except Exception:
        info["wsl_status"] = "[检测失败]"
    return info


def _get_tenant_access_token(app_id: str, app_secret: str) -> str:
    """Obtain a Feishu tenant_access_token via app_id/app_secret."""
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    code = body.get("code", -1)
    if code != 0:
        msg = body.get("msg", "unknown error")
        raise AppServerError(
            f"获取飞书 tenant_access_token 失败: {msg} (code={code})",
            code="FEISHU_AUTH_ERROR",
        )
    token = body.get("tenant_access_token", "")
    if not token:
        raise AppServerError(
            "飞书返回的 tenant_access_token 为空", code="FEISHU_AUTH_ERROR",
        )
    return token


def _upload_attachment(
    token: str,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    parent_node: str,
) -> str:
    """Upload an attachment to Feishu Drive and return the file_token.

    Used for Bitable attachment fields: the record references the file_token
    rather than embedding the bytes inline.

    `parent_node` MUST be the target Bitable's app_token — empty strings
    cause the upload to fail before the record is created.
    """
    resp = requests.post(
        "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "file_name": filename,
            "parent_type": "bitable_file",
            "parent_node": parent_node,
            "size": str(len(data)),
        },
        files={"file": (filename, data, content_type)},
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()
    code = body.get("code", -1)
    if code != 0:
        msg = body.get("msg", "unknown error")
        raise AppServerError(
            f"上传截图失败: {msg} (code={code})", code="FEISHU_UPLOAD_ERROR",
        )
    file_token = body.get("data", {}).get("file_token", "")
    if not file_token:
        raise AppServerError(
            "上传截图返回的 file_token 为空", code="FEISHU_UPLOAD_ERROR",
        )
    return file_token


def _decode_data_url(data_url: str) -> tuple[str, str, bytes]:
    """Decode a `data:<mime>;base64,<...>` URL to (mime, filename, bytes).

    Validates the encoded b64 section's size BEFORE decoding to bound memory
    usage — a malicious caller could otherwise send a multi-GB data URL.
    """
    if not data_url.startswith("data:"):
        raise AppServerError(
            "Screenshot format error (expected data URL)", code="INVALID_PARAMS",
        )
    try:
        header, b64 = data_url.split(",", 1)
        mime = header.split(";", 1)[0].split(":", 1)[1]
    except Exception as exc:
        raise AppServerError(
            f"Screenshot header parse failed: {exc}", code="INVALID_PARAMS",
        ) from exc

    # Bound the encoded size before decoding.  base64 inflates by ~4/3, so
    # 14 MB encoded gives at most ~10.5 MB decoded — leave a small buffer.
    if len(b64) > 14 * 1024 * 1024:
        raise AppServerError(
            "Screenshot exceeds 10MB limit (encoded)",
            code="FILE_TOO_LARGE",
        )

    import base64
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise AppServerError(
            f"Screenshot base64 decode failed: {exc}", code="INVALID_PARAMS",
        ) from exc
    ext_map = {
        "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
        "image/gif": "gif", "image/webp": "webp", "image/bmp": "bmp",
    }
    ext = ext_map.get(mime, "png")
    return mime, f"screenshot.{ext}", raw


def _add_bitable_record(
    token: str,
    app_token: str,
    table_id: str,
    fields: dict[str, Any],
) -> str:
    """Add one record to a Feishu Bitable and return the record_id."""
    resp = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"fields": fields},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    code = body.get("code", -1)
    if code != 0:
        msg = body.get("msg", "unknown error")
        raise AppServerError(
            f"写入飞书多维表格失败: {msg} (code={code})",
            code="FEISHU_BITABLE_ERROR",
        )
    record = body.get("data", {}).get("record", {})
    record_id = record.get("record_id", "")
    logger.info("Feedback submitted to Feishu Bitable, record_id={}", record_id)
    return record_id


def _save_local_backup(entry: dict[str, Any]) -> None:
    """Append one feedback entry to the local backup JSONL file."""
    _ensure_memory_dir()
    feedback_file = _get_feedback_file()
    try:
        with feedback_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("Failed to write local feedback backup: {}", exc)


def _append_feedback(entry: dict[str, Any]) -> None:
    """Backward-compat alias for _save_local_backup."""
    _save_local_backup(entry)


def _read_local_backups() -> list[dict[str, Any]]:
    """Read all local feedback backup entries (newest first)."""
    feedback_file = _get_feedback_file()
    if not feedback_file.exists():
        return []

    entries: list[dict[str, Any]] = []
    try:
        for line in feedback_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception as exc:
        logger.warning("Failed to read local feedback backups: {}", exc)
        return []

    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return entries


def _read_local_feedbacks() -> list[dict[str, Any]]:
    """Backward-compat alias for _read_local_backups."""
    return _read_local_backups()


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def feedback_submit_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Submit user feedback + collected logs to Feishu Bitable."""
    raw_category = str(params.get("category", "other"))
    allowed_categories = {"bug", "question", "suggestion", "other"}
    category = raw_category if raw_category in allowed_categories else "other"
    title = str(params.get("title", "")).strip()
    content = str(params.get("content", "")).strip()
    contact = str(params.get("contact", "")).strip()
    app_version = str(params.get("app_version", "unknown"))
    prompt_used = str(params.get("prompt_used", "")).strip()
    repro_frequency = str(params.get("repro_frequency", "")).strip()
    screenshots_raw = params.get("screenshots") or []
    if not isinstance(screenshots_raw, list):
        screenshots_raw = []
    screenshots = [str(s) for s in screenshots_raw if s][:5]  # cap at 5

    if not title:
        raise AppServerError("反馈标题不能为空", code="INVALID_PARAMS")
    if not content:
        raise AppServerError("反馈内容不能为空", code="INVALID_PARAMS")

    workspace = _get_workspace_path()
    log_dir = workspace / "logs"

    # 1. Collect all logs
    logger.info("feedback:submit — collecting logs from {}", log_dir)
    log_content = _collect_all_logs(log_dir)

    # 2. Collect system info
    sys_info = _collect_system_info()
    os_str = f"{sys_info['os']} {sys_info['os_version']} ({sys_info['machine']})"

    # 3. Get Feishu config
    import miqi.bridge.server as bridge_module

    state = getattr(bridge_module, "_state", None)
    if state is None:
        raise AppServerError("Bridge state not available", code="INTERNAL")
    config = state.load_config()
    fb_cfg = config.channels.feedback

    if not fb_cfg.enabled:
        raise AppServerError("反馈功能未启用，请在配置中开启", code="FEEDBACK_DISABLED")

    # --- Resolve credentials: user-config value → schema field default fallback ---
    #
    # Pydantic preserves explicit empty strings from old config files, which would
    # override the new hardcoded schema defaults.  ``model_fields[...].default``
    # reads the Python-class default regardless of what the user's JSON carries.

    app_id = fb_cfg.feishu_app_id or FeedbackConfig.model_fields["feishu_app_id"].default
    app_secret = fb_cfg.feishu_app_secret or FeedbackConfig.model_fields["feishu_app_secret"].default
    bitable_app_token = fb_cfg.bitable_app_token or FeedbackConfig.model_fields["bitable_app_token"].default
    bitable_table_id = fb_cfg.bitable_table_id or FeedbackConfig.model_fields["bitable_table_id"].default

    # Only fail when the resolved values are truly blank (would be a broken schema build)
    if not app_id or not app_secret:
        raise AppServerError(
            "飞书 App ID / App Secret 未配置", code="FEISHU_NOT_CONFIGURED",
        )
    if not bitable_app_token or not bitable_table_id:
        raise AppServerError(
            "飞书多维表格 app_token / table_id 未配置", code="BITABLE_NOT_CONFIGURED",
        )

    now_iso = datetime.now(timezone.utc).isoformat()

    # 4. Build Bitable fields — cap per-field text sizes to stay within
    #    Feishu per-cell limits (multiline text ≈ 196,608 bytes per cell).

    def _cap_text(value: str, max_bytes: int = MAX_CELL_BYTES) -> str:
        """Truncate *value* so its UTF-8 encoding fits within *max_bytes*."""
        encoded = value.encode("utf-8")
        if len(encoded) <= max_bytes:
            return value
        head = encoded[:max_bytes].decode("utf-8", errors="ignore")
        dropped = len(encoded) - len(head.encode("utf-8"))
        return head + f"\n...(截断 {dropped} 字节)"

    fields: dict[str, Any] = {
        "类别": category,
        "标题": title,
        "详细描述（复现步骤，期望行为，实际行为等）": content,
        "联系方式": contact,
        "应用版本": app_version,
        "操作系统": os_str,
        "Python版本": sys_info["python_version"],
        "日志内容（设置界面日志栏目-复制日志）": _cap_text(log_content),
        "提交时间": now_iso,
        "使用的提示词": _cap_text(prompt_used),
        "复现频率": repro_frequency,
    }

    # 5. Send to Feishu — get token first, then upload screenshots, then add record
    try:
        token = _get_tenant_access_token(app_id, app_secret)

        # 5a. Upload each screenshot to get file_token references
        if screenshots:
            file_tokens: list[dict[str, str]] = []
            for idx, data_url in enumerate(screenshots):
                try:
                    mime, filename, raw = _decode_data_url(data_url)
                except AppServerError:
                    raise
                except Exception as exc:
                    raise AppServerError(
                        f"截图 {idx + 1} 处理失败: {exc}",
                        code="INVALID_PARAMS",
                    ) from exc
                # 10 MB cap per image
                if len(raw) > 10 * 1024 * 1024:
                    raise AppServerError(
                        f"截图 {idx + 1} 超过 10MB 限制",
                        code="FILE_TOO_LARGE",
                    )
                logger.info("Uploading screenshot {} ({} bytes)", idx + 1, len(raw))
                file_token = _upload_attachment(
                    token,
                    filename=filename,
                    content_type=mime,
                    data=raw,
                    parent_node=bitable_app_token,
                )
                file_tokens.append({"file_token": file_token})
            fields["附件"] = file_tokens

        # 5b. Add the Bitable record (with attachment references)
        record_id = _add_bitable_record(
            token, bitable_app_token, bitable_table_id, fields,
        )
    except AppServerError:
        raise
    except Exception as exc:
        logger.exception("feedback:submit — Feishu API error")
        raise AppServerError(
            f"提交到飞书失败: {exc}", code="FEISHU_API_ERROR",
        ) from exc

    # 6. Local backup (strip log content to avoid huge local file)
    local_entry = {
        "id": f"fbk_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "category": category,
        "title": title,
        "content": content,
        "contact": contact,
        "app_version": app_version,
        "os": os_str,
        "python_version": sys_info["python_version"],
        "feishu_record_id": record_id,
        "created_at": now_iso,
    }
    _save_local_backup(local_entry)

    return {"result": {"ok": True, "record_id": record_id}}


async def feedback_list_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """List local feedback backups."""
    # Validate and clamp limit.  Accept int, treat None/0/non-positive as
    # "no limit" (return all).  Clamp to a sane upper bound to bound work.
    raw_limit = params.get("limit")
    if raw_limit is None or raw_limit == 0:
        limit = 0  # 0 = no limit
    else:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            raise AppServerError(
                f"Invalid limit value: {raw_limit!r}", code="INVALID_PARAMS",
            )
    if limit < 0:
        limit = 0
    if limit > 200:
        limit = 200

    entries = _read_local_backups()
    if limit > 0 and len(entries) > limit:
        entries = entries[:limit]
    return {"result": {"entries": entries}}
