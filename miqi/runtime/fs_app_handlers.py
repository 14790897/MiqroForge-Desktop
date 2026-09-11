"""Codex-style fs/* AppServer handlers (Phase 46).

Registers: fs/readFile, fs/writeFile, fs/createDirectory, fs/getMetadata,
fs/readDirectory, fs/remove, fs/copy.

All mutations are workspace-contained.  No text-suffix filtering is
applied (unlike the MiQi ``files.*`` API).
"""

from __future__ import annotations

import shutil
from typing import Any

import miqi.runtime.protocol_specs as protocol_specs
from miqi.runtime.app_server import AppServer, AppServerError
from miqi.runtime.filesystem_request_models import (
    FsCopyParams,
    FsCreateDirectoryParams,
    FsGetMetadataParams,
    FsReadDirectoryParams,
    FsReadFileParams,
    FsRemoveParams,
    FsWriteFileParams,
    validate_filesystem_params,
)
from miqi.runtime.fs_protocol import (
    decode_data_base64,
    directory_entry_response,
    encode_data_base64,
    map_os_error,
    metadata_response,
    resolve_workspace_absolute_path,
)

# ── Handler implementations ──────────────────────────────────────────────────


async def fs_read_file_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Handle fs/readFile — read a file and return base64-encoded bytes."""
    typed = validate_filesystem_params(FsReadFileParams, params)
    path = resolve_workspace_absolute_path(registry, typed.path)

    if not path.exists():
        raise AppServerError(
            "path does not exist",
            code="NOT_FOUND",
        )
    if not path.is_file():
        raise AppServerError(
            "path is not a file",
            code="INVALID_PARAMS",
        )
    try:
        return {"result": {"dataBase64": encode_data_base64(path.read_bytes())}}
    except OSError as exc:
        raise map_os_error(exc) from exc


async def fs_write_file_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Handle fs/writeFile — write raw bytes from base64 to a file."""
    typed = validate_filesystem_params(FsWriteFileParams, params)
    path = resolve_workspace_absolute_path(registry, typed.path)
    data = decode_data_base64(typed.data_base64)

    if not path.parent.is_dir():
        raise AppServerError(
            "parent directory does not exist",
            code="NOT_FOUND",
        )

    try:
        path.write_bytes(data)
    except OSError as exc:
        raise map_os_error(exc) from exc
    return {"result": {}}


async def fs_create_directory_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Handle fs/createDirectory — create a directory on the filesystem."""
    typed = validate_filesystem_params(FsCreateDirectoryParams, params)
    path = resolve_workspace_absolute_path(registry, typed.path)
    recursive = typed.recursive

    try:
        if recursive:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=False, exist_ok=False)
    except FileExistsError:
        # Already exists — succeed silently
        pass
    except OSError as exc:
        raise map_os_error(exc) from exc
    return {"result": {}}


async def fs_get_metadata_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Handle fs/getMetadata — return filesystem metadata for a path."""
    typed = validate_filesystem_params(FsGetMetadataParams, params)
    path = resolve_workspace_absolute_path(registry, typed.path, resolve_symlinks=False)

    if not path.exists():
        raise AppServerError(
            "path does not exist",
            code="NOT_FOUND",
        )

    try:
        return {"result": metadata_response(path)}
    except OSError as exc:
        raise map_os_error(exc) from exc


async def fs_read_directory_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Handle fs/readDirectory — list direct children of a directory."""
    typed = validate_filesystem_params(FsReadDirectoryParams, params)
    path = resolve_workspace_absolute_path(registry, typed.path)

    if not path.is_dir():
        raise AppServerError(
            "path is not a directory",
            code="INVALID_PARAMS",
        )

    try:
        entries = sorted(
            path.iterdir(),
            key=lambda p: p.name,
        )
    except OSError as exc:
        raise map_os_error(exc) from exc

    return {
        "result": {
            "entries": [directory_entry_response(e) for e in entries],
        },
    }


async def fs_remove_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Handle fs/remove — remove a file or directory tree."""
    typed = validate_filesystem_params(FsRemoveParams, params)
    path = resolve_workspace_absolute_path(registry, typed.path)
    recursive = typed.recursive
    force = typed.force

    # Missing path
    if not path.exists():
        if force:
            return {"result": {}}
        raise AppServerError(
            "path does not exist",
            code="NOT_FOUND",
        )

    try:
        if path.is_dir():
            if recursive:
                shutil.rmtree(path)
            else:
                path.rmdir()  # fails if non-empty
        else:
            path.unlink()
    except OSError as exc:
        raise map_os_error(exc) from exc
    return {"result": {}}


async def fs_copy_handler(
    request_id: str,
    params: dict[str, Any],
    client_id: str,
    session_id: str | None,
    registry: Any,
) -> dict[str, Any]:
    """Handle fs/copy — copy a file or directory tree."""
    typed = validate_filesystem_params(FsCopyParams, params)
    src = resolve_workspace_absolute_path(registry, typed.source_path, field_name="sourcePath")
    dst = resolve_workspace_absolute_path(registry, typed.destination_path, field_name="destinationPath")
    recursive = typed.recursive

    if not src.exists():
        raise AppServerError(
            "sourcePath does not exist",
            code="NOT_FOUND",
        )

    if not dst.parent.is_dir():
        raise AppServerError(
            "destination parent directory does not exist",
            code="NOT_FOUND",
        )

    try:
        if src.is_dir():
            if not recursive:
                raise AppServerError(
                    "recursive must be true for directory copy",
                    code="INVALID_PARAMS",
                )
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    except OSError as exc:
        raise map_os_error(exc) from exc
    return {"result": {}}


# ── Registration ─────────────────────────────────────────────────────────────


def register_fs_handlers(server: AppServer) -> None:
    """Register Codex fs/* handlers on *server*."""
    server.register_method("fs/readFile", fs_read_file_handler, spec=protocol_specs.FS_READ_FILE)
    server.register_method("fs/writeFile", fs_write_file_handler, spec=protocol_specs.FS_WRITE_FILE)
    server.register_method("fs/createDirectory", fs_create_directory_handler, spec=protocol_specs.FS_CREATE_DIRECTORY)
    server.register_method("fs/getMetadata", fs_get_metadata_handler, spec=protocol_specs.FS_GET_METADATA)
    server.register_method("fs/readDirectory", fs_read_directory_handler, spec=protocol_specs.FS_READ_DIRECTORY)
    server.register_method("fs/remove", fs_remove_handler, spec=protocol_specs.FS_REMOVE)
    server.register_method("fs/copy", fs_copy_handler, spec=protocol_specs.FS_COPY)
