"""Typed request params for Codex-style turn App Server methods."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from miqi.runtime.app_server import AppServerError
from miqi.runtime.turn_protocol import (
    TurnProtocolError,
    input_text,
    normalize_turn_input,
)

T = TypeVar("T", bound=BaseModel)


def _non_empty_string(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


class _TurnParams(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
    )


class TurnStartParams(_TurnParams):
    thread_id: str = Field(validation_alias="threadId")
    input: list[dict[str, Any]]
    client_user_message_id: str | None = Field(default=None, validation_alias="clientUserMessageId")
    model: str | None = None
    effort: str | None = None
    summary: str | None = None
    personality: str | None = None
    output_schema: Any | None = Field(default=None, validation_alias="outputSchema")
    environments: Any | None = None

    @field_validator("thread_id")
    @classmethod
    def _thread_id(cls, value: str) -> str:
        return _non_empty_string(value, "threadId")

    @field_validator("client_user_message_id")
    @classmethod
    def _client_user_message_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _non_empty_string(value, "clientUserMessageId")

    @property
    def input_items(self) -> list[dict[str, Any]]:
        return normalize_turn_input(self.input)

    @property
    def content(self) -> str:
        return input_text(self.input_items)

    @property
    def settings_overrides(self) -> dict[str, Any]:
        pairs = {
            "model": self.model,
            "effort": self.effort,
            "summary": self.summary,
            "personality": self.personality,
            "outputSchema": self.output_schema,
            "environments": self.environments,
        }
        return {key: value for key, value in pairs.items() if value is not None}

    @model_validator(mode="after")
    def _validate_content(self) -> "TurnStartParams":
        try:
            content = self.content
        except TurnProtocolError as exc:
            raise ValueError(str(exc)) from exc
        if not content:
            raise ValueError("turn/start requires at least one text input item")
        return self


class TurnInterruptParams(_TurnParams):
    thread_id: str = Field(validation_alias="threadId")
    turn_id: str = Field(validation_alias="turnId")

    @field_validator("thread_id")
    @classmethod
    def _thread_id(cls, value: str) -> str:
        return _non_empty_string(value, "threadId")

    @field_validator("turn_id")
    @classmethod
    def _turn_id(cls, value: str) -> str:
        return _non_empty_string(value, "turnId")


class TurnSteerParams(_TurnParams):
    thread_id: str = Field(validation_alias="threadId")
    expected_turn_id: str = Field(validation_alias="expectedTurnId")
    input: list[dict[str, Any]]
    client_user_message_id: str | None = Field(default=None, validation_alias="clientUserMessageId")

    @field_validator("thread_id")
    @classmethod
    def _thread_id(cls, value: str) -> str:
        return _non_empty_string(value, "threadId")

    @field_validator("expected_turn_id")
    @classmethod
    def _expected_turn_id(cls, value: str) -> str:
        return _non_empty_string(value, "expectedTurnId")

    @field_validator("client_user_message_id")
    @classmethod
    def _client_user_message_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _non_empty_string(value, "clientUserMessageId")

    @property
    def input_items(self) -> list[dict[str, Any]]:
        return normalize_turn_input(self.input)

    @property
    def content(self) -> str:
        return input_text(self.input_items)

    @model_validator(mode="after")
    def _validate_content(self) -> "TurnSteerParams":
        try:
            content = self.content
        except TurnProtocolError as exc:
            raise ValueError(str(exc)) from exc
        if not content:
            raise ValueError("turn/steer requires at least one text input item")
        return self


class ThreadCompactStartParams(_TurnParams):
    thread_id: str = Field(validation_alias="threadId")

    @field_validator("thread_id")
    @classmethod
    def _thread_id(cls, value: str) -> str:
        return _non_empty_string(value, "threadId")


class ThreadInjectItemsParams(_TurnParams):
    thread_id: str = Field(validation_alias="threadId")
    items: list[dict[str, Any]]

    @field_validator("thread_id")
    @classmethod
    def _thread_id(cls, value: str) -> str:
        return _non_empty_string(value, "threadId")

    @field_validator("items")
    @classmethod
    def _items(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not value:
            raise ValueError("items must be a non-empty list")
        return value


def _with_aliases(params: dict[str, Any]) -> dict[str, Any]:
    """Preserve existing snake_case aliases while preferring camelCase."""
    aliased = dict(params)
    aliases = {
        "thread_id": "threadId",
        "turn_id": "turnId",
        "expected_turn_id": "expectedTurnId",
        "client_user_message_id": "clientUserMessageId",
        "output_schema": "outputSchema",
    }
    for old, new in aliases.items():
        if old in aliased and new not in aliased:
            aliased[new] = aliased[old]
    return aliased


TURN_METHOD_PARAM_MODELS = {
    "turn/start": TurnStartParams,
    "turn/interrupt": TurnInterruptParams,
    "turn/steer": TurnSteerParams,
    "thread/compact/start": ThreadCompactStartParams,
    "thread/inject_items": ThreadInjectItemsParams,
}


def required_fields_for_model(model: type[BaseModel]) -> list[str]:
    """Return required external camelCase field names for a request model."""
    required: list[str] = []
    for name, field in model.model_fields.items():
        if not field.is_required():
            continue
        alias = field.validation_alias
        required.append(str(alias) if alias is not None else name)
    return sorted(required)


def validate_turn_params(model: type[T], params: dict[str, Any]) -> T:
    """Validate turn handler params and convert failures to AppServerError."""
    try:
        return model.model_validate(_with_aliases(params))
    except ValidationError as exc:
        raise AppServerError("Invalid turn params", code="INVALID_PARAMS") from exc
    except TurnProtocolError as exc:
        raise AppServerError("Invalid turn params", code="INVALID_PARAMS") from exc
