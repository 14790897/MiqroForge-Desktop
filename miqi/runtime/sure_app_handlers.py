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
from miqi.runtime.sure_report import SureRepairContract
from miqi.runtime.sure_task_runtime import (
    SureBusyError,
    SureTaskRuntime,
    SureUnavailableError,
    probe_sure_health,
)

__all__ = ["build_fix_task", "register_sure_handlers"]


def build_fix_task(project: str, contracts: list[SureRepairContract]) -> str:
    """把修复契约渲染成修复子代理的任务书(纯函数;修复以契约为界)。

    措辞对齐内置 sure-fix 技能:只修点名问题、不破坏"必须保留"、不做契约
    外重构;并内置硬规则——完成语不算证据,只有 SURE 复核通过才算修好。
    """
    lines = [
        "你是修复子代理:按 SURE 修复契约修复项目。",
        f"项目(绝对路径):{project}",
        "用户已通过验收面板把该项目交给你修复;修复以契约为界,只修契约点名的问题。",
        "",
    ]
    total = len(contracts)
    for i, c in enumerate(contracts, 1):
        lines.append(f"【契约 {i}/{total}】{c.problem}")
        lines.append(f"- 为什么重要:{c.why_it_matters}")
        for label, items in (
            ("必须修复", c.required_fix),
            ("必须保留", c.preserve),
            ("验收标准", c.acceptance),
            ("禁止走捷径", c.forbidden_shortcuts),
        ):
            for item in items:
                lines.append(f"- {label}:{item}")
        if c.rechecks_that_must_pass:
            lines.append(f"- 复核项:{'、'.join(c.rechecks_that_must_pass)}")
        lines.append("")
    lines.extend(
        [
            "硬规则:",
            "- 你自己的完成语不算证据——SURE 会复核(recheck),契约点名的检查全部通过才算修好;",
            "- 修复后如实汇报改了什么;无法修复的项如实说明,不要假装完成;",
            "- 不要隐藏症状(例如只删掉可疑输出而不修产生它的代码路径)。",
        ]
    )
    return "\n".join(lines)


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

    async def _sure_fix_start(request_id, params, client_id, session_id, registry):
        """把最近一次 repair 契约交给自己会话里的 code-agent 子代理执行修复。

        侦察结论(2026-10-10):不注入回合——`RuntimeSession` 只有一条共享事件
        队列,面板 drain 会偷 UI 回合事件;spawn 子代理走 agent_jobs,完全绕开
        该队列,完成经 `subagent_result` 事件回到本客户端。
        """
        project = (params or {}).get("project")
        if not isinstance(project, str) or not project.strip():
            raise AppServerError("project 必须是项目绝对路径", code="INVALID_PARAMS")
        project = project.strip()
        if not os.path.isabs(project):
            raise AppServerError("project 必须是绝对路径", code="INVALID_PARAMS")
        if not os.path.isdir(project):
            raise AppServerError(f"项目目录不存在:{project}", code="INVALID_PARAMS")

        runtime = _get_runtime(registry)
        envelope = runtime.last_report(client_id, command="repair")
        if envelope is None:
            raise AppServerError(
                "请先在本页生成修复契约,再交给 AI 修复", code="SURE_NO_CONTRACT"
            )

        # 面板是 client 级(无 session_id):选该客户端最近活跃的会话
        session_key = None
        best_activity = -1.0
        for sid in registry.list_sessions(client_id):
            activity = registry._last_activity.get(sid, 0.0)
            if activity >= best_activity:
                best_activity = activity
                session_key = sid
        session = (
            await registry.get_session(client_id, session_key)
            if session_key is not None
            else None
        )
        if session is None:
            raise AppServerError(
                "没有可用的聊天会话——修复子代理运行在会话中,请先在聊天里创建一个会话",
                code="SURE_NO_SESSION",
            )
        control = getattr(getattr(session, "services", None), "agent_control", None)
        if control is None:
            raise AppServerError("会话缺少子代理能力,无法发起修复", code="INTERNAL")

        task_text = build_fix_task(project, list(envelope.details.repairs))
        label = f"SURE 修复:{os.path.basename(project) or project}"
        try:
            live = await control.spawn(
                "code-agent", task_text, label=label, user_roots=[project]
            )
        except RuntimeError as exc:
            # Phase 35 审计:固定安全文案,异常原文只进日志(并发上限等)
            logger.warning("sure.fix.start 被拒: {}", exc)
            raise AppServerError(
                "修复子代理已达并发上限(3),请稍后再试", code="SURE_FIX_BUSY"
            ) from exc
        except Exception as exc:  # noqa: BLE001 —— 统一折叠为可展示错误
            logger.exception("sure.fix.start 失败: {}", exc)
            raise AppServerError("修复子代理启动失败,详见日志", code="INTERNAL") from exc
        return {
            "result": {"agentId": live.agent_id, "sessionKey": session_key, "project": project}
        }

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
    server.register_method(
        "sure.fix.start", _sure_fix_start, spec=protocol_specs.SURE_FIX_START
    )
