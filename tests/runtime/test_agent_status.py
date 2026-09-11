"""Tests for miqi.runtime.agent_status."""

import pytest

from miqi.protocol.events import AgentStatus
from miqi.runtime.agent_status import VALID_TRANSITIONS, AgentStateMachine


def test_initial_state():
    sm = AgentStateMachine()
    assert sm.current == AgentStatus.IDLE


def test_valid_transition_idle_to_thinking():
    sm = AgentStateMachine()
    sm.transition(AgentStatus.THINKING)
    assert sm.current == AgentStatus.THINKING


def test_valid_transition_thinking_to_executing():
    sm = AgentStateMachine()
    sm.transition(AgentStatus.THINKING)
    sm.transition(AgentStatus.EXECUTING)
    assert sm.current == AgentStatus.EXECUTING


def test_valid_transition_executing_to_thinking():
    sm = AgentStateMachine()
    sm.transition(AgentStatus.THINKING)
    sm.transition(AgentStatus.EXECUTING)
    sm.transition(AgentStatus.THINKING)
    assert sm.current == AgentStatus.THINKING


def test_valid_transition_thinking_to_waiting_approval():
    sm = AgentStateMachine()
    sm.transition(AgentStatus.THINKING)
    sm.transition(AgentStatus.WAITING_APPROVAL)
    assert sm.current == AgentStatus.WAITING_APPROVAL


def test_valid_transition_to_completed():
    sm = AgentStateMachine()
    sm.transition(AgentStatus.THINKING)
    sm.transition(AgentStatus.COMPLETED)
    assert sm.current == AgentStatus.COMPLETED


def test_invalid_transition_idle_to_executing():
    sm = AgentStateMachine()
    with pytest.raises(ValueError, match="Invalid agent state transition"):
        sm.transition(AgentStatus.EXECUTING)


def test_invalid_transition_completed_to_thinking():
    sm = AgentStateMachine()
    sm.transition(AgentStatus.THINKING)
    sm.transition(AgentStatus.COMPLETED)
    with pytest.raises(ValueError, match="Invalid agent state transition"):
        sm.transition(AgentStatus.THINKING)


def test_terminal_states_have_no_valid_transitions():
    for state in [AgentStatus.COMPLETED, AgentStatus.ERROR, AgentStatus.ABORTED]:
        assert VALID_TRANSITIONS[state] == set()


def test_abort_from_thinking():
    sm = AgentStateMachine()
    sm.transition(AgentStatus.THINKING)
    sm.transition(AgentStatus.ABORTED)
    assert sm.current == AgentStatus.ABORTED


def test_abort_from_waiting_approval():
    sm = AgentStateMachine()
    sm.transition(AgentStatus.THINKING)
    sm.transition(AgentStatus.WAITING_APPROVAL)
    sm.transition(AgentStatus.ABORTED)
    assert sm.current == AgentStatus.ABORTED
