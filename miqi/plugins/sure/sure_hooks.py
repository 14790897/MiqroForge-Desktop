"""阶段5:把 MiQi 的会话事件桥接给 SURE(`sure hook ingest --source miqi`)。

插件加载器以 sys.path + 顶层模块名导入本文件(见
plugin_manager._make_module_callback),模块名要避免与其他插件撞车,
故用 sure_hooks 而非 hooks。

设计约束(阶段5 方案定稿;评审第 2 轮补齐):
- 事件是观测(Tier 1),严格 fire-and-forget:钩子回调只做**非阻塞投递**
  (有界队列 + 按需排空任务,队列空即收摊、不留常驻后台任务),SURE 子
  进程的启动/等待/超时全部发生在排空任务里——单次工具调用的延迟不受
  观测影响;
- 队列满、SURE 未装、超时、返回错误:一律静默丢弃并计数(bridge_stats
  区分投递/入队/写入成功/未写入,评审第 3 轮),**绝不反过来阻塞工具**;
  应用退出时未投出的事件随进程结束丢弃(观测通道的既定边界);
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
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger

from miqi.config.schema import resolve_sure_bin
from miqi.execution.orchestrator import OrchestrationResult
from miqi.runtime.sure_task_runtime import (
    _close_subprocess_transport,
    _kill_process_tree,
    build_sure_env,
)

#: 后台 ingest 的等待上限(秒)。等待发生在队列工作者里,不在工具执行路径上。
INGEST_TIMEOUT_SECONDS = 3.0

#: 事件队列上限。满即丢弃(观测通道:丢弃优于阻塞)。
MAX_QUEUE = 256

#: 会话停止时的排空等待上限(秒)。
FLUSH_TIMEOUT_SECONDS = 3.0

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


async def _ingest(payload: dict, workspace: str | None) -> bool:
    """把一条事件喂给 ``sure hook ingest``;任何失败静默跳过。

    超时/异常时终止整棵子进程树并回收(与 sure_task_runtime 同款清理),
    再吞掉异常——本通道是观测,不是门禁。

    返回 True 仅当事件**确认写入 SURE**(子进程退出码 0);未装、超时、
    异常、非零退出均返回 False——统计据此区分「写入成功」与「排空未
    写入」(评审第 3 轮)。退出码 1 是例外语义:那是「决策非放行」(block),
    事件与决策其实已落库(2026-10-15 用真实 store 复现确认)——计数口径
    不随之改,如实区分只做在日志文案里。
    """
    binary = resolve_sure_bin()
    if not binary:
        return False
    spawn_kwargs: dict = {}
    if os.name == "nt":
        spawn_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        spawn_kwargs["start_new_session"] = True
    cwd = workspace if workspace and os.path.isdir(workspace) else None
    proc: asyncio.subprocess.Process | None = None
    try:
        # 取消防护(实测:pytest 事件循环收尾的 Runner 会把挂起任务"取消并等待",
        # 而 Windows proactor 上 spawn 中途被取消时 connect_write_pipe 的
        # future 永不落定 → 收尾永久挂起)。shield 把 spawn 保护到自然完成,
        # 取消在下面被吸收:先拿到 proc、树杀回收,再原样上抛取消。
        spawn = asyncio.ensure_future(
            asyncio.create_subprocess_exec(
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
        )
        try:
            proc = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            # spawn 继续在后台跑完(有界),收下 proc 清理后再上抛取消
            try:
                proc = await asyncio.wait_for(asyncio.shield(spawn), timeout=10.0)
            except Exception:
                logger.debug("SURE ingest 取消后未能取回子进程(放弃回收)")
                raise
            try:
                await _kill_process_tree(proc)
            except Exception:
                logger.debug("SURE ingest 取消后清理子进程失败(忽略)")
            _close_subprocess_transport(proc)
            raise
        await asyncio.wait_for(
            proc.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            timeout=INGEST_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            # communicate 不因非零退出抛错:显式判退出码,才算「写入成功」。
            # 退出码 1 不是写失败:SURE 的语义是「跑了,但答案不干净」= 决策
            # 为 block——事件与决策都已落库(2026-10-15 复现确认),文案如实
            # 区分,别把排查带向「写入失败」。
            if proc.returncode == 1:
                logger.debug(
                    "SURE hook ingest 退出码 1(决策非放行,事件已记录): {}",
                    payload.get("event"),
                )
            else:
                logger.debug(
                    "SURE hook ingest 退出码 {} (未写入): {}",
                    proc.returncode,
                    payload.get("event"),
                )
            return False
        return True
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
        return False


@dataclass
class _BridgeState:
    """一条事件循环的桥接状态:有界队列 + 计数(不持循环的强引用)。

    worker 只留**弱引用**:Task 强引用其事件循环,若这里强持 Task,模块
    (sys.modules 永生)→ 循环 → 循环上一切挂起回调 → ……的引用链会让循环
    永不回收——实测后果:测试进程退出时 sqlite 非守护工作线程无人关闭,
    解释器卡死在 shutdown(评审第 2 轮连带修复)。

    计数口径(评审第 3 轮):attempted=投递调用;enqueued=成功入队;
    dropped=队满丢弃;ingested=确认写入(退出码 0);ingest_failed=排空
    未写入(未装/超时/出错/非零退出)。
    """

    queue: asyncio.Queue
    worker_ref: weakref.ref | None = None
    attempted: int = 0
    enqueued: int = 0
    dropped: int = 0
    ingested: int = 0
    ingest_failed: int = 0


#: 按事件循环隔离(测试每例一条新循环;生产全程同一条,随循环回收)。
_states: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


async def _drain(state: _BridgeState) -> None:
    """消费到队列空即收摊——不留常驻任务(投递时按需重建,见 _enqueue)。

    串行消费保序;SURE 的全部等待发生在这里,不在工具执行路径上。
    """
    while True:
        try:
            payload, workspace = state.queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            if await _ingest(payload, workspace):
                state.ingested += 1
            else:
                state.ingest_failed += 1
        finally:
            state.queue.task_done()


def _state() -> _BridgeState:
    loop = asyncio.get_running_loop()
    state: _BridgeState | None = _states.get(loop)
    if state is None:
        state = _BridgeState(queue=asyncio.Queue(maxsize=MAX_QUEUE))
        _states[loop] = state
    return state


def _enqueue(payload: dict, workspace: str | None) -> None:
    """非阻塞投递;队列满 → 静默丢弃并计数(观测绝不阻塞执行)。

    单线程协作调度下无滞留窗口:若排空任务此刻不在跑(空闲已收摊,或从未
    创建),就在这里重建——投递与重建同一同步段,不存在"刚判空、又被投递"
    的交错。
    """
    state = _state()
    state.attempted += 1
    try:
        state.queue.put_nowait((payload, workspace))
    except asyncio.QueueFull:
        state.dropped += 1
        logger.debug(
            "SURE 事件队列已满,丢弃事件: {} (累计丢弃 {})",
            payload.get("event"),
            state.dropped,
        )
        return
    state.enqueued += 1
    if state.worker_ref is None or not _worker_alive(state):
        state.worker_ref = weakref.ref(asyncio.get_running_loop().create_task(_drain(state)))


def _worker_alive(state: _BridgeState) -> bool:
    task = state.worker_ref() if state.worker_ref is not None else None
    return task is not None and not task.done()


async def flush(timeout: float = FLUSH_TIMEOUT_SECONDS) -> None:
    """等待队列排空(带界)。超时即放弃——观测通道不保证送达。"""
    state: _BridgeState | None = _states.get(asyncio.get_running_loop())
    if state is None:
        return
    try:
        await asyncio.wait_for(state.queue.join(), timeout)
    except asyncio.TimeoutError:
        logger.debug("SURE 事件队列排空超时(放弃等待)")


def bridge_stats() -> dict:
    """当前循环的桥接计数(可观测性;不含任何载荷内容)。

    口径(评审第 3 轮):attempted=投递调用(含被丢弃);enqueued=成功
    入队;dropped=队满丢弃;ingested=确认写入 SURE(退出码 0);failed=
    排空时未写入(未装/超时/出错/非零退出);pending=仍在队列中(不含
    正在处理的一条)。
    """
    state: _BridgeState | None = _states.get(asyncio.get_running_loop())
    if state is None:
        return {
            "attempted": 0,
            "enqueued": 0,
            "dropped": 0,
            "ingested": 0,
            "failed": 0,
            "pending": 0,
        }
    return {
        "attempted": state.attempted,
        "enqueued": state.enqueued,
        "dropped": state.dropped,
        "ingested": state.ingested,
        "failed": state.ingest_failed,
        "pending": state.queue.qsize(),
    }


def _send(
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
    _enqueue(payload, workspace)


async def on_session_start(ctx) -> None:
    """HookPoint.SESSION_START → SessionStart(生命周期 ctx,数据在 ctx.data)。"""
    data = getattr(ctx, "data", None) or {}
    _send(
        "SessionStart",
        str(data.get("session_id") or ""),
        data.get("workspace"),
    )


async def on_stop(ctx) -> None:
    """HookPoint.STOP → Stop;会话收尾时排空队列(带界),尽量少丢事件。"""
    data = getattr(ctx, "data", None) or {}
    _send(
        "Stop",
        str(data.get("session_id") or ""),
        data.get("workspace"),
    )
    await flush()


async def on_pre_tool_use(ctx) -> None:
    """HookPoint.PRE_TOOL_USE → PreToolUse(仅词表内工具)。"""
    tool = getattr(ctx, "tool_name", "")
    if tool not in _VOCABULARY:
        return
    _send(
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
    _send(
        "PostToolUse",
        str(getattr(ctx, "session_id", "") or ""),
        getattr(ctx, "workspace", "") or None,
        tool=tool,
        result="ok",
    )
