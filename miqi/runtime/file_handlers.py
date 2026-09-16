"""File artifact handlers for AppServer dispatch.

Phase 30: Migrates files.tree, files.read, files.write, files.delete,
files.diff, files.revert, and files.accept from bridge legacy handlers
to AppServer async handlers with client-scoped ownership enforcement.

Key semantics:
- All file operations (except files.tree without session_key) require
  client_id and verify session ownership via SessionManager.
- files.tree: workspace tree only when no session_key; session-scoped
  tree when session_key is provided and owned by client.
- files.read/write/delete: session ownership required for session-scoped
  paths; workspace-only paths are OK without ownership.
- files.diff/revert/accept: session ownership required; snapshots are
  resolved through the ownership-aware path resolver.
- Path resolution is centralized through _resolve_session_files_path()
  and _resolve_session_snapshot_dir() — no raw session_key concatenation
  in handler code.
- Bug fixes:
  1. _remove_tracked_file: was undefined — uses SessionManager.remove_tracked_file
  2. _reset_tracked_file_op: was bypassing ownership — now passes client_id
  3. files.write: tracked_files write was bypassing ownership — now passes client_id
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from loguru import logger

from miqi.agent.tools.filesystem import (
    _delete_snapshot,
    _maybe_snapshot,
    _read_snapshot,
    _restore_snapshot,
    _snapshots_lock,
)
from miqi.runtime.app_server import AppServerError
from miqi.runtime.fs_protocol import decode_data_base64, encode_data_base64
from miqi.session.manager import OwnershipError
from miqi.session.session_keys import session_files_dir_key

# ── workspace / SessionManager access ──────────────────────────────────────


def _get_workspace_path() -> Path:
    """Get the workspace path from bridge state config."""
    import miqi.bridge.server as bridge_module

    state = getattr(bridge_module, "_state", None)
    if state is None:
        raise AppServerError("Bridge state not available", code="INTERNAL")
    config = state.load_config()
    return config.workspace_path.resolve()


def _get_session_manager() -> Any:
    """Get a SessionManager for the current workspace."""
    import miqi.bridge.server as bridge_module

    state = getattr(bridge_module, "_state", None)
    if state is None:
        raise AppServerError("Bridge state not available", code="INTERNAL")
    config = state.load_config()
    from miqi.session.manager import SessionManager
    return SessionManager(config.workspace_path)


# ── ownership verification ─────────────────────────────────────────────────


def _verify_session_ownership(client_id: str, session_key: str) -> None:
    """Verify that client_id owns session_key.

    Raises AppServerError with:
    - REQUIRES_CLAIM: session is unowned legacy
    - UNAUTHORIZED: session is owned by a different client
    """
    sm = _get_session_manager()
    try:
        sm._verify_ownership_for_mutation(session_key, client_id)
    except OwnershipError as exc:
        raise AppServerError(exc.args[0], code=exc.code) from exc


def _require_owned_session(client_id: str, session_key: str) -> None:
    """Require an on-disk session, owned by client_id, for this key.

    Deliberately stricter than :func:`_verify_session_ownership`, which (via
    ``SessionManager._verify_ownership_for_mutation``) passes when no session
    file exists on disk because there is "nothing to protect".  At the file
    boundary there *is* something to protect — the session directory itself —
    and the derivation is not injective: ``session_files_dir_key`` folds ``:``
    to ``_`` and drops the leading client segment, so ``z:desktop:vic`` and
    ``miqi-desktop:desktop:vic`` both derive ``desktop_vic``.  With no
    ``conversation.jsonl`` to read an owner from, that let a caller address a
    sibling session's directory simply by naming a key that derives it (#1051).

    Sessions that exist only in the AppServer registry therefore cannot use
    session-scoped file operations until they are persisted; that trade is
    taken deliberately, since the alternative is an unverifiable owner.

    Raises AppServerError with:
    - REQUIRES_CLAIM: no owned session on disk for this key
    - UNAUTHORIZED: session is owned by a different client
    """
    sm = _get_session_manager()
    owner = sm.get_owner(session_key)
    if owner is None:
        raise AppServerError(
            f"Session '{session_key}' has no owner on disk; the session must "
            "exist and be claimed before session-scoped file access.",
            code="REQUIRES_CLAIM",
        )
    if owner != client_id:
        raise AppServerError(
            f"Session '{session_key}' is owned by client '{owner}', "
            f"not '{client_id}'",
            code="UNAUTHORIZED",
        )


def _session_dir_key(session_key: str) -> str:
    """Derive the on-disk session directory name, rejecting unsafe results.

    ``session_files_dir_key`` folds ``:`` to ``_`` (so separators cannot
    survive) but keeps literal ``.``/``..``, which would move the session root
    outside ``<ws>/sessions/`` and silently disable session isolation.  Names
    ending in a dot are also rejected: Windows cannot create such a directory,
    and the failure surfaced as an internal error rather than INVALID_PARAMS.
    """
    safe_key = session_files_dir_key(session_key)
    if not safe_key or safe_key in (".", "..") or safe_key[-1] in (".", " "):
        raise AppServerError(
            f"Invalid session key: {session_key!r}", code="INVALID_PARAMS",
        )
    if "/" in safe_key or "\\" in safe_key:
        raise AppServerError(
            f"Invalid session key: {session_key!r}", code="INVALID_PARAMS",
        )
    return safe_key


# ── path resolution ────────────────────────────────────────────────────────


def _resolve_session_files_path(client_id: str, session_key: str) -> Path:
    """Resolve the client-scoped session files directory.

    Verifies session ownership before returning the path.
    Uses the same session directory naming as SessionManager
    (``session_files_dir_key``), gated by ownership verification.
    """
    safe_key = _session_dir_key(session_key)
    _verify_session_ownership(client_id, session_key)
    workspace = _get_workspace_path()
    files_dir = workspace / "sessions" / safe_key / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    return files_dir


def _resolve_session_snapshot_dir(client_id: str, session_key: str) -> Path:
    """Resolve the client-scoped session snapshot directory.

    Verifies session ownership before returning the path.
    """
    safe_key = _session_dir_key(session_key)
    _verify_session_ownership(client_id, session_key)
    workspace = _get_workspace_path()
    snap_dir = workspace / "sessions" / safe_key / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    return snap_dir


# Sandbox-internal workspace prefix.  The bwrap sandbox always mounts the
# host workspace at this location, so when the agent reports paths like
# /home/miqi/workspace/report.md we can extract the workspace-relative
# portion by stripping this prefix.
_SANDBOX_WORKSPACE_PREFIX = "/home/miqi/workspace"


# Directory under the workspace root holding the per-session isolation
# directories.  It is a *session* boundary, not a workspace one: a
# workspace-scoped operation (no session_key) may never reach into it, and a
# session-scoped operation may only reach the caller's own session directory.
# Checking containment against the workspace instead let a relative path with
# ``..`` land in a sibling session's directory and overwrite its
# ``conversation.jsonl`` — including its ``owner_client_id`` metadata line
# (#1051).
_SESSIONS_DIR_NAME = "sessions"

# Top-level directories that are runtime state rather than user workspace
# content.  Hidden from the workspace tree so the editor never offers a file
# the workspace-scoped handlers will refuse to write.
_RESERVED_ROOT_DIRS = frozenset({_SESSIONS_DIR_NAME, "_legacy_sessions"})


def _is_within(path: Path, root: Path) -> bool:
    """True when *path* is *root* itself, or lives underneath it."""
    return path == root or path.is_relative_to(root)


def _resolve_under(root: Path, file_path: str) -> Path:
    """Join *file_path* to *root* and resolve it, normalising ``..``.

    Raises AppServerError(INVALID_PARAMS) on paths the OS refuses to resolve,
    instead of letting a ValueError/OSError escape as an internal error.
    """
    try:
        return (root / file_path).resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise AppServerError("Invalid file path", code="INVALID_PARAMS") from exc


def _strip_sandbox_prefix(file_path: str) -> str:
    """Drop the sandbox workspace prefix, returning a relative path."""
    prefix = _SANDBOX_WORKSPACE_PREFIX
    if file_path == prefix:
        return "."
    return file_path[len(prefix) + 1:]


def _validate_file_path(
    file_path: str,
    client_id: str,
    session_key: str | None = None,
) -> Path:
    """Resolve a file path with session-granularity traversal protection.

    Containment is enforced at the *session* boundary, not the workspace
    boundary:

    - With a ``session_key``: the result must stay inside the caller's own
      session directory (``<ws>/sessions/<key>``).  A path is accepted either
      session-relative (``notes.md`` →
      ``<ws>/sessions/<key>/files/notes.md``) or workspace-relative when it
      already names a location inside that same session directory
      (``sessions/<key>/files/notes.md``).  Ordinary workspace files outside
      ``<ws>/sessions/`` remain reachable, as before.
    - Without a ``session_key``: the result must be inside the workspace and
      **outside** ``<ws>/sessions/``, so a workspace-scoped operation can
      never address another session's files.

    Accepts both relative paths and absolute paths that fall within the
    workspace.  Paths under the sandbox workspace prefix
    (``/home/miqi/workspace/…``) are treated as workspace-relative; other
    absolute paths — POSIX-rooted, Windows drive-letter, and UNC alike — are
    resolved on the filesystem and must fall inside the workspace.

    Blocks path traversal (``..``) and absolute paths that escape the
    permitted root.
    """
    workspace = _get_workspace_path()

    if not file_path:
        raise AppServerError(
            "path is required", code="INVALID_PARAMS",
        )

    # ── Normalise absolute paths to a workspace-relative form ─────────────
    prefix = _SANDBOX_WORKSPACE_PREFIX
    if file_path == prefix or file_path.startswith((prefix + "/", prefix + "\\")):
        # Case 1: sandbox-internal path — the agent ran inside bwrap and
        # reported a path under /home/miqi/workspace/.  Strip the prefix to
        # get the workspace-relative path.  Note this is a *prefix strip*, not
        # a sanitiser: a trailing `..` is still handled by the containment
        # checks below.
        file_path = _strip_sandbox_prefix(file_path)
    elif file_path.startswith(("/", "\\")) or Path(file_path).is_absolute():
        # Case 2: host absolute path — resolve it, then require it to fall
        # inside the workspace.  The Windows drive-letter form
        # (``C:\...``/``C:/...``) reaches here via Path.is_absolute(); it used
        # to skip normalisation entirely and be re-joined as if relative.
        candidate = _resolve_under(Path(), file_path)
        if not _is_within(candidate, workspace):
            raise AppServerError(
                f"Path is outside workspace: {file_path}"
                "（工作区外的文件请改用 exec 命令读取）",
                code="INVALID_PARAMS",
            )
        file_path = str(candidate.relative_to(workspace))

    # ── Session-scoped resolution ─────────────────────────────────────────
    sessions_root = workspace / _SESSIONS_DIR_NAME
    if session_key:
        # Reject keys whose derived directory name is not a single safe
        # segment before consulting disk state, so the caller gets a precise
        # INVALID_PARAMS rather than an ownership error.
        _session_dir_key(session_key)
        # Ownership must be conclusive at the file boundary — see
        # _require_owned_session for why the lenient "no session on disk"
        # pass is unsafe here.
        _require_owned_session(client_id, session_key)
        # Verifies ownership before returning the directory.
        session_files = _resolve_session_files_path(client_id, session_key)
        session_root = session_files.parent

        # (a) workspace-relative path that already names a location inside the
        #     caller's OWN session directory
        #     (``sessions/<key>/files/…``), as the desktop sends.
        workspace_candidate = _resolve_under(workspace, file_path)
        if _is_within(workspace_candidate, session_root):
            return workspace_candidate

        # A path naming the sessions subtree that is not the caller's own
        # session is somebody else's session.  It must be rejected rather than
        # re-interpreted below as a session-relative path — that would quietly
        # re-root it inside the caller's files directory instead of failing.
        if _is_within(workspace_candidate, sessions_root):
            raise AppServerError(
                f"Path escapes session: {file_path}"
                "（会话目录外的路径请改用工作区路径或 exec 命令）",
                code="INVALID_PARAMS",
            )

        # (b) path relative to the session files directory (``notes.md``).
        session_candidate = _resolve_under(session_files, file_path)
        if _is_within(session_candidate, session_files):
            return session_candidate

        # (c) ordinary workspace files outside the sessions subtree stay
        #     reachable for session-scoped callers.
        if _is_within(workspace_candidate, workspace):
            return workspace_candidate

        raise AppServerError(
            f"Path escapes session: {file_path}"
            "（会话目录外的路径请改用工作区路径或 exec 命令）",
            code="INVALID_PARAMS",
        )

    # ── Workspace-scoped resolution ───────────────────────────────────────
    resolved = _resolve_under(workspace, file_path)
    if not _is_within(resolved, workspace):
        raise AppServerError(
            f"Path escapes workspace: {file_path}"
            "（工作区外的文件请改用 exec 命令读取）",
            code="INVALID_PARAMS",
        )
    # The sessions subtree is per-session isolated state; a workspace-scoped
    # operation has no session to be scoped to, so it may not enter it at all.
    if _is_within(resolved, sessions_root):
        raise AppServerError(
            f"Path is inside the sessions directory: {file_path}"
            "（会话目录需带 session_key 访问）",
            code="INVALID_PARAMS",
        )
    return resolved


# ── allowed file types ─────────────────────────────────────────────────────

_ALLOWED_SUFFIXES: set[str] = {
    ".md", ".txt", ".py", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".js", ".ts", ".tsx", ".jsx", ".css", ".html", ".xml", ".svg",
    ".sh", ".bash", ".zsh", ".ps1", ".bat",
    ".env", ".gitignore", ".dockerignore", ".editorconfig",
    ".csv", ".log", ".lock", ".jsonl",
}

_ALLOWED_NAMES: set[str] = {
    ".gitignore", ".dockerignore", ".editorconfig", ".env",
}

_BINARY_VIEWABLE_SUFFIXES: set[str] = {
    ".pdf",
    # Images — 附件内联显示 + 跨 session 恢复 (#659)，与 document_parser
    # 的 _SUFFIX_TO_MIME 保持一致
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tiff", ".tif", ".ico",
    # SVG 矢量图 — graph_render 工具产物内联展示 (#715)
    ".svg",
}

# Office 后缀只在显式 as_binary=true 时可读（issue #877「下载/另存为」需
# 要原始字节）；不加入 _BINARY_VIEWABLE_SUFFIXES，避免工作区编辑器对它们
# 也走 iframe blob 路径（Chromium 无法渲染 xlsx/docx）。
_BINARY_READABLE_SUFFIXES: set[str] = _BINARY_VIEWABLE_SUFFIXES | {
    ".xlsx", ".xls", ".ods",
    ".docx", ".doc", ".odt",
    ".pptx", ".ppt", ".odp",
    ".csv",
}

_SUFFIX_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".csv": "text/csv",
}

_TREE_SKIP_SUFFIXES: set[str] = {
    ".sqlite", ".sqlite-shm", ".sqlite-wal", ".sqlite-journal",
    ".db", ".db-shm", ".db-wal",
    ".pyc", ".pyo", ".pyd",
    ".so", ".dll", ".dylib", ".exe",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
    ".bin", ".dat", ".pkl", ".npz", ".npy", ".h5", ".hdf5",
}

_TEXT_SAFE_SUFFIXES = _ALLOWED_SUFFIXES
_TEXT_SAFE_NAMES = _ALLOWED_NAMES


# ── files.tree ─────────────────────────────────────────────────────────────


def _build_tree(
    path: Path,
    relative_to: Path,
    depth: int = 0,
    max_depth: int = 6,
    skip_dirs: frozenset[str] = frozenset(),
) -> dict:
    """Build a FileNode tree for a directory.

    *skip_dirs* hides the named children of the tree root — used to keep the
    per-session isolation subtree out of the workspace tree, which the
    workspace-scoped handlers cannot address anyway (#1051).
    """
    node: dict[str, Any] = {
        "name": path.name or str(path),
        "path": str(path.relative_to(relative_to)).replace("\\", "/"),
        "is_dir": path.is_dir(),
    }
    if path.is_dir() and depth < max_depth:
        children = []
        at_root = path == relative_to
        try:
            for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if child.name.startswith(".") or child.name == "__pycache__":
                    continue
                if at_root and child.is_dir() and child.name in skip_dirs:
                    continue
                if child.suffix.lower() in _TREE_SKIP_SUFFIXES:
                    continue
                children.append(_build_tree(child, relative_to, depth + 1, max_depth))
        except PermissionError:
            pass
        node["children"] = children
    return node


async def files_tree_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Build a file tree.

    Without session_key: returns the workspace tree (read-only, low risk).
    With session_key: returns session-scoped file tree after ownership verification.
    """
    workspace = _get_workspace_path()
    session_key = params.get("session_key")

    if session_key:
        # Session-scoped tree: verify ownership first
        _verify_session_ownership(client_id, session_key)
        _require_owned_session(client_id, session_key)
        session_files = _resolve_session_files_path(client_id, session_key)
        if not session_files.exists() or not any(session_files.iterdir()):
            root = {
                "name": session_key,
                "path": ".",
                "is_dir": True,
                "children": [],
            }
        else:
            root = _build_tree(session_files, session_files)
        return {
            "result": {
                "root": root,
                "workspace_path": str(workspace),
                "session_key": session_key,
            },
        }

    # Workspace tree only
    if not workspace.exists():
        return {
            "result": {
                "root": {"name": workspace.name, "path": ".", "is_dir": True, "children": []},
                "workspace_path": str(workspace),
            },
        }
    root = _build_tree(workspace, workspace, skip_dirs=_RESERVED_ROOT_DIRS)
    return {"result": {"root": root, "workspace_path": str(workspace)}}


# ── files.read ─────────────────────────────────────────────────────────────


def _is_wsl_sandbox_path(path: str) -> bool:
    """Return True if *path* looks like a WSL-side sandbox path.

    When the bridge runs on Windows but sandboxes live in WSL, direct
    :meth:`Path.exists` checks fail because the Windows kernel cannot
    see WSL's filesystem.  We detect this situation by looking for
    Linux-style absolute paths that start with ``/tmp/`` or ``/home/``
    while the current platform is Windows.
    """
    if path.startswith("/tmp/") or path.startswith("/home/"):
        import platform
        return platform.system() == "Windows"
    return False


def _wsl_file_exists(wsl_path: str, distro: str = "") -> bool:
    """Check whether *wsl_path* exists inside WSL via ``wsl.exe``."""
    import subprocess
    cmd = ["wsl.exe"]
    if distro:
        cmd.extend(["-d", distro])
    cmd.extend(["--", "test", "-f", wsl_path])
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def _wsl_read_file(wsl_path: str, distro: str = "") -> str:
    """Read a text file from inside WSL via ``wsl.exe cat``.

    Raises :class:`OSError` on failure.
    """
    import subprocess
    cmd = ["wsl.exe"]
    if distro:
        cmd.extend(["-d", distro])
    cmd.extend(["--", "cat", wsl_path])
    result = subprocess.run(
        cmd, capture_output=True, timeout=30, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise OSError(
            f"wsl.exe cat failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def _wsl_read_file_bytes(wsl_path: str, distro: str = "") -> bytes:
    """Read a binary file from inside WSL via ``wsl.exe base64``.

    Raises :class:`OSError` on failure.
    """
    import base64
    import subprocess
    cmd = ["wsl.exe"]
    if distro:
        cmd.extend(["-d", distro])
    cmd.extend(["--", "base64", wsl_path])
    result = subprocess.run(
        cmd, capture_output=True, timeout=30, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise OSError(
            f"wsl.exe base64 failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return base64.b64decode(result.stdout.strip())


def _find_in_sandbox_workspaces(
    file_path: str,
    host_resolved: Path,
    session_key: str | None = None,
) -> tuple[Path, str] | None:
    """Search active sandbox workspace directories for *file_path*.

    When the bwrap sandbox uses per-session workspace copies (Issue #221),
    files created inside the sandbox are not immediately visible at the
    host workspace path.  This function walks every active sandbox's
    workspace directory looking for the file so that ``files.read`` works
    regardless of how the file was created (``write_file``, shell command,
    download, etc.).

    When *session_key* is provided the function also checks the
    session-scoped subdirectory (``sessions/<safe_key>/files/``) that
    ``_get_session_workspace`` uses.

    Returns ``(resolved_path, wsl_distro)`` if found, or ``None``.
    The *wsl_distro* string is empty unless the file lives on a WSL
    filesystem and needs to be read via ``wsl.exe``.
    """
    try:
        import miqi.bridge.server as bridge_module
        state = getattr(bridge_module, "_state", None)
        if state is None:
            logger.info("[files:read] sandbox fallback: bridge state unavailable")
            return None
        sm = getattr(state, "_sandbox_manager", None)
        if sm is None or sm == "disabled":
            logger.info("[files:read] sandbox fallback: sandbox manager disabled/absent")
            return None

        # Build the session-scoped suffix with the canonical key derivation
        # (``_session_files_dir_key``, #1003) — the same one
        # ``_get_session_workspace`` uses.  Deriving it locally produced a
        # divergent directory for two-segment channel keys
        # (``desktop:<ts>`` → ``sessions/<ts>/files`` instead of
        # ``sessions/desktop_<ts>/files``), so this fallback searched a
        # directory no writer ever creates (issue #1005).
        session_suffix = ""
        if session_key:
            from miqi.agent.tools.filesystem import _session_files_dir_key

            safe_key = _session_files_dir_key(session_key)
            session_suffix = f"sessions/{safe_key}/files"

        sandboxes = sm.list_sandboxes()
        if not sandboxes:
            logger.info("[files:read] sandbox fallback: no active sandboxes")
            return None

        for entry in sandboxes:
            sandbox_ws = entry.get("workspace")
            if not sandbox_ws:
                continue

            # (candidate, containment_root) pairs.  The raw *file_path* is
            # joined here, so a `..` segment would otherwise point outside the
            # sandbox workspace; each candidate is checked against the root it
            # was built from.
            candidates: list[tuple[Path, Path]] = []
            # Strip leading separator so an absolute *file_path* cannot
            # discard the sandbox-workspace prefix via Path "/" semantics.
            rel = file_path.lstrip("/").lstrip("\\")

            # 1) Check at workspace root
            ws_root = Path(sandbox_ws).resolve()
            candidates.append(((ws_root / rel).resolve(), ws_root))

            # 2) Check session-scoped subdirectory
            if session_suffix:
                sess_root = (ws_root / session_suffix).resolve()
                candidates.append(((sess_root / rel).resolve(), sess_root))

            distro = entry.get("distro", "")

            for candidate, containment_root in candidates:
                if not _is_within(candidate, containment_root):
                    continue
                try:
                    if candidate.exists() and candidate.is_file():
                        logger.info(
                            "[files:read] sandbox fallback found: {}",
                            candidate,
                        )
                        return (candidate, "")
                except OSError:
                    # Cross-platform path may not be directly accessible
                    # (e.g. WSL path from Windows).  Fall through to the
                    # wsl.exe helper below.
                    pass

            # 3) Cross-platform fallback: when the bridge runs on Windows
            #    but the sandbox lives inside WSL, WSL paths like
            #    /tmp/miqi-sandboxes/... are not reachable via Path.exists().
            #    Use wsl.exe to probe the file.
            if _is_wsl_sandbox_path(str(sandbox_ws)):
                for candidate, containment_root in candidates:
                    if not _is_within(candidate, containment_root):
                        continue
                    if _wsl_file_exists(str(candidate), distro):
                        logger.info(
                            "[files:read] sandbox fallback found via wsl.exe: {}",
                            candidate,
                        )
                        return (candidate, distro)

    except Exception as exc:
        logger.warning("[files:read] sandbox fallback error: {}", exc)
    return None


async def files_read_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Read a text file.

    For session-scoped paths (session_key provided): verifies session ownership.
    For workspace-only paths: no ownership check needed.
    """
    file_path = params.get("path", "").strip()
    session_key = params.get("session_key")
    # #776：svg 等同时属文本安全集与二进制可读集的后缀，默认走二进制
    # 分支（前端内联展示需 data_base64/mime_type）；调用方想读纯文本
    # 时显式传 as_text=true 强制走文本分支。
    as_text = bool(params.get("as_text"))
    # #877：显式请求原始字节（「下载/另存为」需要 Office 文件字节）。
    as_binary = bool(params.get("as_binary"))

    logger.info(
        "[files:read] req={} path={} session_key={} client={}",
        request_id, file_path, session_key, client_id,
    )

    if not file_path:
        raise AppServerError("path is required", code="INVALID_PARAMS")

    try:
        resolved = _validate_file_path(file_path, client_id, session_key)
    except AppServerError:
        raise
    except ValueError as exc:
        logger.warning("[files:read] invalid path {}: {}", file_path, exc)
        raise AppServerError(
            "Invalid file path", code="INVALID_PARAMS",
        ) from exc

    # Files read from a WSL sandbox need to be accessed via wsl.exe because
    # the Windows kernel cannot see WSL's filesystem directly.
    wsl_distro = ""

    if not resolved.exists():
        # The file may have been created inside a sandbox whose workspace
        # is a per-session copy (not a bind-mount of the host workspace).
        # Search active sandbox workspace directories as a fallback.
        sandbox_result = _find_in_sandbox_workspaces(file_path, resolved, session_key)
        if sandbox_result is not None:
            resolved, wsl_distro = sandbox_result
            logger.info(
                "[files:read] found in sandbox workspace: {} → {}",
                file_path, resolved,
            )
        else:
            logger.warning(
                "[files:read] file not found — checked host={} session_key={}",
                resolved, session_key,
            )
            raise AppServerError(
                f"File not found: {file_path}"
                + (f" (session: {session_key})" if session_key else ""),
                code="NOT_FOUND",
            )

    if not wsl_distro:
        # Host-accessible file — use normal path checks
        if resolved.is_dir():
            raise AppServerError(f"Path is a directory: {file_path}", code="INVALID_PARAMS")

    suffix = resolved.suffix.lower()
    # 二进制可读后缀（含 .svg）优先：svg 同时属于文本安全集（.svg 在
    # _ALLOWED_SUFFIXES）与二进制可读集——文本分支先命中会返回纯文本
    # content，前端内联展示需要 data_base64/mime_type（CodeRabbit #761）。
    # as_text=true（#776）显式请求纯文本时例外，svg 走文本分支。
    # as_binary=true（#877）时对 Office 后缀等也走二进制分支。
    in_text_safe = suffix in _TEXT_SAFE_SUFFIXES or resolved.name in _TEXT_SAFE_NAMES
    want_binary = as_binary and suffix in _BINARY_READABLE_SUFFIXES
    if in_text_safe and (suffix not in _BINARY_VIEWABLE_SUFFIXES or as_text) and not want_binary:
        # ── text file ──────────────────────────────────────────────────
        try:
            if wsl_distro:
                content = _wsl_read_file(str(resolved), wsl_distro)
            else:
                content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise AppServerError(
                "File is not valid UTF-8 text", code="INVALID_PARAMS",
            ) from None
        except Exception as exc:
            logger.warning("[files:read] read error {}: {}", file_path, exc)
            raise AppServerError(
                "Failed to read file", code="INTERNAL",
            ) from exc

        logger.info("[files:read] ok path={} size={}", file_path, len(content))
        return {
            "result": {
                "path": file_path,
                "content": content,
                "size": len(content),
            },
        }

    if suffix in _BINARY_VIEWABLE_SUFFIXES or want_binary:
        # ── binary file — return base64 ───────────────────────────────
        try:
            if wsl_distro:
                data = _wsl_read_file_bytes(str(resolved), wsl_distro)
            else:
                data = resolved.read_bytes()
        except Exception as exc:
            logger.warning("[files:read] binary read error {}: {}", file_path, exc)
            raise AppServerError(
                "Failed to read file", code="INTERNAL",
            ) from exc

        mime = _SUFFIX_TO_MIME.get(suffix, "application/octet-stream")
        logger.info("[files:read] ok (binary) path={} size={} mime={}", file_path, len(data), mime)
        return {
            "result": {
                "path": file_path,
                "data_base64": encode_data_base64(data),
                "size": len(data),
                "mime_type": mime,
                "is_binary": True,
            },
        }

    raise AppServerError(
        f"File type not supported: {suffix or resolved.name}",
        code="INVALID_PARAMS",
    )


# ── files.write ────────────────────────────────────────────────────────────


async def files_write_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Write content to a text file.

    For session-scoped writes: verifies ownership, snapshots before first write,
    and updates tracked_files with client_id (fixing the bug where tracked_files
    write bypassed ownership verification).
    """
    file_path = params.get("path", "").strip()
    content = params.get("content", "")
    session_key = params.get("session_key")

    logger.info(
        "[files:write] req={} path={} size={} session_key={} client={}",
        request_id, file_path, len(content), session_key, client_id,
    )

    if not file_path:
        raise AppServerError("path is required", code="INVALID_PARAMS")

    try:
        resolved = _validate_file_path(file_path, client_id, session_key)
    except AppServerError:
        raise
    except ValueError as exc:
        logger.warning("[files:write] invalid path {}: {}", file_path, exc)
        raise AppServerError(
            "Invalid file path", code="INVALID_PARAMS",
        ) from exc

    suffix = resolved.suffix.lower()
    data_base64_param = params.get("data_base64", "")

    if suffix in _TEXT_SAFE_SUFFIXES or resolved.name in _TEXT_SAFE_NAMES:
        # ── text file write ───────────────────────────────────────────
        # Snapshot original content before first write (enables diff/revert)
        snapshot_dir: Path | None = None
        if session_key:
            snapshot_dir = _resolve_session_snapshot_dir(client_id, session_key)
        _maybe_snapshot(resolved, snapshot_dir=snapshot_dir)

        resolved.parent.mkdir(parents=True, exist_ok=True)
        try:
            resolved.write_text(content, encoding="utf-8")
        except Exception as exc:
            logger.warning("[files:write] write error {}: {}", file_path, exc)
            raise AppServerError(
                "Failed to write file", code="INTERNAL",
            ) from exc

    elif suffix in _BINARY_VIEWABLE_SUFFIXES:
        # ── binary file write ─────────────────────────────────────────
        if not data_base64_param:
            raise AppServerError(
                "data_base64 is required for binary file writes",
                code="INVALID_PARAMS",
            )
        try:
            data = decode_data_base64(data_base64_param)
        except AppServerError:
            raise
        except Exception as exc:
            logger.warning("[files:write] base64 decode error {}: {}", file_path, exc)
            raise AppServerError(
                "Invalid base64 data", code="INVALID_PARAMS",
            ) from exc

        resolved.parent.mkdir(parents=True, exist_ok=True)
        try:
            resolved.write_bytes(data)
        except Exception as exc:
            logger.warning("[files:write] binary write error {}: {}", file_path, exc)
            raise AppServerError(
                "Failed to write file", code="INTERNAL",
            ) from exc

    else:
        raise AppServerError(
            f"File type not supported: {suffix or resolved.name}",
            code="INVALID_PARAMS",
        )

    # Update tracked_files with client_id ownership check (BUG FIX A.3).
    # The RAW session_key is passed on purpose (#1005): normalization happens
    # one layer down — SessionManager.get_session_dir applies
    # ``session_files_dir_key`` — so the tracked_files.json lands in the same
    # session directory this handler just resolved above.  Do not "fix" this
    # call site by pre-normalizing the key; that would derive the name twice.
    if session_key:
        sm = _get_session_manager()
        try:
            sm.save_tracked_file(
                session_key, file_path, op="write", client_id=client_id,
            )
        except OwnershipError as exc:
            raise AppServerError(exc.args[0], code=exc.code) from exc

    logger.info("[files:write] ok path={}", file_path)
    return {"result": {"saved": True, "path": file_path}}


# ── files.delete ───────────────────────────────────────────────────────────


async def files_delete_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Delete a workspace file or empty directory.

    For session-scoped deletes: verifies session ownership.
    """
    file_path = params.get("path", "").strip()
    session_key = params.get("session_key")

    logger.info(
        "[files:delete] req={} path={} session_key={} client={}",
        request_id, file_path, session_key, client_id,
    )

    if not file_path:
        raise AppServerError("path is required", code="INVALID_PARAMS")

    try:
        resolved = _validate_file_path(file_path, client_id, session_key)
    except AppServerError:
        raise
    except ValueError as exc:
        logger.warning("[files] invalid path {}: {}", file_path, exc)
        raise AppServerError(
            "Invalid file path", code="INVALID_PARAMS",
        ) from exc

    if not resolved.exists():
        raise AppServerError(f"Not found: {file_path}", code="NOT_FOUND")

    workspace = _get_workspace_path()
    if resolved == workspace:
        raise AppServerError("Cannot delete workspace root", code="INVALID_PARAMS")

    if resolved.is_dir():
        if any(resolved.iterdir()):
            raise AppServerError("Directory is not empty", code="INVALID_PARAMS")
        resolved.rmdir()
    else:
        resolved.unlink()

    logger.info("[files:delete] ok path={}", file_path)
    return {"result": {"deleted": True, "path": file_path}}


# ── files.diff ─────────────────────────────────────────────────────────────


async def files_diff_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Diff a file against its pre-session snapshot (no git required).

    Verifies session ownership before accessing snapshots.
    """
    file_path = params.get("path", "").strip()
    session_key = params.get("session_key")

    logger.info(
        "[files:diff] req={} path={} session_key={} client={}",
        request_id, file_path, session_key, client_id,
    )

    if not file_path:
        raise AppServerError("path is required", code="INVALID_PARAMS")

    try:
        resolved = _validate_file_path(file_path, client_id, session_key)
    except AppServerError:
        raise
    except ValueError as exc:
        logger.warning("[files] invalid path {}: {}", file_path, exc)
        raise AppServerError(
            "Invalid file path", code="INVALID_PARAMS",
        ) from exc

    snapshot_key = str(resolved)

    # Resolve snapshot dir with ownership verification
    snapshot_dir: Path | None = None
    if session_key:
        snapshot_dir = _resolve_session_snapshot_dir(client_id, session_key)

    with _snapshots_lock:
        original_content: str | None = _read_snapshot(snapshot_key, snapshot_dir=snapshot_dir)

    # Fall back to global snapshots dir
    if original_content is None:
        original_content = _read_snapshot(snapshot_key)

    # Read current content
    current_content: str | None = None
    file_exists = resolved.exists()
    if file_exists:
        try:
            current_content = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.info("[files:diff] read current failed: {}", exc)

    # If no snapshot exists, generate a diff showing all content as additions
    if original_content is None:
        if file_exists and current_content is not None and current_content != "":
            logger.info("[files:diff] new file detected for {}", snapshot_key)
            current_lines = current_content.splitlines(keepends=True)
            diff_lines = [
                "--- /dev/null",
                f"+++ b/{file_path}",
            ]
            line_count = len(current_lines)
            diff_lines.append(f"@@ -0,0 +1,{line_count} @@")
            diff_lines.extend("+" + line for line in current_lines)
            diff_text = "\n".join(diff_lines)
            return {
                "result": {
                    "path": file_path,
                    "diff": diff_text,
                    "has_diff": True,
                    "original_content": None,
                    "current_content": current_content,
                    "is_new_file": True,
                },
            }
        logger.info("[files:diff] no snapshot for {}", snapshot_key)
        return {
            "result": {
                "path": file_path,
                "diff": None,
                "has_diff": False,
                "original_content": None,
                "current_content": current_content,
                "error": "No snapshot found — file was not modified in this session",
            },
        }

    # Generate unified diff for modified files
    original_lines = original_content.splitlines(keepends=True)
    current_lines = (current_content or "").splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        original_lines,
        current_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm="",
    ))
    diff_text = "\n".join(diff_lines) if diff_lines else None
    has_diff = bool(diff_text)
    logger.info("[files:diff] ok has_diff={} lines={} path={}", has_diff, len(diff_lines), file_path)

    return {
        "result": {
            "path": file_path,
            "diff": diff_text,
            "has_diff": has_diff,
            "original_content": original_content,
            "current_content": current_content,
        },
    }


# ── files.revert ───────────────────────────────────────────────────────────


async def files_revert_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Revert a file to its pre-session snapshot (no git required).

    Verifies session ownership before accessing snapshots.
    BUG FIX: Uses SessionManager.remove_tracked_file with client_id instead
    of calling the undefined _remove_tracked_file.
    """
    file_path = params.get("path", "").strip()
    session_key = params.get("session_key")

    logger.info(
        "[files:revert] req={} path={} session_key={} client={}",
        request_id, file_path, session_key, client_id,
    )

    if not file_path:
        raise AppServerError("path is required", code="INVALID_PARAMS")

    try:
        resolved = _validate_file_path(file_path, client_id, session_key)
    except AppServerError:
        raise
    except ValueError as exc:
        logger.warning("[files] invalid path {}: {}", file_path, exc)
        raise AppServerError(
            "Invalid file path", code="INVALID_PARAMS",
        ) from exc

    snapshot_key = str(resolved)

    # Resolve snapshot dir with ownership verification
    snapshot_dir: Path | None = None
    if session_key:
        snapshot_dir = _resolve_session_snapshot_dir(client_id, session_key)

    with _snapshots_lock:
        has_snapshot = _read_snapshot(snapshot_key, snapshot_dir=snapshot_dir) is not None

    if not has_snapshot:
        # Also check global snapshots dir
        has_snapshot = _read_snapshot(snapshot_key) is not None
        if has_snapshot:
            snapshot_dir = None  # use global dir for restore/delete
            logger.info("[files:revert] found snapshot in global dir for {}", snapshot_key)

    if not has_snapshot:
        raise AppServerError(
            "No snapshot found — cannot revert (file was not modified in this session)",
            code="NOT_FOUND",
        )

    ok = _restore_snapshot(resolved, snapshot_dir=snapshot_dir)
    if not ok:
        raise AppServerError(
            "Revert failed — could not write original content",
            code="INTERNAL",
        )

    # Delete snapshot so the file is treated as clean again
    with _snapshots_lock:
        _delete_snapshot(snapshot_key, snapshot_dir=snapshot_dir)

    # Remove tracked file entry with ownership check (BUG FIX A.1)
    if session_key:
        sm = _get_session_manager()
        try:
            sm.remove_tracked_file(session_key, file_path, client_id=client_id)
        except OwnershipError as exc:
            raise AppServerError(exc.args[0], code=exc.code) from exc

    logger.info("[files:revert] ok path={}", file_path)
    return {"result": {"reverted": True, "path": file_path}}


# ── files.accept ───────────────────────────────────────────────────────────


async def files_accept_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Accept all changes for a file — keep current content, delete snapshot.

    Verifies session ownership before accessing snapshots and tracked_files.
    BUG FIX: Passes client_id to SessionManager.reset_tracked_file_op
    instead of bypassing ownership verification.
    """
    file_path = params.get("path", "").strip()
    session_key = params.get("session_key")

    logger.info(
        "[files:accept] req={} path={} session_key={} client={}",
        request_id, file_path, session_key, client_id,
    )

    if not file_path:
        raise AppServerError("path is required", code="INVALID_PARAMS")

    # Reset tracked file entry with ownership check (BUG FIX A.2)
    if session_key:
        sm = _get_session_manager()
        try:
            sm.reset_tracked_file_op(
                session_key, file_path, op="read", client_id=client_id,
            )
        except OwnershipError as exc:
            raise AppServerError(exc.args[0], code=exc.code) from exc

    try:
        resolved = _validate_file_path(file_path, client_id, session_key)
    except AppServerError:
        # Path validation failed — still report accepted for tracked_files reset
        return {"result": {"accepted": True, "path": file_path}}
    except ValueError:
        return {"result": {"accepted": True, "path": file_path}}

    snapshot_key = str(resolved)

    # Resolve snapshot dir with ownership verification
    snapshot_dir: Path | None = None
    if session_key:
        try:
            snapshot_dir = _resolve_session_snapshot_dir(client_id, session_key)
        except AppServerError:
            snapshot_dir = None

    # Delete snapshot from session dir
    with _snapshots_lock:
        _delete_snapshot(snapshot_key, snapshot_dir=snapshot_dir)

    # Also clean global dir if snapshot landed there
    with _snapshots_lock:
        _delete_snapshot(snapshot_key)

    logger.info("[files:accept] ok path={}", file_path)
    return {"result": {"accepted": True, "path": file_path}}
