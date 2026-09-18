"""Canonical path resolution for MiQi-owned files and directories."""

from __future__ import annotations

import os
from pathlib import Path

MIQI_HOME_ENV = "MIQI_HOME"
DEFAULT_HOME_NAME = ".miqi"
LEGACY_HOME_NAME = ".assistant"


def _miqi_home_is_configured() -> bool:
    """Return True when MIQI_HOME is explicitly set and non-empty."""
    return bool(os.environ.get(MIQI_HOME_ENV, "").strip())


def get_miqi_home() -> Path:
    configured = os.environ.get(MIQI_HOME_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / DEFAULT_HOME_NAME).resolve()


def get_config_path() -> Path:
    return get_miqi_home() / "config.json"


def get_legacy_data_dir() -> Path:
    return (Path.home() / LEGACY_HOME_NAME).resolve()


def get_legacy_config_path() -> Path:
    return get_legacy_data_dir() / "config.json"


# ── session files layout ───────────────────────────────────────────────
# The per-session files root is ``<workspace>/sessions/<key>/files``.  Tools
# are handed that directory as their *workspace*, while the agent often
# writes paths relative to the workspace BASE (``sessions/<key>/files/...``).
# Normalizing the second form into the first is a rule every resolving layer
# must agree on: the office document tools and the agent file tools each grew
# their own answer and the two disagreed, so a declared deliverable was
# resolved to a doubled ``files/sessions/<key>/files/...`` path that never
# existed (#1131).  Both now import the one implementation below.  It lives
# here because this module is stdlib-only and importable from every layer
# without dragging in ``miqi.session.manager`` (see the note in
# ``miqi.session.session_keys``, which owns the key → directory-name half).


def session_files_layout(workspace: Path | None) -> tuple[Path, str] | None:
    """If *workspace* is ``<base>/sessions/<key>/files``, return ``(base, key)``.

    Returns None for anything else — a custom (non-default) workspace, the
    workspace base itself, or a directory that merely has a ``files`` name.
    Callers treat None as "not session-structured".
    """
    if workspace is None:
        return None
    try:
        if workspace.name == "files" and workspace.parent.parent.name == "sessions":
            return workspace.parent.parent.parent, workspace.parent.name
    except Exception:  # pragma: no cover - defensive
        pass
    return None


def normalize_session_prefixed(rel: Path, workspace: Path | None) -> Path | None:
    """Resolve a workspace-base-relative path against the session files root.

    *rel* is relative and starts with ``sessions/<key>/files/...``:

    - ``key`` is the current session key: strip the prefix so the file lands
      in the session files root instead of being nested under it (#806).
    - ``key`` is another session: reject — sessions are isolated.
    - *workspace* is not session-structured: return None, so the caller
      falls back to plain ``workspace / rel`` joining.
    """
    layout = session_files_layout(workspace)
    if layout is None:
        return None
    base, current_key = layout
    parts = list(rel.parts)
    if len(parts) < 3 or parts[0].lower() != "sessions" or parts[2].lower() != "files":
        return None
    other_key = parts[1]
    if other_key != current_key:
        raise PermissionError(
            f"Path '{rel}' 指向其他会话（{other_key}）的目录；"
            f"只能写入当前会话 files 目录（{workspace}）"
        )
    candidate = base.joinpath(*parts)
    # Defense-in-depth: the normalized candidate must stay inside the
    # session files root (guards against ".." escaping the prefix).
    try:
        candidate.resolve().relative_to(Path(workspace).resolve())
    except ValueError:
        raise PermissionError(
            f"Path '{rel}' escapes the session files root '{workspace}'"
        )
    return candidate
