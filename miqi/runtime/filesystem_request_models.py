"""Typed request params for fs/*, fs/watch, and fuzzyFileSearch* methods."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from miqi.runtime.app_server import AppServerError

T = TypeVar("T", bound=BaseModel)


def _non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _strict_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name}[{index}] must be a non-empty string")
        result.append(item)
    return result


class _FilesystemParams(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class _PathParam(_FilesystemParams):
    path: str

    @field_validator("path", mode="before")
    @classmethod
    def _path(cls, value: Any) -> Any:
        return _non_empty_string(value, "path")


class FsReadFileParams(_PathParam):
    pass


class FsWriteFileParams(_PathParam):
    data_base64: str = Field(validation_alias="dataBase64")

    @field_validator("data_base64", mode="before")
    @classmethod
    def _data_base64(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("dataBase64 must be a string")
        return value


class FsCreateDirectoryParams(_PathParam):
    recursive: bool = True

    @field_validator("recursive", mode="before")
    @classmethod
    def _recursive(cls, value: Any) -> Any:
        return _strict_bool(value, "recursive")


class FsGetMetadataParams(_PathParam):
    pass


class FsReadDirectoryParams(_PathParam):
    pass


class FsRemoveParams(_PathParam):
    recursive: bool = True
    force: bool = True

    @field_validator("recursive", "force", mode="before")
    @classmethod
    def _flags(cls, value: Any) -> Any:
        return _strict_bool(value, "recursive/force")


class FsCopyParams(_FilesystemParams):
    source_path: str = Field(validation_alias="sourcePath")
    destination_path: str = Field(validation_alias="destinationPath")
    recursive: bool = False

    @field_validator("source_path", mode="before")
    @classmethod
    def _source_path(cls, value: Any) -> Any:
        return _non_empty_string(value, "sourcePath")

    @field_validator("destination_path", mode="before")
    @classmethod
    def _destination_path(cls, value: Any) -> Any:
        return _non_empty_string(value, "destinationPath")

    @field_validator("recursive", mode="before")
    @classmethod
    def _recursive(cls, value: Any) -> Any:
        return _strict_bool(value, "recursive")


class FsWatchParams(_PathParam):
    watch_id: str = Field(validation_alias="watchId")

    @field_validator("watch_id", mode="before")
    @classmethod
    def _watch_id(cls, value: Any) -> Any:
        return _non_empty_string(value, "watchId")


class FsUnwatchParams(_FilesystemParams):
    watch_id: str = Field(validation_alias="watchId")

    @field_validator("watch_id", mode="before")
    @classmethod
    def _watch_id(cls, value: Any) -> Any:
        return _non_empty_string(value, "watchId")


class FuzzyFileSearchParams(_FilesystemParams):
    query: str
    roots: list[str]

    @field_validator("query", mode="before")
    @classmethod
    def _query(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("query must be a string")
        return value

    @field_validator("roots", mode="before")
    @classmethod
    def _roots(cls, value: Any) -> Any:
        return _string_list(value, "roots")


class FuzzySessionStartParams(_FilesystemParams):
    session_id: str = Field(validation_alias="sessionId")
    roots: list[str]

    @field_validator("session_id", mode="before")
    @classmethod
    def _session_id(cls, value: Any) -> Any:
        return _non_empty_string(value, "sessionId")

    @field_validator("roots", mode="before")
    @classmethod
    def _roots(cls, value: Any) -> Any:
        return _string_list(value, "roots")


class FuzzySessionUpdateParams(_FilesystemParams):
    session_id: str = Field(validation_alias="sessionId")
    query: str

    @field_validator("session_id", mode="before")
    @classmethod
    def _session_id(cls, value: Any) -> Any:
        return _non_empty_string(value, "sessionId")

    @field_validator("query", mode="before")
    @classmethod
    def _query(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("query must be a string")
        return value


class FuzzySessionStopParams(_FilesystemParams):
    session_id: str = Field(validation_alias="sessionId")

    @field_validator("session_id", mode="before")
    @classmethod
    def _session_id(cls, value: Any) -> Any:
        return _non_empty_string(value, "sessionId")


FILESYSTEM_METHOD_PARAM_MODELS = {
    "fs/readFile": FsReadFileParams,
    "fs/writeFile": FsWriteFileParams,
    "fs/createDirectory": FsCreateDirectoryParams,
    "fs/getMetadata": FsGetMetadataParams,
    "fs/readDirectory": FsReadDirectoryParams,
    "fs/remove": FsRemoveParams,
    "fs/copy": FsCopyParams,
    "fs/watch": FsWatchParams,
    "fs/unwatch": FsUnwatchParams,
    "fuzzyFileSearch": FuzzyFileSearchParams,
    "fuzzyFileSearch/sessionStart": FuzzySessionStartParams,
    "fuzzyFileSearch/sessionUpdate": FuzzySessionUpdateParams,
    "fuzzyFileSearch/sessionStop": FuzzySessionStopParams,
}


def required_fields_for_model(model: type[BaseModel]) -> list[str]:
    required: list[str] = []
    for name, field in model.model_fields.items():
        if not field.is_required():
            continue
        alias = field.validation_alias
        required.append(str(alias) if alias is not None else name)
    return sorted(required)


def validate_filesystem_params(model: type[T], params: dict[str, Any]) -> T:
    """Validate filesystem/search handler params and convert failures to AppServerError."""
    try:
        return model.model_validate(params)
    except ValidationError as exc:
        raise AppServerError("Invalid filesystem params", code="INVALID_PARAMS") from exc
    except ValueError as exc:
        raise AppServerError("Invalid filesystem params", code="INVALID_PARAMS") from exc
