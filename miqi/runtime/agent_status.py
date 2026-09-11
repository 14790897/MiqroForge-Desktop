"""Agent status state machine."""

from __future__ import annotations

from dataclasses import dataclass

from miqi.protocol.events import AgentStatus

VALID_TRANSITIONS: dict[AgentStatus, set[AgentStatus]] = {
    AgentStatus.IDLE: {AgentStatus.THINKING, AgentStatus.ABORTED},
    AgentStatus.THINKING: {
        AgentStatus.EXECUTING,
        AgentStatus.WAITING_APPROVAL,
        AgentStatus.COMPLETED,
        AgentStatus.ERROR,
        AgentStatus.ABORTED,
    },
    AgentStatus.EXECUTING: {
        AgentStatus.THINKING,
        AgentStatus.WAITING_APPROVAL,
        AgentStatus.COMPLETED,
        AgentStatus.ERROR,
        AgentStatus.ABORTED,
    },
    AgentStatus.WAITING_APPROVAL: {
        AgentStatus.THINKING,
        AgentStatus.EXECUTING,
        AgentStatus.ABORTED,
    },
    AgentStatus.COMPLETED: set(),   # Terminal
    AgentStatus.ERROR: set(),        # Terminal
    AgentStatus.ABORTED: set(),      # Terminal
}


@dataclass
class AgentStateMachine:
    """Tracks agent state and enforces valid transitions."""

    current: AgentStatus = AgentStatus.IDLE

    def transition(self, to: AgentStatus) -> None:
        valid = VALID_TRANSITIONS.get(self.current, set())
        if to not in valid:
            raise ValueError(
                f"Invalid agent state transition: "
                f"{self.current.value} → {to.value}"
            )
        self.current = to
