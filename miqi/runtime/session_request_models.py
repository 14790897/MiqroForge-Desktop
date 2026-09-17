"""Typed request params for sessions.* App Server methods."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from miqi.runtime.app_server import AppServerError


class _Params(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SessionsListParams(_Params):
    """sessions.list — empty params, extra allowed."""
    pass


class SessionsListArchivedParams(_Params):
    """sessions.list_archived — empty params, extra allowed."""
    pass


class SessionKeyParams(_Params):
    """Shared params for all session-keyed methods.

    Accepts both session_key and sessionKey (wire name).
    Normalizes to session_key.
    """

    session_key: str = Field(default="", validation_alias="sessionKey")
    workspace: str | None = None

    @model_validator(mode="after")
    def _check_required(self) -> "SessionKeyParams":
        if not self.session_key.strip():
            raise ValueError("session_key is required")
        return self

    @field_validator("session_key", mode="before")
    @classmethod
    def _validate_key(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("session_key must be a string")
        if "/" in value or "\\" in value:
            raise ValueError("session_key must not contain path separators")
        # 只拒绝**恰好是** `.` / `..` 的冒号分段，而不是含点的 key：Slack 会话是
        # `slack:{chat_id}:{thread_ts}`，thread_ts 形如 `1789628394.123456`，
        # 一刀切会把合法 Slack 会话判成非法（#1103 review）。
        if any(segment in (".", "..") for segment in value.split(":")):
            raise ValueError("session_key must not contain . or .. segments")
        return value


class SessionRenameParams(SessionKeyParams):
    """sessions.rename — new display title for a session.

    Accepts both title and sessionKey. Title is required (missing or
    non-string rejected); whitespace-only and overlong titles pass through
    so SessionManager.rename applies its fallback/truncation behavior.
    """

    title: str | None = Field(default=None, validation_alias="title")

    @model_validator(mode="after")
    def _check_title(self) -> "SessionRenameParams":
        if self.title is None:
            raise ValueError("title is required")
        return self


SESSION_METHOD_PARAM_MODELS: dict[str, type[BaseModel]] = {
    "sessions.list": SessionsListParams,
    "sessions.get": SessionKeyParams,
    "sessions.delete": SessionKeyParams,
    "sessions.archive": SessionKeyParams,
    "sessions.unarchive": SessionKeyParams,
    "sessions.list_archived": SessionsListArchivedParams,
    "sessions.get_tracked_files": SessionKeyParams,
    "sessions.clear_tracked_files": SessionKeyParams,
    "sessions.claim_legacy": SessionKeyParams,
    "sessions.rename": SessionRenameParams,
}


def validate_session_params(method: str, params: dict[str, Any]) -> BaseModel:
    model = SESSION_METHOD_PARAM_MODELS[method]
    try:
        return model.model_validate(params)
    except ValidationError as exc:
        raise AppServerError("Invalid params", code="INVALID_PARAMS") from exc
