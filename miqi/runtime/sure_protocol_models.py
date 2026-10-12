"""阶段 3:sure/* 方法族的协议模型(参数 / 结果 / 事件)。

供 protocol_specs 派生 paramsSchema/resultSchema/eventSchemas(协议目录与
兼容快照),同时是 wire 形状的单一事实源:结果与事件用 snake_case 字段 +
``serialization_alias`` 映射到 camelCase wire 名(与处理器返回的 dict 一致)。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SureCheckStartParams(BaseModel):
    project: str = Field(description="项目绝对路径(缺省/非绝对/不存在一律拒绝)")


class SureHealthResult(BaseModel):
    installed: bool
    binary: str | None
    version: str | None
    error: str | None


class SureCheckStartResult(BaseModel):
    task_id: str = Field(serialization_alias="taskId")
    project: str


class SureCheckCancelResult(BaseModel):
    ok: bool


class SureTaskSnapshot(BaseModel):
    task_id: str = Field(serialization_alias="taskId")
    project: str
    state: str
    elapsed_ms: int = Field(serialization_alias="elapsedMs")
    started_at: int = Field(serialization_alias="startedAt")


class SureCheckStatusResult(BaseModel):
    task: SureTaskSnapshot | None


class SureCheckProgressEvent(BaseModel):
    task_id: str = Field(serialization_alias="taskId")
    project: str
    elapsed_ms: int = Field(serialization_alias="elapsedMs")
    state: str


class SureCheckReportEvent(BaseModel):
    task_id: str = Field(serialization_alias="taskId")
    project: str
    envelope: dict[str, Any]
    elapsed_ms: int = Field(serialization_alias="elapsedMs")


class SureCheckFailedEvent(BaseModel):
    task_id: str = Field(serialization_alias="taskId")
    project: str
    message: str
    code: str
    stderr_tail: str | None = Field(default=None, serialization_alias="stderrTail")


class SureCheckCancelledEvent(BaseModel):
    task_id: str = Field(serialization_alias="taskId")
    project: str
    elapsed_ms: int = Field(serialization_alias="elapsedMs")


SURE_METHOD_RESULT_MODELS: dict[str, type[BaseModel]] = {
    "sure.health": SureHealthResult,
    "sure.check.start": SureCheckStartResult,
    "sure.check.cancel": SureCheckCancelResult,
    "sure.check.status": SureCheckStatusResult,
}

SURE_EVENT_MODELS: dict[str, type[BaseModel]] = {
    "sure_check_progress": SureCheckProgressEvent,
    "sure_check_report": SureCheckReportEvent,
    "sure_check_failed": SureCheckFailedEvent,
    "sure_check_cancelled": SureCheckCancelledEvent,
}
