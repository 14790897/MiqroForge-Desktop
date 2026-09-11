from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from miqi.runtime.workspace_logging import (
    _redact_message,
    append_workspace_log,
    append_workspace_log_json,
)


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_append_workspace_log_writes_redacted_entry(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    append_workspace_log(
        workspace,
        "api_key=sk-secret-value token=ghp_example_token user=alice",
    )

    log_path = workspace / "logs" / f"bridge-{_today_str()}.log"
    assert log_path.exists()

    contents = log_path.read_text(encoding="utf-8")
    assert "user=alice" in contents
    assert "sk-secret-value" not in contents
    assert "ghp_example_token" not in contents


def test_redact_json_quoted_key_value() -> None:
    """JSON-style quoted pairs like "api_key": "sk-secret" must redact the value."""
    msg = '{"api_key": "sk-secret-123", "name": "test"}'
    redacted = _redact_message(msg)
    assert "sk-secret-123" not in redacted
    assert '"name": "test"' in redacted


def test_redact_multi_word_authorization() -> None:
    """Authorization: Bearer <token> — the full two-word value must be redacted."""
    msg = "Authorization: Bearer sk-abc123xyz"
    redacted = _redact_message(msg)
    assert "sk-abc123xyz" not in redacted
    assert "Bearer" not in redacted or "[REDACTED]" in redacted


def test_redact_screaming_snake_case_env_var() -> None:
    """SCREAMING_SNAKE_CASE env var names like OPENAI_API_KEY=sk-xxx must be matched."""
    msg = "OPENAI_API_KEY=sk-proj-abc123 OTHER_VAR=hello"
    redacted = _redact_message(msg)
    assert "sk-proj-abc123" not in redacted
    assert "OTHER_VAR=hello" in redacted


def test_session_key_persisted_in_log_entry(tmp_path: Path) -> None:
    """session_key parameter must appear in the written log line."""
    workspace = tmp_path / "workspace"

    append_workspace_log(
        workspace,
        "tool call executed",
        source="tool",
        session_key="desktop:default:abc123",
    )

    log_path = workspace / "logs" / f"tool-{_today_str()}.log"
    assert log_path.exists()

    contents = log_path.read_text(encoding="utf-8")
    assert "session=desktop:default:abc123" in contents
    assert "tool call executed" in contents


def test_session_key_omitted_when_none(tmp_path: Path) -> None:
    """When session_key is None, the entry must not contain a session= field."""
    workspace = tmp_path / "workspace"

    append_workspace_log(workspace, "bridge started", source="bridge")

    log_path = workspace / "logs" / f"bridge-{_today_str()}.log"
    contents = log_path.read_text(encoding="utf-8")
    assert "session=" not in contents


def test_deep_json_redaction(tmp_path: Path) -> None:
    """Nested dict/list values inside JSON payloads must also be redacted."""
    workspace = tmp_path / "workspace"

    payload = {
        "source": "tool",
        "action": "api_call",
        "headers": {"Authorization": "Bearer sk-secret-nested"},
        "body": {"api_key": "sk-body-secret", "query": "hello"},
        "tags": ["token=ghp_list_secret", "safe_tag"],
    }

    append_workspace_log_json(workspace, payload)

    log_path = workspace / "logs" / f"tool-{_today_str()}.jsonl"
    assert log_path.exists()

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    # Nested secret in headers must be redacted
    assert "sk-secret-nested" not in json.dumps(record)
    # Nested secret in body must be redacted
    assert "sk-body-secret" not in json.dumps(record)
    # Secret inside list element must be redacted
    assert "ghp_list_secret" not in json.dumps(record)
    # Non-secret values survive
    assert record["action"] == "api_call"
    assert "safe_tag" in record["tags"]


def test_key_context_redaction_without_value_keywords(tmp_path: Path) -> None:
    """Values under sensitive keys must be redacted even when the value itself
    contains no secret keyword — e.g. a raw JWT under an 'Authorization' key."""
    workspace = tmp_path / "workspace"

    payload = {
        "source": "bridge",
        "request": {
            "Authorization": "my-jwt-payload-value",
            "Content-Type": "application/json",
            "x-custom-token": "abc123-no-keyword-here",
        },
    }

    append_workspace_log_json(workspace, payload)

    log_path = workspace / "logs" / f"bridge-{_today_str()}.jsonl"
    assert log_path.exists()

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    dumped = json.dumps(record)
    # The value under 'Authorization' must be redacted by key context
    assert "my-jwt-payload-value" not in dumped
    # The value under 'x-custom-token' must also be redacted (key matches 'token')
    assert "abc123-no-keyword-here" not in dumped
    # Non-sensitive header value survives
    assert record["request"]["Content-Type"] == "application/json"


# ── Date-based rotation ──────────────────────────────────────────────────────


def test_get_log_path_uses_todays_date(tmp_path: Path) -> None:
    from miqi.runtime.workspace_logging import get_log_dir, get_log_path

    ws = tmp_path / "ws"
    path = get_log_path(ws, "bridge")
    assert path.name == f"bridge-{_today_str()}.log"
    assert path.parent == get_log_dir(ws)


def test_different_sources_write_to_different_files(tmp_path: Path) -> None:
    """Each source writes to its own date-based file."""
    ws = tmp_path / "ws"
    append_workspace_log(ws, "bridge msg", source="bridge")
    append_workspace_log(ws, "sandbox msg", source="sandbox")
    append_workspace_log(ws, "tool msg", source="tool")

    assert (ws / "logs" / f"bridge-{_today_str()}.log").exists()
    assert (ws / "logs" / f"sandbox-{_today_str()}.log").exists()
    assert (ws / "logs" / f"tool-{_today_str()}.log").exists()


# ── 7-day retention ──────────────────────────────────────────────────────────


def test_cleanup_old_logs_removes_expired_files(tmp_path: Path) -> None:
    """Files with mtime older than 7 days must be deleted."""
    import os

    from miqi.runtime.workspace_logging import cleanup_old_logs, get_log_dir

    ws = tmp_path / "ws"
    log_dir = get_log_dir(ws)

    # Create an old file (mtime = 2020-01-01)
    old_file = log_dir / "bridge-2020-01-01.log"
    old_file.write_text("old\n", encoding="utf-8")
    old_ts = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(str(old_file), (old_ts, old_ts))

    # Create a recent file (today)
    recent_file = log_dir / f"bridge-{_today_str()}.log"
    recent_file.write_text("recent\n", encoding="utf-8")

    cleanup_old_logs(ws)

    assert not old_file.exists(), "old log must be deleted"
    assert recent_file.exists(), "recent log must be kept"


def test_cleanup_keeps_recent_files(tmp_path: Path) -> None:
    """Files younger than 7 days must survive cleanup."""
    from miqi.runtime.workspace_logging import cleanup_old_logs, get_log_dir

    ws = tmp_path / "ws"
    log_dir = get_log_dir(ws)
    recent = log_dir / f"bridge-{_today_str()}.log"
    recent.write_text("ok\n", encoding="utf-8")

    cleanup_old_logs(ws)
    assert recent.exists()


def test_cleanup_handles_empty_log_dir(tmp_path: Path) -> None:
    """cleanup must not crash on a nonexistent or empty log directory."""
    from miqi.runtime.workspace_logging import cleanup_old_logs

    ws = tmp_path / "ws"
    cleanup_old_logs(ws)  # no logs/ dir yet

    (ws / "logs").mkdir(parents=True)
    cleanup_old_logs(ws)  # empty logs/ dir


# ── 50 MB size enforcement ───────────────────────────────────────────────────


def test_size_enforcement_deletes_oldest_first(tmp_path: Path) -> None:
    """When total size exceeds the limit, oldest files are deleted first."""
    import os

    from miqi.runtime.workspace_logging import _enforce_size_limit, get_log_dir

    ws = tmp_path / "ws"
    log_dir = get_log_dir(ws)

    f1 = log_dir / "bridge-2026-01-01.log"
    f2 = log_dir / "bridge-2026-06-01.log"

    # Write ~3KB each; enforce a 5KB limit → total > limit, f1 deleted
    f1.write_text("A" * 3000 + "\n", encoding="utf-8")
    f2.write_text("B" * 3000 + "\n", encoding="utf-8")

    os.utime(str(f1), (1000000000, 1000000000))

    _enforce_size_limit(log_dir, max_total_bytes=5000)

    assert not f1.exists(), "oldest file must be deleted first"
    assert f2.exists(), "newer file must survive"


def test_size_enforcement_always_keeps_at_least_one_file(tmp_path: Path) -> None:
    """Even when the single file exceeds the limit, it must not be deleted."""
    from miqi.runtime.workspace_logging import _enforce_size_limit, get_log_dir

    ws = tmp_path / "ws"
    log_dir = get_log_dir(ws)
    f = log_dir / "bridge-2026-01-01.log"
    f.write_text("X" * 10000 + "\n", encoding="utf-8")

    _enforce_size_limit(log_dir, max_total_bytes=100)

    assert f.exists(), "last remaining file must not be deleted"


def test_prune_log_file_truncates_oversized(tmp_path: Path) -> None:
    """A single log file exceeding the limit must be truncated to last N lines."""
    from miqi.runtime.workspace_logging import _prune_log_file

    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    log_path = ws / "test.log"

    # Write 200 lines (each ~100 bytes), set max_bytes low, keep_lines=50
    with log_path.open("w", encoding="utf-8") as f:
        for i in range(200):
            f.write(f"line {i:04d} " + "x" * 90 + "\n")

    _prune_log_file(log_path, max_bytes=100, keep_lines=50)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 50, f"expected 50 lines, got {len(lines)}"
    assert "line 0150" in lines[0], "must keep tail, not head"


def test_prune_skips_small_files(tmp_path: Path) -> None:
    """Files under the size limit must not be truncated."""
    from miqi.runtime.workspace_logging import _prune_log_file

    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    log_path = ws / "test.log"
    log_path.write_text("small file\n", encoding="utf-8")

    _prune_log_file(log_path, max_bytes=1_000_000)
    assert log_path.read_text() == "small file\n"


# ── Entry format ─────────────────────────────────────────────────────────────


def test_entry_includes_level_and_source(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    append_workspace_log(ws, "info msg", level="INFO", source="sandbox")
    append_workspace_log(ws, "warn msg", level="WARN", source="bridge")
    append_workspace_log(ws, "err msg", level="ERROR", source="tool")

    log = ws / "logs" / f"sandbox-{_today_str()}.log"
    assert "[INFO] [sandbox] info msg" in log.read_text()

    log2 = ws / "logs" / f"bridge-{_today_str()}.log"
    assert "[WARN] [bridge] warn msg" in log2.read_text()

    log3 = ws / "logs" / f"tool-{_today_str()}.log"
    assert "[ERROR] [tool] err msg" in log3.read_text()


def test_jsonl_includes_timestamp(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    append_workspace_log_json(ws, {"source": "sandbox", "cmd": "ls"})
    log = ws / "logs" / f"sandbox-{_today_str()}.jsonl"
    record = json.loads(log.read_text().strip())
    assert "timestamp" in record
    assert record["cmd"] == "ls"


# ── Redaction edge cases ─────────────────────────────────────────────────────


def test_safe_content_preserved(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    append_workspace_log(ws, "user=bob action=deploy env=prod status=ok")
    log = ws / "logs" / f"bridge-{_today_str()}.log"
    content = log.read_text()
    assert "user=bob" in content
    assert "action=deploy" in content


def test_redact_empty_message(tmp_path: Path) -> None:
    """Empty messages must not crash redaction."""
    ws = tmp_path / "ws"
    append_workspace_log(ws, "")
    log = ws / "logs" / f"bridge-{_today_str()}.log"
    content = log.read_text()
    assert content.strip().endswith("[] []") or "[INFO] [bridge]" in content


def test_redact_no_sensitive_data(tmp_path: Path) -> None:
    """Messages without secrets must pass through unchanged."""
    msg = "The quick brown fox jumps over the lazy dog"
    assert _redact_message(msg) == msg
