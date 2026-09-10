"""Task-level lifecycle state for agent collaboration and execution."""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskPhase(str, Enum):
    PLANNING = "planning"
    WAIT_USER_PLAN_CONFIRM = "wait_user_plan_confirm"
    RUNNING = "running"
    WAIT_ACTION_CONFIRM = "wait_action_confirm"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS: dict[TaskPhase, frozenset[TaskPhase]] = {
    TaskPhase.PLANNING: frozenset({TaskPhase.WAIT_USER_PLAN_CONFIRM, TaskPhase.CANCELLED}),
    TaskPhase.WAIT_USER_PLAN_CONFIRM: frozenset({TaskPhase.RUNNING, TaskPhase.PLANNING, TaskPhase.CANCELLED}),
    TaskPhase.RUNNING: frozenset({TaskPhase.WAIT_ACTION_CONFIRM, TaskPhase.COMPLETED, TaskPhase.CANCELLED}),
    TaskPhase.WAIT_ACTION_CONFIRM: frozenset({TaskPhase.RUNNING, TaskPhase.COMPLETED, TaskPhase.CANCELLED}),
    TaskPhase.COMPLETED: frozenset(),
    TaskPhase.CANCELLED: frozenset(),
}

_VALID_STEP_STATUS = frozenset({"pending", "running", "done", "failed"})


@dataclass
class TaskState:
    task_id: str
    session_key: str
    phase: TaskPhase = TaskPhase.PLANNING
    title: str = ""
    goal: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    step_status: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        session_key: str,
        *,
        title: str,
        goal: str,
        steps: list[dict[str, Any]],
        permissions: list[str],
    ) -> "TaskState":
        normalized_steps = copy.deepcopy(steps)
        step_status: dict[str, str] = {}
        for index, step in enumerate(normalized_steps):
            if not isinstance(step, dict):
                continue
            name = str(step.get("name") or step.get("title") or f"step_{index}").strip()
            step_status[name] = "pending"
        return cls(
            task_id=f"task_{uuid.uuid4().hex[:12]}",
            session_key=session_key,
            phase=TaskPhase.WAIT_USER_PLAN_CONFIRM,
            title=title,
            goal=goal,
            steps=normalized_steps,
            permissions=copy.deepcopy(permissions),
            step_status=step_status,
        )

    def _transition(self, target: TaskPhase) -> None:
        if target not in _ALLOWED_TRANSITIONS[self.phase]:
            raise ValueError(f"Invalid task transition: {self.phase.value} -> {target.value}")
        self.phase = target
        self._touch()

    def confirm(self) -> None:
        self._transition(TaskPhase.RUNNING)

    def cancel(self) -> None:
        if self.phase in (TaskPhase.COMPLETED, TaskPhase.CANCELLED):
            return
        self._transition(TaskPhase.CANCELLED)

    def mark_dangerous(self) -> None:
        self._transition(TaskPhase.WAIT_ACTION_CONFIRM)

    def complete(self) -> None:
        if self.phase == TaskPhase.WAIT_ACTION_CONFIRM:
            self._transition(TaskPhase.COMPLETED)
            return
        self._transition(TaskPhase.COMPLETED)

    def set_step(self, name: str, status: str) -> None:
        if name not in self.step_status:
            raise KeyError(f"Unknown task step: {name}")
        if status not in _VALID_STEP_STATUS:
            raise ValueError(f"Invalid task step status: {status}")
        self.step_status[name] = status
        self._touch()

    def progress(self) -> tuple[int, int]:
        """Return (completed, total) using canonical step statuses."""
        total = len(self.step_status)
        completed = sum(1 for status in self.step_status.values() if status == "done")
        return completed, total

    def _touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        # Return detached collections so callers cannot mutate task state without
        # going through the lifecycle/step validation methods.
        return {
            "task_id": self.task_id,
            "session_key": self.session_key,
            "phase": self.phase.value,
            "title": self.title,
            "goal": self.goal,
            "steps": copy.deepcopy(self.steps),
            "permissions": copy.deepcopy(self.permissions),
            "step_status": dict(self.step_status),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
