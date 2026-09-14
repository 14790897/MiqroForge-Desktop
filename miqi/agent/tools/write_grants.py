"""Session-scoped write grants shared by the file tools and ``exec`` (#1013).

``#864``'s write-authorization card records its SESSION-scoped answers
("本目录不再询问" / the approval-bypass switch) on the *file-tool instances* —
``WriteFileTool._granted`` / ``EditFileTool._granted`` /
``ApplyPatchTool._granted``, shaped ``dict[session_key -> {normcase(path)}]``.
``ExecTool`` holds no reference to those sets: its write boundary only honours
the harness-injected ``_user_roots`` (#821/#984, layers 1 and 3 —
``_exec_rw_binds`` and ``_guard_write_roots``), so the very same session's
``exec`` still refused to write a directory the user had just authorized.
``ExecTool._exec_rw_binds``'s docstring names that gap explicitly.

This module is the process-level bridge.  The file tools publish every
session-scoped grant here (both grant points live in
``filesystem._resolve_write_shared_roots``, the single funnel all three write
tools share), and ``ToolOrchestrator._execute_in_sandbox`` merges
``get(session_id)`` into the ``_user_roots`` injection that ``exec`` already
trusts.  No new channel is invented: the store only feeds the one authorization
channel the sandbox already treats as harness-owned.

Invariants (fail-closed — this store only ever ADDS roots):
  * only SESSION-scoped grants are published.  "允许本次" (``once_granted``)
    is invocation-scoped and must never reach this store, or one "once" would
    widen every later ``exec`` in the session.
  * the key space is the tool session key, normalised exactly like the file
    tools' ``_session_granted`` (``None`` and ``""`` are the same bucket), so
    both sides agree on which session a grant belongs to.
  * ``get()`` never returns ``None``: an unknown session — or an empty store —
    is an empty ``frozenset``, so with nothing granted the injected
    ``_user_roots`` list is byte-for-byte what it was before #1013.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Iterable


def norm_session_key(session_key: Any) -> str:
    """Bucket key for *session_key* — the file tools' ``session_key or ""``.

    Shared by both sides on purpose: the file tools key their ``_granted``
    sets with it, the store keys its buckets with it, and the orchestrator
    looks grants up with it.  One function, one key space (locked by
    ``tests/agent/tools/test_write_grants.py``).
    """
    return str(session_key) if session_key else ""


class SessionWriteGrants:
    """Process-level ``session_key -> granted write dirs`` store.

    Entries are deduplicated case-insensitively (``os.path.normcase``, the same
    judge the file tools' ``_granted`` sets use) while the ORIGINAL spelling is
    kept for consumers: ``exec`` hands these strings to bwrap as rw bind
    sources and resolves them for the command guard, so a lower-cased rewrite
    would be a needless (on case-sensitive WSL mounts, harmful) change of the
    path the user authorized.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # session_key -> {normcase(path): original path}
        self._grants: dict[str, dict[str, str]] = {}

    def add(self, session_key: Any, path: Any) -> None:
        """Grant *path* to *session_key* (idempotent; empty input ignored)."""
        try:
            raw = os.fspath(path)
        except TypeError:
            return
        if not isinstance(raw, str) or not raw:
            return
        key = norm_session_key(session_key)
        with self._lock:
            bucket = self._grants.setdefault(key, {})
            bucket.setdefault(os.path.normcase(raw), raw)

    def get(self, session_key: Any) -> frozenset[str]:
        """Granted dirs for *session_key* (empty set when unknown)."""
        with self._lock:
            bucket = self._grants.get(norm_session_key(session_key))
            return frozenset(bucket.values()) if bucket else frozenset()

    def session_keys(self) -> Iterable[str]:
        """Sessions currently holding at least one grant (diagnostics/tests)."""
        with self._lock:
            return tuple(self._grants)

    def clear(self) -> None:
        """Drop every grant — test isolation only; never called from runtime."""
        with self._lock:
            self._grants.clear()


_PROCESS_GRANTS = SessionWriteGrants()


def get_write_grants() -> SessionWriteGrants:
    """The process-wide store shared by the file tools and the orchestrator."""
    return _PROCESS_GRANTS


def reset_write_grants() -> SessionWriteGrants:
    """Empty the process-wide store and return it (test helper)."""
    _PROCESS_GRANTS.clear()
    return _PROCESS_GRANTS
