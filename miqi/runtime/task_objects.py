"""Agent task models for #646.

PlanSnapshot is the immutable record of what the user approved.
TodoState is mutable execution progress projected to the UI.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone
from typing import Literal

TodoStatus = Literal["queued", "in_progress", "blocked", "completed", "cancelled"]

_ALLOWED_TRANSITIONS: dict[TodoStatus, set[TodoStatus]] = {
    "queued": {"in_progress", "cancelled"},
    "in_progress": {"completed", "blocked", "cancelled"},
    "blocked": {"in_progress", "cancelled"},
    "completed": {"cancelled"},
    "cancelled": set(),
}


def validate_transition(old: TodoStatus, new: TodoStatus) -> bool:
    if old == new:
        return True
    return new in _ALLOWED_TRANSITIONS.get(old, set())


@dataclasses.dataclass
class TodoItem:
    id: str
    content: str
    status: TodoStatus = "queued"
    kind: Literal["plan", "auxiliary", "observed"] = "plan"
    source: Literal["model", "harness"] = "model"
    blocked_reason: str | None = None


class TodoMutationError(Exception):
    """Reserved for future explicit todo mutation errors."""


@dataclasses.dataclass
class TodoState:
    run_id: str
    revision: int = 0
    items: list[TodoItem] = dataclasses.field(default_factory=list)

    def item(self, item_id: str) -> TodoItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def initialize_from_plan(self, steps: list[tuple[str, str]]) -> None:
        self.items = [
            TodoItem(id=step_id, content=content, status="queued", kind="plan", source="model")
            for step_id, content in steps
        ]
        self.revision += 1

    def merge(self, patches: list[dict]) -> list[dict]:
        rejected: list[dict] = []
        valid_statuses = set(_ALLOWED_TRANSITIONS)
        for p in patches:
            item_id = str(p.get("id") or "")
            status = p.get("status")
            content = p.get("content")
            kind = p.get("kind")

            existing = self.item(item_id)
            if existing is None:
                if kind in ("auxiliary", "observed") and content:
                    new_status = str(status or "queued")
                    if new_status not in valid_statuses:
                        rejected.append({
                            "status": "rejected",
                            "reason": f"INVALID_STATUS: {new_status}",
                            "id": item_id,
                        })
                        continue
                    self.items.append(TodoItem(
                        id=item_id,
                        content=str(content),
                        status=new_status,  # type: ignore[arg-type]
                        kind=kind,  # type: ignore[arg-type]
                        source="model" if kind == "auxiliary" else "harness",
                    ))
                    self.revision += 1
                    continue
                rejected.append({
                    "status": "rejected",
                    "reason": "PLAN_MUTATION_REQUIRES_CONFIRMATION",
                    "suggestion": "ask_user_plan_confirm",
                    "id": item_id,
                })
                continue

            if content and existing.kind == "plan":
                rejected.append({
                    "status": "rejected",
                    "reason": "PLAN_MUTATION_REQUIRES_CONFIRMATION",
                    "suggestion": "ask_user_plan_confirm",
                    "id": item_id,
                })
                continue
            if content and existing.kind == "auxiliary":
                existing.content = str(content)
                if not status:
                    self.revision += 1
                    continue
            if status:
                new_status = str(status)
                if not validate_transition(existing.status, new_status):  # type: ignore[arg-type]
                    rejected.append({
                        "status": "rejected",
                        "reason": f"INVALID_TRANSITION: {existing.status} -> {status}",
                        "id": item_id,
                    })
                    continue
                existing.status = new_status  # type: ignore[assignment]
                if new_status == "blocked" and p.get("blocked_reason"):
                    existing.blocked_reason = str(p["blocked_reason"])
                elif new_status != "blocked":
                    existing.blocked_reason = None
                self.revision += 1
        return rejected

    def summary(self) -> dict:
        counts = {"queued": 0, "in_progress": 0, "blocked": 0, "completed": 0, "cancelled": 0}
        in_progress: list[str] = []
        for it in self.items:
            counts[it.status] = counts.get(it.status, 0) + 1
            if it.status == "in_progress":
                in_progress.append(it.content)
        return {
            "total": len(self.items),
            "completed": counts["completed"],
            "in_progress": in_progress,
            "pending": counts["queued"] + counts["in_progress"],
            "blocked": counts["blocked"],
        }


@dataclasses.dataclass(frozen=True)
class ArtifactRef:
    type: str
    name: str


@dataclasses.dataclass(frozen=True)
class ExternalAction:
    provider: str
    operation: str


@dataclasses.dataclass(frozen=True)
class ApprovedScope:
    sources: tuple[str, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    external_actions: tuple[ExternalAction, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "external_actions", tuple(self.external_actions))


@dataclasses.dataclass(frozen=True)
class PlanSnapshot:
    """Immutable record of the exact plan fact approved by the user."""

    plan_id: str
    goal: str
    steps: tuple[tuple[str, str], ...]
    approved_scope: ApprovedScope = dataclasses.field(default_factory=ApprovedScope)
    plan_version: int = 1
    approved_at: str = dataclasses.field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    approved_by: str = "user"

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(tuple(pair) for pair in self.steps))
        scope = self.approved_scope
        if not isinstance(scope, ApprovedScope):
            scope = ApprovedScope(**scope)
        object.__setattr__(self, "approved_scope", scope)


@dataclasses.dataclass
class AgentRunContext:
    run_id: str = dataclasses.field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_key: str = ""
    plan_snapshot: PlanSnapshot | None = None
    todo_state: TodoState = dataclasses.field(default_factory=lambda: TodoState(run_id=""))
    action_history: list[dict] = dataclasses.field(default_factory=list)

    def __post_init__(self):
        if not self.todo_state.run_id:
            self.todo_state.run_id = self.run_id
