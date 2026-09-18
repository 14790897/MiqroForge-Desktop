"""request_action_confirmation 工具单测（#646-v2 R2c）。

锁住：参数 schema、normalize_args 默认值/timeout 夹紧、build_result 的
confirmed/cancelled 语义，以及「无 resolver 时绝不虚构同意」。
"""

from __future__ import annotations

import json

import pytest

from miqi.agent.tools.request_action_confirmation import (
    DEFAULT_TIMEOUT_SECONDS,
    RequestActionConfirmationTool,
)


@pytest.fixture
def tool() -> RequestActionConfirmationTool:
    return RequestActionConfirmationTool()


# ── 参数 schema ─────────────────────────────────────────────────────────────


def test_action_enum_has_four_values(tool: RequestActionConfirmationTool) -> None:
    params = tool.parameters
    assert params["properties"]["action"]["enum"] == [
        "upload",
        "payment",
        "delete",
        "external",
    ]
    assert params["required"] == ["action", "target"]


def test_timeout_schema_default_and_bounds(tool: RequestActionConfirmationTool) -> None:
    timeout = tool.parameters["properties"]["timeout_seconds"]
    assert timeout["default"] == DEFAULT_TIMEOUT_SECONDS
    assert (timeout["minimum"], timeout["maximum"]) == (5, 600)


def test_description_points_dangerous_actions_here(tool: RequestActionConfirmationTool) -> None:
    assert tool.name == "request_action_confirmation"
    assert "不要用 ask_user_confirm_card 代替" in tool.description


# ── normalize_args ──────────────────────────────────────────────────────────


def test_normalize_args_defaults(tool: RequestActionConfirmationTool) -> None:
    out = tool.normalize_args({})
    assert out["action"] == "external"
    assert out["target"] == ""
    assert out["file_name"] == ""
    assert out["size_bytes"] is None
    assert out["sha256"] == ""
    assert out["description"] == ""
    assert out["timeout_seconds"] == DEFAULT_TIMEOUT_SECONDS


def test_normalize_args_keeps_caller_fields(tool: RequestActionConfirmationTool) -> None:
    out = tool.normalize_args(
        {
            "action": "upload",
            "target": "Qraft",
            "file_name": "a.zip",
            "size_bytes": 12,
            "sha256": "deadbeef",
            "description": "上传工作流定义",
        }
    )
    assert out["action"] == "upload"
    assert out["target"] == "Qraft"
    assert out["file_name"] == "a.zip"
    assert out["size_bytes"] == 12
    assert out["sha256"] == "deadbeef"
    assert out["description"] == "上传工作流定义"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, 5),  # 低于下界 → 夹紧到 5
        (5, 5),
        (120, 120),
        (600, 600),
        (9999, 600),  # 高于上界 → 夹紧到 600
        ("not-a-number", DEFAULT_TIMEOUT_SECONDS),  # 非法值 → 回落默认
        (None, DEFAULT_TIMEOUT_SECONDS),
    ],
)
def test_timeout_is_clamped_to_5_600(
    tool: RequestActionConfirmationTool, raw: object, expected: int
) -> None:
    assert tool.normalize_args({"timeout_seconds": raw})["timeout_seconds"] == expected


# ── build_result ────────────────────────────────────────────────────────────


def test_build_result_confirmed() -> None:
    out = json.loads(
        RequestActionConfirmationTool.build_result(
            {
                "status": "submitted",
                "answers": {"choice_id": "confirm"},
                "remembered": True,
            }
        )
    )
    assert out["status"] == "confirmed"
    assert out["action_confirmed"] is True
    assert out["choice_id"] == "confirm"
    assert out["remembered"] is True


def test_build_result_submitted_cancel_is_cancelled() -> None:
    out = json.loads(
        RequestActionConfirmationTool.build_result(
            {"status": "submitted", "answers": {"choice_id": "cancel"}}
        )
    )
    assert out["status"] == "cancelled"
    assert out["action_confirmed"] is False
    assert out["choice_id"] == "cancel"
    assert out["reason"]


@pytest.mark.parametrize("gate_status", ["timeout", "cancelled", "error"])
def test_build_result_non_confirm_statuses_are_cancelled(gate_status: str) -> None:
    out = json.loads(RequestActionConfirmationTool.build_result({"status": gate_status}))
    assert out["status"] == "cancelled"
    assert out["action_confirmed"] is False
    assert out["choice_id"] == "cancel"
    assert out["reason"]


# ── execute ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_without_resolver_returns_structured_error(
    tool: RequestActionConfirmationTool,
) -> None:
    """没有用户通道时必须报错，绝不虚构同意。"""
    out = await tool.execute(action="upload", target="Qraft")
    assert out.startswith("错误：")
    assert "confirmed" not in out
    assert "action_confirmed" not in out


@pytest.mark.asyncio
async def test_execute_with_resolver_confirmed() -> None:
    seen: dict = {}

    async def resolver(payload: dict) -> dict:
        seen.update(payload)
        return {"status": "submitted", "answers": {"choice_id": "confirm"}}

    tool = RequestActionConfirmationTool(resolver=resolver)
    out = json.loads(
        await tool.execute(
            action="upload",
            target="Qraft",
            file_name="a.zip",
            size_bytes=10,
            sha256="deadbeef",
            description="上传工作流定义",
        )
    )
    assert out["status"] == "confirmed"
    assert out["action_confirmed"] is True
    # resolver 收到的是 normalize 后的 payload
    assert seen["action"] == "upload"
    assert seen["target"] == "Qraft"
    assert seen["timeout_seconds"] == DEFAULT_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_execute_with_resolver_cancel_is_cancelled() -> None:
    async def resolver(payload: dict) -> dict:
        return {"status": "submitted", "answers": {"choice_id": "cancel"}}

    tool = RequestActionConfirmationTool(resolver=resolver)
    out = json.loads(await tool.execute(action="payment", target="外部平台"))
    assert out["status"] == "cancelled"
    assert out["action_confirmed"] is False


@pytest.mark.asyncio
async def test_execute_resolver_failure_is_structured_error() -> None:
    async def resolver(payload: dict) -> dict:
        raise RuntimeError("gate exploded")

    tool = RequestActionConfirmationTool(resolver=resolver)
    out = await tool.execute(action="delete", target="/tmp/x")
    assert out.startswith("错误：")
    assert "gate exploded" in out
    assert "action_confirmed" not in out
