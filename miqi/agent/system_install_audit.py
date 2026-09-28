"""System-install authorization audit (issue #935).

「允许并记住」（allow and remember）on a system package install card hands
the agent a persistent, root-capable package-management ability: the
machine stops asking from then on.  The external review on #875 asked for
at least one explicit event per grant, so "why does this machine suddenly
allow system package installs" stays answerable after the fact.

Every card resolution is written as TWO append-only rows sharing a
``grant_id``:

- ``kind="authorization"`` — written the moment the card resolves: who
  (``session_key``, which carries the desktop client id), when, which
  thread/turn, the decision (``once`` / ``always`` / ``deny`` /
  ``deny_no_channel``), the NORMALIZED command the user actually approved
  (display == execution, #875 review P3-3), and the persist/runtime state
  of the grant.
- ``kind="result"`` — written once the install finishes or is
  intercepted: exit code, duration, success flag, and the reason for an
  interception that never reached the distro.

The file stays append-only (crash-safe: a killed bridge still leaves the
grant behind); :func:`get_install_audit` merges the rows back into one
logical record per grant.  Mirrors the approval-history pattern in
``miqi/agent/command_approval.py``: in-memory ring + optional JSONL file,
initialised by the bridge via :func:`init_audit_file`.

Every write is best-effort — an audit failure must never change a decision
or break an install.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

#: Cap on the in-memory ring.  Only the ring is capped — the JSONL file is
#: append-only and keeps the full trail.  Two rows per grant, so this holds
#: the last ~500 grants; older ones stay readable in the file and simply
#: fall out of the API view.
_MAX_ENTRIES = 1000

_lock = threading.Lock()
_entries: list[dict[str, Any]] = []
_audit_file: str | None = None


def init_audit_file(path: str) -> None:
    """Load existing rows from *path* and append new ones to it.

    Idempotent per path: a second call with the same path does not re-append
    the persisted rows (mirrors ``user_input_history.init_history_file``).
    """
    global _audit_file
    loaded: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    loaded.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        # Missing/unreadable/not-a-file: audit stays in-memory (best-effort).
        pass
    with _lock:
        if _audit_file == path:
            return  # already initialised from this file
        _audit_file = path
        # Replace (not append) so a re-init with a different path swaps the
        # backing store instead of duplicating entries.
        _entries.clear()
        _entries.extend(loaded[-_MAX_ENTRIES:])


def _append(entry: dict[str, Any]) -> None:
    with _lock:
        _entries.append(entry)
        if len(_entries) > _MAX_ENTRIES:
            del _entries[: len(_entries) - _MAX_ENTRIES]
        if _audit_file:
            try:
                with open(_audit_file, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass


def record_authorization(
    *,
    decision: str,
    command: str,
    session_key: str = "",
    thread_id: str = "",
    turn_id: str = "",
    persist_failed: bool = False,
    runtime_failed: bool = False,
) -> str:
    """Record one system-install card resolution; returns its ``grant_id``.

    *decision* is the card's verdict — ``once`` / ``always`` for the two
    grant kinds #935 names, plus ``deny`` / ``deny_no_channel`` for the
    refusals, which are recorded too: an audit that only keeps the grants
    cannot answer "was the user ever asked, and what did they say".

    *command* must be the NORMALIZED command — the one the card displayed
    and the one that runs as root.
    """
    grant_id = str(uuid.uuid4())
    entry = {
        "id": str(uuid.uuid4()),
        "kind": "authorization",
        "grant_id": grant_id,
        "timestamp": time.time(),
        "session_key": session_key,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "decision": decision,
        "command": command,
        "persist_failed": bool(persist_failed),
        "runtime_failed": bool(runtime_failed),
    }
    try:
        _append(entry)
    except Exception:  # noqa: BLE001 - audit is best-effort
        pass
    return grant_id


def record_result(
    grant_id: str,
    *,
    exit_code: int,
    duration_ms: int = 0,
    reason: str = "",
) -> None:
    """Record how a granted install ended (issue #935 "结果").

    *reason* carries the interception message when the install never
    reached the distro (no sandbox / WSL-only / cancelled) — the exit code
    alone would not explain those.
    """
    if not grant_id:
        return
    entry = {
        "id": str(uuid.uuid4()),
        "kind": "result",
        "grant_id": grant_id,
        "timestamp": time.time(),
        "exit_code": int(exit_code),
        "success": int(exit_code) == 0,
        "duration_ms": int(duration_ms),
        "reason": reason,
    }
    try:
        _append(entry)
    except Exception:  # noqa: BLE001 - audit is best-effort
        pass


def get_install_audit(limit: int = 200) -> list[dict[str, Any]]:
    """Return merged grant records, most recent first.

    One record per authorization row, with the matching result row folded
    in under ``"result"`` (``None`` while the install is still running or
    when the card was denied).  A result row whose authorization row has
    aged out of the ring is dropped rather than returned orphaned.
    """
    with _lock:
        rows = list(_entries)
    grants: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        if row.get("kind") == "authorization":
            grant_id = str(row.get("grant_id") or "")
            if not grant_id:
                continue
            grants[grant_id] = {
                "id": grant_id,
                "source": "system_install",
                # Same key the other audit stream carries, so both render
                # through one shape on the approvals-history page.
                "description": "系统包安装授权",
                "timestamp": row.get("timestamp", 0.0),
                "session_key": row.get("session_key", ""),
                "thread_id": row.get("thread_id", ""),
                "turn_id": row.get("turn_id", ""),
                "decision": row.get("decision", ""),
                "command": row.get("command", ""),
                "persist_failed": bool(row.get("persist_failed", False)),
                "runtime_failed": bool(row.get("runtime_failed", False)),
                "result": None,
            }
            order.append(grant_id)
        elif row.get("kind") == "result":
            record = grants.get(str(row.get("grant_id") or ""))
            if record is None:
                continue  # authorization row aged out of the ring
            record["result"] = {
                "exit_code": row.get("exit_code", 0),
                "success": bool(row.get("success", False)),
                "duration_ms": row.get("duration_ms", 0),
                "reason": row.get("reason", ""),
            }
    merged = [grants[g] for g in order]
    merged.reverse()
    return merged[:limit]


def clear_audit() -> None:
    """Clear the in-memory ring (used by tests; the file is not touched)."""
    with _lock:
        _entries.clear()
