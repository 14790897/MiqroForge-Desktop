"""阶段 3:sure/* AppServer 方法族(健康检查 / 核查启动 / 取消 / 状态)。

事件经 ``server.emit_client_event``(孤儿事件,request_id=None)发出——
Electron 侧 bridge.ts 按 IPC_EVENTS 常量名自动转发到渲染层,主进程无需
逐条接线(见 apps/desktop/src/main/bridge.ts 的孤儿事件转发器)。
"""

from __future__ import annotations

import os
import time
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
        # task = 运行中的对账;lastReport = 最近一次完成报告——面板切页/重开
        # 后靠它恢复完成态(2026-10-14:此前只带回"运行中",完成态一离开
        # 面板再回来报告与项目全部丢失)。
        # runningAgents/agentCap = 与 sure.fix.start 并发闸同源的快照——
        # 面板凭它把「交给 AI 修复」显示为禁用,避免"新报告让按钮重新亮起、
        # 用户连点撞上限爆红"(2026-10-14 用户实测)。
        runtime = _get_runtime(registry)
        last = runtime.last_report(client_id)
        running, cap = await _running_agents(registry, client_id)
        return {
            "result": {
                "task": runtime.status(client_id=client_id),
                "lastReport": last.model_dump(mode="json") if last is not None else None,
                "runningAgents": running,
                "agentCap": cap,
            }
        }

    async def _running_agents(registry, client_id) -> tuple[int, int]:
        """最近活跃会话里未终结的子代理数,及该会话的并发上限。

        与 `sure.fix.start` 选会话、数并发的方式一致(该入口的闸在
        `AgentControl.spawn`:`list_agents` 的 status 叠加作业运行时状态,
        queued/running 视为未终结)。无会话/无能力时如实回 (0, 3):这是一份
        尽力而为的快照,不是闸门本身——越权拒绝仍由 spawn 侧负责。
        """
        session_key = None
        best_activity = -1.0
        for sid in registry.list_sessions(client_id):
            activity = registry._last_activity.get(sid, 0.0)
            if activity >= best_activity:
                best_activity = activity
                session_key = sid
        if session_key is None:
            return 0, 3
        session = await registry.get_session(client_id, session_key)
        control = getattr(getattr(session, "services", None), "agent_control", None) if session else None
        if control is None:
            return 0, 3
        try:
            agents = control.list_agents()
        except Exception as exc:  # noqa: BLE001 — 快照尽力而为,失败如实回 0
            logger.debug("running-agents 快照失败: {}", exc)
            return 0, getattr(control, "max_concurrent", 3)
        running = sum(1 for a in agents if a.get("status") in ("queued", "running"))
        return running, getattr(control, "max_concurrent", 3)

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

        # 评审第 2 轮 P2:报告项目与请求项目必须一致——后端不依赖前端状态
        # 保证一致性;授权子代理的目录只能是被修复契约覆盖的那个项目。
        if os.path.normcase(os.path.normpath(envelope.details.project)) != os.path.normcase(
            os.path.normpath(project)
        ):
            raise AppServerError(
                "修复契约与当前项目不一致——请对当前项目重新生成修复契约",
                code="SURE_PROJECT_MISMATCH",
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
            # 2026-10-14:没有任何会话时自动开一个——修复派发不再要求用户
            # 先手动创建(空会话不落盘,没有宿主时此前直接爆红,很不方便)。
            # 建会话的取材由 bridge 发布(ensure_session,与 chat.send 一致);
            # 桥未提供该能力时保留旧行为与旧文案。
            ensure = getattr(registry, "bridge_context", {}).get("ensure_session")
            if ensure is not None:
                try:
                    new_key = f"desktop:{int(time.time() * 1000)}"
                    await ensure(client_id, new_key)
                    session = await registry.get_session(
                        client_id, f"{client_id}:{new_key}"
                    )
                    if session is not None:
                        # 与既有会话路径同语义:返回完整会话 id 作 sessionKey
                        session_key = f"{client_id}:{new_key}"
                except Exception as exc:  # noqa: BLE001 — 如实回退到旧路径
                    logger.warning("sure.fix.start: 自动建会话失败: {}", exc)
                    session = None
        if session is None:
            raise AppServerError(
                "没有可用的聊天会话,且自动创建失败——请先在聊天里创建一个会话",
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

        # 2026-10-14:派发后立刻补一条作业事件 drain。侦察结论(2026-10-10)
        # 说作业"完全绕开该队列"——实测不是:作业回合的 turn_runner 用的是
        # 会话级 emitter,它的流仍落进会话事件队列(4096 有界),而队列唯一的
        # 消费者是 chat.send 的 drain;面板派发没有聊天请求兜着,填满后作业
        # 冻在第一个增量上(agent_jobs 失速 dump 的现场)。桥未提供该能力时
        # 如实跳过:派发照旧,只是不再有实时流。
        drain = getattr(registry, "bridge_context", {}).get("drain_agent_job_events")
        if drain is not None:
            try:
                await drain(
                    # 会话对象本身:它持有 next_event,drain 靠它消费事件队列。
                    # 2026-10-14 修复:此前误传 `_get_runtime` 的 SureTaskRuntime
                    # ——drain 任务第一次 next_event 就 AttributeError 而死,队列
                    # 无人排空,子代理的流一满(4096)就冻在 emit 上(CI 重试两次
                    # 900s stall,chain 与旧形态相同;本机"通过"是事件量没到
                    # 上限的运气)。
                    runtime=session,
                    thread_id=live.thread_id,
                    session_id=getattr(session, "session_id", None) or session_key,
                    session_key=session_key,
                    client_id=client_id,
                    agent_id=live.agent_id,
                )
            except Exception as exc:  # noqa: BLE001 —— drain 注册失败不连累派发
                logger.warning("sure.fix.start: 作业事件 drain 注册失败: {}", exc)
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
