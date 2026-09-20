"""Typed result payloads for sessions.* App Server methods."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Result(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SessionsListResult(_Result):
    """sessions.list — list of session summaries."""
    sessions: list[dict[str, Any]]


class SessionsGetResult(_Result):
    """sessions.get — session detail (permissive dynamic object)."""
    key: str | None = None
    session_id: str | None = None
    status: str | None = None
    agent_count: int | None = None
    messages: list[dict[str, Any]] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] | None = None
    ownership: str | None = None


class SessionsDeleteResult(_Result):
    """sessions.delete — delete confirmation."""
    deleted: bool


class SessionsArchiveResult(_Result):
    """sessions.archive — archive confirmation."""
    archived: bool


class SessionsUnarchiveResult(_Result):
    """sessions.unarchive — unarchive confirmation."""
    unarchived: bool


class SessionsListArchivedResult(_Result):
    """sessions.list_archived — list of archived sessions."""
    sessions: list[dict[str, Any]]


class SessionsGetTrackedFilesResult(_Result):
    """sessions.get_tracked_files — tracked files list."""
    tracked_files: list[dict[str, Any]]


class SessionsClearTrackedFilesResult(_Result):
    """sessions.clear_tracked_files — clear confirmation."""
    cleared: bool


class SessionsClaimLegacyResult(_Result):
    """sessions.claim_legacy — claim result."""
    claimed: bool
    was_already_claimed: bool = Field(validation_alias="was_already_claimed")


class SessionsRenameResult(_Result):
    """sessions.rename — rename confirmation."""
    renamed: bool = True
    key: str | None = None
    title: str | None = None


class SessionsTruncateResult(_Result):
    """sessions.truncate — truncation confirmation (#1020)."""
    truncated: bool = True
    removed_messages: int = 0


SESSION_METHOD_RESULT_MODELS: dict[str, type[BaseModel]] = {
    "sessions.list": SessionsListResult,
    "sessions.get": SessionsGetResult,
    "sessions.delete": SessionsDeleteResult,
    "sessions.archive": SessionsArchiveResult,
    "sessions.unarchive": SessionsUnarchiveResult,
    "sessions.list_archived": SessionsListArchivedResult,
    "sessions.get_tracked_files": SessionsGetTrackedFilesResult,
    "sessions.clear_tracked_files": SessionsClearTrackedFilesResult,
    "sessions.claim_legacy": SessionsClaimLegacyResult,
    "sessions.rename": SessionsRenameResult,
    "sessions.truncate": SessionsTruncateResult,
}
