"""Typed request params for command/exec* and process/* methods."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from miqi.runtime.app_server import AppServerError
from miqi.runtime.workbench_process_runtime import (
    BLOCKED_ENV_PREFIXES,
    DEFAULT_OUTPUT_BYTES_CAP,
    DEFAULT_TIMEOUT_MS,
)

T = TypeVar("T", bound=BaseModel)


def _handle_id(raw: str, field_name: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{field_name} must be a non-empty string")
    if len(raw) > 128:
        raise ValueError(f"{field_name} must be <= 128 characters")
    for ch in raw:
        if ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-":
            raise ValueError(f"{field_name} contains invalid character: {ch!r}")
    if "/" in raw or "\\" in raw:
        raise ValueError(f"{field_name} must not contain slashes")
    if ".." in raw:
        raise ValueError(f"{field_name} must not contain '..'")
    return raw


def _resolve_cwd(raw: str | None, workspace: Path, *, required: bool) -> Path:
    if raw is None:
        if required:
            raise ValueError("cwd is required")
        return workspace
    if not isinstance(raw, str) or not raw:
        raise ValueError("cwd must be a non-empty string")
    cwd = Path(raw)
    if not cwd.is_absolute():
        raise ValueError("cwd must be an absolute path")
    if not cwd.exists():
        raise ValueError(f"cwd does not exist: {cwd}")
    if not cwd.is_dir():
        raise ValueError(f"cwd is not a directory: {cwd}")
    try:
        cwd.resolve().relative_to(workspace.resolve())
    except ValueError:
        raise ValueError(f"cwd is outside workspace: {cwd}")
    return cwd


def _decode_base64(raw: str, field_name: str = "deltaBase64") -> bytes:
    if not isinstance(raw, str):
        raise ValueError(f"{field_name} must be a base64-encoded string")
    try:
        return base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError(f"{field_name} is not valid base64") from exc


class _ProcessParams(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class _CommandMixin(BaseModel):
    command: list[str]

    @field_validator("command")
    @classmethod
    def _command(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("command must be a non-empty list of strings")
        for index, arg in enumerate(value):
            if not isinstance(arg, str) or not arg:
                raise ValueError(f"command[{index}] must be a non-empty string")
        return value


class _EnvMixin(BaseModel):
    env: dict[str, str | None] | None = None

    @field_validator("env")
    @classmethod
    def _env(cls, value: dict[str, str | None] | None) -> dict[str, str | None] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("env must be a dict of string -> string|null")
        for key, env_value in value.items():
            if not isinstance(key, str):
                raise ValueError("env keys must be strings")
            if env_value is not None and not isinstance(env_value, str):
                raise ValueError(f"env[{key!r}] must be a string or null")
            for prefix in BLOCKED_ENV_PREFIXES:
                if key.upper().startswith(prefix.upper()):
                    raise ValueError(f"env key {key!r} is not allowed for security reasons")
        return value


class _CapTimeoutMixin(BaseModel):
    disable_output_cap: bool = Field(default=False, validation_alias="disableOutputCap")
    output_bytes_cap: int | float | None = Field(default=None, validation_alias="outputBytesCap")
    disable_timeout: bool = Field(default=False, validation_alias="disableTimeout")
    timeout_ms_raw: int | float | None = Field(default=None, validation_alias="timeoutMs")

    @field_validator("disable_output_cap", "disable_timeout", mode="before")
    @classmethod
    def _strict_bools(cls, value: Any) -> Any:
        if not isinstance(value, bool):
            raise ValueError("flag must be a boolean")
        return value

    @field_validator("output_bytes_cap", "timeout_ms_raw", mode="before")
    @classmethod
    def _strict_numbers(cls, value: Any) -> Any:
        if isinstance(value, str):
            raise ValueError("must be a number, not a string")
        return value

    @field_validator("disable_output_cap", "disable_timeout")
    @classmethod
    def _bools(cls, value: bool) -> bool:
        if not isinstance(value, bool):
            raise ValueError("flag must be a boolean")
        return value

    @property
    def output_cap(self) -> int | None:
        if self.disable_output_cap:
            if "output_bytes_cap" in self.model_fields_set:
                raise ValueError("disableOutputCap and outputBytesCap are mutually exclusive")
            return None
        if "output_bytes_cap" not in self.model_fields_set and "outputBytesCap" not in self.model_fields_set:
            return DEFAULT_OUTPUT_BYTES_CAP
        raw = self.output_bytes_cap
        if raw is None:
            raise ValueError("outputBytesCap must not be null")
        output_cap = int(raw)
        if output_cap < 0:
            raise ValueError("outputBytesCap must be >= 0")
        return output_cap

    def command_timeout_ms(self) -> int | None:
        if self.disable_timeout:
            if "timeout_ms_raw" in self.model_fields_set:
                raise ValueError("disableTimeout and timeoutMs are mutually exclusive")
            return None
        if "timeout_ms_raw" not in self.model_fields_set and "timeoutMs" not in self.model_fields_set:
            return DEFAULT_TIMEOUT_MS
        if self.timeout_ms_raw is None:
            return DEFAULT_TIMEOUT_MS
        timeout_ms = int(self.timeout_ms_raw)
        if timeout_ms < 0:
            raise ValueError("timeoutMs must be >= 0")
        return timeout_ms

    def process_timeout_ms(self) -> int | None:
        if self.disable_timeout:
            if "timeout_ms_raw" in self.model_fields_set:
                raise ValueError("disableTimeout and timeoutMs are mutually exclusive")
            return None
        if "timeout_ms_raw" not in self.model_fields_set and "timeoutMs" not in self.model_fields_set:
            return DEFAULT_TIMEOUT_MS
        if self.timeout_ms_raw is None:
            return None
        timeout_ms = int(self.timeout_ms_raw)
        if timeout_ms < 0:
            raise ValueError("timeoutMs must be >= 0")
        return timeout_ms


class CommandExecParams(_ProcessParams, _CommandMixin, _EnvMixin, _CapTimeoutMixin):
    process_id: str | None = Field(default=None, validation_alias="processId")
    cwd_raw: str | None = Field(default=None, validation_alias="cwd")
    tty: bool = False
    size: Any | None = None
    stream_stdout_stderr: bool = Field(default=False, validation_alias="streamStdoutStderr")
    stream_stdin: bool = Field(default=False, validation_alias="streamStdin")
    delta_base64: str | None = Field(default=None, validation_alias="deltaBase64")
    cwd: Path | None = None

    @field_validator("process_id")
    @classmethod
    def _process_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _handle_id(value, "processId")

    @field_validator("tty", "stream_stdout_stderr", "stream_stdin", mode="before")
    @classmethod
    def _strict_flags(cls, value: Any) -> Any:
        if not isinstance(value, bool):
            raise ValueError("flag must be a boolean")
        return value

    @field_validator("tty", "stream_stdout_stderr", "stream_stdin")
    @classmethod
    def _flags(cls, value: bool) -> bool:
        if not isinstance(value, bool):
            raise ValueError("flag must be a boolean")
        return value

    @model_validator(mode="after")
    def _validate_shape(self) -> "CommandExecParams":
        if self.size is not None and not self.tty:
            raise ValueError("size requires tty: true")
        if self.stream_stdout_stderr and self.process_id is None:
            raise ValueError("processId is required when streamStdoutStderr is true")
        if self.stream_stdin and self.process_id is None:
            raise ValueError("processId is required when streamStdin is true")
        _ = self.output_cap
        _ = self.command_timeout_ms()
        return self

    @property
    def timeout_ms(self) -> int | None:
        return self.command_timeout_ms()

    @property
    def stdin_enabled(self) -> bool:
        return bool(self.stream_stdin or self.delta_base64)

    @property
    def client_visible(self) -> bool:
        return self.process_id is not None


class CommandExecWriteParams(_ProcessParams):
    process_id: str = Field(validation_alias="processId")
    delta_base64: str | None = Field(default=None, validation_alias="deltaBase64")
    close_stdin: bool = Field(default=False, validation_alias="closeStdin")

    @field_validator("process_id")
    @classmethod
    def _process_id(cls, value: str) -> str:
        return _handle_id(value, "processId")

    @field_validator("close_stdin", mode="before")
    @classmethod
    def _strict_close_stdin(cls, value: Any) -> Any:
        if not isinstance(value, bool):
            raise ValueError("closeStdin must be a boolean")
        return value

    @field_validator("close_stdin")
    @classmethod
    def _close_stdin(cls, value: bool) -> bool:
        if not isinstance(value, bool):
            raise ValueError("closeStdin must be a boolean")
        return value

    @model_validator(mode="after")
    def _validate_write_shape(self) -> "CommandExecWriteParams":
        if not self.delta_base64 and not self.close_stdin:
            raise ValueError("At least one of deltaBase64 or closeStdin is required")
        if self.delta_base64:
            _ = self.delta_bytes
        return self

    @property
    def delta_bytes(self) -> bytes | None:
        if not self.delta_base64:
            return None
        return _decode_base64(self.delta_base64)


class CommandExecResizeParams(_ProcessParams):
    process_id: str | None = Field(default=None, validation_alias="processId")

    @field_validator("process_id")
    @classmethod
    def _process_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _handle_id(value, "processId")


class CommandExecTerminateParams(_ProcessParams):
    process_id: str = Field(validation_alias="processId")

    @field_validator("process_id")
    @classmethod
    def _process_id(cls, value: str) -> str:
        return _handle_id(value, "processId")


class ProcessSpawnParams(_ProcessParams, _CommandMixin, _EnvMixin, _CapTimeoutMixin):
    process_handle: str = Field(validation_alias="processHandle")
    cwd_raw: str = Field(validation_alias="cwd")
    tty: bool = False
    stream_stdout_stderr: bool = Field(default=True, validation_alias="streamStdoutStderr")
    cwd: Path | None = None

    @field_validator("process_handle")
    @classmethod
    def _process_handle(cls, value: str) -> str:
        return _handle_id(value, "processHandle")

    @field_validator("tty", "stream_stdout_stderr", mode="before")
    @classmethod
    def _strict_flags(cls, value: Any) -> Any:
        if not isinstance(value, bool):
            raise ValueError("flag must be a boolean")
        return value

    @field_validator("tty", "stream_stdout_stderr")
    @classmethod
    def _flags(cls, value: bool) -> bool:
        if not isinstance(value, bool):
            raise ValueError("flag must be a boolean")
        return value

    @model_validator(mode="after")
    def _validate_shape(self) -> "ProcessSpawnParams":
        _ = self.output_cap
        _ = self.process_timeout_ms()
        return self

    @property
    def timeout_ms(self) -> int | None:
        return self.process_timeout_ms()

    @property
    def stdin_enabled(self) -> bool:
        return bool(self.stream_stdout_stderr)


class ProcessWriteStdinParams(_ProcessParams):
    process_handle: str = Field(validation_alias="processHandle")
    delta_base64: str | None = Field(default=None, validation_alias="deltaBase64")
    close_stdin: bool = Field(default=False, validation_alias="closeStdin")

    @field_validator("process_handle")
    @classmethod
    def _process_handle(cls, value: str) -> str:
        return _handle_id(value, "processHandle")

    @field_validator("close_stdin", mode="before")
    @classmethod
    def _strict_close_stdin(cls, value: Any) -> Any:
        if not isinstance(value, bool):
            raise ValueError("closeStdin must be a boolean")
        return value

    @field_validator("close_stdin")
    @classmethod
    def _close_stdin(cls, value: bool) -> bool:
        if not isinstance(value, bool):
            raise ValueError("closeStdin must be a boolean")
        return value

    @model_validator(mode="after")
    def _validate_write_shape(self) -> "ProcessWriteStdinParams":
        if not self.delta_base64 and not self.close_stdin:
            raise ValueError("At least one of deltaBase64 or closeStdin is required")
        if self.delta_base64:
            _ = self.delta_bytes
        return self

    @property
    def delta_bytes(self) -> bytes | None:
        if not self.delta_base64:
            return None
        return _decode_base64(self.delta_base64)


class ProcessResizePtyParams(_ProcessParams):
    process_handle: str | None = Field(default=None, validation_alias="processHandle")

    @field_validator("process_handle")
    @classmethod
    def _process_handle(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _handle_id(value, "processHandle")


class ProcessKillParams(_ProcessParams):
    process_handle: str = Field(validation_alias="processHandle")

    @field_validator("process_handle")
    @classmethod
    def _process_handle(cls, value: str) -> str:
        return _handle_id(value, "processHandle")


COMMAND_PROCESS_METHOD_PARAM_MODELS = {
    "command/exec": CommandExecParams,
    "command/exec/write": CommandExecWriteParams,
    "command/exec/resize": CommandExecResizeParams,
    "command/exec/terminate": CommandExecTerminateParams,
    "process/spawn": ProcessSpawnParams,
    "process/writeStdin": ProcessWriteStdinParams,
    "process/resizePty": ProcessResizePtyParams,
    "process/kill": ProcessKillParams,
}


def required_fields_for_model(model: type[BaseModel]) -> list[str]:
    required: list[str] = []
    for name, field in model.model_fields.items():
        # Skip internal fields that are set after validation
        if name == "cwd":
            continue
        if not field.is_required():
            continue
        alias = field.validation_alias
        required.append(str(alias) if alias is not None else name)
    return sorted(required)


def validate_process_params(
    model: type[T],
    params: dict[str, Any],
    *,
    workspace: Path | None = None,
) -> T:
    """Validate process handler params and convert failures to AppServerError."""
    try:
        parsed = model.model_validate(params)
        if isinstance(parsed, CommandExecParams):
            parsed.cwd = _resolve_cwd(parsed.cwd_raw, workspace or Path.cwd(), required=False)
        elif isinstance(parsed, ProcessSpawnParams):
            parsed.cwd = _resolve_cwd(parsed.cwd_raw, workspace or Path.cwd(), required=True)
        return parsed
    except ValidationError as exc:
        raise AppServerError("Invalid process params", code="INVALID_PARAMS") from exc
    except ValueError:
        raise AppServerError("Invalid process params", code="INVALID_PARAMS")
