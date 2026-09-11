"""Tests for TaskRunner (Phase 11.3)."""

import asyncio
import warnings
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.protocol.commands import AbortTurn, UserMessage
from miqi.runtime.task_runner import TaskRunner


@pytest.mark.asyncio
async def test_task_runner_routes_user_message_to_turn_runner(fake_services):
    """UserMessage goes through TurnRunner.run."""
    events = asyncio.Queue()
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(UserMessage(content="hello", thread_id="cli:default"))

    # TurnRunner is the new path
    fake_services.turn_runner.run.assert_awaited_once()

    # Phase 17: TurnStartedEvent is emitted before AgentMessageEvent.
    # Drain past non-content events until we find the final message.
    event = None
    while True:
        ev = await asyncio.wait_for(events.get(), timeout=1)
        if hasattr(ev, "content"):
            event = ev
            break
    assert event.content == "hi there"


@pytest.mark.asyncio
async def test_task_runner_injects_local_skills_into_system_prompt(fake_services):
    """The system prompt must carry the Local Skills summary.

    agent_registry 规则 7 要求 agent 先查 "Local Skills" 列表；此前该列表
    从未注入（量化评估 14 条直接提示词 11 条零技能接触）。此测试锁定注入
    行为，防止回退。
    """
    events = asyncio.Queue()
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(UserMessage(content="hello", thread_id="cli:default"))

    kwargs = fake_services.turn_runner.run.call_args.kwargs
    system_prompt = kwargs["system_prompt"]
    assert "本地技能清单" in system_prompt
    assert "<skills>" in system_prompt
    assert "<name>pptx-generator</name>" in system_prompt
    assert "<location>cron/SKILL.md</location>" in system_prompt
    # 强制规则：处理请求第一步先 list，匹配后 view 加载全文；典型映射随清单注入
    assert "【强制规则】" in system_prompt
    assert "skill_manage(action='list')" in system_prompt
    assert "做PPT→pptx-generator" in system_prompt
    # 渐进披露第一层：技能正文不注入（只注入名称+描述+位置）。
    # LAYOUT_16x9 只出现在 pptx-generator 的 SKILL.md 正文里。
    assert "LAYOUT_16x9" not in system_prompt


@pytest.mark.asyncio
async def test_task_runner_forwards_tool_calls_on_final_agent_message(fake_services):
    """Realtime final events must carry assistant tool_calls for #106."""
    from miqi.protocol.events import AgentMessageEvent

    tool_calls = [{
        "id": "tc-search",
        "type": "function",
        "function": {"name": "web_search", "arguments": '{"query":"MiQi"}'},
    }]
    fake_services.turn_runner.run.return_value.final_content = "found it"
    fake_services.turn_runner.run.return_value.messages_delta = [
        {"role": "assistant", "content": None, "tool_calls": tool_calls},
        {"role": "tool", "tool_call_id": "tc-search", "content": "result"},
        {"role": "assistant", "content": "found it"},
    ]
    fake_services.turn_runner.run.return_value.tools_used = ["web_search"]

    events = asyncio.Queue()
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(UserMessage(content="search", thread_id="cli:default"))

    while True:
        event = await asyncio.wait_for(events.get(), timeout=1)
        if isinstance(event, AgentMessageEvent):
            assert event.content == "found it"
            assert event.tool_calls == tool_calls
            break


@pytest.mark.asyncio
async def test_task_runner_handles_abort_turn(fake_services):
    """AbortTurn signals cancellation event and emits ErrorEvent (Phase 14 follow-up).

    Sets the per-thread cancel event and emits an ErrorEvent warning.
    """
    events = asyncio.Queue()
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(AbortTurn(thread_id="cli:default"))

    # Should emit an ErrorEvent (warning about abort)
    event = await asyncio.wait_for(events.get(), timeout=1)
    assert event.__class__.__name__ == "ErrorEvent"
    assert "aborted" in event.message.lower()


@pytest.mark.asyncio
async def test_task_runner_abort_turn_uses_non_deprecated_coroutine_check(fake_services):
    events = asyncio.Queue()
    runner = TaskRunner(services=fake_services, event_queue=events)
    called = []

    async def cancel_approvals_for_thread(thread_id: str, reason: str) -> None:
        called.append((thread_id, reason))

    fake_services.orchestrator.cancel_approvals_for_thread = cancel_approvals_for_thread

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        await runner.handle(AbortTurn(thread_id="cli:default"))

    assert called == [("cli:default", "Turn aborted by user.")]


@pytest.mark.asyncio
async def test_approval_response_resolves_orchestrator(fake_services):
    """ApprovalResponse resolves the orchestrator's pending approval (Phase 18)."""
    from miqi.execution.orchestrator import ApprovalResolveResult
    from miqi.protocol.commands import ApprovalResponse
    from miqi.protocol.events import ApprovalResolvedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()
    seen: dict[str, str] = {}

    def resolve_approval(approval_id: str, decision: str) -> ApprovalResolveResult:
        seen["approval_id"] = approval_id
        seen["decision"] = decision
        return ApprovalResolveResult(
            resolved=True,
            approval_id=approval_id,
            normalized_decision="once",  # "allow" maps to "once"
            turn_id="turn-test",
        )

    fake_services.orchestrator.resolve_approval = resolve_approval
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(ApprovalResponse(approval_id="ap-1", decision="allow"))

    # Verify ApprovalResolvedEvent emitted
    event: object | None = None
    while True:
        ev = await asyncio.wait_for(events.get(), timeout=1)
        if isinstance(ev, ApprovalResolvedEvent):
            event = ev
            break

    assert event is not None, "Expected ApprovalResolvedEvent"
    assert event.approval_id == "ap-1"  # type: ignore[union-attr]
    assert event.decision == "once"  # type: ignore[union-attr]  # normalized
    assert seen == {"approval_id": "ap-1", "decision": "allow"}


@pytest.mark.asyncio
async def test_approval_response_nonexistent_approval_rejected(fake_services):
    """Phase 31.4: ApprovalResponse for nonexistent approval emits
    CommandRejectedEvent, NOT ApprovalResolvedEvent."""
    from miqi.execution.orchestrator import ApprovalResolveResult
    from miqi.protocol.commands import ApprovalResponse
    from miqi.protocol.events import CommandRejectedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()

    def resolve_approval(approval_id: str, _decision: str) -> ApprovalResolveResult:
        return ApprovalResolveResult(
            resolved=False,
            approval_id=approval_id,
            normalized_decision="",
            turn_id="",
            reason="Approval not found or already resolved",
        )

    fake_services.orchestrator.resolve_approval = resolve_approval
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(ApprovalResponse(approval_id="nonexistent", decision="once"))

    # Should emit CommandRejectedEvent, NOT ApprovalResolvedEvent
    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, CommandRejectedEvent), (
        f"Expected CommandRejectedEvent for nonexistent approval, got {type(event).__name__}"
    )
    assert "not found" in event.reason.lower()

    # Verify no ApprovalResolvedEvent is in the queue
    assert events.empty(), "No other events should be emitted for failed resolve"


@pytest.mark.asyncio
async def test_approval_response_invalid_decision_rejected(fake_services):
    """Phase 31.4: ApprovalResponse with invalid decision emits
    CommandRejectedEvent, NOT ApprovalResolvedEvent."""
    from miqi.execution.orchestrator import ApprovalResolveResult
    from miqi.protocol.commands import ApprovalResponse
    from miqi.protocol.events import CommandRejectedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()

    def resolve_approval(approval_id: str, _decision: str) -> ApprovalResolveResult:
        return ApprovalResolveResult(
            resolved=False,
            approval_id=approval_id,
            normalized_decision="",
            turn_id="",
            reason="Invalid decision: 'bogus'",
        )

    fake_services.orchestrator.resolve_approval = resolve_approval
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(ApprovalResponse(approval_id="ap-1", decision="bogus"))

    # Should emit CommandRejectedEvent, NOT ApprovalResolvedEvent
    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, CommandRejectedEvent), (
        f"Expected CommandRejectedEvent for invalid decision, got {type(event).__name__}"
    )
    assert "invalid" in event.reason.lower()

    # Verify no ApprovalResolvedEvent is in the queue
    assert events.empty(), "No other events should be emitted for invalid decision"


@pytest.mark.asyncio
async def test_approval_response_resolved_event_has_correct_turn_id(fake_services):
    """Phase 31.4: successful TaskRunner ApprovalResponse emits
    ApprovalResolvedEvent with turn_id from the orchestrator result."""
    from miqi.execution.orchestrator import ApprovalResolveResult
    from miqi.protocol.commands import ApprovalResponse
    from miqi.protocol.events import ApprovalResolvedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()

    def resolve_approval(approval_id: str, decision: str) -> ApprovalResolveResult:
        # turn_id should come from metadata, not parsed from approval_id
        return ApprovalResolveResult(
            resolved=True,
            approval_id=approval_id,
            normalized_decision="once",
            turn_id="turn-from-metadata",
        )

    fake_services.orchestrator.resolve_approval = resolve_approval
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(ApprovalResponse(approval_id="some-turn:tool-1", decision="allow"))

    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, ApprovalResolvedEvent)
    assert event.turn_id == "turn-from-metadata", (
        "turn_id must come from orchestrator metadata, not parsed from approval_id"
    )


@pytest.mark.asyncio
async def test_task_runner_unknown_submission_command_rejected(fake_services):
    """Unknown submission types emit CommandRejectedEvent (Phase 18)."""
    from miqi.protocol.events import CommandRejectedEvent

    events = asyncio.Queue()
    runner = TaskRunner(services=fake_services, event_queue=events)

    class BogusCommand:
        pass

    await runner.handle(BogusCommand())

    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, CommandRejectedEvent)
    assert event.recoverable is False


# ---------------------------------------------------------------------------
# Phase 18: ThreadCommand wired to ThreadRuntime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_command_create_emits_thread_created(fake_services):
    """ThreadCommand(action='new') must call ThreadRuntime.create_thread
    and emit ThreadCreatedEvent."""

    from miqi.protocol.commands import ThreadCommand
    from miqi.protocol.events import ThreadCreatedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()
    thread = type("Thread", (), {
        "thread_id": "thread-new",
        "title": "New thread",
        "parent_thread_id": None,
    })()

    fake_services.thread_runtime = MagicMock()
    fake_services.thread_runtime.create_thread = AsyncMock(return_value=thread)
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(ThreadCommand(
        action="new",
        thread_id="ignored",
        params={"title": "New thread"},
    ))

    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, ThreadCreatedEvent)
    assert event.thread_id == "thread-new"
    assert event.title == "New thread"


@pytest.mark.asyncio
async def test_thread_command_unknown_action_rejected(fake_services):
    """ThreadCommand with unknown action emits CommandRejectedEvent."""
    from miqi.protocol.commands import ThreadCommand
    from miqi.protocol.events import CommandRejectedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()
    fake_services.thread_runtime = MagicMock()
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(ThreadCommand(action="explode", thread_id="t1"))

    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, CommandRejectedEvent)
    assert event.command_type == "ThreadCommand"


# ---------------------------------------------------------------------------
# Phase 18: ConfigUpdate wired to SessionState
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_update_mutates_session_state(fake_services):
    """ConfigUpdate must call SessionState.apply_config_update and emit
    ConfigUpdatedEvent."""
    from miqi.protocol.commands import ConfigUpdate
    from miqi.protocol.events import ConfigUpdatedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()
    fake_services.session_state = MagicMock()
    fake_services.session_state.apply_config_update = MagicMock()
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(ConfigUpdate(path="agents.defaults.temperature", value=0.2))

    fake_services.session_state.apply_config_update.assert_called_once_with(
        "agents.defaults.temperature",
        0.2,
    )
    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, ConfigUpdatedEvent)
    assert event.path == "agents.defaults.temperature"
    assert event.value == 0.2


def test_session_state_rejects_dunder_paths():
    """apply_config_update must reject paths with __ or _private segments."""
    import pytest as _pytest

    from miqi.runtime.session_state import SessionState

    state = SessionState(
        session_id="sess-1",
        workspace=__import__("pathlib").Path("/tmp"),
        active_thread_id="t1",
        config_snapshot=type("C", (), {"agents": type("A", (), {"defaults": type("D", (), {"temperature": 0.1})()})()})(),
    )

    # Valid path
    state.apply_config_update("agents.defaults.temperature", 0.9)
    assert state.config_snapshot.agents.defaults.temperature == 0.9

    # Dunder path
    with _pytest.raises(ValueError, match="dunder.*private"):
        state.apply_config_update("agents.__class__", "bad")

    # Private path
    with _pytest.raises(ValueError, match="dunder.*private"):
        state.apply_config_update("_private.attr", "bad")

    # Nested dunder
    with _pytest.raises(ValueError, match="dunder.*private"):
        state.apply_config_update("agents.defaults.__init__", "bad")

    # Empty path
    with _pytest.raises(ValueError, match="empty"):
        state.apply_config_update("", "bad")

    # Empty segment
    with _pytest.raises(ValueError, match="empty segment"):
        state.apply_config_update("agents..defaults", "bad")


# ---------------------------------------------------------------------------
# Phase 18 hardening: ConfigUpdate failure → CommandRejectedEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_update_empty_path_rejected(fake_services):
    """ConfigUpdate with empty path emits CommandRejectedEvent, never crashes."""
    from miqi.protocol.commands import ConfigUpdate
    from miqi.protocol.events import CommandRejectedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()
    fake_services.session_state = MagicMock()
    fake_services.session_state.apply_config_update = MagicMock(
        side_effect=ValueError("Config update path must not be empty"),
    )
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(ConfigUpdate(path="", value=42))

    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, CommandRejectedEvent)
    assert "empty" in event.reason.lower()


@pytest.mark.asyncio
async def test_config_update_missing_attribute_rejected(fake_services):
    """ConfigUpdate targeting a nonexistent attribute emits CommandRejectedEvent."""
    from miqi.protocol.commands import ConfigUpdate
    from miqi.protocol.events import CommandRejectedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()
    fake_services.session_state = MagicMock()
    fake_services.session_state.apply_config_update = MagicMock(
        side_effect=AttributeError("no such attribute"),
    )
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(ConfigUpdate(path="nonexistent.field", value=42))

    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, CommandRejectedEvent)
    assert "no such attribute" in event.reason


# ---------------------------------------------------------------------------
# Phase 18 hardening: ThreadCommand failure → CommandRejectedEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_command_rename_missing_title_rejected(fake_services):
    """ThreadCommand rename without title emits CommandRejectedEvent."""
    from miqi.protocol.commands import ThreadCommand
    from miqi.protocol.events import CommandRejectedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()
    fake_services.thread_runtime = MagicMock()
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(ThreadCommand(
        action="rename",
        thread_id="t1",
        params={},  # missing title
    ))

    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, CommandRejectedEvent)
    assert "title" in event.reason.lower()


@pytest.mark.asyncio
async def test_thread_command_archive_unknown_thread_rejected(fake_services):
    """ThreadCommand archive on nonexistent thread emits CommandRejectedEvent."""

    from miqi.protocol.commands import ThreadCommand
    from miqi.protocol.events import CommandRejectedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()
    fake_services.thread_runtime = MagicMock()
    fake_services.thread_runtime.archive_thread = AsyncMock(
        side_effect=KeyError("thread not found"),
    )
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(ThreadCommand(action="archive", thread_id="no-such"))

    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, CommandRejectedEvent)
    assert "thread not found" in event.reason


@pytest.mark.asyncio
async def test_thread_command_fork_unknown_parent_rejected(fake_services):
    """ThreadCommand fork on nonexistent parent emits CommandRejectedEvent."""

    from miqi.protocol.commands import ThreadCommand
    from miqi.protocol.events import CommandRejectedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()
    fake_services.thread_runtime = MagicMock()
    fake_services.thread_runtime.fork_thread = AsyncMock(
        side_effect=KeyError("parent thread not found"),
    )
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(ThreadCommand(action="fork", thread_id="no-such"))

    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, CommandRejectedEvent)
    assert "parent thread not found" in event.reason


# ---------------------------------------------------------------------------
# Phase 19: CompactCommand verification
# ---------------------------------------------------------------------------


def test_compact_command_is_submission():
    """CompactCommand exists, has correct type, and is part of Submission."""
    from miqi.protocol.commands import CompactCommand

    cmd = CompactCommand(thread_id="thread-1")
    assert cmd.type == "compact"
    assert cmd.reason == "manual"


# ---------------------------------------------------------------------------
# Phase 19: CompactCommand wired to ContextRuntime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compact_command_emits_context_compacted(fake_services):
    """CompactCommand must call context_runtime.compact_thread and emit
    ContextCompactedEvent (Phase 19)."""

    from miqi.protocol.commands import CompactCommand
    from miqi.protocol.events import ContextCompactedEvent
    from miqi.runtime.context_runtime import CompactionResult
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()
    fake_services.context_runtime.compact_thread = AsyncMock(return_value=CompactionResult(
        thread_id="thread-1",
        messages_before=10,
        messages_after=3,
        tokens_saved=100,
        replacement_messages=[],
    ))
    fake_services.history_runtime = MagicMock()
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(CompactCommand(thread_id="thread-1"))

    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, ContextCompactedEvent)
    assert event.messages_before == 10
    assert event.messages_after == 3
    assert event.tokens_saved == 100
    # Phase 19 follow-up: turn_id must be the compact turn id, not thread_id
    assert event.turn_id != "thread-1"
    assert event.turn_id.startswith("compact-")


@pytest.mark.asyncio
async def test_compact_command_no_runtime_emits_rejected(fake_services):
    """CompactCommand without context_runtime or history_runtime emits
    CommandRejectedEvent."""
    from miqi.protocol.commands import CompactCommand
    from miqi.protocol.events import CommandRejectedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = asyncio.Queue()
    fake_services.context_runtime = None
    fake_services.history_runtime = None
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(CompactCommand(thread_id="thread-1"))

    event = await asyncio.wait_for(events.get(), timeout=1)
    assert isinstance(event, CommandRejectedEvent)
    assert event.command_type == "CompactCommand"


@pytest.mark.asyncio
async def test_task_runner_sanitizes_processing_errors(fake_services):
    """Exception messages from turn runner are sanitized, not leaked."""
    import asyncio as _asyncio

    # TurnRunner.run raises with sensitive details
    fake_services.turn_runner.run.side_effect = RuntimeError(
        "secret API key leaked in stack trace"
    )

    events = _asyncio.Queue()
    runner = TaskRunner(services=fake_services, event_queue=events)

    from miqi.protocol.commands import UserMessage
    await runner.handle(UserMessage(content="test", thread_id="cli:default"))

    # Phase 17: TurnStartedEvent arrives first, then ErrorEvent.
    # Drain past non-ErrorEvent events.
    event = None
    while True:
        ev = await _asyncio.wait_for(events.get(), timeout=1)
        if ev.__class__.__name__ == "ErrorEvent":
            event = ev
            break
    assert "secret API key" not in event.message
    assert "内部错误" in event.message


# ---------------------------------------------------------------------------
# Phase 13: TaskRunner main path uses CapabilityResolver + PermissionProfile
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_runner_attaches_capabilities_and_permission_profile(fake_services):
    """The main UserMessage path must resolve capabilities via CapabilityResolver
    and attach a PermissionProfile to the TurnContext before calling TurnRunner."""
    import asyncio as _asyncio
    from unittest.mock import MagicMock

    from miqi.runtime.capabilities import CapabilityResolver
    from miqi.runtime.task_runner import TaskRunner

    # Create a proper CapabilityResolver
    tool_reg = MagicMock()
    tool_reg.get_definitions.return_value = [
        {"type": "function", "function": {"name": "read_file", "parameters": {}}},
        {"type": "function", "function": {"name": "exec", "parameters": {}}},
    ]
    fake_services.tool_registry = tool_reg

    capability_resolver = CapabilityResolver(
        tool_registry=tool_reg,
        plugin_manager=None,
    )
    fake_services.capability_resolver = capability_resolver

    # Track what turn and tools TurnRunner receives
    captured_turn = None
    captured_tools = None

    async def _capture(**kwargs):
        nonlocal captured_turn, captured_tools
        captured_turn = kwargs.get("turn")
        captured_tools = kwargs.get("tools")
        run_result = MagicMock()
        run_result.final_content = "ok"
        return run_result

    fake_services.turn_runner.run.side_effect = _capture

    events = _asyncio.Queue()
    runner = TaskRunner(services=fake_services, event_queue=events)

    from miqi.protocol.commands import UserMessage
    await runner.handle(UserMessage(content="hello", thread_id="cli:default"))

    # TurnContext should have capabilities attached
    assert captured_turn is not None, "TurnRunner was not called"
    assert captured_turn.capabilities is not None, "turn.capabilities was not set"
    assert len(captured_turn.capabilities.tool_definitions) > 0, (
        "capabilities.tool_definitions should have tools"
    )

    # Tools passed to TurnRunner should come from capability resolver
    assert captured_tools is not None, "tools argument was not passed"
    assert len(captured_tools) > 0, "tools should come from capabilities"
    assert captured_tools == captured_turn.capabilities.tool_definitions

    # TurnContext should have permission_profile
    assert captured_turn.permission_profile is not None, (
        "turn.permission_profile was not set"
    )
    from miqi.runtime.permission_profile import PermissionProfile
    assert isinstance(captured_turn.permission_profile, PermissionProfile)
    assert captured_turn.permission_profile.workspace == fake_services.workspace

    # Phase 17: verify AgentMessageEvent was queued (after TurnStartedEvent)
    event = None
    while True:
        ev = await _asyncio.wait_for(events.get(), timeout=1)
        if hasattr(ev, "content"):
            event = ev
            break
    assert event.content == "ok"


# ---------------------------------------------------------------------------
# Phase 41: Preallocated turn IDs and active turn tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_message_can_preallocate_turn_id(fake_services):
    import asyncio as _asyncio

    from miqi.protocol.commands import UserMessage
    from miqi.protocol.events import TurnStartedEvent
    from miqi.runtime.task_runner import TaskRunner

    events = _asyncio.Queue()
    runner = TaskRunner(services=fake_services, event_queue=events)

    await runner.handle(UserMessage(
        content="hello",
        thread_id="thread-1",
        turn_id="turn-preallocated",
        input_items=[{"type": "text", "text": "hello"}],
        client_user_message_id="client-msg-1",
    ))

    event = await events.get()
    assert isinstance(event, TurnStartedEvent)
    assert event.turn_id == "turn-preallocated"


@pytest.mark.asyncio
async def test_task_runner_active_turn_id_visible_while_turn_running(fake_services):
    import asyncio as _asyncio

    from miqi.protocol.commands import UserMessage
    from miqi.runtime.task_runner import TaskRunner

    events = _asyncio.Queue()
    gate = _asyncio.Event()

    async def _blocking_run(**kwargs):
        await gate.wait()
        result = type("Result", (), {})()
        result.final_content = "done"
        result.tools_used = []
        result.token_usage = {}
        result.messages_delta = [{"role": "assistant", "content": "done"}]
        return result

    fake_services.turn_runner.run.side_effect = _blocking_run
    runner = TaskRunner(services=fake_services, event_queue=events)

    task = _asyncio.create_task(runner.handle(UserMessage(
        content="hello",
        thread_id="thread-active",
        turn_id="turn-active",
    )))
    await events.get()

    assert runner.active_turn_id("thread-active") == "turn-active"

    gate.set()
    await task
    assert runner.active_turn_id("thread-active") is None


# ---------------------------------------------------------------------------
# PR #58: concurrent turns on same thread reuse cancel event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_user_messages_reuse_cancel_event(fake_services):
    """Two concurrent UserMessage on same thread go through
    _handle_user_message and must end up sharing one cancel event.

    Turn A enters _handle_user_message, registers cancel_evt_A, then blocks
    inside turn_runner.run.  Turn B enters _handle_user_message through the
    REAL handle() pipeline before A finishes — the fix must make Turn B
    reuse cancel_evt_A rather than overwrite it.  The registry is observed
    while B is parked inside turn_runner.run (i.e. after B's registration
    step but before B completes), so a regression that overwrites the dict
    entry with a fresh Event fails the test.
    """
    turn_a_blocked = asyncio.Event()
    turn_b_in_run = asyncio.Event()
    turn_b_can_proceed = asyncio.Event()
    turn_a_can_proceed = asyncio.Event()

    call_count = 0

    async def _blocking_run(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Turn A: signal we've registered, then block until B finished
            turn_a_blocked.set()
            await turn_a_can_proceed.wait()
        else:
            # Turn B: signal entry into run(), then hold until the test
            # has observed the shared cancel event
            turn_b_in_run.set()
            await turn_b_can_proceed.wait()
        result = type("Result", (), {})()
        result.final_content = "ok"
        result.tools_used = []
        result.token_usage = {}
        result.messages_delta = [{"role": "assistant", "content": "ok"}]
        return result

    fake_services.turn_runner.run.side_effect = _blocking_run

    events = asyncio.Queue()
    runner = TaskRunner(services=fake_services, event_queue=events)

    async def _wait_event(
        event: asyncio.Event,
        what: str,
        *tasks: asyncio.Task,
    ) -> None:
        # Bounded wait: if the production pipeline changes so a turn never
        # reaches the expected stage (e.g. handle() serializes turns on the
        # same thread), fail loudly instead of hanging the suite.
        try:
            await asyncio.wait_for(event.wait(), timeout=30)
        except asyncio.TimeoutError:
            for t in tasks:
                t.cancel()
            pytest.fail(f"Timed out waiting for {what}")

    # ── Start Turn A ──
    t1 = asyncio.create_task(runner.handle(UserMessage(
        content="first", thread_id="thread-shared", turn_id="turn-A",
    )))

    # Wait until Turn A has registered its cancel event and is blocked
    await _wait_event(turn_a_blocked, "Turn A to enter turn_runner.run", t1)
    cancel_after_a = runner._turn_cancel_events.get("thread-shared")
    assert cancel_after_a is not None, "Turn A must register a cancel event"

    # ── Start Turn B through the real handle() pipeline while A is running ──
    t2 = asyncio.create_task(runner.handle(UserMessage(
        content="second", thread_id="thread-shared", turn_id="turn-B",
    )))

    # Hold Turn B inside turn_runner.run and observe the registry state.
    await _wait_event(turn_b_in_run, "Turn B to enter turn_runner.run", t1, t2)
    assert runner._turn_cancel_events.get("thread-shared") is cancel_after_a, (
        "Turn B must reuse Turn A's cancel event, not create a new one"
    )

    # Let Turn B finish, then unblock Turn A so both can complete
    turn_b_can_proceed.set()
    await asyncio.wait_for(t2, timeout=30)
    turn_a_can_proceed.set()
    await asyncio.wait_for(t1, timeout=30)


@pytest.mark.asyncio
async def test_abort_signals_both_turns_on_shared_event(fake_services):
    """AbortTurn via handle() must signal the shared cancel event,
    and both turns waiting on it must wake up."""
    events = asyncio.Queue()
    runner = TaskRunner(services=fake_services, event_queue=events)

    thread_id = "abort-shared"
    shared_evt = asyncio.Event()
    runner._turn_cancel_events[thread_id] = shared_evt

    # Two turns are concurrently waiting on the same cancel event
    results: list[str] = []

    async def _wait(tag: str):
        await shared_evt.wait()
        results.append(tag)

    t1 = asyncio.create_task(_wait("turn-A"))
    t2 = asyncio.create_task(_wait("turn-B"))
    await asyncio.sleep(0.01)

    # Abort via handle() — this mirrors what the real code path does
    await runner.handle(AbortTurn(thread_id=thread_id))

    await asyncio.gather(t1, t2)

    assert "turn-A" in results, "Turn A must be woken by AbortTurn"
    assert "turn-B" in results, "Turn B must be woken by AbortTurn"
    assert len(results) == 2, "Both turns must be woken"


@pytest.mark.asyncio
async def test_new_turn_after_abort_gets_fresh_cancel_event(fake_services):
    """A user message sent after an abort on the same thread must NOT reuse the
    already-set cancel event, or it would abort "before start" (#542)."""
    seen_cancel_events: list[asyncio.Event | None] = []

    async def _record_run(**kwargs):
        seen_cancel_events.append(kwargs.get("cancel_event"))
        result = type("Result", (), {})()
        result.final_content = "ok"
        result.tools_used = []
        result.token_usage = {}
        result.messages_delta = [{"role": "assistant", "content": "ok"}]
        return result

    fake_services.turn_runner.run.side_effect = _record_run

    events = asyncio.Queue()
    runner = TaskRunner(services=fake_services, event_queue=events)
    thread_id = "fresh-after-abort"

    # Simulate a prior turn on this thread that was aborted but whose cancel
    # event is still registered and set (old turn hasn't cleaned up yet).
    stale = asyncio.Event()
    stale.set()
    runner._turn_cancel_events[thread_id] = stale

    await runner.handle(UserMessage(
        content="after abort", thread_id=thread_id, turn_id="new-turn",
    ))

    assert len(seen_cancel_events) == 1, "turn must run, not abort before start"
    fresh = seen_cancel_events[0]
    assert fresh is not None
    assert fresh is not stale, "must not reuse the stale set event"
    assert not fresh.is_set(), "fresh event must start unset"


@pytest.mark.asyncio
async def test_single_turn_abort_during_turn_runner(fake_services):
    """The cancel event registered by _handle_user_message must be
    reachable by AbortTurn while turn_runner.run is still executing."""
    turn_started = asyncio.Event()
    abort_received = asyncio.Event()

    async def _long_run(**kwargs):
        turn_started.set()
        # Wait for abort — this is what the real turn runner would do
        # (check cancel event periodically)
        cancel_evt = kwargs.get("cancel_event")
        if cancel_evt:
            await cancel_evt.wait()
        abort_received.set()
        result = type("Result", (), {})()
        result.final_content = "aborted"
        result.tools_used = []
        result.token_usage = {}
        result.messages_delta = []
        return result

    fake_services.turn_runner.run.side_effect = _long_run

    events = asyncio.Queue()
    runner = TaskRunner(services=fake_services, event_queue=events)

    # Start a turn — _handle_user_message registers cancel event internally
    task = asyncio.create_task(runner.handle(UserMessage(
        content="hello", thread_id="abort-me", turn_id="turn-to-abort",
    )))
    await turn_started.wait()

    # Verify cancel event was registered
    cancel_evt = runner._turn_cancel_events.get("abort-me")
    assert cancel_evt is not None, "_handle_user_message must register cancel event"

    # Send AbortTurn — this must find and signal the event
    await runner.handle(AbortTurn(thread_id="abort-me"))

    await abort_received.wait()
    await task
    assert cancel_evt.is_set(), "AbortTurn must set the cancel event"
