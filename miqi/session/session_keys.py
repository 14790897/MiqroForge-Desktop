"""Canonical session-key → on-disk directory-name derivation.

Session keys are colon-separated (``client_id:channel:chat_id``).  Every
writer and reader of a per-session directory under
``<workspace>/sessions/`` must derive the directory name the same way, or
one side silently searches a directory no writer ever creates (#1005).
That single derivation lives here.

This module owns session-key *semantics*, so it lives in the session
package next to :class:`~miqi.session.manager.SessionManager` rather than
in ``miqi.paths`` — the latter is a stdlib-only home/config path module
that ``miqi.utils.helpers`` already imports, so hosting the rule there
would need a reverse import of ``safe_filename`` and create a cycle.

It depends only on ``miqi.utils.helpers`` and imports nothing from the
agent/runtime/bridge layers, so every layer may import it freely.

Naming invariant: callers may hold either the bare session key
(``desktop:<ts>``) or the client-namespaced form
(``miqi-desktop:desktop:<ts>``); both MUST derive the same directory.
That shared directory is deliberate (one logical session, one folder)
and is NOT a cross-client isolation boundary — cross-client isolation
relies on session ownership checks, and this derivation must not be
scoped by client_id (the bare form carries no client information).
"""

from __future__ import annotations

from miqi.utils.helpers import safe_filename

__all__ = ["session_files_dir_key"]


def session_files_dir_key(session_key: str) -> str:
    """Derive the on-disk per-session directory key from a session key.

    Strips the client_id prefix only for fully namespaced keys (three or
    more colon segments, e.g. ``miqi-desktop:desktop:1786...`` →
    ``desktop_1786...``) and keeps the whole key for two-segment channel
    keys (``desktop:1786...`` → ``desktop_1786...``) — matching the disk
    convention used by the session manager, ``files.read`` and attachment
    saving.

    The derivation is idempotent: the output never contains ``:`` (it is
    folded to ``_`` by ``safe_filename``), so re-deriving from an already
    derived name returns that name unchanged.
    """
    parts = session_key.split(":")
    if len(parts) >= 3:
        parts = parts[1:]
    return safe_filename("_".join(parts))
