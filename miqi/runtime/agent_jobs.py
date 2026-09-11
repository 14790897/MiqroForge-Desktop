"""Agent job runtime — manages sub-agent execution as runtime-owned jobs.

Replaces the legacy SubagentManager pattern. Each sub-agent spawn
becomes an AgentJob tracked by the runtime, with lifecycle management
(start, list, kill) and status persistence.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from miqi.runtime.agent_graph_store import AgentGraphStore


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

    def __init__(self, *, services: Any, store: AgentGraphStore | None = None):
        self.services = services
        self._store = store
        self._jobs: dict[str, AgentJob] = {}
        self._tasks: dict[str, asyncio.Task | Any] = {}
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
        try:
            result = await self.services.turn_runner.run_agent_job(job)
            job.result = result.final_content
            job.status = "completed"
        except asyncio.CancelledError:
            job.error = "Cancelled"
            job.status = "aborted"
        except Exception as exc:
            job.error = str(exc)
            job.status = "error"
            logger.error("Agent job {} failed: {}", job.job_id, exc)
        finally:
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
