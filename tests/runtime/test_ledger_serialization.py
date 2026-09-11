"""Tests for ledger serialization hardening (Phase 25.2).

Ensures that all ledger item payloads are JSON-serializable,
that protocol events serialize correctly via dataclasses.asdict(),
and that non-JSON-safe values (enums, etc.) are properly converted.
"""

import json

import pytest

# ── Event to_dict / serialization ────────────────────────────────────────


def test_event_to_dict_is_json_serializable():
    """All protocol events must produce JSON-serializable dicts via dataclasses.asdict()."""
    from dataclasses import asdict

    from miqi.protocol.events import (
        AgentMessageDeltaEvent,
        AgentMessageEvent,
        AgentReasoningEvent,
        ApprovalRequestedEvent,
        ApprovalResolvedEvent,
        CommandRejectedEvent,
        ConfigUpdatedEvent,
        ContextCompactedEvent,
        ErrorEvent,
        EventSeverity,
        ExecCommandBeginEvent,
        ExecCommandEndEvent,
        ExecCommandOutputDeltaEvent,
        PlanUpdateEvent,
        SessionConfiguredEvent,
        SubAgentCompletedEvent,
        SubAgentSpawnedEvent,
        ThreadCreatedEvent,
        ThreadDeletedEvent,
        ThreadUpdatedEvent,
        ToolCallBeginEvent,
        ToolCallEndEvent,
        ToolCallOutputDeltaEvent,
        TurnAbortedEvent,
        TurnCompleteEvent,
        TurnStartedEvent,
        WarningEvent,
    )

    events = [
        TurnStartedEvent(turn_id="t1", agent_name="main", thread_id="th-1"),
        TurnCompleteEvent(
            turn_id="t1", thread_id="th-1", outcome="success",
            tools_used=["read"], token_usage={"in": 10, "out": 5},
        ),
        TurnAbortedEvent(turn_id="t1", thread_id="th-1", reason="user"),
        AgentMessageDeltaEvent(turn_id="t1", delta="hel", index=0),
        AgentMessageEvent(turn_id="t1", content="hello", finish_reason="stop"),
        AgentReasoningEvent(turn_id="t1", content="thinking..."),
        ToolCallBeginEvent(
            turn_id="t1", tool_call_id="tc1", tool_name="read",
            tool_display="Read file", arguments={"path": "/x"},
        ),
        ToolCallEndEvent(
            turn_id="t1", tool_call_id="tc1", tool_name="read",
            success=True, output_preview="content", output_size=100, duration_ms=42,
        ),
        ToolCallOutputDeltaEvent(turn_id="t1", tool_call_id="tc1", delta="out"),
        ExecCommandBeginEvent(
            turn_id="t1", tool_call_id="tc1", command="ls",
            cwd="/tmp", sandbox_type="none",
        ),
        ExecCommandOutputDeltaEvent(
            turn_id="t1", tool_call_id="tc1", stream="stdout", delta="line",
        ),
        ExecCommandEndEvent(
            turn_id="t1", tool_call_id="tc1", exit_code=0,
            duration_ms=10, output_size=5,
        ),
        ApprovalRequestedEvent(
            approval_id="a1", turn_id="t1", category="exec",
            description="Run cmd", details={},
        ),
        ApprovalResolvedEvent(approval_id="a1", decision="allow"),
        SubAgentSpawnedEvent(
            parent_turn_id="t1", sub_agent_id="sa1",
            sub_thread_id="st1", agent_type="code-agent", task_label="fix",
        ),
        SubAgentCompletedEvent(
            sub_agent_id="sa1", sub_thread_id="st1", outcome="success",
            summary="done",
        ),
        PlanUpdateEvent(turn_id="t1", plan={"steps": []}),
        ErrorEvent(
            turn_id="t1", severity=EventSeverity.ERROR,
            message="err", recoverable=False,
        ),
        WarningEvent(turn_id="t1", message="warn"),
        ContextCompactedEvent(
            turn_id="t1", thread_id="th-1",
            messages_before=10, messages_after=5, tokens_saved=200,
        ),
        SessionConfiguredEvent(
            thread_id="th-1", model="gpt-4",
            permission_profile="strict", sandbox_enforcement="on",
        ),
        ThreadCreatedEvent(thread_id="th-1", title="New"),
        ThreadUpdatedEvent(thread_id="th-1", title="Renamed"),
        ThreadDeletedEvent(thread_id="th-1"),
        ConfigUpdatedEvent(path="model", value="claude"),
        CommandRejectedEvent(
            command_type="UnknownCommand", reason="unsupported",
            recoverable=False,
        ),
    ]

    for event in events:
        d = asdict(event)
        d.pop("type", None)  # Mirror the ledger mirror behavior
        # Must not raise TypeError
        json_str = json.dumps(d)
        assert isinstance(json_str, str)
        # Round-trip: ensure values survive
        restored = json.loads(json_str)
        assert isinstance(restored, dict)


def test_enum_values_are_serialized_as_strings():
    """EventSeverity enum must serialize as its string value, not as Enum object."""
    from dataclasses import asdict

    from miqi.protocol.events import ErrorEvent, EventSeverity

    event = ErrorEvent(
        turn_id="t1", severity=EventSeverity.ERROR,
        message="bad", recoverable=False,
    )
    d = asdict(event)
    assert d["severity"] == "error"
    assert isinstance(d["severity"], str)

    json.dumps(d)  # must not raise


# ── Mirror layer uses asdict ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mirror_sanitizes_enum_values(fake_config, fake_provider, tmp_path):
    """When an ErrorEvent (with EventSeverity enum) is mirrored to ledger,
    the payload must be JSON-safe — severity should be a string."""
    from miqi.protocol.events import ErrorEvent, EventSeverity
    from miqi.runtime.session import RuntimeSession

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=fake_provider,
        session_id="sess-serialize",
        workspace=tmp_path,
    )
    await runtime.start()
    try:
        # Mirror an error event which has EventSeverity (an Enum)
        await runtime._mirror_event_to_ledger(ErrorEvent(
            turn_id="turn-1",
            severity=EventSeverity.WARNING,
            message="test warning",
            recoverable=True,
        ))

        # ErrorEvent has turn_id="turn-1" but no thread_id, so the mirror
        # falls back to using turn_id as the thread_id key.
        items = await runtime.services.ledger_runtime.load_items("turn-1")
        error_items = [i for i in items if i.item_type == "error"]
        assert len(error_items) == 1, f"Expected 1 error item, got {error_items}"
        # Payload severity must be a string, not an Enum
        assert isinstance(error_items[0].payload.get("severity"), str)
        assert error_items[0].payload.get("severity") == "warning"
    finally:
        await runtime.stop()


# ── Non-JSON-safe types are rejected ─────────────────────────────────────


@pytest.mark.asyncio
async def test_ledger_payload_rejects_non_json_types(tmp_path):
    """LedgerRuntime.append_item() must safely handle payloads
    with non-JSON-serializable values instead of crashing."""
    from miqi.runtime.ledger_runtime import LedgerRuntime

    runtime = LedgerRuntime(tmp_path / "runtime.db", session_id="sess-1")
    await runtime.initialize()
    try:
        # payload with a bytes value — not JSON-serializable
        item = await runtime.append_item(
            thread_id="thread-1",
            turn_id="turn-1",
            item_type="test_item",
            payload={"data": b"not json", "nested": {"inner": set([1, 2])}},
        )
        # Must succeed — LedgerRuntime should convert non-JSON types
        assert item is not None
        # Verify the payload was stored safely
        items = await runtime.load_items("thread-1")
        assert len(items) == 1
        # After loading back, all values should be JSON-safe types
        loaded_payload = items[0].payload
        json.dumps(loaded_payload)  # must not raise
    finally:
        await runtime.close()
