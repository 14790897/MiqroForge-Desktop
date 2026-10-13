"""阶段 3:SURE 可执行报告(`sure check --format json`)的类型绑定与解析。

绑定 SURE 仓库 `schemas/report.schema.json`(当前 v3)。设计原则:

- **如实保真**:`severity`/`status` 在报告里是自由字符串(CLI 以展示形态
  输出,如 "Must fix"/"Cannot confirm")——不做枚举改写、不猜测;
- **不丢信息**:未建模的字段(grants/model_use/privacy/support/未来新增)
  经 ``extra="allow"`` 原样保留,渲染层按需取用;
- **不猜测兼容**:``schema_version`` 低于支持下限时抛 :class:`SureReportError`,
  由调用方如实报告"报告版本不支持",绝不半渲染。

解析入口见 :func:`parse_check_output`;``sure --version`` 解析见
:func:`parse_sure_version`。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: 本模块建模所依据的最低报告 schema 版本(schemas/report.schema.json)。
SUPPORTED_SCHEMA_MIN = 3


class SureReportError(ValueError):
    """报告无法解析/不受支持。消息面向用户可读。"""


class _Model(BaseModel):
    """共同基座:未建模字段一律保留(如实呈现,不丢信息)。"""

    model_config = ConfigDict(extra="allow")


class Aggregate(_Model):
    severity: str
    headline: str
    is_green: bool


class Capability(_Model):
    tier: int
    summary: str
    blind_spots: list[str] = Field(default_factory=list)


class EvidenceAnchor(_Model):
    location: str
    locator: str
    subject: str


class Finding(_Model):
    id: str
    title: str
    what: str
    impact: str
    severity: str
    status: str
    next_action: str
    is_model_only: bool
    evidence_anchors: list[EvidenceAnchor] = Field(default_factory=list)


class NotChecked(_Model):
    id: str
    title: str
    reason: str
    is_critical: bool


class Totals(_Model):
    checked: int
    skipped: int
    could_not_run: int
    open_findings: int


class Report(_Model):
    schema_version: int
    project_fingerprint: str
    aggregate: Aggregate
    ready_for_hand_off: bool
    must_caveat_requirements: bool
    capability: Capability
    findings: list[Finding] = Field(default_factory=list)
    not_checked: list[NotChecked] = Field(default_factory=list)
    totals: Totals
    caveat: str | None = None
    coverage_caveat: str | None = None


class StageRecord(_Model):
    number: int
    stage: str
    title: str
    outcome: str
    reason: str | None = None
    reason_explained: str | None = None
    detail: str | None = None


class CheckDetails(_Model):
    project: str
    purpose: str
    mode: str
    state: str
    report: Report
    stages: list[StageRecord] = Field(default_factory=list)


class CheckEnvelope(_Model):
    command: str
    details: CheckDetails
    exit_code: int
    outcome: str
    protocol_version: int
    sure_version: str


def parse_check_output(stdout: str) -> CheckEnvelope:
    """解析 ``sure check --format json`` 的 stdout(约定为单行 JSON 对象)。

    Raises:
        SureReportError: 输出非法 JSON、缺必需字段或 schema_version 不受支持。
    """
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    if not lines:
        raise SureReportError("SURE 没有输出任何内容")
    try:
        raw: Any = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise SureReportError(f"SURE 输出不是合法 JSON:{exc.msg}") from exc
    if not isinstance(raw, dict):
        raise SureReportError("SURE 输出不是 JSON 对象")

    try:
        envelope = CheckEnvelope.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError → 用户可读错误
        raise SureReportError(f"SURE 报告缺少必需字段或字段类型不符:{exc}") from exc

    if envelope.command != "check":
        raise SureReportError(f"不是 check 报告(command={envelope.command!r})")
    if envelope.details.report.schema_version < SUPPORTED_SCHEMA_MIN:
        raise SureReportError(
            "SURE 报告 schema_version "
            f"{envelope.details.report.schema_version} 低于支持下限 "
            f"{SUPPORTED_SCHEMA_MIN},请升级 SURE 或本应用"
        )
    return envelope


def parse_sure_version(output: str) -> str | None:
    """从 ``sure --version`` 的输出(形如 ``sure 0.1.2``)提取版本号。"""
    parts = output.strip().split()
    if len(parts) >= 2 and parts[0] == "sure":
        return parts[1]
    return None
