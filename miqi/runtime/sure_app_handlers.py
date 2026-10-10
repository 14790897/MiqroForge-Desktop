"""阶段 3:sure/* AppServer 方法族(健康检查 / 核查启动 / 取消 / 状态)。

事件经 ``server.emit_client_event``(孤儿事件,request_id=None)发出——
Electron 侧 bridge.ts 按 IPC_EVENTS 常量名自动转发到渲染层,主进程无需
逐条接线(见 apps/desktop/src/main/bridge.ts 的孤儿事件转发器)。
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger

from miqi.runtime import protocol_specs
from miqi.runtime.app_server import AppServerError, get_bridge_context
from miqi.runtime.sure_task_runtime import (
    SureBusyError,
    SureTaskRuntime,
    SureUnavailableError,
    probe_sure_health,
)

__all__ = ["register_sure_handlers"]


def _get_runtime(registry: Any) -> SureTaskRuntime:
    """懒创建并写入 bridge_context(仿 workbench_process 模式)。"""
    runtime = get_bridge_context(registry, "sure_task_runtime")
    if runtime is None:
        runtime = SureTaskRuntime()
        registry.bridge_context["sure_task_runtime"] = runtime
    return runtime


def register_sure_handlers(server) -> None:
    """Register sure.* handlers on an AppServer instance."""

    async def _sure_health(request_id, params, client_id, session_id, registry):
        return {"result": await probe_sure_health()}

    async def _sure_check_start(request_id, params, client_id, session_id, registry):
        project = (params or {}).get("project")
        if not isinstance(project, str) or not project.strip():
            raise AppServerError("project 必须是项目绝对路径", code="INVALID_PARAMS")
        project = project.strip()
        # 与 MCP 工具的运行时保护同语义(拒绝制,permission_engine.SURE_PROJECT_TOOLS):
        # 缺路径/非绝对/不存在一律拒绝,绝不回落到任何进程 cwd。
        if not os.path.isabs(project):
            raise AppServerError("project 必须是绝对路径", code="INVALID_PARAMS")
        if not os.path.isdir(project):
            raise AppServerError(f"项目目录不存在:{project}", code="INVALID_PARAMS")

        command = (params or {}).get("command", "check")
        if command not in ("check", "repair", "recheck"):
            raise AppServerError(
                "command 必须是 check/repair/recheck", code="INVALID_PARAMS"
            )

        runtime = _get_runtime(registry)

        async def _on_event(kind: str, data: dict) -> None:
            await server.emit_client_event(client_id, kind, data)

        try:
            result = await runtime.start(
                client_id=client_id, project=project, on_event=_on_event, command=command
            )
        except SureUnavailableError as exc:
            # Phase 35 审计:AppServerError 只带固定安全文案,异常原文进日志
            logger.warning("sure.check.start 不可用: {}", exc)
            raise AppServerError(
                SureUnavailableError.USER_MESSAGE, code=SureUnavailableError.code
            ) from exc
        except SureBusyError as exc:
            logger.warning("sure.check.start 被拒(忙): {}", exc)
            raise AppServerError(SureBusyError.USER_MESSAGE, code=SureBusyError.code) from exc
        return {"result": result}

    async def _sure_check_cancel(request_id, params, client_id, session_id, registry):
        ok = await _get_runtime(registry).cancel(client_id=client_id)
        return {"result": {"ok": ok}}

    async def _sure_check_status(request_id, params, client_id, session_id, registry):
        return {"result": {"task": _get_runtime(registry).status(client_id=client_id)}}

    server.register_method("sure.health", _sure_health, spec=protocol_specs.SURE_HEALTH)
    server.register_method(
        "sure.check.start", _sure_check_start, spec=protocol_specs.SURE_CHECK_START
    )
    server.register_method(
        "sure.check.cancel", _sure_check_cancel, spec=protocol_specs.SURE_CHECK_CANCEL
    )
    server.register_method(
        "sure.check.status", _sure_check_status, spec=protocol_specs.SURE_CHECK_STATUS
    )
