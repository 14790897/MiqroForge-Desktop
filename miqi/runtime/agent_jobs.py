"""Agent job runtime — manages sub-agent execution as runtime-owned jobs.

Replaces the legacy SubagentManager pattern. Each sub-agent spawn
becomes an AgentJob tracked by the runtime, with lifecycle management
(start, list, kill) and status persistence.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from miqi.runtime.agent_graph_store import AgentGraphStore

# 失速看门狗的默认阈值(判定逻辑见 AgentJobRuntime._watch_stall)。
#
# **阈值必须长于该路径最坏的合法单次等待,否则看门狗杀掉的是在工作、只是慢
# 的作业**——采样的是"挂起点连续不动",而这些等待里挂起点本来就不动,合法
# 与卡死对它没有区别。实测最坏:一次网关非流式回退可达 600s(2026-10-13,
# e2e 注释同款记录);2026-10-14 首战:修复子代理在网关等待里被旧的
# 5×60s=300s 阈值误杀(本机 12:06 的 stall dump,作业按 stalled 收尾)。
# 取 15×60s=15 分钟:超过实测最坏 + 余量,且仍短于完整修复闭环 E2E 的
# 30 分钟等待窗口——真卡死依然有界、依然自诊、依然如实收尾。
# tests/runtime/test_agent_jobs.py::test_the_stall_threshold_outlasts_the_measured_worst_case_wait
# 把这条策略钉在两个实测边界之间。
STALL_INTERVAL_SECONDS: float = 60.0
STALL_SAMPLES: int = 15


@dataclass
class AgentJob:
    """A tracked sub-agent execution job."""

    job_id: str
    agent_type: str
    task: str
    thread_id: str
    parent_thread_id: str
    status: str = "queued"  # queued | running | completed | error | aborted
    result: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    # #984: authorized output roots inherited from the parent turn, so the
    # sub-agent's exec/file tools can write where the user asked.  In-memory
    # only — AgentGraphStore.save_job takes explicit keyword args and the
    # SQLite schema is fixed, so a restart loses them (documented in the PR).
    user_roots: list[str] = field(default_factory=list)


class AgentJobRuntime:
    """Manages the lifecycle of sub-agent execution jobs.

    Owns the job registry and background task tracking.  Jobs are
    started via start() and can be listed, queried, or killed.
    """

    def __init__(
        self,
        *,
        services: Any,
        store: AgentGraphStore | None = None,
        stall_interval: float = STALL_INTERVAL_SECONDS,
        stall_samples: int = STALL_SAMPLES,
    ):
        self.services = services
        self._store = store
        self._jobs: dict[str, AgentJob] = {}
        self._tasks: dict[str, asyncio.Task | Any] = {}
        # 失速看门狗(2026-10-14):见 _watch_stall。间隔与采样次数可在测试
        # 里调小;默认必须长于实测最坏的合法单次等待——见模块常量处的记录。
        self._stall_interval = stall_interval
        self._stall_samples = stall_samples
        if store is not None:
            self._load_jobs()

    def _load_jobs(self) -> None:
        """Load persisted jobs into memory, marking stale 'running' jobs as interrupted."""
        for row in self._store.load_jobs():
            status = row.status
            if status == "running":
                status = "interrupted"
            job = AgentJob(
                job_id=row.job_id,
                agent_type=row.agent_type or "",
                task=row.task or "",
                thread_id=row.thread_id or "",
                parent_thread_id=row.parent_thread_id or "",
                status=status,
                result=row.result,
                error=row.error,
                created_at=row.created_at or time.time(),
                completed_at=row.completed_at,
            )
            self._jobs[job.job_id] = job

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(
        self,
        *,
        agent_type: str,
        task: str,
        parent_thread_id: str,
        user_roots: list[str] | None = None,
    ) -> AgentJob:
        """Start a new agent job and return its handle.

        ``user_roots`` (#984) are the parent turn's authorized output dirs;
        they are inherited by the sub-agent turn.  They are intentionally
        NOT persisted — see :class:`AgentJob`.
        """
        job_id = str(uuid.uuid4())[:12]
        thread_id = f"{self.services.session_id}:{job_id}"

        job = AgentJob(
            job_id=job_id,
            agent_type=agent_type,
            task=task,
            thread_id=thread_id,
            parent_thread_id=parent_thread_id,
            user_roots=list(user_roots or []),
        )
        self._jobs[job_id] = job

        if self._store is not None:
            self._store.save_job(
                job_id=job.job_id,
                agent_type=job.agent_type,
                task=job.task,
                thread_id=job.thread_id,
                parent_thread_id=job.parent_thread_id,
                status=job.status,
                result=job.result,
                error=job.error,
                created_at=job.created_at,
                completed_at=job.completed_at,
            )

        task_ref = asyncio.create_task(self._run(job))
        self._tasks[job_id] = task_ref
        task_ref.add_done_callback(lambda _t: self._tasks.pop(job_id, None))

        return job

    def list(self) -> list[dict[str, Any]]:
        """List all jobs with summary fields."""
        return [
            {
                "job_id": job.job_id,
                "agent_type": job.agent_type,
                "thread_id": job.thread_id,
                "parent_thread_id": job.parent_thread_id,
                "status": job.status,
                "result_preview": (job.result or "")[:200],
                "error": job.error,
                "created_at": job.created_at,
                "completed_at": job.completed_at,
            }
            for job in self._jobs.values()
        ]

    def get(self, job_id: str) -> AgentJob:
        """Get a job by ID. Raises KeyError if not found."""
        return self._jobs[job_id]

    async def kill(self, job_id: str) -> None:
        """Kill a running job and mark it as aborted."""
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()

        job = self._jobs.get(job_id)
        if job is not None:
            job.status = "aborted"
            job.completed_at = time.time()
            if self._store is not None:
                self._store.update_job_status(
                    job_id=job.job_id,
                    status=job.status,
                    result=job.result,
                    error=job.error,
                    completed_at=job.completed_at,
                )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run(self, job: AgentJob) -> None:
        """Execute the job's task through TurnRunner.run()."""
        job.status = "running"
        if self._store is not None:
            self._store.update_job_status(
                job_id=job.job_id,
                status=job.status,
                result=None,
                error=None,
                completed_at=None,
            )
        job_task = asyncio.current_task()
        watchdog = (
            asyncio.create_task(self._watch_stall(job, job_task))
            if job_task is not None
            else None
        )
        try:
            # 依赖就绪(2026-10-14 CI 实测):作业回合经 HistoryRuntime 落 turn,
            # 而它只在 RuntimeSession.start() 里 initialize 一次——作业落在
            # 「建了没 start / start 未完成」的会话上时,首个落库即
            # `HistoryRuntime.initialize() must be called before use`,派发 4 秒
            # 即 failed(修复子代理 attempt 1 因此必红,错误只有一行、无堆栈)。
            # 这里保证依赖已就绪,不赌会话启动的时序;守卫幂等,见其 docstring。
            history = getattr(self.services, "history_runtime", None)
            if history is not None:
                await history.ensure_initialized()
            # 会话身份随任务下传(2026-10-15):作业 turn 的 workspace 会被工具
            # ctx 继承、再被 SURE 事件桥接当 project_root 发出——不传时作业
            # 路径落到默认 "."，SURE 按设计拒收非绝对路径，子代理的工具事件
            # 会整段静默丢失(本地与 CI 实测)。
            result = await self.services.turn_runner.run_agent_job(
                job,
                workspace=self.services.workspace,
                session_id=self.services.session_id,
            )
            job.result = result.final_content
            job.status = "completed"
        except asyncio.CancelledError:
            # 看门狗判失速时会先写好 job.error/status 再取消本任务:如实保留
            # 失速结论;其余来源的取消仍按 aborted 记录。
            if not job.error:
                job.error = "Cancelled"
                job.status = "aborted"
        except Exception as exc:
            job.error = str(exc)
            job.status = "error"
            logger.error("Agent job {} failed: {}", job.job_id, exc)
        finally:
            if watchdog is not None:
                watchdog.cancel()
            job.completed_at = time.time()
            if self._store is not None:
                self._store.update_job_status(
                    job_id=job.job_id,
                    status=job.status,
                    result=job.result,
                    error=job.error,
                    completed_at=job.completed_at,
                )
            # Notify the frontend (AgentControl.completion_callback → Desktop
            # `chat:subagent_result`).  The job path bypasses
            # AgentControl._run_agent, so its completion hook never fires —
            # this is the only notification point for AgentJobRuntime agents.
            ac = self.services.agent_control
            if ac is not None:
                try:
                    await ac.notify_completed(job.job_id, job.status)
                except Exception:
                    logger.warning(
                        "notify_completed failed for job {}", job.job_id,
                    )

    async def _watch_stall(self, job: AgentJob, job_task: asyncio.Task) -> None:
        """失速看门狗(2026-10-14:修复子代理无声卡死事故)。

        现场:作业可能停在一个永不触发的内部等待上——事件循环空闲、无
        socket、无数据库活动、所有已知超时层都不触发,作业则永远停在
        running(面板长期"运行中",重启后还留僵尸行)。

        判定:每 `stall_interval` 秒采样作业任务的最内层挂起帧(文件:行号),
        连续 `stall_samples` 次相同即判失速。默认见模块常量
        `STALL_INTERVAL_SECONDS`/`STALL_SAMPLES`:阈值必须长于实测最坏的合法
        单次等待——旧的 5×60s=300s 假设"最坏重试爆发 <4 分钟",被同一 PR
        实测的网关非流式回退 600s 证伪,首战即在完整修复闭环里误杀了还在
        网关等待中的子代理。判失速时先把全部 asyncio 任务的挂起栈 dump 进
        日志(下一次现场自诊,不必再靠 py-spy),再以 "stalled" 如实收尾——
        绝不无声挂起。
        """
        last: tuple[str, int] | None = None
        same = 0
        while not job_task.done():
            await asyncio.sleep(self._stall_interval)
            if job_task.done():
                return
            frames = job_task.get_stack()
            point = (
                (frames[-1].f_code.co_filename, frames[-1].f_lineno)
                if frames
                else None
            )
            if point is not None and point == last:
                same += 1
            else:
                same = 0
                last = point
            if same >= self._stall_samples - 1:
                where = f"{point[0]}:{point[1]}" if point else "<no stack>"
                seconds = self._stall_interval * (same + 1)
                logger.error(
                    "Agent job {} stalled at {} (no progress for {:.0f}s); "
                    "dumping tasks and failing the job",
                    job.job_id,
                    where,
                    seconds,
                )
                self._dump_tasks(job)
                job.error = (
                    f"stalled at {where} (no progress for {seconds:.0f}s)"
                )
                job.status = "error"
                job_task.cancel()
                return

    def _dump_tasks(self, job: AgentJob) -> None:
        """把当前全部 asyncio 任务的 await 链写进日志(失速现场自诊)。

        `Task.get_stack()` 对挂起的协程只给最外层一帧——真正的卡点在
        `cr_await` 链里(2026-10-14 看门狗首战实测:作业任务只打出一帧,
        断点不可见)。这里手动沿 `cr_await` 走到底,打印完整等待链。
        """
        try:
            tasks = list(asyncio.all_tasks())
        except RuntimeError:
            return
        logger.error(
            "stall dump for job {}: {} task(s)", job.job_id, len(tasks)
        )
        for task in tasks:
            chain = self._await_chain(task)
            logger.error(
                "  task {!r} done={} wait-chain: {}",
                task.get_name(),
                task.done(),
                " <- ".join(chain) if chain else "<none>",
            )

    @staticmethod
    def _await_chain(task: asyncio.Task) -> list[str]:
        """任务协程的完整 await 链(外层 → 最内层卡点),最多 16 层。"""
        chain: list[str] = []
        try:
            coro = task.get_coro()
        except Exception:  # noqa: BLE001 — 诊断尽力而为
            return chain
        seen = 0
        while coro is not None and seen < 16:
            frame = getattr(coro, "cr_frame", None) or getattr(
                coro, "gi_frame", None
            )
            if frame is not None:
                chain.append(
                    f"{os.path.basename(frame.f_code.co_filename)}:"
                    f"{frame.f_lineno}"
                )
            coro = getattr(coro, "cr_await", None) or getattr(
                coro, "gi_yieldfrom", None
            )
            seen += 1
        return chain
