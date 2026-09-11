"""Codex-style filesystem protocol helpers (Phase 46).

Provides workspace path resolution, base64 encoding/decoding, metadata
and directory-entry response builders, and OS error mapping for use by
``fs/*`` AppServer handlers.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

from miqi.runtime.app_server import AppServerError, get_bridge_state

# ── Workspace resolution ─────────────────────────────────────────────────────


def workspace_root(registry: Any) -> Path:
    """Return the configured workspace root absolute path."""
    state = get_bridge_state(registry)
    config = state.load_config()
    root = Path(config.workspace_path)
    return root.resolve(strict=False)


def resolve_workspace_absolute_path(
    registry: Any,
    raw_path: Any,
    *,
    field_name: str = "path",
    resolve_symlinks: bool = True,
) -> Path:
    """Validate and resolve an absolute path inside the configured workspace.

    *raw_path* must be a non-empty string representing an absolute path.
    The resolved real path must fall under the workspace root returned
    by :func:`workspace_root`.  Symlinks are resolved before the
    containment check.

    When *resolve_symlinks* is ``False`` the containment check is still
    performed against the resolved target, but the returned path is the
    **original** unresolved absolute path.  This lets callers such as
    ``fs/getMetadata`` use :func:`Path.lstat` to inspect the symlink
    itself rather than its target.

    Raises:
        AppServerError(INVALID_PARAMS) on validation failure.
    """
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise AppServerError(
            f"{field_name} is required",
            code="INVALID_PARAMS",
        )

    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise AppServerError(
            f"{field_name} must be an absolute path",
            code="INVALID_PARAMS",
        )

    resolved = candidate.resolve(strict=False)
    root = workspace_root(registry)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AppServerError(
            f"{field_name} must be inside workspace",
            code="INVALID_PARAMS",
        ) from exc

    if resolve_symlinks:
        return resolved
    return candidate


# ── Base64 helpers ───────────────────────────────────────────────────────────


def decode_data_base64(raw: Any) -> bytes:
    """Decode a base64-encoded string to raw bytes.

    Raises:
        AppServerError(INVALID_PARAMS) if *raw* is not a string or the
        content is not valid base64.
    """
    if not isinstance(raw, str):
        raise AppServerError(
            "dataBase64 must be a string",
            code="INVALID_PARAMS",
        )
    try:
        return base64.b64decode(raw.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise AppServerError(
            f"fs/writeFile requires valid base64 dataBase64: {exc}",
            code="INVALID_PARAMS",
        ) from exc


def encode_data_base64(data: bytes) -> str:
    """Encode raw bytes as a base64 string."""
    return base64.b64encode(data).decode("ascii")


# ── Response builders ────────────────────────────────────────────────────────


def metadata_response(path: Path) -> dict[str, Any]:
    """Build an ``fs/getMetadata`` response from *path*.

    Returns a dict with **camelCase** keys: ``isDirectory``, ``isFile``,
    ``isSymlink``, ``createdAtMs``, ``modifiedAtMs``.
    """
    stat_info = path.lstat()
    is_symlink = path.is_symlink()

    if is_symlink:
        try:
            resolved = path.resolve(strict=True)
            resolved_stat = resolved.stat()
            is_dir = resolved.is_dir()
            is_file = resolved.is_file()
            ctime_ns = resolved_stat.st_ctime_ns
            mtime_ns = resolved_stat.st_mtime_ns
        except OSError:
            is_dir = False
            is_file = False
            ctime_ns = int(stat_info.st_ctime_ns)
            mtime_ns = int(stat_info.st_mtime_ns)
    else:
        is_dir = path.is_dir()
        is_file = path.is_file()
        ctime_ns = int(stat_info.st_ctime_ns)
        mtime_ns = int(stat_info.st_mtime_ns)

    created_at_ms = ctime_ns // 1_000_000
    modified_at_ms = mtime_ns // 1_000_000

    return {
        "isDirectory": is_dir,
        "isFile": is_file,
        "isSymlink": is_symlink,
        "createdAtMs": created_at_ms,
        "modifiedAtMs": modified_at_ms,
    }


def directory_entry_response(path: Path) -> dict[str, Any]:
    """Build an ``fs/readDirectory`` entry dict for *path*.

    Returns a dict with **camelCase** keys: ``fileName``, ``isDirectory``,
    ``isFile``.
    """
    return {
        "fileName": path.name,
        "isDirectory": path.is_dir(),
        "isFile": path.is_file(),
    }


# ── OS error mapping ─────────────────────────────────────────────────────────


# Note: OSError has many subclasses. The following block extracts
# the exact class name from the exception to produce safe error codes.
# This avoids a fragile is-a chain that can break when Python deprecates
# or renames OS error types.
_OS_ERROR_CODE_MAP: dict[type, str] = {
    FileNotFoundError: "NOT_FOUND",
    FileExistsError: "ALREADY_EXISTS",
    PermissionError: "PERMISSION_DENIED",
    NotADirectoryError: "INVALID_PARAMS",
    IsADirectoryError: "INVALID_PARAMS",
}


def map_os_error(exc: OSError, *, default_code: str = "INTERNAL") -> AppServerError:
    """Map an :class:`OSError` subclass to an :class:`AppServerError`.

    *default_code* is used when the concrete exception type is not in the
    known mapping table.
    """
    code = _OS_ERROR_CODE_MAP.get(type(exc), default_code)

    # Provide a sensible message, hiding internal paths
    msg = str(exc)
    if not msg:
        msg = exc.__class__.__name__

    return AppServerError(msg, code=code)
