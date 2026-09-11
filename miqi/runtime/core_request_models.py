"""Typed request params for core App Server capability methods."""

from __future__ import annotations

from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from miqi.runtime.app_server import AppServerError

T = TypeVar("T", bound=BaseModel)


class _CoreParams(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class EmptyParams(_CoreParams):
    pass


class ClientInfoParams(_CoreParams):
    name: str
    title: str = ""
    version: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("clientInfo.name must be a non-empty string")
        return value.strip()

    @field_validator("title", "version", mode="before")
    @classmethod
    def _optional_strings(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("clientInfo title/version must be strings")
        return value


class InitializeCapabilitiesParams(_CoreParams):
    experimental_api: bool = Field(default=False, validation_alias="experimentalApi")
    opt_out_notification_methods: list[str] = Field(
        default_factory=list,
        validation_alias="optOutNotificationMethods",
    )

    @field_validator("experimental_api", mode="before")
    @classmethod
    def _experimental_api(cls, value: Any) -> Any:
        if not isinstance(value, bool):
            raise ValueError("experimentalApi must be a boolean")
        return value

    @field_validator("opt_out_notification_methods")
    @classmethod
    def _opt_out(cls, value: list[str]) -> list[str]:
        for item in value:
            if not isinstance(item, str):
                raise ValueError("optOutNotificationMethods must be strings")
        return value


class InitializeParams(_CoreParams):
    client_info: ClientInfoParams = Field(validation_alias="clientInfo")
    capabilities: InitializeCapabilitiesParams | None = None
    client_id: str | None = Field(default=None, validation_alias="clientId")

    @field_validator("client_id")
    @classmethod
    def _client_id(cls, value: str | None) -> str | None:
        if value is not None and not isinstance(value, str):
            raise ValueError("clientId must be a string")
        return value


class ModelListParams(_CoreParams):
    include_hidden: bool = Field(default=False, validation_alias="includeHidden")

    @field_validator("include_hidden", mode="before")
    @classmethod
    def _include_hidden(cls, value: Any) -> Any:
        if not isinstance(value, bool):
            raise ValueError("includeHidden must be a boolean")
        return value


class ModelProviderCapabilitiesReadParams(_CoreParams):
    provider: str | None = None
    provider_name: str | None = Field(default=None, validation_alias="providerName")

    @field_validator("provider", "provider_name")
    @classmethod
    def _provider(cls, value: str | None) -> str | None:
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("provider must be a non-empty string")
        return value.strip() if isinstance(value, str) else value


class ExperimentalFeatureListParams(_CoreParams):
    cursor: str | None = None
    limit: int = 100
    thread_id: str | None = Field(default=None, validation_alias="threadId")

    @field_validator("cursor", "thread_id")
    @classmethod
    def _optional_string(cls, value: str | None) -> str | None:
        if value is not None and not isinstance(value, str):
            raise ValueError("cursor/threadId must be strings")
        return value

    @field_validator("limit", mode="before")
    @classmethod
    def _limit(cls, value: Any) -> Any:
        if not isinstance(value, int):
            raise ValueError("limit must be an integer")
        if value < 1 or value > 500:
            raise ValueError("limit must be between 1 and 500")
        return value


class ExperimentalFeatureEnablementSetParams(_CoreParams):
    features: dict[str, bool] = Field(default_factory=dict)
    enablement: dict[str, bool] | None = None

    @model_validator(mode="after")
    def _merge_alias(self) -> "ExperimentalFeatureEnablementSetParams":
        if self.enablement is not None:
            self.features = self.enablement
        return self

    @field_validator("features", "enablement", mode="before")
    @classmethod
    def _feature_map(cls, value: Any) -> Any:
        if value is None:
            return value
        if not isinstance(value, dict):
            raise ValueError("features must be an object")
        for key, enabled in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("feature names must be non-empty strings")
            if not isinstance(enabled, bool):
                raise ValueError("feature enablement values must be booleans")
        return value


class PermissionProfileListParams(_CoreParams):
    cwd: str | None = None
    cursor: str | None = None
    limit: int = 100

    @field_validator("cwd", "cursor")
    @classmethod
    def _optional_string(cls, value: str | None) -> str | None:
        if value is not None and not isinstance(value, str):
            raise ValueError("cwd/cursor must be strings")
        return value

    @field_validator("limit", mode="before")
    @classmethod
    def _limit(cls, value: Any) -> Any:
        if not isinstance(value, int):
            raise ValueError("limit must be an integer")
        if value < 1 or value > 500:
            raise ValueError("limit must be between 1 and 500")
        return value


class ConfigEditParams(_CoreParams):
    op: Literal["set", "delete"] = "set"
    path: str
    value: Any = None

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("path must be a non-empty string")
        return value


class ConfigBatchWriteParams(_CoreParams):
    edits: list[ConfigEditParams]
    reload_user_config: bool = Field(default=True, validation_alias="reloadUserConfig")

    @field_validator("edits")
    @classmethod
    def _edits(cls, value: list[ConfigEditParams]) -> list[ConfigEditParams]:
        if not value:
            raise ValueError("edits is required and must be a non-empty list")
        return value

    @field_validator("reload_user_config", mode="before")
    @classmethod
    def _reload(cls, value: Any) -> Any:
        if not isinstance(value, bool):
            raise ValueError("reloadUserConfig must be a boolean")
        return value


class ConfigUpdateParams(_CoreParams):
    config: dict[str, Any]
    # 比较并设置（#991）：调用方声明「期望当前默认模型仍为此值」，与磁盘
    # 当前值不一致时 handler 跳过写入 —— 自动同步（登录后兜底写网关模型）
    # 用它在模型被并发修改时不覆盖用户更新的选择。
    expect_model: str | None = Field(default=None, validation_alias="expectModel")

    @field_validator("config")
    @classmethod
    def _config(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict) or not value:
            raise ValueError("config is required and must be a non-empty object")
        return value


CORE_METHOD_PARAM_MODELS: dict[str, type[BaseModel]] = {
    "initialize": InitializeParams,
    "initialized": EmptyParams,
    "status": EmptyParams,
    "python.check": EmptyParams,
    "config/read": EmptyParams,
    "config/batchWrite": ConfigBatchWriteParams,
    "config.get": EmptyParams,
    "config.update": ConfigUpdateParams,
    "model/list": ModelListParams,
    "modelProvider/capabilities/read": ModelProviderCapabilitiesReadParams,
    "experimentalFeature/list": ExperimentalFeatureListParams,
    "experimentalFeature/enablement/set": ExperimentalFeatureEnablementSetParams,
    "permissionProfile/list": PermissionProfileListParams,
}


def validate_core_params(method: str, params: dict[str, Any]) -> BaseModel:
    model = CORE_METHOD_PARAM_MODELS[method]
    try:
        return model.model_validate(params)
    except ValidationError as exc:
        raise AppServerError("Invalid params", code="INVALID_PARAMS") from exc
