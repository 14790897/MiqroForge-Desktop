"""阶段 3:SURE 原生核查任务运行时(独立于会话的生命周期管理)。

方案 v7(四轮评审)定稿的三条工程约束,全部落在这里:

- 数据路径走**原生 spawn**:``sure[.exe] check "<项目绝对路径>" --format json``,
  显式传参(避免 CLI 省略路径取进程 cwd 的坑);JSON 是**结束时的一份完整
  报告**,进度只发"运行中 + 已耗时"心跳——**不虚构阶段级进度**;
- 子进程环境**白名单化**(SystemRoot/PATH/TEMP/LOCALAPPDATA/APPDATA/
  USERPROFILE/SURE_BIN):不透传平台令牌、MIQI_* 等;SURE_OPENAI_API_KEY
  不注入(面板不启用模型评估,记录为边界);
- 取消终止**整个进程树**(Windows ``taskkill /T /F``;POSIX 以
  ``start_new_session`` 分组后 killpg),取消不产出报告——与 CLI 语义一致
  (JSON 只在正常结束时发射)。

运行时本身不做传输:事件经 ``on_event(kind, data)`` 异步回调上报,
由接入层(sure_app_handlers)转 ``emit_client_event``。
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from loguru import logger

from miqi.config.schema import resolve_sure_bin
from miqi.runtime.sure_report import SureReportError, parse_check_output, parse_sure_version

#: 进度心跳间隔(秒)。静默期也必须发,防 bridge 600s 空闲 drain(方案 §3 通道 B)。
PROGRESS_INTERVAL_SECONDS = 5.0

#: 健康检查(sure --version)超时。
HEALTH_TIMEOUT_SECONDS = 10.0

#: stdout 报文大小上限(防失控输出;正常报告几十 KB)。
MAX_REPORT_BYTES = 8 * 1024 * 1024

#: 子进程环境白名单(方案 §阶段 3 工程选型,已评审)。
_ENV_ALLOWLIST = (
    "SystemRoot",
    "PATH",
    "TEMP",
    "LOCALAPPDATA",
    "APPDATA",
    "USERPROFILE",
    "SURE_BIN",
)

EventCallback = Callable[[str, dict], Awaitable[None]]


def build_sure_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """白名单子进程环境。``base`` 仅用于测试注入口;生产走 ``os.environ``。"""
    source = os.environ if base is None else base
    return {name: source[name] for name in _ENV_ALLOWLIST if source.get(name)}


class SureUnavailableError(RuntimeError):
    """本机没有可用的 SURE 二进制(或无法启动)。message 面向用户。"""

    code = "SURE_UNAVAILABLE"
    #: 面向用户的固定安全文案(处理器转 AppServerError 时使用,避免注入异常原文)
    USER_MESSAGE = "未找到 SURE:请安装 SURE,或用 SURE_BIN 指向其可执行文件"


class SureBusyError(RuntimeError):
    """该客户端已有核查在运行(每客户端单任务)。"""

    code = "SURE_BUSY"
    USER_MESSAGE = "已有核查在运行——请等待完成或先取消"


@dataclass
class SureTask:
    task_id: str
    client_id: str
    project: str
    started_monotonic: float
    started_at_ms: int
    proc: asyncio.subprocess.Process | None = None
    cancelled: bool = False
    runner: asyncio.Task | None = None

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_monotonic) * 1000)


async def _kill_process_tree(
    proc: asyncio.subprocess.Process, *, grace_seconds: float = 5.0
) -> None:
    """终止 *proc* 的整个进程树(实现参照 shell.py ShellTool._kill_process,#810 语义)。

    Windows: ``taskkill /T /F``(杀包装进程+孙进程),失败回退 terminate→kill;
    POSIX: 对 spawn 时 ``start_new_session`` 建立的进程组发 SIGTERM→SIGKILL。
    """
    if os.name == "nt":
        killer = None
        try:
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(proc.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                await killer.wait()
            except asyncio.CancelledError:
                # 取消落在清理中途:吸收到 taskkill 完成再抛出,避免二次 taskkill
                if killer is not None:
                    await asyncio.shield(killer.wait())
                raise
        except Exception:
            logger.warning("taskkill 失败 pid={},回退 terminate", proc.pid)
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
        return

    try:
        pgid: int | None = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError):
        pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError):
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
    except asyncio.TimeoutError:
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
    else:
        # 组长已退但组内可能有 SIGTERM 免疫的孙进程:补一次 SIGKILL 扫尾
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


async def probe_sure_health(
    *,
    bin_provider: Callable[[], str | None] | None = None,
    env_builder: Callable[[], dict[str, str]] | None = None,
    timeout: float = HEALTH_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """``sure --version`` 探测:二进制是否可用 + 版本号(供面板"引导安装"分支)。

    永不抛出:任何失败折叠为 ``installed=False`` + 面向用户的 error。
    """
    provider = bin_provider or resolve_sure_bin
    builder = env_builder or build_sure_env
    binary = provider()
    if not binary:
        return {
            "installed": False,
            "binary": None,
            "version": None,
            "error": "未找到 SURE:请安装 SURE,或用 SURE_BIN 指向其可执行文件",
        }
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "--version",
            env=builder(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **(dict(creationflags=subprocess.CREATE_NO_WINDOW) if os.name == "nt" else {}),
        )
        stdout_b, _stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 —— 健康检查绝不抛出
        return {
            "installed": False,
            "binary": binary,
            "version": None,
            "error": f"无法运行 SURE:{exc}",
        }
    version = parse_sure_version((stdout_b or b"").decode("utf-8", errors="replace"))
    return {"installed": True, "binary": binary, "version": version, "error": None}


class SureTaskRuntime:
    """管理每客户端至多一个的原生 SURE 核查任务。"""

    def __init__(
        self,
        *,
        bin_provider: Callable[[], str | None] | None = None,
        env_builder: Callable[[], dict[str, str]] | None = None,
        progress_interval: float = PROGRESS_INTERVAL_SECONDS,
    ) -> None:
        self._bin_provider = bin_provider or resolve_sure_bin
        self._env_builder = env_builder or build_sure_env
        self._progress_interval = progress_interval
        self._tasks: dict[str, SureTask] = {}

    # ── 查询 ─────────────────────────────────────────────────────────────

    def active_for(self, client_id: str) -> SureTask | None:
        for task in self._tasks.values():
            if task.client_id == client_id:
                return task
        return None

    def status(self, client_id: str) -> dict[str, Any] | None:
        task = self.active_for(client_id)
        if task is None:
            return None
        return {
            "taskId": task.task_id,
            "project": task.project,
            "state": "running",
            "elapsedMs": task.elapsed_ms(),
            "startedAt": task.started_at_ms,
        }

    # ── 生命周期 ─────────────────────────────────────────────────────────

    async def start(
        self, *, client_id: str, project: str, on_event: EventCallback
    ) -> dict[str, Any]:
        """启动一次核查;立即返回 {taskId, project},完成经 on_event 上报。

        Raises:
            SureBusyError: 该客户端已有核查在运行。
            SureUnavailableError: 探测不到或无法启动 SURE 二进制。
        """
        if self.active_for(client_id) is not None:
            raise SureBusyError(SureBusyError.USER_MESSAGE)
        binary = self._bin_provider()
        if not binary:
            raise SureUnavailableError(SureUnavailableError.USER_MESSAGE)
        task = SureTask(
            task_id=f"sure-{uuid.uuid4().hex[:12]}",
            client_id=client_id,
            project=project,
            started_monotonic=time.monotonic(),
            started_at_ms=int(time.time() * 1000),
        )
        self._tasks[task.task_id] = task
        spawn_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            spawn_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            spawn_kwargs["start_new_session"] = True  # 进程组 → killpg 可及全树
        try:
            task.proc = await asyncio.create_subprocess_exec(
                binary, "check", project, "--format", "json",
                cwd=project,
                env=self._env_builder(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **spawn_kwargs,
            )
        except OSError as exc:
            self._tasks.pop(task.task_id, None)
            raise SureUnavailableError(f"无法启动 SURE:{exc}") from exc
        task.runner = asyncio.create_task(
            self._run(task, on_event), name=f"sure-run:{task.task_id}"
        )
        logger.info(
            "SURE 核查启动: {} project={} pid={}", task.task_id, project, task.proc.pid
        )
        return {"taskId": task.task_id, "project": project}

    async def cancel(self, *, client_id: str) -> bool:
        """终止该客户端当前核查的进程树;取消不产出报告。"""
        task = self.active_for(client_id)
        if task is None or task.proc is None:
            return False
        task.cancelled = True
        logger.info("SURE 核查取消: {}", task.task_id)
        await _kill_process_tree(task.proc)
        return True

    async def kill_client(self, client_id: str) -> None:
        """客户端断连清理钩子(与 workbench 进程族同语义)。"""
        if self.active_for(client_id) is not None:
            await self.cancel(client_id=client_id)

    async def stop_all(self) -> None:
        """应用退出:终止全部任务并等 runner 收尾(带界)。"""
        tasks = list(self._tasks.values())
        for task in tasks:
            if task.proc is not None:
                task.cancelled = True
                await _kill_process_tree(task.proc)
        runners = [t.runner for t in tasks if t.runner is not None]
        if runners:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*runners, return_exceptions=True), timeout=15.0
                )
            except asyncio.TimeoutError:
                logger.warning("SURE 任务收尾超时,强制清理")
        self._tasks.clear()

    # ── 执行 ─────────────────────────────────────────────────────────────

    async def _run(self, task: SureTask, on_event: EventCallback) -> None:
        proc = task.proc
        assert proc is not None

        async def _emit(kind: str, data: dict[str, Any]) -> None:
            payload = {"taskId": task.task_id, "project": task.project, **data}
            try:
                await on_event(kind, payload)
            except Exception:
                logger.exception("SURE 事件回调异常(不影响任务本身)")
            else:
                # 事件即活动信号:任务在跑也说明客户端活着(bridge 侧另有 drain 保护)
                pass

        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(self._progress_interval)
                await _emit(
                    "sure_check_progress",
                    {"elapsedMs": task.elapsed_ms(), "state": "running"},
                )

        # 启动即一条进度(0ms):客户端据此立刻进入"运行中"
        await _emit("sure_check_progress", {"elapsedMs": 0, "state": "running"})
        heartbeat = asyncio.create_task(_heartbeat(), name=f"sure-hb:{task.task_id}")
        try:
            stdout_b, stderr_b = await proc.communicate()
        except asyncio.CancelledError:
            # 运行时整体收停:先确保进程树终止,再向外抛
            await _kill_process_tree(proc)
            self._tasks.pop(task.task_id, None)
            raise
        finally:
            heartbeat.cancel()

        self._tasks.pop(task.task_id, None)
        if task.cancelled:
            await _emit("sure_check_cancelled", {"elapsedMs": task.elapsed_ms()})
            return

        stdout_b = stdout_b or b""
        stderr_b = stderr_b or b""
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        if len(stdout_b) > MAX_REPORT_BYTES:
            await _emit(
                "sure_check_failed",
                {"message": "SURE 输出超过上限,已放弃解析", "code": "SURE_OUTPUT_TOO_LARGE"},
            )
            return
        try:
            envelope = parse_check_output(stdout)
        except SureReportError as exc:
            tail = (stderr.strip() or stdout.strip())[-400:]
            message = str(exc)
            if proc.returncode == 2:
                message = "SURE 命令行调用失败(请确认已安装 SURE 且版本不低于 0.1.2)"
            await _emit(
                "sure_check_failed",
                {"message": message, "code": "SURE_REPORT_INVALID", "stderrTail": tail},
            )
            return
        await _emit(
            "sure_check_report",
            {"envelope": envelope.model_dump(mode="json"), "elapsedMs": task.elapsed_ms()},
        )
        logger.info(
            "SURE 核查完成: {} outcome={} exit={}",
            task.task_id,
            envelope.outcome,
            envelope.exit_code,
        )
