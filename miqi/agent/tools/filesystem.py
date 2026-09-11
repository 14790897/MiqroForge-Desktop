"""File system tools: read, write, edit — with sandbox path mapping support.

When a WSL-based sandbox is active, file operations are routed through
the sandbox's run_command() method, which executes inside WSL+bwrap.
Otherwise, local filesystem operations are used directly.
"""

import difflib
import hashlib as _hashlib
import json as _json
import logging
import threading
from pathlib import Path
from typing import Any, Iterable

from miqi.agent.tools.base import Tool

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File snapshot store — keeps original content before first write/edit
# so we can diff and revert without git.
# Snapshots are persisted to ~/.miqi/snapshots/<sha256>.json
# ---------------------------------------------------------------------------

_snapshots_lock = threading.Lock()


def _snapshots_dir() -> Path:
    from miqi.paths import get_miqi_home

    d = get_miqi_home() / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _snapshot_file_for_dir(snapshot_dir: Path, key: str) -> Path:
    h = _hashlib.sha256(key.encode()).hexdigest()
    return snapshot_dir / f"{h}.json"


def _snapshot_file(key: str) -> Path:
    return _snapshot_file_for_dir(_snapshots_dir(), key)


def _read_snapshot(key: str, snapshot_dir: Path | None = None) -> str | None:
    if snapshot_dir:
        p = _snapshot_file_for_dir(snapshot_dir, key)
        if p.exists():
            try:
                data = _json.loads(p.read_text(encoding="utf-8"))
                return data.get("content")
            except Exception:
                pass
    # Fall back to global dir
    p = _snapshot_file(key)
    try:
        if p.exists():
            data = _json.loads(p.read_text(encoding="utf-8"))
            return data.get("content")
    except Exception:
        pass
    return None


def _write_snapshot_to(snapshot_dir: Path, key: str, content: str) -> bool:
    """Write a snapshot file. Returns True on success, False on failure."""
    p = _snapshot_file_for_dir(snapshot_dir, key)
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        p.write_text(
            _json.dumps({"path": key, "content": content}, ensure_ascii=False),
            encoding="utf-8",
        )
        return True
    except Exception:
        _log.warning("Failed to write snapshot for %s to %s", key, p, exc_info=True)
        return False


def _write_snapshot(key: str, content: str) -> None:
    _write_snapshot_to(_snapshots_dir(), key, content)


def _maybe_snapshot(resolved: Path, snapshot_dir: Path | None = None) -> bool:
    """Save a snapshot of *resolved* if not already snapshotted (disk-backed).

    Returns True if a snapshot was successfully written or already existed,
    False if the write failed.
    """
    key = str(resolved)
    effective_dir = snapshot_dir or _snapshots_dir()
    with _snapshots_lock:
        if _read_snapshot(key, snapshot_dir=snapshot_dir) is not None:
            return True
        if resolved.exists():
            try:
                content = resolved.read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = ""
        else:
            content = ""
        return _write_snapshot_to(effective_dir, key, content)


def _restore_snapshot(resolved: Path, snapshot_dir: Path | None = None) -> bool:
    """Restore file from disk snapshot. Returns True if successful."""
    key = str(resolved)
    with _snapshots_lock:
        original = _read_snapshot(key, snapshot_dir=snapshot_dir)
    if original is None:
        return False
    try:
        if original == "":
            if resolved.exists():
                resolved.unlink()
        else:
            resolved.write_text(original, encoding="utf-8")
        return True
    except Exception:
        return False


def _delete_snapshot(key: str, snapshot_dir: Path | None = None) -> None:
    """Remove snapshot file from disk."""
    effective_dir = snapshot_dir or _snapshots_dir()
    p = _snapshot_file_for_dir(effective_dir, key)
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass


def _has_symlink_in_path(p: Path) -> bool:
    """Return True if any existing component of *p* is a symbolic link.

    Used as defense-in-depth when a directory restriction is active:
    symlinks inside the allowed directory that point outside it would
    otherwise pass the ``relative_to`` check after ``resolve()``.
    """
    accumulated = Path(p.anchor)
    for part in p.parts[1:]:  # Skip the root anchor ('/' or 'C:\\')
        accumulated = accumulated / part
        if accumulated.is_symlink():
            return True
        if not accumulated.exists():
            break  # Remaining components don't exist yet; no further symlinks.
    return False


# ---------------------------------------------------------------------------
# Sandbox-aware path resolution & file operations
# ---------------------------------------------------------------------------

def _get_active_sandbox(sandbox_manager):
    """Get the active sandbox from the manager, if any."""
    if sandbox_manager is None:
        return None
    sandbox = sandbox_manager.active_sandbox
    if sandbox and sandbox.is_running:
        return sandbox
    return None


def _effective_shared_roots(
    base: Iterable[Path],
    user_roots: Iterable[Any] | None,
    allow_user_roots: bool,
) -> list[Path]:
    """Merge per-call user-mentioned roots into the static shared roots.

    The KUN runtime injects ``_user_roots`` (auto-sensed directories from
    the user's message, issue #821) into each file-tool call.  When
    ``tools.auto_user_dirs`` is disabled, or no roots were injected, the
    static shared roots are returned unchanged.
    """
    merged = list(base or [])
    if not allow_user_roots or not user_roots:
        return merged
    for r in user_roots:
        try:
            p = Path(r)
        except (TypeError, ValueError):
            continue
        if p not in merged:
            merged.append(p)
    return merged


def _persist_tracked_file(
    workspace: Path | None,
    file_path: str | Path,
    op: str = "write",
    session_key: str | None = None,
) -> None:
    """Save a tracked file entry to the session's tracked_files.json.

    This ensures the Task Assets panel survives session switches — without
    this, files discovered from agent tool calls exist only in the
    frontend's in-memory state and are lost when the component unmounts.
    """
    if not session_key or not workspace:
        _log.debug("_persist_tracked_file: skipped (no session_key or workspace)")
        return
    try:
        from miqi.session.manager import SessionManager
        sm = SessionManager(workspace)
        # Strip the client_id prefix.  The orchestrator passes
        # ctx.session_id which has the form "<client_id>:<session_key>"
        # (e.g. "miqi-desktop:desktop:1784099553254"), but the frontend
        # and SessionManager use just "<session_key>" as the lookup key.
        if ":" in session_key:
            parts = session_key.split(":", 1)
            if len(parts) == 2 and parts[0] != "desktop":
                session_key = parts[1]
        # Use workspace-relative paths for consistent reads across sessions
        rel_path = str(file_path)
        ws_str = str(workspace.resolve()).replace("\\", "/")
        rel_str = str(Path(file_path)).replace("\\", "/")
        if rel_str.startswith(ws_str + "/"):
            rel_path = rel_str[len(ws_str) + 1:]
        elif rel_str.startswith(ws_str):
            rel_path = rel_str[len(ws_str):].lstrip("/")
        sm.save_tracked_file(session_key, rel_path, op=op)
        _log.info("_persist_tracked_file: ok session=%s path=%s", session_key, rel_path)
    except Exception as exc:
        _log.warning("_persist_tracked_file: failed session=%s path=%s: %s", session_key, file_path, exc)


def _sandbox_to_host_path(sandbox_path: str, workspace: Path | None, sandbox) -> str:
    """Map sandbox-internal path to host workspace path.

    The bwrap sandbox always mounts the workspace at ``/home/miqi/workspace``
    inside its mount namespace (see :meth:`BwrapSandbox._build_bwrap_args`).
    This function maps sandbox-internal absolute paths like
    ``/home/miqi/workspace/report.md`` to their host-workspace equivalent
    (e.g. ``/home/user/.miqi/workspace/report.md``).

    Also handles /mnt/<drive>/... paths from WSL sandbox that access the
    host filesystem directly (issue #474).

    Returns *sandbox_path* unchanged when it does not start with a known
    sandbox workspace prefix.
    """
    import re as _re

    if not sandbox_path or not workspace:
        return sandbox_path

    # Handle /mnt/<drive>/... paths from WSL sandbox — convert to Windows path (issue #474)
    mnt_match = _re.match(r"^/mnt/([a-z])/(.+)$", sandbox_path)
    if mnt_match:
        drive = mnt_match.group(1).upper()
        rest = mnt_match.group(2)
        return f"{drive}:/{rest}"

    # The sandbox-internal workspace prefix — hard-coded in bwrap's
    # --bind <host_dir> /home/miqi/workspace argument.
    sb_internal_ws = "/home/miqi/workspace"
    if sandbox_path == sb_internal_ws:
        return str(workspace.resolve()).replace("\\", "/")
    if sandbox_path.startswith(sb_internal_ws + "/"):
        host_ws = str(workspace.resolve()).replace("\\", "/")
        rel = sandbox_path[len(sb_internal_ws) + 1:]
        return f"{host_ws}/{rel}"
    return sandbox_path



def _canonicalize_wsl_mnt_path(
    mnt_path: str,
    workspace: Path | None,
    extra_roots: Iterable[Path] | None = None,
    session_files_dir: Path | None = None,
) -> str:
    """Canonicalize a /mnt/<drive>/... path and verify workspace containment.

    Converts to host path, resolves ``..`` traversal, checks the resolved
    path stays within *workspace* (or within any of the ``extra_roots``),
    and returns the canonical /mnt/ path.  Raises PermissionError if the
    path escapes every legal root (issue #474).

    ``extra_roots`` lets callers broaden the whitelist beyond the per-session
    workspace to host-global shared roots (issue #516) such as
    ``~/.miqi/workspace/memory`` and ``~/.miqi/workspace/skills`` — the
    directories the system prompt legitimately directs the agent to read and
    write.  A path under another session's ``sessions/<other>/files`` is never
    in any root, so per-session isolation is preserved.

    ``session_files_dir`` is the per-session files dir (when session
    isolation is active, i.e. ``<workspace>/sessions/<key>/files``).  When
    provided, a path that lives under *any* session's files dir must be the
    current session's — a path under another session's files dir is rejected
    even though it is inside *workspace*.  This lets read tools resolve
    against the root workspace (the working directory the system prompt
    tells the agent) while preserving session isolation (#613 follow-up).

    Returns *mnt_path* unchanged for non-/mnt/ paths or when no workspace
    is configured.
    """
    import re as _re

    if workspace is None and not extra_roots:
        return mnt_path

    m = _re.match(r"^/mnt/([a-z])/(.+)$", mnt_path)
    if not m:
        return mnt_path

    drive = m.group(1).upper()
    rest = m.group(2)
    host_str = f"{drive}:/{rest}"

    try:
        import os as _os
        import sys as _sys
        normalized = _os.path.normpath(host_str)

        # On non-Windows, drive-letter paths (C:/...) don't resolve
        # meaningfully — Path.resolve() prepends CWD.  Use normpath
        # for .. resolution and skip the host-filesystem containment
        # check (WSL sandbox only exists on Windows anyway).
        if _sys.platform != "win32":
            # Resolve .. via normpath and convert back to /mnt/ format
            nm = _re.match(r"^([A-Za-z]):/(.+)$", normalized)
            if nm:
                return f"/mnt/{nm.group(1).lower()}/{nm.group(2)}"
            return mnt_path

        resolved = Path(normalized).resolve()
    except Exception:
        _log.warning("_canonicalize_wsl_mnt_path: cannot resolve %s, rejecting path", host_str)
        raise PermissionError(
            f"无法规范化路径 '{host_str}'：解析失败"
        )

    # Build the list of legal roots: the per-session workspace plus any
    # host-global shared roots (issue #516).  A path is accepted if it lives
    # under ANY of them; only a path under none is rejected.  This keeps the
    # default single-root behavior (extra_roots empty) identical to before.
    roots: list[Path] = []
    if workspace is not None:
        roots.append(workspace.resolve())
    if extra_roots:
        for r in extra_roots:
            try:
                roots.append(Path(r).resolve())
            except Exception:
                _log.debug("_canonicalize_wsl_mnt_path: skipping unresolvable extra root %r", r)

    for root in roots:
        try:
            if root.is_file():
                # File roots (e.g. config.json) gate the exact file only —
                # never sibling/descendant paths like config.json.bak.
                if resolved == root.resolve():
                    break
            else:
                resolved.relative_to(root)
                break  # contained in this root — accept
        except ValueError:
            continue
    else:
        roots_str = ", ".join(str(r) for r in roots) if roots else "<none>"
        raise PermissionError(
            f"路径 '{host_str}'（规范化后：'{normalized}'）解析为 '{resolved}'，"
            f"不在任何合法根目录 [{roots_str}] 内。 "
            "如需访问，请在 MiQroForge 配置的 tools.extra_roots 中添加该目录。"
        )

    # Per-session isolation: when session isolation is active, a path under
    # ANY session's files dir must be the current session's.  This allows
    # read tools to resolve against the root workspace (the working dir the
    # system prompt advertises) without opening another session's files.
    if session_files_dir is not None:
        # Canonicalize once: resolved/workspace are canonical, so a relative
        # or symlinked session_files_dir would make sessions_root lexical
        # and let another session's canonical path skip the check.
        try:
            canonical_session_files_dir = session_files_dir.resolve()
        except OSError:
            canonical_session_files_dir = Path(session_files_dir).absolute()
        parents = canonical_session_files_dir.parents
        sessions_root = parents[1] if len(parents) >= 2 else None
        # sessions_root = <workspace>/sessions
        if sessions_root is not None:
            try:
                resolved.relative_to(sessions_root)
            except ValueError:
                pass  # not under sessions/ — no isolation constraint
            else:
                try:
                    resolved.relative_to(canonical_session_files_dir)
                except ValueError:
                    # Do NOT include the resolved path: it can reveal
                    # another session's identifier and file layout.
                    raise PermissionError(
                        "路径位于其他会话的 files 目录内——"
                        "会话隔离禁止跨会话访问。 "
                        "不要重试或枚举 sessions/；请使用当前会话的工作区，"
                        "或请用户通过文件面板分享文件。"
                    )

    resolved_str = str(resolved).replace("\\", "/")
    rm = _re.match(r"^([A-Za-z]):/(.+)$", resolved_str)
    if rm:
        return f"/mnt/{rm.group(1).lower()}/{rm.group(2)}"
    return mnt_path


async def _ensure_sandbox(sandbox_manager, tool_name="file_tool", session_key=None):
    """Get or create a session-isolated sandbox.

    Industry standard: sandboxes MUST be per-session. session_key is not optional.
    Without session_key, returns None (caller must handle, no shared fallback).
    """
    if sandbox_manager is None:
        return None
    if not session_key:
        _log.warning("%s: no session_key provided, cannot ensure isolation", tool_name)
        return None
    sandbox = await sandbox_manager.get_or_create(session_key)
    if sandbox is None or not sandbox.is_running:
        _log.error("%s: failed to get_or_create sandbox for session=%s", tool_name, session_key)
        return None
    return sandbox


def _is_default_workspace(path: Path | None) -> bool:
    """Return True when *path* is the global default workspace.

    The default is ``~/.miqi/workspace`` (rebased under MIQI_HOME when set).
    When a session has a custom workspace (the user picked a project
    directory in the workspace picker), the file tools must operate
    directly on that directory — nesting it under ``sessions/<key>/files``
    would hide the project's own files (README.md, sources) from the AI.
    """
    if path is None:
        return True
    try:
        # Resolve both the candidate and the default workspace to canonical
        # absolute paths and compare exactly.  A loose `endswith("/workspace")`
        # would misclassify any custom project dir that happens to end in
        # `workspace` (e.g. /home/user/projects/workspace) as the default and
        # wrongly enable per-session files isolation for it.
        from miqi.paths import get_miqi_home
        resolved = path.expanduser().resolve()
        default = (get_miqi_home() / "workspace").resolve()
        return resolved == default
    except Exception:
        return True


def _get_session_workspace(base_workspace: Path | None, sandbox) -> Path | None:
    """Compute the per-session workspace directory based on the sandbox session_key.

    When session_workspace_enabled is True, each session gets its own
    isolated directory under <base_workspace>/sessions/<safe_key>/files/.
    This is used by WriteFileTool/ReadFileTool/EditFileTool to ensure
    files created in one session are not visible to another.

    When no sandbox is available (sandbox_manager.active_sandbox is None),
    returns the base workspace unchanged.  In that case file tools operate
    on the host filesystem which has no sandbox isolation.

    A custom (non-default) workspace — e.g. a project directory the user
    picked in the workspace picker — skips the session-files nesting: the
    project directory IS the workspace, and its own files must be directly
    readable/writable by the file tools.
    """
    if base_workspace is None or sandbox is None:
        return base_workspace
    session_key = getattr(sandbox, "session_key", None) or ""
    if not session_key:
        return base_workspace
    session_ws = _session_files_dir_for_key(base_workspace, session_key)
    return session_ws if session_ws is not None else base_workspace


def _resolve_session_dir(
    factory_session_dir: Path | None,
    sandbox_session_ws: Path | None,
    tool_workspace: Path | None,
    session_key: str | None,
    base_workspace: Path | None,
) -> Path | None:
    """Resolve the per-session files dir for one tool call.

    Precedence: factory-provided dir (single-session runtimes) → sandbox-
    derived dir → per-call derivation from the injected ``_session_key``
    (KUN multi-thread runtime / native no-sandbox path).  Returns None when
    no isolation applies; callers then keep the tool's base workspace.

    Note: the factory dir is per-REGISTRY, so a runtime that builds one
    registry for many sessions should not pass ``session_id`` to the
    factory — the per-call ``_session_key`` derivation covers that case.
    """
    if factory_session_dir is not None:
        return factory_session_dir
    if sandbox_session_ws is not None and sandbox_session_ws != tool_workspace:
        return sandbox_session_ws
    if session_key:
        return _session_files_dir_for_key(base_workspace, session_key)
    return None


def _session_files_dir_key(session_key: str) -> str:
    """Derive the on-disk per-session directory key from a session key.

    Strips the client_id prefix only for fully namespaced keys (three or
    more colon segments, e.g. ``miqi-desktop:desktop:1786...`` →
    ``desktop_1786...``) and keeps the whole key for two-segment channel
    keys (``desktop:1786...`` → ``desktop_1786...``) — matching the disk
    convention used by ``files.read`` and attachment saving.
    """
    from miqi.utils.helpers import safe_filename

    parts = session_key.split(":")
    if len(parts) >= 3:
        parts = parts[1:]
    return safe_filename("_".join(parts))


def _session_files_dir_for_key(
    base_workspace: Path | None, session_key: str | None,
) -> Path | None:
    """Compute ``<base>/sessions/<safe_key>/files`` for a session key.

    Returns None for a custom (non-default) workspace or an empty key —
    per-session isolation only applies to the default workspace.  Used by
    ``_get_session_workspace`` (sandbox-derived) and by the file tools as a
    per-call fallback when the injected ``_session_key`` is the only source
    (KUN multi-thread runtime, native no-sandbox path).
    """
    if base_workspace is None or not session_key:
        return None
    if not _is_default_workspace(base_workspace):
        _log.debug("Custom workspace, skipping session-files isolation: %s", base_workspace)
        return None
    safe_key = _session_files_dir_key(session_key)
    session_ws = base_workspace / "sessions" / safe_key / "files"
    session_ws.mkdir(parents=True, exist_ok=True)
    _log.debug("Session workspace: %s → %s", session_key, session_ws)
    return session_ws


# Sub-directories of the default workspace that stay SHARED across sessions
# (issue #516 / #689): the system prompt legitimately directs the agent to
# read/write these, so session-isolation redirection must not touch them.
# "sessions" holds the per-session dirs themselves — cross-session access is
# enforced by the containment checks, never by re-anchoring into the current
# session's dir.
_ROOT_EXEMPT_SUBDIRS = ("memory", "skills", ".skills", "sessions")


def _norm_host_path(path: str) -> str:
    """Normalize a host path for prefix comparison.

    Converts Windows backslashes and WSL ``/mnt/c/...`` forms to a single
    ``C:/...`` representation and canonicalizes through ``Path.resolve()``
    (when the path is absolute on the current platform).  The resolve step is
    what makes comparisons robust against Windows 8.3 short names (e.g. a
    temp dir handed out as ``C:\\Users\\INTERS~1\\...`` while the workspace
    resolves to ``C:\\Users\\Intership003\\...``) and case differences.
    ``..`` segments are collapsed lexically.
    """
    import os as _os
    import re as _re

    s = str(path).replace("\\", "/")
    m = _re.match(r"^/mnt/([a-zA-Z])/(.*)$", s)
    if m:
        s = f"{m.group(1).upper()}:/{m.group(2)}"
    m = _re.match(r"^([a-zA-Z]):/(.*)$", s)
    if m:
        s = f"{m.group(1).upper()}:/{m.group(2)}"
    if Path(s).is_absolute():
        try:
            s = str(Path(s).resolve()).replace("\\", "/")
        except Exception:
            pass
    return _os.path.normpath(s).replace("\\", "/")


def _is_absolute_host_path(path: str) -> bool:
    """Return True when *path* is absolute in host terms (Windows drive,
    ``/mnt/<drive>/``, or a POSIX absolute path)."""
    import re as _re

    s = str(path).replace("\\", "/")
    return bool(
        _re.match(r"^[a-zA-Z]:/", s)
        or _re.match(r"^/mnt/[a-zA-Z]/", s)
        or s.startswith("/")
    )


def _redirect_path_to_session(
    path: str,
    base_workspace: Path | None,
    session_files_dir: Path | None,
) -> str | None:
    """Map *path* into the per-session files dir; return None when it does not apply.

    Applies to:
      - absolute host paths under *base_workspace* (the directory the system
        prompt advertises as the working directory) — they are re-anchored to
        the session dir, except for the shared sub-roots (memory/ skills/ .skills/);
      - relative paths — re-anchored to *session_files_dir*.

    Paths already inside the session dir, outside the base workspace, or with
    no session dir configured return None.
    """
    if not base_workspace or not session_files_dir:
        return None

    sess_norm = _norm_host_path(str(session_files_dir.resolve())).rstrip("/")

    if not _is_absolute_host_path(path):
        # Relative paths are re-anchored to the session dir; reject any that
        # would climb out of it (../) — those must be handled (and usually
        # denied) by the callers' containment checks instead.
        import os as _os

        norm_rel = _os.path.normpath(str(path).replace("\\", "/"))
        if norm_rel == ".." or norm_rel.startswith("../"):
            return None
        return str(session_files_dir / norm_rel)

    norm = _norm_host_path(path)
    base_norm = _norm_host_path(str(base_workspace.resolve())).rstrip("/")
    if norm == base_norm or norm.startswith(base_norm + "/"):
        pass
    else:
        return None  # outside the default workspace root
    if norm == sess_norm or norm.startswith(sess_norm + "/"):
        return None  # already session-scoped
    rel = norm[len(base_norm):].lstrip("/")
    if not rel:
        return None  # the workspace root itself is not a file target
    if rel.split("/", 1)[0] in _ROOT_EXEMPT_SUBDIRS:
        return None  # shared sub-roots stay shared

    # Preserve /mnt/<drive>/ input style for WSL callers
    if str(path).replace("\\", "/").startswith("/mnt/"):
        import re as _re

        m = _re.match(r"^/mnt/([a-zA-Z])/(.*)$", str(path).replace("\\", "/"))
        sm = _re.match(r"^([A-Za-z]):/(.*)$", sess_norm)
        if m and sm:
            return f"/mnt/{m.group(1).lower()}/{sm.group(2)}/{rel}"
    return str(session_files_dir / Path(*rel.split("/")))


async def _redirect_new_file_write(
    path: str,
    base_workspace: Path | None,
    session_files_dir: Path | None,
    exists_check,
) -> str:
    """Redirect a NEW-file write under the default workspace root into the
    per-session files dir (session isolation, #221 / #613 follow-up).

    The system prompt advertises the workspace root as the working directory,
    so models write absolute root paths (e.g. ``C:\\Users\\...\\.miqi\\workspace\\x.md``).
    Those must land in ``sessions/<key>/files/`` instead of the shared root.
    Files that already exist at the target are edited in place (shared
    bootstrap files such as AGENTS.md), and shared sub-roots (memory/,
    skills/, .skills/) are never redirected.
    """
    redirected = _redirect_path_to_session(path, base_workspace, session_files_dir)
    if redirected is None or redirected == path:
        return path
    try:
        if await exists_check(path):
            return path  # edit-in-place of an existing shared file
    except Exception:
        return path  # existence unknown — never redirect blindly
    return redirected


def _reject_foreign_session_path(
    resolved: Path, base_workspace: Path | None, session_files_dir: Path | None,
) -> None:
    """Raise PermissionError when *resolved* lands in another session's dir.

    The default workspace's ``sessions/`` tree is per-session isolated: when
    isolation is active, any target inside ``<base>/sessions/`` must be the
    CURRENT session's files dir (its snapshots dir is covered by the same
    ancestor check).  Native (no-sandbox) resolution has no other
    enforcement point, and the WSL path enforces this via
    ``_canonicalize_wsl_mnt_path(session_files_dir=...)``.
    """
    if base_workspace is None or session_files_dir is None:
        return
    try:
        sessions_root = base_workspace.resolve() / "sessions"
        if resolved == sessions_root or resolved.is_relative_to(sessions_root):
            sess = session_files_dir.resolve()
            if resolved != sess and not resolved.is_relative_to(sess):
                raise PermissionError(
                    f"Path '{resolved}' is inside another session's files "
                    f"directory (current session dir: {sess})"
                )
    except PermissionError:
        raise
    except OSError:
        pass  # unresolvable path — later operations will surface the error


def _make_exists_check(shared_roots, sandbox, session_ws, native_base_dir=None):
    """Async 'does *path* exist?' callable for ``_redirect_new_file_write``.

    Sandbox-aware when a WSL sandbox is active; otherwise a native
    ``Path.exists()`` probe (Windows/posix).  Relative paths are resolved
    against ``native_base_dir`` (the tool's workspace) — never the process
    CWD.  Probe failures PROPAGATE so ``_redirect_new_file_write`` keeps the
    original path instead of treating an existing shared file as new
    (CodeRabbit #731).
    """
    if sandbox is not None and getattr(sandbox, "_use_wsl", False):

        async def _check(p: str) -> bool:
            sb = _resolve_sandbox_path(
                p, session_ws, sandbox, extra_roots=shared_roots,
            )
            return await _sandbox_file_exists(sandbox, sb)

        return _check

    async def _check(p: str) -> bool:
        if not _is_absolute_host_path(p) and native_base_dir:
            p = str(Path(native_base_dir) / p)
        return Path(_norm_host_path(p)).exists()

    return _check


def _resolve_sandbox_path(
    path: str,
    workspace: Path | None,
    sandbox,
    extra_roots: Iterable[Path] | None = None,
    session_files_dir: Path | None = None,
) -> str:
    """Resolve a path for use inside the sandbox.

    Returns a Linux-style absolute path inside the sandbox filesystem.
    Handles Windows paths (e.g. C:\\Users\\...) by mapping them to
    /home/miqi/workspace/... relative to the workspace root.
    """
    import re as _re

    original_path = path

    # ── Windows absolute path: C:\... → /home/miqi/workspace/... ──
    win_match = _re.match(r"^([A-Za-z]):[/\\](.+)$", path)
    if win_match:
        drive = win_match.group(1).lower()
        rest = win_match.group(2).replace("\\", "/")
        # WSL sandbox: use /mnt/ for direct host filesystem access (issue #474)
        if sandbox is not None and getattr(sandbox, "_use_wsl", False):
            result = f"/mnt/{drive}/{rest}"
            result = _canonicalize_wsl_mnt_path(result, workspace, extra_roots, session_files_dir)
            _log.debug("Sandbox path: %s → %s (WSL /mnt/ direct)", original_path, result)
            return result
        # If the workspace matches this drive, compute relative path
        if workspace:
            ws_str = str(workspace).replace("\\", "/")
            ws_match = _re.match(r"^([A-Za-z]):/(.+)$", ws_str)
            if ws_match and ws_match.group(1).lower() == drive:
                ws_rest = ws_match.group(2).rstrip("/")
                if rest.startswith(ws_rest + "/") or rest == ws_rest:
                    rel = rest[len(ws_rest):].lstrip("/")
                    result = f"/home/miqi/workspace/{rel}" if rel else "/home/miqi/workspace"
                    _log.debug("Sandbox path: %s → %s (Windows workspace remap)", original_path, result)
                    return result
        # Fallback: map full Windows path under /mnt/ in the sandbox
        result = f"/mnt/{drive}/{rest}"
        _log.debug("Sandbox path: %s → %s (Windows /mnt/ fallback)", original_path, result)
        return result

    # ── Relative path → resolve against sandbox workspace ──
    if not path.startswith("/"):
        # WSL sandbox: resolve relative to host workspace via /mnt/ (issue #474)
        if sandbox is not None and getattr(sandbox, "_use_wsl", False) and workspace:
            ws_str = str(workspace.resolve()).replace("\\", "/")
            ws_match = _re.match(r"^([A-Za-z]):/(.+)$", ws_str)
            if ws_match:
                drive = ws_match.group(1).lower()
                ws_rest = ws_match.group(2).rstrip("/")
                result = f"/mnt/{drive}/{ws_rest}/{path}"
                result = _canonicalize_wsl_mnt_path(result, workspace, extra_roots, session_files_dir)
                _log.debug("Sandbox path: %s → %s (WSL relative /mnt/)", original_path, result)
                return result
        # Compute the correct sandbox base path.
        # If the tool's workspace is a subdirectory of the sandbox's global
        # workspace (e.g. per-session dir), use the corresponding sandbox path
        # so that per-session files are isolated from other sessions.
        sandbox_base = "/home/miqi/workspace"
        if workspace:
            ws_str = str(workspace.resolve()).replace("\\", "/")
            sb_ws_str = str(sandbox.workspace).replace("\\", "/")
            if ws_str.startswith(sb_ws_str) and ws_str != sb_ws_str:
                rel_subdir = ws_str[len(sb_ws_str):].lstrip("/")
                sandbox_base = f"/home/miqi/workspace/{rel_subdir}"
        result = f"{sandbox_base}/{path}"
        _log.debug("Sandbox path: %s → %s (relative remap, base=%s)", original_path, result, sandbox_base)
        return result

    # ── Linux path that starts with workspace prefix → remap ──
    if workspace:
        ws_str = str(workspace)
        # Handle case where workspace is a Windows path but input is already /mnt/c/...
        if ws_str[1:2] == ":":
            # WSL sandbox: /mnt/ paths already access host filesystem directly (issue #474)
            if sandbox is not None and getattr(sandbox, "_use_wsl", False):
                result = _canonicalize_wsl_mnt_path(path, workspace, extra_roots, session_files_dir)
                _log.debug("Sandbox path: %s → %s (WSL /mnt/ keep)", original_path, result)
                return result
            drive = ws_str[0].lower()
            ws_rest = ws_str[2:].replace("\\", "/").lstrip("/")
            mnt_prefix = f"/mnt/{drive}/{ws_rest}"
            if path.startswith(mnt_prefix):
                rel = path[len(mnt_prefix):].lstrip("/")
                result = f"/home/miqi/workspace/{rel}" if rel else "/home/miqi/workspace"
                _log.debug("Sandbox path: %s → %s (/mnt/ remap)", original_path, result)
                return result

    _log.debug("Sandbox path: %s → %s (no remap needed)", original_path, path)
    return path


# ---------------------------------------------------------------------------
# Write authorization card (issue #864) — on-demand write access outside the
# write whitelist.  Reads stay wide (home + whole disk, see tool_registry_factory
# `_read_shared_roots`); writes stay narrow (workspace + shared roots +
# extra_roots) and, when a target escapes every legal write root, this pops the
# existing user-input card with [允许本次 / 本目录不再询问 / 拒绝] instead of a
# hard PermissionError dead-end.
# ---------------------------------------------------------------------------

def _write_target_host_path(path: str, base_dir: Path | None) -> Path:
    """Resolve a write target to a canonical host path for authorization."""
    p = Path(path).expanduser()
    if not p.is_absolute() and base_dir is not None:
        p = base_dir / p
    try:
        return p.resolve()
    except Exception:
        try:
            return p.absolute()
        except Exception:
            return p


def _target_in_roots(target: Path, roots: Iterable[Path]) -> bool:
    """Return True when *target* is contained in any of *roots*."""
    for root in roots:
        try:
            target.relative_to(Path(root).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def _grantable_dir(target: Path, workspace_root: Path | None) -> Path | None:
    """Return the directory a write authorization would grant, or None when it
    is protected (must never be grantable): drive/filesystem root, the user
    profile root, top-level system dirs, the host config, or any session dir.
    """
    try:
        grant = target.parent.resolve()
    except Exception:
        grant = target.parent.absolute()
    if grant == grant.parent:
        return None  # drive root / filesystem root
    try:
        if grant == Path.home().resolve():
            return None
    except Exception:
        pass
    from miqi.agent.tools.user_roots import (
        _is_protected_extra_root,
        _is_top_level_system_dir,
    )
    if _is_top_level_system_dir(grant):
        return None
    if workspace_root is not None and _is_protected_extra_root(grant, workspace_root):
        return None
    return grant


async def _ask_write_permission(write_resolver, target: Path, grant_dir: Path) -> str:
    """Pop the write-authorization card; return 'once' | 'always_dir' | 'deny'."""
    payload = {
        "title": "授权写入工作区外目录",
        "message": (
            f"AI 请求写入 {target}（目录 {grant_dir}），"
            f"该目录不在已授权的写入根内。是否允许？"
        ),
        "choices": [
            {"id": "once", "label": "允许本次"},
            {"id": "always_dir", "label": "本目录不再询问"},
            {"id": "deny", "label": "拒绝", "role": "cancel"},
        ],
        "timeout_seconds": 120,
    }
    try:
        result = await write_resolver(payload)
    except Exception:
        return "deny"
    if not isinstance(result, dict) or result.get("status") != "submitted":
        return "deny"
    answers = result.get("answers") or {}
    cid = str(answers.get("choice_id") or "")
    return cid if cid in ("once", "always_dir") else "deny"


async def _resolve_write_shared_roots(
    path: str,
    *,
    base_dir: Path | None,
    workspace_root: Path | None,
    shared: Iterable[Path],
    granted: set[str],
    once_granted: set[str] | None = None,
    write_resolver=None,
    persist_extra_root=None,
    boundary_enforced: bool = True,
    bypass: bool = False,
) -> list[Path] | None:
    """Pre-flight write authorization (issue #864).

    Returns an augmented shared-roots list when the write target is authorized
    (already in-roots, previously granted, or newly granted via the card), or
    None when the write must be denied (out-of-roots and declined / headless /
    protected).  The caller uses the returned list as its ``shared_roots`` and
    keeps its existing PermissionError path on None — this helper only ever
    ADDS roots, never removes enforcement.

    ``boundary_enforced`` marks whether the current execution path has an
    actual write whitelist (WSL sandbox containment, or native
    ``restrict_to_workspace``).  When False — the native unrestricted path —
    there is no whitelist to widen, so the card must not fire and deny an
    otherwise-legal write.

    ``bypass`` reflects the approval-bypass switches (``approvals.bypass_all`` /
    ``approvals.bypass_file_write_approval``).  When True the card is skipped
    and the directory is granted session-scoped (never persisted to
    ``tools.extra_roots`` — a bypass is not consent to widen the whitelist).
    Protected targets (config / sessions / drive root / home root / top-level
    system dirs) remain non-grantable regardless of ``bypass``.

    ``granted`` is the SESSION-scoped set (populated by "本目录不再询问" and
    bypass, survives across tool calls).  ``once_granted`` is the
    INVOCATION-scoped set shared by the authorize_paths pre-flight and the
    actual write path WITHIN one tool call — "允许本次" is recorded there, so
    a later tool call must re-authorize (it is not a session-wide grant).
    """
    import os as _os

    shared_list = list(shared or [])
    if not boundary_enforced:
        return shared_list

    target = _write_target_host_path(path, base_dir)

    roots: list[Path] = []
    if base_dir is not None:
        roots.append(base_dir)
    if workspace_root is not None:
        roots.append(workspace_root)
    roots.extend(shared_list)
    granted_roots: list[Path] = [Path(g) for g in granted] if granted else []
    once_roots: list[Path] = [Path(g) for g in once_granted] if once_granted else []
    roots.extend(granted_roots)
    roots.extend(once_roots)
    if _target_in_roots(target, roots):
        # Already authorized (in-workspace/shared, previously granted this
        # session, or granted "once" earlier in this same tool call).  Return
        # the granted dirs alongside the static roots so the caller's
        # `_resolve_path`/`_resolve_sandbox_path` whitelist check also accepts
        # them — a granted dir must widen the actual enforcement, not just pass
        # this pre-flight.
        return [*shared_list, *granted_roots, *once_roots]

    grant_dir = _grantable_dir(target, workspace_root)
    if grant_dir is None:
        return None
    if bypass:
        # Approval bypass: skip the card, grant the directory for this session.
        granted.add(_os.path.normcase(str(grant_dir)))
        return [*shared_list, grant_dir]
    if write_resolver is None:
        return None

    choice = await _ask_write_permission(write_resolver, target, grant_dir)
    if choice == "always_dir":
        granted.add(_os.path.normcase(str(grant_dir)))
        if persist_extra_root is not None:
            try:
                await persist_extra_root(grant_dir)
            except Exception:
                _log.warning("persist_extra_root failed for %s", grant_dir, exc_info=True)
        return [*shared_list, grant_dir]
    if choice == "once":
        # "允许本次" is invocation-scoped: record it on the shared once set so
        # the authorize_paths pre-flight and the actual write path within THIS
        # tool call agree, but never on the session set — a later call must
        # re-authorize.  Not persisted to tools.extra_roots.
        if once_granted is not None:
            once_granted.add(_os.path.normcase(str(grant_dir)))
        return [*shared_list, grant_dir]
    return None


def _resolve_path(
    path: str,
    workspace: Path | None = None,
    allowed_dir: Path | None = None,
    sandbox_manager=None,
    shared_roots: Iterable[Path] | None = None,
) -> Path:
    """Resolve path against workspace (if relative) and enforce directory restriction.

    When a sandbox_manager is provided and has an active sandbox, file
    operations are automatically redirected to the sandbox's workspace
    directory. This ensures each conversation's AI only accesses its
    own isolated filesystem.

    When *allowed_dir* is set, paths inside *shared_roots* are also
    accepted so ``tools.extra_roots`` and workspace memory/skills roots
    keep working on native paths, not only WSL sandbox paths.
    """
    p = Path(path).expanduser()
    if not p.is_absolute() and workspace:
        p = workspace / p

    # If sandbox is active, redirect path into sandbox workspace
    sandbox = _get_active_sandbox(sandbox_manager)
    if sandbox is not None:
        # Map the path into the sandbox's workspace on the host
        sandbox_ws = Path(sandbox.workspace_path)
        if workspace:
            try:
                # If the path is under the original workspace, remap
                resolved_orig = p.resolve()
                orig_ws = workspace.resolve()
                try:
                    rel = resolved_orig.relative_to(orig_ws)
                    p = sandbox_ws / rel
                except ValueError:
                    # Path is outside workspace — leave as-is
                    pass
            except Exception:
                pass
        elif not p.is_absolute():
            p = sandbox_ws / p

    # Defense-in-depth: reject symlink components before resolving (SEC-06).
    if allowed_dir and _has_symlink_in_path(p):
        raise PermissionError(
            f"路径 '{path}' 包含符号链接，受限模式下不允许。"
        )
    resolved = p.resolve()
    if allowed_dir:
        try:
            resolved.relative_to(allowed_dir.resolve())
        except ValueError:
            allowed = False
            for root in shared_roots or []:
                try:
                    resolved.relative_to(Path(root).resolve())
                    allowed = True
                    break
                except ValueError:
                    continue
            if not allowed:
                raise PermissionError(f"路径 {path} 超出允许目录 {allowed_dir}")
    return resolved


async def _sandbox_read_file(sandbox, sandbox_path: str) -> str:
    """Read a file inside the sandbox via run_command."""
    escaped = sandbox_path.replace("'", "'\\''")
    rc, stdout, stderr = await sandbox.run_command(f"cat '{escaped}'")
    if rc != 0:
        raise FileNotFoundError(f"无法读取 {sandbox_path}：{stderr}")
    return stdout


async def _sandbox_write_file(sandbox, sandbox_path: str, content: str) -> None:
    """Write content to a file inside the sandbox via run_command."""
    escaped_path = sandbox_path.replace("'", "'\\''")
    # Use base64 encoding to safely transfer content through shell
    import base64
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    rc, _, stderr = await sandbox.run_command(
        f"mkdir -p '$(dirname \"{escaped_path}\")' && "
        f"echo '{encoded}' | base64 -d > '{escaped_path}'"
    )
    if rc != 0:
        raise IOError(f"Cannot write {sandbox_path}: {stderr}")


async def _sandbox_file_exists(sandbox, sandbox_path: str) -> bool:
    """Check if a file exists inside the sandbox."""
    escaped = sandbox_path.replace("'", "'\\''")
    rc, _, _ = await sandbox.run_command(f"test -f '{escaped}'")
    return rc == 0


async def _sandbox_dir_exists(sandbox, sandbox_path: str) -> bool:
    """Check if a directory exists inside the sandbox."""
    escaped = sandbox_path.replace("'", "'\\''")
    rc, _, _ = await sandbox.run_command(f"test -d '{escaped}'")
    return rc == 0


async def _sandbox_list_dir(sandbox, sandbox_path: str) -> str:
    """List directory contents inside the sandbox."""
    escaped = sandbox_path.replace("'", "'\\''")
    # Simple approach: use ls -1p (appends '/' to directory names),
    # then format in Python rather than in bash to avoid f-string
    # escaping issues with bash variables like ${line: -1}.
    rc, stdout, stderr = await sandbox.run_command(
        f"ls -1p '{escaped}' 2>&1"
    )
    if rc != 0:
        raise IOError(f"Cannot list {sandbox_path}: {stderr}")
    # Format: directories get 'dir ' prefix, files get 5-space indent
    lines = []
    for entry in stdout.strip().splitlines():
        if entry.endswith("/"):
            lines.append(f"dir {entry.rstrip('/')}")
        else:
            lines.append(f"     {entry}")
    return "\n".join(lines)


class ReadFileTool(Tool):
    """Tool to read file contents — works with local or sandbox filesystems."""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        sandbox_manager=None,
        shared_roots: Iterable[Path] | None = None,
        session_files_dir: Path | None = None,
        allow_user_roots: bool = True,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._sandbox_manager = sandbox_manager
        self._shared_roots = list(shared_roots or [])
        self._session_files_dir = session_files_dir
        self._allow_user_roots = allow_user_roots

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read the contents of a file at the given path."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to read"
                }
            },
            "required": ["path"]
        }

    async def execute(self, path: str, **kwargs: Any) -> str:
        _sess_key = kwargs.pop("_session_key", None)
        shared = _effective_shared_roots(
            self._shared_roots, kwargs.pop("_user_roots", None), self._allow_user_roots,
        )
        sandbox = await _ensure_sandbox(self._sandbox_manager, session_key=_sess_key)
        session_ws = _get_session_workspace(self._workspace, sandbox)
        # Factory-provided session dir wins over the sandbox-derived one: it
        # exists even when no sandbox is active (native/macOS path).  The
        # injected _session_key is the last source (KUN multi-thread runtime).
        # Reads resolve against the root workspace (self._workspace IS the
        # default root for read tools), which is also the redirect base.
        base_ws = self._workspace if _is_default_workspace(self._workspace) else None
        session_dir = _resolve_session_dir(
            self._session_files_dir, session_ws, self._workspace, _sess_key, base_ws,
        )
        if sandbox is not None and getattr(sandbox, "_use_wsl", False):
            # WSL sandbox — route file operations through the sandbox.
            # Read tools resolve against the ROOT workspace (the working dir
            # the system prompt advertises), while session isolation is
            # enforced separately via session_files_dir (#613 follow-up).
            sandbox_path = _resolve_sandbox_path(
                path, self._workspace, sandbox,
                extra_roots=shared,
                session_files_dir=session_ws,
            )
            _log.info("read_file [sandbox]: %s → %s", path, sandbox_path)
            try:
                exists = await _sandbox_file_exists(sandbox, sandbox_path)
            except Exception as e:
                return f"Error: 沙箱中检查文件是否存在失败（path={sandbox_path}）：{e}"
            if not exists:
                # Session-dir fallback: writes are redirected into
                # sessions/<key>/files, so a path that misses at the root may
                # live there (relative paths resolve against the root).
                alt = _redirect_path_to_session(path, self._workspace, session_dir)
                if alt:
                    alt_sandbox = _resolve_sandbox_path(
                        alt, self._workspace, sandbox,
                        extra_roots=shared,
                        session_files_dir=session_ws,
                    )
                    if alt_sandbox != sandbox_path:
                        try:
                            if await _sandbox_file_exists(sandbox, alt_sandbox):
                                content = await _sandbox_read_file(sandbox, alt_sandbox)
                                _log.info(
                                    "read_file [sandbox fallback]: %s → %s", path, alt_sandbox,
                                )
                                return content
                        except Exception as e:
                            _log.warning(
                                "read_file [sandbox fallback] failed (%s): %s", alt_sandbox, e,
                            )
                return f"Error: 文件不存在：{path}（沙箱路径：{sandbox_path}）"
            try:
                content = await _sandbox_read_file(sandbox, sandbox_path)
                return content
            except FileNotFoundError as e:
                return f"Error: 沙箱中文件不存在：{sandbox_path}：{e}"
            except Exception as e:
                return f"Error: 沙箱中读取文件失败（path={sandbox_path}）：{type(e).__name__}：{e}"
        else:
            # Native sandbox or no sandbox — use local filesystem
            try:
                file_path = _resolve_path(
                    path,
                    self._workspace,
                    self._allowed_dir,
                    self._sandbox_manager,
                    shared_roots=shared,
                )
                # Cross-session isolation for native reads too: another
                # session's files dir must not be readable (CodeRabbit #731).
                if file_path.exists():
                    _reject_foreign_session_path(file_path, base_ws, session_dir)
                if not file_path.exists():
                    # Session-dir fallback (mirrors the sandbox branch).  The
                    # fallback resolves against the SESSION workspace so the
                    # native-sandbox remap matches where writes landed.
                    alt = _redirect_path_to_session(path, self._workspace, session_dir)
                    if alt:
                        alt_path = _resolve_path(
                            alt,
                            session_dir or self._workspace,
                            self._allowed_dir,
                            self._sandbox_manager,
                            shared_roots=shared,
                        )
                        if alt_path != file_path and alt_path.exists():
                            _reject_foreign_session_path(alt_path, base_ws, session_dir)
                            return alt_path.read_text(encoding="utf-8")
                    return f"Error: 文件不存在：{path}"
                if not file_path.is_file():
                    return f"Error: 不是文件：{path}"

                content = file_path.read_text(encoding="utf-8")
                return content
            except PermissionError as e:
                return f"Error: 权限被拒绝：{e}"
            except Exception as e:
                return f"Error: 读取文件失败：{type(e).__name__}: {e}"


class WriteFileTool(Tool):
    """Tool to write content to a file — works with local or sandbox filesystems."""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        snapshot_dir: Path | None = None,
        sandbox_manager=None,
        shared_roots: Iterable[Path] | None = None,
        session_files_dir: Path | None = None,
        base_workspace: Path | None = None,
        allow_user_roots: bool = True,
        write_resolver=None,
        persist_extra_root=None,
        bypass_approval: bool = False,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._snapshot_dir = snapshot_dir
        self._sandbox_manager = sandbox_manager
        self._shared_roots = list(shared_roots or [])
        # Session isolation (#221 / #613): factory-provided per-session files
        # dir (works without a sandbox) and the default workspace root used to
        # re-anchor absolute root paths into the session dir.
        self._session_files_dir = session_files_dir
        self._base_workspace = base_workspace
        self._allow_user_roots = allow_user_roots
        # Write authorization card (issue #864).
        self._write_resolver = write_resolver
        self._persist_extra_root = persist_extra_root
        self._bypass_approval = bypass_approval
        # Session-scoped grants: a single tool instance serves every session in
        # the runtime, so "允许本次 / 本目录不再询问" must never leak a grant
        # from one session into another (CodeRabbit #866).
        self._granted: dict[str, set[str]] = {}

    def _session_granted(self, session_key: str | None) -> set[str]:
        """Return the session-scoped grant set for *session_key*."""
        return self._granted.setdefault(session_key or "", set())

    @property
    def _tracking_workspace(self) -> Path | None:
        """Workspace root used for tracked_files.json bookkeeping.

        Tracked files are stored per-session under the DEFAULT workspace
        (``sessions/<key>/tracked_files.json``), never under the per-session
        files dir itself.
        """
        return self._base_workspace or self._workspace

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file at the given path. Creates parent directories if needed."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to write to"
                },
                "content": {
                    "type": "string",
                    "description": "The content to write"
                },
                "authorize_paths": {
                    "type": "array",
                    "description": (
                        "（可选）提前声明需要写入的、位于已授权写根之外的绝对路径。"
                        "提供后会在写入前先向用户发起授权，避免写入时才被拒绝。"
                    ),
                    "items": {"type": "string"},
                },
            },
            "required": ["path", "content"]
        }

    async def _preauthorize_paths(
        self, paths: list[str], base_dir: Path | None, shared: list[Path],
        session_key: str | None = None, once_granted: set[str] | None = None,
        boundary_enforced: bool = True,
    ) -> str | None:
        """Pre-authorize declared paths (issue #864). Returns an error string
        when the user declines, or None when authorization succeeded/was unneeded.

        ``shared`` is the call-scoped root list (static roots + injected
        ``_user_roots``) so a declared path already covered by a user-mentioned
        root never pops a card before the write accepts it.  ``once_granted``
        carries "允许本次" grants so the actual write path within the SAME tool
        call does not re-pop the card.
        """
        granted = self._session_granted(session_key)
        for p in paths:
            result = await _resolve_write_shared_roots(
                p,
                base_dir=base_dir,
                workspace_root=self._base_workspace or self._workspace,
                shared=shared,
                granted=granted,
                once_granted=once_granted,
                write_resolver=self._write_resolver,
                persist_extra_root=self._persist_extra_root,
                boundary_enforced=boundary_enforced,
                bypass=self._bypass_approval,
            )
            if result is None:
                return f"Error: 权限被拒绝：用户未授权写入 {p}"
        return None

    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        office_suffixes = {".docx", ".xlsx", ".pptx"}
        if Path(path).suffix.lower() in office_suffixes:
            return (
                "Error: write_file 无法创建 Office 二进制文件。 "
                "Use create_docx, create_xlsx, or create_pptx instead."
            )

        _sess_key = kwargs.pop("_session_key", None)
        shared = _effective_shared_roots(
            self._shared_roots, kwargs.pop("_user_roots", None), self._allow_user_roots,
        )
        sandbox = await _ensure_sandbox(self._sandbox_manager, session_key=_sess_key)
        session_ws = _get_session_workspace(self._workspace, sandbox)
        base_ws = self._base_workspace or (
            self._workspace if _is_default_workspace(self._workspace) else None
        )
        boundary_enforced = (
            (sandbox is not None and getattr(sandbox, "_use_wsl", False))
            or self._allowed_dir is not None
        )
        # Session isolation: factory dir → sandbox dir → per-call derivation
        # from the injected _session_key (KUN multi-thread / native path).
        session_dir = _resolve_session_dir(
            self._session_files_dir, session_ws, self._workspace, _sess_key, base_ws,
        )
        # Agent-declared write paths (issue #864): authorize upfront so a
        # declined path aborts before any write, not as a silent downgrade.
        # "允许本次" grants are invocation-scoped, shared between the
        # authorize_paths pre-flight and the actual write path below.
        _authorize = kwargs.pop("authorize_paths", None)
        once_granted: set[str] = set()
        if isinstance(_authorize, list) and _authorize:
            _pre_err = await self._preauthorize_paths(
                [str(x) for x in _authorize], base_ws or self._workspace,
                shared, session_key=_sess_key, once_granted=once_granted,
                boundary_enforced=boundary_enforced,
            )
            if _pre_err:
                return _pre_err
        # Session isolation: new files written under the default workspace
        # root (the dir the system prompt advertises) land in the session
        # files dir instead of the shared root.
        requested_path = path
        path = await _redirect_new_file_write(
            path, base_ws, session_dir, _make_exists_check(shared, sandbox, session_ws, native_base_dir=self._workspace),
        )
        # Delivery truthfulness (miqibug 路径归一化): when an absolute write
        # is normalized into the session files dir, the success message must
        # state BOTH paths so the model relays the REAL location to the user
        # instead of echoing the requested one.
        _redirected_note = (
            f"（请求路径 {requested_path} 已按会话隔离归一化到会话 files 目录）"
            if path != requested_path and _is_absolute_host_path(requested_path)
            else ""
        )
        # Write authorization card (issue #864): when the resolved target is
        # outside every legal write root, offer [允许本次 / 本目录不再询问 /
        # 拒绝].  A grant ADDS the directory to the shared roots; a non-grant
        # leaves them unchanged so the existing _resolve_sandbox_path /
        # _resolve_path containment check still raises PermissionError exactly
        # as before (WSL path) — we never convert that dead-end into a silent
        # string, so headless/no-resolver callers keep their old behavior.
        authorized = await _resolve_write_shared_roots(
            path,
            base_dir=base_ws or self._workspace,
            workspace_root=self._base_workspace or self._workspace,
            shared=shared,
            granted=self._session_granted(_sess_key),
            once_granted=once_granted,
            write_resolver=self._write_resolver,
            persist_extra_root=self._persist_extra_root,
            boundary_enforced=boundary_enforced,
            bypass=self._bypass_approval,
        )
        if authorized is not None:
            shared = authorized
        if sandbox is not None and getattr(sandbox, "_use_wsl", False):
            # WSL sandbox — route file operations through the sandbox.
            # session_files_dir enforces cross-session isolation: a path
            # under another session's files dir is rejected (CodeRabbit #731).
            sandbox_path = _resolve_sandbox_path(
                path, session_ws, sandbox, extra_roots=shared,
                session_files_dir=session_dir,
            )
            _log.info("write_file [sandbox]: %s → %s", path, sandbox_path)
            try:
                await _sandbox_write_file(sandbox, sandbox_path, content)
            except IOError as e:
                return f"Error: 沙箱中写入文件失败（path={sandbox_path}）：{e}"
            except Exception as e:
                return f"Error: 沙箱中写入文件失败（path={sandbox_path}）：{type(e).__name__}：{e}"

            # Mirror the file to the host workspace so that files.read
            # (which resolves against the host workspace) can find it.
            # Skip mirror for /mnt/ paths: the sandbox already wrote directly
            # to the host filesystem via the WSL bind-mount (issue #474).
            host_path = _sandbox_to_host_path(sandbox_path, self._workspace, sandbox)
            if not sandbox_path.startswith("/mnt/"):
                try:
                    host_file = Path(host_path)
                    host_file.parent.mkdir(parents=True, exist_ok=True)
                    host_file.write_text(content, encoding="utf-8")
                    _log.info("write_file [mirror]: %s → %s", sandbox_path, host_path)
                except Exception as exc:
                    _log.warning("write_file [mirror] failed for %s: %s", host_path, exc)

            # Persist to tracked_files.json so the Task Assets panel
            # survives session switches.
            _persist_tracked_file(
                self._tracking_workspace, host_path, op="write", session_key=_sess_key,
            )

            return f"Successfully wrote {len(content)} bytes to {host_path}{_redirected_note}"
        else:
            # Native sandbox or no sandbox — use local filesystem
            try:
                file_path = _resolve_path(
                    path,
                    session_dir or self._workspace,
                    self._allowed_dir,
                    self._sandbox_manager,
                    shared_roots=shared,
                )
                _reject_foreign_session_path(file_path, base_ws, session_dir)
                # Snapshot original content before first write (enables non-git diff/revert)
                snap_ok = _maybe_snapshot(file_path, snapshot_dir=self._snapshot_dir)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
                # Persist to tracked_files.json for session switch survival
                _persist_tracked_file(
                    self._tracking_workspace, file_path, op="write", session_key=_sess_key,
                )
                result = f"Successfully wrote {len(content)} bytes to {file_path}{_redirected_note}"
                if not snap_ok:
                    _log.warning("Snapshot failed for %s — revert will not be available", file_path)
                return result
            except PermissionError as e:
                return f"Error: 权限被拒绝：{e}"
            except Exception as e:
                return f"Error writing file: {type(e).__name__}: {e}"


class EditFileTool(Tool):
    """Tool to edit a file by replacing text — works with local or sandbox filesystems."""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        snapshot_dir: Path | None = None,
        sandbox_manager=None,
        shared_roots: Iterable[Path] | None = None,
        session_files_dir: Path | None = None,
        base_workspace: Path | None = None,
        allow_user_roots: bool = True,
        write_resolver=None,
        persist_extra_root=None,
        bypass_approval: bool = False,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._snapshot_dir = snapshot_dir
        self._sandbox_manager = sandbox_manager
        self._shared_roots = list(shared_roots or [])
        self._session_files_dir = session_files_dir
        self._base_workspace = base_workspace
        self._allow_user_roots = allow_user_roots
        # Write authorization card (issue #864).
        self._write_resolver = write_resolver
        self._persist_extra_root = persist_extra_root
        self._bypass_approval = bypass_approval
        # Session-scoped grants (CodeRabbit #866).
        self._granted: dict[str, set[str]] = {}

    def _session_granted(self, session_key: str | None) -> set[str]:
        """Return the session-scoped grant set for *session_key*."""
        return self._granted.setdefault(session_key or "", set())

    @property
    def _tracking_workspace(self) -> Path | None:
        """Workspace root used for tracked_files.json bookkeeping."""
        return self._base_workspace or self._workspace

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "Edit a file by replacing old_text with new_text. The old_text must exist exactly in the file."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to edit"
                },
                "old_text": {
                    "type": "string",
                    "description": "The exact text to find and replace"
                },
                "new_text": {
                    "type": "string",
                    "description": "The text to replace with"
                },
                "authorize_paths": {
                    "type": "array",
                    "description": (
                        "（可选）提前声明需要写入的、位于已授权写根之外的绝对路径。"
                        "提供后会在写入前先向用户发起授权，避免写入时才被拒绝。"
                    ),
                    "items": {"type": "string"},
                },
            },
            "required": ["path", "old_text", "new_text"]
        }

    async def _preauthorize_paths(
        self, paths: list[str], base_dir: Path | None, shared: list[Path],
        session_key: str | None = None, once_granted: set[str] | None = None,
        boundary_enforced: bool = True,
    ) -> str | None:
        """Pre-authorize declared paths (issue #864). Returns an error string
        when the user declines, or None when authorization succeeded/was unneeded.

        ``shared`` is the call-scoped root list (static roots + injected
        ``_user_roots``) so a declared path already covered by a user-mentioned
        root never pops a card before the write accepts it.  ``once_granted``
        carries "允许本次" grants so the actual write path within the SAME tool
        call does not re-pop the card.
        """
        granted = self._session_granted(session_key)
        for p in paths:
            result = await _resolve_write_shared_roots(
                p,
                base_dir=base_dir,
                workspace_root=self._base_workspace or self._workspace,
                shared=shared,
                granted=granted,
                once_granted=once_granted,
                write_resolver=self._write_resolver,
                persist_extra_root=self._persist_extra_root,
                boundary_enforced=boundary_enforced,
                bypass=self._bypass_approval,
            )
            if result is None:
                return f"Error: 权限被拒绝：用户未授权写入 {p}"
        return None

    async def execute(self, path: str, old_text: str, new_text: str, **kwargs: Any) -> str:
        _sess_key = kwargs.pop("_session_key", None)
        shared = _effective_shared_roots(
            self._shared_roots, kwargs.pop("_user_roots", None), self._allow_user_roots,
        )
        sandbox = await _ensure_sandbox(self._sandbox_manager, session_key=_sess_key)
        session_ws = _get_session_workspace(self._workspace, sandbox)
        base_ws = self._base_workspace or (
            self._workspace if _is_default_workspace(self._workspace) else None
        )
        boundary_enforced = (
            (sandbox is not None and getattr(sandbox, "_use_wsl", False))
            or self._allowed_dir is not None
        )
        session_dir = _resolve_session_dir(
            self._session_files_dir, session_ws, self._workspace, _sess_key, base_ws,
        )
        # Agent-declared write paths (issue #864): authorize upfront.
        _authorize = kwargs.pop("authorize_paths", None)
        once_granted: set[str] = set()
        if isinstance(_authorize, list) and _authorize:
            _pre_err = await self._preauthorize_paths(
                [str(x) for x in _authorize], base_ws or self._workspace,
                shared, session_key=_sess_key, once_granted=once_granted,
                boundary_enforced=boundary_enforced,
            )
            if _pre_err:
                return _pre_err
        # Session isolation: edits of files that only exist in the session
        # dir resolve there; shared root files are edited in place.
        requested_path = path
        path = await _redirect_new_file_write(
            path, base_ws, session_dir, _make_exists_check(shared, sandbox, session_ws, native_base_dir=self._workspace),
        )
        # Delivery truthfulness (miqibug 路径归一化): when an absolute edit
        # is normalized into the session files dir, the success message must
        # state BOTH paths so the model relays the REAL location.
        _redirected_note = (
            f"（请求路径 {requested_path} 已按会话隔离归一化到会话 files 目录）"
            if path != requested_path and _is_absolute_host_path(requested_path)
            else ""
        )
        # Write authorization card (issue #864).
        authorized = await _resolve_write_shared_roots(
            path,
            base_dir=base_ws or self._workspace,
            workspace_root=base_ws,
            shared=shared,
            granted=self._session_granted(_sess_key),
            once_granted=once_granted,
            write_resolver=self._write_resolver,
            persist_extra_root=self._persist_extra_root,
            boundary_enforced=boundary_enforced,
            bypass=self._bypass_approval,
        )
        if authorized is not None:
            shared = authorized
        if sandbox is not None and getattr(sandbox, "_use_wsl", False):
            # WSL sandbox — route file operations through the sandbox.
            # session_files_dir enforces cross-session isolation: a path
            # under another session's files dir is rejected (CodeRabbit #731).
            sandbox_path = _resolve_sandbox_path(
                path, session_ws, sandbox, extra_roots=shared,
                session_files_dir=session_dir,
            )
            _log.info("edit_file [sandbox]: %s → %s", path, sandbox_path)
            try:
                exists = await _sandbox_file_exists(sandbox, sandbox_path)
            except Exception as e:
                return f"Error: 沙箱中检查文件是否存在失败（path={sandbox_path}）：{e}"
            if not exists:
                return f"Error: 文件不存在：{path}（沙箱路径：{sandbox_path}）"

            try:
                content = await _sandbox_read_file(sandbox, sandbox_path)
            except Exception as e:
                return f"Error: 沙箱中读取文件用于编辑失败（path={sandbox_path}）：{type(e).__name__}：{e}"

            if old_text not in content:
                return self._not_found_message(old_text, content, path)

            # Count occurrences
            count = content.count(old_text)
            if count > 1:
                return f"Warning: old_text appears {count} times. Please provide more context to make it unique."

            new_content = content.replace(old_text, new_text, 1)
            try:
                await _sandbox_write_file(sandbox, sandbox_path, new_content)
            except Exception as e:
                return f"Error: 沙箱中写入编辑后文件失败（path={sandbox_path}）：{type(e).__name__}：{e}"

            # Mirror the file to the host workspace so files.read can find it.
            # Skip mirror for /mnt/ paths: the sandbox already wrote directly
            # to the host filesystem via the WSL bind-mount (issue #474).
            host_path = _sandbox_to_host_path(sandbox_path, self._workspace, sandbox)
            if not sandbox_path.startswith("/mnt/"):
                try:
                    host_file = Path(host_path)
                    host_file.parent.mkdir(parents=True, exist_ok=True)
                    host_file.write_text(new_content, encoding="utf-8")
                    _log.info("edit_file [mirror]: %s → %s", sandbox_path, host_path)
                except Exception as exc:
                    _log.warning("edit_file [mirror] failed for %s: %s", host_path, exc)

            _persist_tracked_file(
                self._tracking_workspace, host_path, op="edit", session_key=_sess_key,
            )

            return f"Successfully edited {host_path}{_redirected_note}"
        else:
            # Native sandbox or no sandbox — use local filesystem
            try:
                file_path = _resolve_path(
                    path,
                    session_dir or self._workspace,
                    self._allowed_dir,
                    self._sandbox_manager,
                    shared_roots=shared,
                )
                _reject_foreign_session_path(file_path, base_ws, session_dir)
                if not file_path.exists():
                    return f"Error: 文件不存在：{path}"

                # Snapshot original content before first edit (enables non-git diff/revert)
                _maybe_snapshot(file_path, snapshot_dir=self._snapshot_dir)

                content = file_path.read_text(encoding="utf-8")

                if old_text not in content:
                    return self._not_found_message(old_text, content, path)

                # Count occurrences
                count = content.count(old_text)
                if count > 1:
                    return f"Warning: old_text appears {count} times. Please provide more context to make it unique."

                new_content = content.replace(old_text, new_text, 1)
                file_path.write_text(new_content, encoding="utf-8")

                _persist_tracked_file(
                    self._tracking_workspace, file_path, op="edit", session_key=_sess_key,
                )

                return f"Successfully edited {file_path}{_redirected_note}"
            except PermissionError as e:
                return f"Error: 权限被拒绝：{e}"
            except Exception as e:
                return f"Error editing file: {type(e).__name__}: {e}"

    @staticmethod
    def _not_found_message(old_text: str, content: str, path: str) -> str:
        """Build a helpful error when old_text is not found."""
        lines = content.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        window = len(old_lines)

        best_ratio, best_start = 0.0, 0
        for i in range(max(1, len(lines) - window + 1)):
            ratio = difflib.SequenceMatcher(None, old_lines, lines[i : i + window]).ratio()
            if ratio > best_ratio:
                best_ratio, best_start = ratio, i

        if best_ratio > 0.5:
            diff = "\n".join(difflib.SequenceMatcher(
                None, old_lines, lines[best_start : best_start + window]
            ).get_opcodes() if False else difflib.unified_diff(
                old_lines, lines[best_start : best_start + window],
                fromfile="old_text (provided)", tofile=f"{path} (actual, line {best_start + 1})",
                lineterm="",
            ))
            return f"Error: 在 {path} 中未找到 old_text。\n最佳匹配（相似度 {best_ratio:.0%}）位于第 {best_start + 1} 行：\n{diff}"
        return f"Error: 在 {path} 中未找到 old_text，也没有相似文本。请核对文件内容。"


class ListDirTool(Tool):
    """Tool to list directory contents — works with local or sandbox filesystems."""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        sandbox_manager=None,
        shared_roots: Iterable[Path] | None = None,
        allow_user_roots: bool = True,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._sandbox_manager = sandbox_manager
        self._shared_roots = list(shared_roots or [])
        self._allow_user_roots = allow_user_roots

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List the contents of a directory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The directory path to list"
                }
            },
            "required": ["path"]
        }

    async def execute(self, path: str, **kwargs: Any) -> str:
        _sess_key = kwargs.pop("_session_key", None)
        shared = _effective_shared_roots(
            self._shared_roots, kwargs.pop("_user_roots", None), self._allow_user_roots,
        )
        sandbox = await _ensure_sandbox(self._sandbox_manager, session_key=_sess_key)
        session_ws = _get_session_workspace(self._workspace, sandbox)
        if sandbox is not None and getattr(sandbox, "_use_wsl", False):
            # WSL sandbox — route file operations through the sandbox.
            # Read tools resolve against the ROOT workspace, session
            # isolation enforced via session_files_dir (#613 follow-up).
            sandbox_path = _resolve_sandbox_path(
                path, self._workspace, sandbox,
                extra_roots=shared,
                session_files_dir=session_ws,
            )
            _log.info("list_dir [sandbox]: %s → %s", path, sandbox_path)
            try:
                exists = await _sandbox_dir_exists(sandbox, sandbox_path)
            except Exception as e:
                return f"Error: 沙箱中检查目录是否存在失败（path={sandbox_path}）：{e}"
            if not exists:
                return f"Error: 目录不存在：{path}（沙箱路径：{sandbox_path}）"
            try:
                content = await _sandbox_list_dir(sandbox, sandbox_path)
            except IOError as e:
                return f"Error: 沙箱中列出目录失败（path={sandbox_path}）：{e}"
            except Exception as e:
                return f"Error: 沙箱中列出目录失败（path={sandbox_path}）：{type(e).__name__}：{e}"
            if not content.strip():
                return f"Directory {path} is empty"
            return content
        else:
            # Native sandbox or no sandbox — use local filesystem
            try:
                dir_path = _resolve_path(
                    path,
                    self._workspace,
                    self._allowed_dir,
                    self._sandbox_manager,
                    shared_roots=shared,
                )
                if not dir_path.exists():
                    return f"Error: 目录不存在：{path}"
                if not dir_path.is_dir():
                    return f"Error: 不是目录：{path}"

                items = []
                for item in sorted(dir_path.iterdir()):
                    prefix = "dir " if item.is_dir() else "     "
                    items.append(f"{prefix}{item.name}")

                if not items:
                    return f"Directory {path} is empty"

                return "\n".join(items)
            except PermissionError as e:
                return f"Error: 权限被拒绝：{e}"
            except Exception as e:
                return f"Error listing directory: {type(e).__name__}: {e}"
