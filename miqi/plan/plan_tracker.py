"""Plan creation, step tracking, and status management."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanStep:
    id: str
    description: str
    status: str = "pending"  # pending | in_progress | completed | skipped
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Plan:
    plan_id: str
    title: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class PlanTracker:
    """Tracks plans for a session."""

    def __init__(self):
        self._plans: dict[str, Plan] = {}

    def create(self, title: str, steps: list[dict[str, Any]]) -> Plan:
        plan_id = str(uuid.uuid4())[:8]
        plan = Plan(
            plan_id=plan_id,
            title=title,
            steps=[PlanStep(**s) for s in steps],
        )
        self._plans[plan_id] = plan
        return plan

    def update_step(self, plan_id: str, step_id: str, status: str) -> PlanStep | None:
        plan = self._plans.get(plan_id)
        if not plan:
            return None
        for step in plan.steps:
            if step.id == step_id:
                step.status = status
                plan.updated_at = time.time()
                return step
        return None

    def get(self, plan_id: str) -> Plan | None:
        return self._plans.get(plan_id)

    def to_dict(self, plan: Plan) -> dict[str, Any]:
        return {
            "plan_id": plan.plan_id,
            "title": plan.title,
            "steps": [
                {
                    "id": s.id,
                    "description": s.description,
                    "status": s.status,
                    "depends_on": s.depends_on,
                }
                for s in plan.steps
            ],
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        }
