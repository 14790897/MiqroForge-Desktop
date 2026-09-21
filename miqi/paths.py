"""Canonical path resolution for MiQi-owned files and directories."""

from __future__ import annotations

import os
import re
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


# ── account-scoped storage layout (#1185) ──────────────────────────────
# Sessions, task assets, memory, skills, experience and the workspace files
# themselves all hang off the *workspace root*, so making that root
# account-scoped is what keeps one account's conversation history out of the
# next account's sidebar on a shared device.
#
# The account dimension deliberately does NOT go into ``MIQI_HOME`` itself:
# ``config.json`` (providers, model choice, approvals) stays device-level, and
# so do the install directory, the Chromium profile, the update cache and the
# WSL sandbox distro — see the 设备级 list in #1185.
#
# Layout::
#
#     <data root>/workspace/                legacy, claimed by the first account
#     <data root>/accounts/.active          登录账号 sub（登出即删）
#     <data root>/accounts/.legacy-owner    认领上面那个目录的账号 sub
#     <data root>/accounts/<sub>/workspace/ 其它账号各自的工作区
#
# Both the Desktop main process (``ipc/workspace-path.ts``) and this module
# implement the same rule; they must stay in step or the renderer's
# workspace-containment check and the runtime's writes disagree about where
# the workspace is.

ACCOUNTS_DIR_NAME = "accounts"
ACTIVE_ACCOUNT_FILE = ".active"
LEGACY_WORKSPACE_OWNER_FILE = ".legacy-owner"

#: Serialised default of ``agents.defaults.workspace``.  A config carrying
#: this exact string means "follow the data root" rather than "the user
#: picked this directory", and only that case gets the account dimension.
DEFAULT_WORKSPACE_VALUE = f"~/{DEFAULT_HOME_NAME}/workspace"

#: Account ids come from the platform and end up as a path segment.  Anything
#: outside this set is treated as "no account" rather than sanitised —
#: sanitising ``../x`` into ``__x`` would silently point a request at a
#: different account's directory.  Leading dots are excluded on top of the
#: separator ban: ``..`` resolves to ``<data root>/workspace`` (the shared
#: root), ``.`` to ``<accounts>/workspace``, and ``.active`` would collide
#: with the marker file itself.
_ACCOUNT_SUB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def is_valid_account_sub(sub: str | None) -> bool:
    """Whether *sub* is safe to use as a path segment (see ``_ACCOUNT_SUB_RE``)."""
    return bool(sub) and bool(_ACCOUNT_SUB_RE.match(sub))


def get_accounts_dir() -> Path:
    return get_miqi_home() / ACCOUNTS_DIR_NAME


def get_active_account_file() -> Path:
    return get_accounts_dir() / ACTIVE_ACCOUNT_FILE


def get_legacy_workspace_owner_file() -> Path:
    return get_accounts_dir() / LEGACY_WORKSPACE_OWNER_FILE


def get_account_workspace(sub: str) -> Path:
    """Workspace root of account *sub* (caller must have validated *sub*)."""
    return get_accounts_dir() / sub / "workspace"


def _read_account_marker(path: Path) -> str | None:
    """Read an account sub from *path*; None when absent or unusable.

    Invalid content is reported as "no account" instead of raising: a
    truncated or hand-edited marker must not take the whole runtime down
    with it, and must never be interpreted as a different account.
    """
    try:
        if not path.exists():
            return None
        sub = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return sub if is_valid_account_sub(sub) else None


def get_active_account() -> str | None:
    """The account the Desktop is currently logged in as, if any.

    Written by the Desktop main process on login and removed on logout, so
    this follows an account switch without restarting the bridge.  Read on
    every call rather than cached: the renderer mounts the sidebar the moment
    login returns, and a stale answer here lists the previous account's
    sessions.
    """
    return _read_account_marker(get_active_account_file())


def get_legacy_workspace_owner() -> str | None:
    """Account that claimed the pre-#1185 ``<data root>/workspace``, if any."""
    return _read_account_marker(get_legacy_workspace_owner_file())


def get_default_workspace_path() -> Path:
    """Default workspace root, account-scoped when an account is active.

    ``<data root>`` is ``MIQI_HOME`` when set, else ``~/.miqi`` with the
    historical ``~/.assistant`` fallback — the same root
    :func:`miqi.utils.helpers.get_data_path` picks, so the runtime's workspace
    and the CLI's no longer disagree on legacy installs.
    """
    if _miqi_home_is_configured():
        data_root = get_miqi_home()
    else:
        default_home = get_miqi_home()
        legacy_home = get_legacy_data_dir()
        data_root = (
            legacy_home if legacy_home.exists() and not default_home.exists() else default_home
        )

    sub = get_active_account()
    if sub is None or get_legacy_workspace_owner() == sub:
        # No account (CLI, tests, Desktop before login) — and the account that
        # claimed the legacy directory keeps working in place: moving
        # ``<data root>/workspace`` would have to race the running bridge for
        # it, and a failed move is exactly the "upgrade ate my history" case
        # #1185 warns about.
        return data_root / "workspace"
    return data_root / ACCOUNTS_DIR_NAME / sub / "workspace"


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


def normalize_declared_separators(raw: str) -> str:
    """Rewrite an agent-declared path to POSIX separators for prefix parsing.

    A backslash is an ordinary filename character on POSIX, so
    ``sessions\\key\\files\\x.pdf`` is a *single* path component there and
    the session-prefix rule below would never see it.  Windows
    rooted-relative input (``\\sessions\\key\\files\\x.pdf``) additionally
    loses its single leading separator.  A genuine POSIX absolute path
    (``/home/...``) is left alone.

    A UNC root keeps **both** leading separators: ``\\\\server\\share\\x``
    becomes ``//server/share/x``.  Dropping one would silently turn it into
    the POSIX-rooted ``/server/share/x``, which is a different location.
    """
    normalized = raw.replace("\\", "/")
    if raw.startswith("\\") and not raw.startswith("\\\\") and normalized.startswith("/"):
        return normalized[1:]
    return normalized


def normalize_session_prefixed(rel: str | Path, workspace: Path | None) -> Path | None:
    """Resolve a workspace-base-relative path against the session files root.

    *rel* is relative and starts with ``sessions/<key>/files/...``:

    - ``key`` is the current session key: strip the prefix so the file lands
      in the session files root instead of being nested under it (#806).
    - ``key`` is another session: reject — sessions are isolated.
    - *workspace* is not session-structured: return None, so the caller
      falls back to plain ``workspace / rel`` joining.

    Separators are normalized here rather than by each caller: what counts as
    ``sessions/<key>/files`` is part of this rule, and a caller that forgets
    the normalization silently gets the pre-#806 nesting back.
    """
    layout = session_files_layout(workspace)
    if layout is None:
        return None
    base, current_key = layout
    parts = list(Path(normalize_declared_separators(str(rel))).parts)
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
