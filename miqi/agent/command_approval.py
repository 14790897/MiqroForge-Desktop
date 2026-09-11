"""Dangerous command approval system, ported from Hermes Agent's tools/approval.py.

This module is the single source of truth for dangerous command detection in MiQi:
  - Pattern detection (DANGEROUS_PATTERNS, detect_dangerous_command)
  - Per-session approval state (thread-safe)
  - Approval prompting (CLI interactive: once/session/always/deny)
  - Permanent allowlist persistence via config

Key differences from Hermes:
  - No Gateway blocking queue (MiQi has no gateway mode)
  - No Smart Approval (no auxiliary LLM client)
  - No Tirith security scanner integration
  - Simpler session-key scheme (uses MiQi session keys directly)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import unicodedata
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# ── Sensitive write target patterns ───────────────────────────────────────
_SSH_SENSITIVE_PATH = r'(?:~|\$home|\$\{home\})/\.ssh(?:/|$)'
_SENSITIVE_WRITE_TARGET = rf'(?:/etc/|/dev/sd|{_SSH_SENSITIVE_PATH})'

# ── Dangerous command patterns (39 patterns, ported from Hermes approval.py) ──
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r'\brm\s+(-[^\s]*\s+)*/', "delete in root path"),
    (r'\brm\s+-[^\s]*r', "recursive delete"),
    (r'\brm\s+--recursive\b', "recursive delete (long flag)"),
    (r'\bchmod\s+(-[^\s]*\s+)*(777|666|o\+[rwx]*w|a\+[rwx]*w)\b', "world/other-writable permissions"),
    (r'\bchmod\s+--recursive\b.*(777|666|o\+[rwx]*w|a\+[rwx]*w)', "recursive world/other-writable (long flag)"),
    (r'\bchown\s+(-[^\s]*)?R\s+root', "recursive chown to root"),
    (r'\bchown\s+--recursive\b.*root', "recursive chown to root (long flag)"),
    (r'\bmkfs\b', "format filesystem"),
    (r'\bdd\s+.*if=', "disk copy"),
    (r'>\s*/dev/sd', "write to block device"),
    (r'\bDROP\s+(TABLE|DATABASE)\b', "SQL DROP"),
    (r'\bDELETE\s+FROM\b(?!.*\bWHERE\b)', "SQL DELETE without WHERE"),
    (r'\bTRUNCATE\s+(TABLE)?\s*\w', "SQL TRUNCATE"),
    (r'>\s*/etc/', "overwrite system config"),
    (r'\bsystemctl\s+(stop|disable|mask)\b', "stop/disable system service"),
    (r'\bkill\s+-9\s+-1\b', "kill all processes"),
    (r'\bpkill\s+-9\b', "force kill processes"),
    (r':\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:', "fork bomb"),
    (r'\b(bash|sh|zsh|ksh)\s+-[^\s]*c(\s+|$)', "shell command via -c/-lc flag"),
    (r'\b(python[23]?|perl|ruby|node)\s+-[ec]\s+', "script execution via -e/-c flag"),
    (r'\b(curl|wget)\b.*\|\s*(ba)?sh\b', "pipe remote content to shell"),
    (r'\b(bash|sh|zsh|ksh)\s+<\s*<?\s*\(\s*(curl|wget)\b', "execute remote script via process substitution"),
    (rf'\btee\b.*["\']?{_SENSITIVE_WRITE_TARGET}', "overwrite system file via tee"),
    (rf'>>?\s*["\']?{_SENSITIVE_WRITE_TARGET}', "overwrite system file via redirection"),
    (r'\bxargs\s+.*\brm\b', "xargs with rm"),
    (r'\bfind\b.*-exec\s+(/\S*/)?rm\b', "find -exec rm"),
    (r'\bfind\b.*-delete\b', "find -delete"),
    (r'\b(cp|mv|install)\b.*\s/etc/', "copy/move file into /etc/"),
    (r'\bsed\s+-[^\s]*i.*\s/etc/', "in-place edit of system config"),
    (r'\bsed\s+--in-place\b.*\s/etc/', "in-place edit of system config (long flag)"),
    (r'\b(python[23]?|perl|ruby|node)\s+<<', "script execution via heredoc"),
    (r'\bgit\s+reset\s+--hard\b', "git reset --hard (destroys uncommitted changes)"),
    (r'\bgit\s+push\b.*--force\b', "git force push (rewrites remote history)"),
    (r'\bgit\s+push\b.*-f\b', "git force push short flag (rewrites remote history)"),
    (r'\bgit\s+clean\s+-[^\s]*f', "git clean with force (deletes untracked files)"),
    (r'\bgit\s+branch\s+-D\b', "git branch force delete"),
    (r'\bchmod\s+\+x\b.*[;&|]+\s*\./', "chmod +x followed by immediate execution"),
    (r'\bkill\b.*\$\(\s*pgrep\b', "kill process via pgrep expansion"),
    (r'\bkill\b.*`\s*pgrep\b', "kill process via backtick pgrep expansion"),
]

# ── Per-session state (thread-safe) ───────────────────────────────────────
_lock = threading.Lock()
_session_approved: dict[str, set[str]] = {}   # session_key → set of approved pattern descriptions
_permanent_approved: set[str] = set()
_permanent_added_at: dict[str, float] = {}     # pattern → added_at timestamp

# Approval history (decisions that were prompted and resolved)
_approval_history: list[dict] = []  # each entry: {id, pattern_key, description, command, decision, timestamp, session_key}
_history_file: str | None = None


def init_history_file(path: str) -> None:
    """Initialize file-backed persistence for approval history.

    Loads existing entries from *path* into memory and configures
    ``add_approval_history`` to append each new entry to the file.
    """
    global _history_file
    _history_file = path
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    _approval_history.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass


def _normalize_command(command: str) -> str:
    """Normalize command string to prevent Unicode obfuscation bypass."""
    command = re.sub(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', command)  # strip ANSI
    command = command.replace('\x00', '')
    command = unicodedata.normalize('NFKC', command)
    return command


def detect_dangerous_command(command: str) -> tuple[bool, Optional[str], Optional[str]]:
    """Check if a command matches any dangerous pattern.

    Returns:
        (is_dangerous, pattern_description, description) or (False, None, None)
    """
    normalized = _normalize_command(command).lower()
    for pattern, description in DANGEROUS_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE | re.DOTALL):
            return (True, description, description)
    return (False, None, None)


def is_approved(session_key: str, pattern_key: str) -> bool:
    """Check if a pattern is approved for this session or permanently."""
    with _lock:
        if pattern_key in _permanent_approved:
            return True
        return pattern_key in _session_approved.get(session_key, set())


def approve_session(session_key: str, pattern_key: str) -> None:
    """Approve a pattern for this session only."""
    with _lock:
        _session_approved.setdefault(session_key, set()).add(pattern_key)


def approve_permanent(pattern_key: str) -> None:
    """Add a pattern to the permanent allowlist."""
    with _lock:
        _permanent_approved.add(pattern_key)
        _permanent_added_at[pattern_key] = time.time()


def remove_permanent(pattern_key: str) -> bool:
    """Remove a pattern from the permanent allowlist. Returns True if found."""
    with _lock:
        if pattern_key in _permanent_approved:
            _permanent_approved.discard(pattern_key)
            _permanent_added_at.pop(pattern_key, None)
            return True
        return False


def get_permanent_allowlist_meta() -> dict[str, float]:
    """Return permanent allowlist with added-at timestamps."""
    with _lock:
        return dict(_permanent_added_at)


def add_approval_history(
    pattern_key: str,
    description: str,
    command: str,
    decision: str,
    session_key: str = "",
) -> None:
    """Record an approval decision in history."""
    entry = {
        "id": str(uuid.uuid4()),
        "pattern_key": pattern_key,
        "description": description,
        "command": command,
        "decision": decision,
        "timestamp": time.time(),
        "session_key": session_key,
    }
    with _lock:
        _approval_history.append(entry)
        if _history_file:
            try:
                with open(_history_file, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass


def get_approval_history(limit: int = 200) -> list[dict]:
    """Return recent approval history (most recent first)."""
    with _lock:
        return list(reversed(_approval_history[-limit:]))


def get_pending_approvals_with_age(
    pending_ids: list[str], approval_data: dict[str, dict]
) -> list[dict]:
    """Return pending approval details with age info for timeout display."""
    now = time.time()
    result = []
    for aid in pending_ids:
        data = approval_data.get(aid, {})
        created_at = data.get("created_at", now)
        result.append({
            "approval_id": aid,
            "command": data.get("command", ""),
            "description": data.get("description", ""),
            "created_at": created_at,
            "age_seconds": now - created_at,
            "allow_permanent": data.get("allow_permanent", True),
        })
    return result


def clear_session(session_key: str) -> None:
    """Clear all approvals for a session (on /new or session end)."""
    with _lock:
        _session_approved.pop(session_key, None)


def load_permanent_allowlist(patterns: set[str]) -> None:
    """Bulk-load permanent allowlist (called from config loader)."""
    with _lock:
        _permanent_approved.update(patterns)


def replace_permanent_allowlist(patterns: set[str]) -> None:
    """Replace the in-memory permanent allowlist to match config exactly.

    Unlike :func:`load_permanent_allowlist` (which only adds), this makes
    the in-memory allowlist equal the persisted config.  Called on hot
    config reload (#789) so removed patterns take effect without restart.

    Timestamps of patterns that SURVIVE the replacement are preserved —
    clearing ``_permanent_added_at`` unconditionally would reset the
    "approved at 14:00" metadata of still-active patterns to epoch
    (2026-08-26 review, #11).
    """
    with _lock:
        _permanent_approved.clear()
        _permanent_approved.update(patterns)
        surviving = {p: t for p, t in _permanent_added_at.items() if p in patterns}
        _permanent_added_at.clear()
        _permanent_added_at.update(surviving)


def get_permanent_allowlist() -> set[str]:
    """Return current permanent allowlist (for config persistence)."""
    with _lock:
        return set(_permanent_approved)


def check_dangerous_command(
    command: str,
    session_key: str = "",
    approval_callback=None,
) -> dict:
    """Main entry point: check if command is dangerous and handle approval.

    Args:
        command: The shell command to check.
        session_key: Session identifier for per-session approvals.
        approval_callback: Optional callback for custom approval UI.
            Signature: (command, description, *, allow_permanent=True) -> str
            Returns: 'once' | 'session' | 'always' | 'deny'

    Returns:
        {"approved": bool, "message": str | None, ...}
    """
    is_dangerous, pattern_key, description = detect_dangerous_command(command)
    if not is_dangerous:
        return {"approved": True, "message": None}

    if is_approved(session_key, pattern_key):
        return {"approved": True, "message": None}

    # If a custom callback is provided (e.g. desktop bridge), always prompt.
    # Otherwise fall back to MIQI_INTERACTIVE guard for CLI mode.
    if approval_callback is None:
        is_cli = os.getenv("MIQI_INTERACTIVE")
        if not is_cli:
            return {"approved": True, "message": None}

    # CLI interactive approval (or custom callback)
    choice = prompt_dangerous_approval(
        command, description,
        approval_callback=approval_callback,
    )

    if choice == "deny":
        add_approval_history(pattern_key, description, command, "deny", session_key)
        return {
            "approved": False,
            "message": (
                f"BLOCKED: User denied this potentially dangerous command "
                f"(matched '{description}' pattern). Do NOT retry this command."
            ),
            "pattern_key": pattern_key,
            "description": description,
        }

    if choice == "session":
        approve_session(session_key, pattern_key)
        add_approval_history(pattern_key, description, command, "session", session_key)
    elif choice == "always":
        approve_session(session_key, pattern_key)
        approve_permanent(pattern_key)
        _save_permanent_allowlist()
        add_approval_history(pattern_key, description, command, "always", session_key)
    else:
        add_approval_history(pattern_key, description, command, "once", session_key)

    return {"approved": True, "message": None}


def prompt_dangerous_approval(
    command: str,
    description: str,
    timeout_seconds: int = 60,
    allow_permanent: bool = True,
    approval_callback=None,
) -> str:
    """Prompt the user to approve a dangerous command (CLI only).

    Returns: 'once' | 'session' | 'always' | 'deny'
    """
    if approval_callback is not None:
        try:
            return approval_callback(command, description, allow_permanent=allow_permanent)
        except Exception as exc:
            logger.error("Approval callback failed: %s", exc)
            return "deny"

    os.environ["MIQI_SPINNER_PAUSE"] = "1"
    try:
        while True:
            print()
            print(f"  ⚠️  DANGEROUS COMMAND: {description}")
            print(f"      {command}")
            print()
            if allow_permanent:
                print("      [o]nce  |  [s]ession  |  [a]lways  |  [d]eny")
            else:
                print("      [o]nce  |  [s]ession  |  [d]eny")
            print()
            sys.stdout.flush()

            result: dict[str, str] = {"choice": ""}

            def _get_input() -> None:
                try:
                    prompt_str = "      Choice [o/s/a/D]: " if allow_permanent else "      Choice [o/s/D]: "
                    result["choice"] = input(prompt_str).strip().lower()
                except (EOFError, OSError):
                    result["choice"] = ""

            thread = threading.Thread(target=_get_input, daemon=True)
            thread.start()
            thread.join(timeout=timeout_seconds)

            if thread.is_alive():
                print("\n      ⏱ Timeout - denying command")
                return "deny"

            choice = result["choice"]
            if choice in ("o", "once"):
                print("      ✓ Allowed once")
                return "once"
            elif choice in ("s", "session"):
                print("      ✓ Allowed for this session")
                return "session"
            elif choice in ("a", "always"):
                if not allow_permanent:
                    print("      ✓ Allowed for this session")
                    return "session"
                print("      ✓ Added to permanent allowlist")
                return "always"
            else:
                print("      ✗ Denied")
                return "deny"

    except (EOFError, KeyboardInterrupt):
        print("\n      ✗ Cancelled")
        return "deny"
    finally:
        os.environ.pop("MIQI_SPINNER_PAUSE", None)
        print()
        sys.stdout.flush()


def _save_permanent_allowlist() -> bool:
    """Persist permanent allowlist to config.

    Returns True when the file write succeeded, False otherwise
    (2026-08-31 review: callers must be able to tell a failed persist from
    a successful one instead of reporting ``cleared: true`` blindly).
    """
    try:
        from miqi.config.loader import save_config_allowlist
        patterns = get_permanent_allowlist()
        save_config_allowlist(patterns)
        return True
    except Exception as exc:
        logger.warning("Could not save permanent allowlist: %s", exc)
        return False
