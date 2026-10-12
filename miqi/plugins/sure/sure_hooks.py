"""阶段5:把 MiQi 的会话事件桥接给 SURE(`sure hook ingest --source miqi`)。

插件加载器以 sys.path + 顶层模块名导入本文件(见
plugin_manager._make_module_callback),模块名要避免与其他插件撞车,
故用 sure_hooks 而非 hooks。

设计约束(阶段5 方案定稿):
- 事件是观测(Tier 1):fire-and-forget——SURE 未装、超时、返回错误时
  一律静默跳过,**绝不阻塞或改变 MiQi 的工具执行结果**;
- 最小采集:只发会话身份/项目根/工具名/结果状态/时间戳,不发工具参数
  (args)与输出内容;子进程环境走 sure_task_runtime 的白名单
  (build_sure_env)——平台令牌与 MIQI_* 不透传;
- 只上报 SURE 侧已建词表的工具(exec/read_file/write_file/edit_file/
  apply_patch);list_dir 与其余工具不发——SURE 侧对未收名字按
  fail-closed 读取(见其 integrations/miqi/README.md)。

事件映射(与 SURE normalizer::miqi 一一对应):
  SESSION_START → SessionStart;PRE_TOOL_USE → PreToolUse;
  POST_TOOL_USE → PostToolUse;STOP → Stop。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone

from loguru import logger

from miqi.config.schema import resolve_sure_bin
from miqi.execution.orchestrator import OrchestrationResult
from miqi.runtime.sure_task_runtime import (
    _close_subprocess_transport,
    _kill_process_tree,
    build_sure_env,
)

#: ingest 子进程的等待上限(秒)。这是工具执行路径上的同步等待,必须短。
INGEST_TIMEOUT_SECONDS = 3.0

#: 只上报这些工具(SURE 侧词表;其余名字在 SURE 侧按未知名 fail-closed)。
_VOCABULARY = frozenset(
    {"exec", "read_file", "write_file", "edit_file", "apply_patch"}
)


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _base_event(event: str, session_id: str, workspace: str | None) -> dict:
    payload: dict = {
        "event": event,
        "harness_session_id": session_id,
        "timestamp_utc": _timestamp_utc(),
        "source": "miqi",
    }
    if workspace:
        payload["project_root"] = workspace
    return payload


async def _ingest(payload: dict, workspace: str | None) -> None:
    """把一条事件喂给 ``sure hook ingest``;任何失败静默跳过。

    超时/异常时终止整棵子进程树并回收(与 sure_task_runtime 同款清理),
    再吞掉异常——本通道是观测,不是门禁。
    """
    binary = resolve_sure_bin()
    if not binary:
        return
    spawn_kwargs: dict = {}
    if os.name == "nt":
        spawn_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        spawn_kwargs["start_new_session"] = True
    cwd = workspace if workspace and os.path.isdir(workspace) else None
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            "hook",
            "ingest",
            "--source",
            "miqi",
            cwd=cwd,
            env=build_sure_env(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            **spawn_kwargs,
        )
        await asyncio.wait_for(
            proc.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            timeout=INGEST_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        # 会话收停时取消落在等待中途:清理子进程后原样上抛。
        if proc is not None:
            try:
                await _kill_process_tree(proc)
            except Exception:
                logger.debug("SURE ingest 取消后清理子进程失败(忽略)")
            _close_subprocess_transport(proc)
        raise
    except Exception:  # noqa: BLE001 —— 观测通道,失败绝不上抛
        logger.debug("SURE hook ingest 失败(静默跳过): {}", payload.get("event"))
        if proc is not None and proc.returncode is None:
            try:
                await _kill_process_tree(proc)
            except Exception:
                logger.debug("SURE ingest 子进程清理失败(忽略)")
        if proc is not None:
            _close_subprocess_transport(proc)


async def _send(
    event_name: str,
    session_id: str,
    workspace: str | None,
    *,
    tool: str | None = None,
    result: str | None = None,
) -> None:
    payload = _base_event(event_name, session_id, workspace)
    if tool is not None:
        payload["tool"] = tool
    if result is not None:
        payload["result"] = result
    await _ingest(payload, workspace)


async def on_session_start(ctx) -> None:
    """HookPoint.SESSION_START → SessionStart(生命周期 ctx,数据在 ctx.data)。"""
    data = getattr(ctx, "data", None) or {}
    await _send(
        "SessionStart",
        str(data.get("session_id") or ""),
        data.get("workspace"),
    )


async def on_stop(ctx) -> None:
    """HookPoint.STOP → Stop。"""
    data = getattr(ctx, "data", None) or {}
    await _send(
        "Stop",
        str(data.get("session_id") or ""),
        data.get("workspace"),
    )


async def on_pre_tool_use(ctx) -> None:
    """HookPoint.PRE_TOOL_USE → PreToolUse(仅词表内工具)。"""
    tool = getattr(ctx, "tool_name", "")
    if tool not in _VOCABULARY:
        return
    await _send(
        "PreToolUse",
        str(getattr(ctx, "session_id", "") or ""),
        getattr(ctx, "workspace", "") or None,
        tool=tool,
    )


async def on_post_tool_use(ctx) -> None:
    """HookPoint.POST_TOOL_USE → PostToolUse(仅词表内工具、且工具确实完成)。

    orchestrator 在工具内部报错(`_execute_in_sandbox` 自标 TOOL_ERROR)后
    仍会调到这里——那种情况**不发 completed**,如实呈现为"有 requested 无
    completed"(失败不误记成功)。result 固定 "ok";失败/拒绝的细节不在
    v1 载荷里(最小采集)。
    """
    tool = getattr(ctx, "tool_name", "")
    if tool not in _VOCABULARY:
        return
    status = getattr(ctx, "status", None)
    if status is not None and status != OrchestrationResult.SUCCESS:
        return
    await _send(
        "PostToolUse",
        str(getattr(ctx, "session_id", "") or ""),
        getattr(ctx, "workspace", "") or None,
        tool=tool,
        result="ok",
    )
