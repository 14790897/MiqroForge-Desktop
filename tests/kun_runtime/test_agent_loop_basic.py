"""Phase 8 tests — AgentLoop basic + tool + compaction + gates."""

from __future__ import annotations

from pathlib import Path

import pytest

from miqi.kun_runtime.cancellation import InflightTracker
from miqi.kun_runtime.compactor import ContextCompactor
from miqi.kun_runtime.event_bus import EventBus
from miqi.kun_runtime.event_recorder import RuntimeEventRecorder
from miqi.kun_runtime.loop import AgentLoop, AgentLoopOptions
from miqi.kun_runtime.model_client import FakeModelClient
from miqi.kun_runtime.stores import FileSessionStore, FileThreadStore
from miqi.kun_runtime.tool_host import FakeToolHost
from miqi.kun_runtime.turn_service import TurnService
from miqi.kun_runtime.usage import UsageService

FIXED_TIME = "2026-06-08T12:00:00Z"


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path / "loop_data"


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def events(bus: EventBus) -> RuntimeEventRecorder:
    return RuntimeEventRecorder(bus, now_iso=lambda: FIXED_TIME)


@pytest.fixture
def thread_store(tmp_dir: Path) -> FileThreadStore:
    return FileThreadStore(tmp_dir)


@pytest.fixture
def session_store(tmp_dir: Path) -> FileSessionStore:
    return FileSessionStore(tmp_dir)


@pytest.fixture
def usage() -> UsageService:
    return UsageService()


@pytest.fixture
def inflight() -> InflightTracker:
    return InflightTracker()


@pytest.fixture
def turn_svc(thread_store: FileThreadStore, session_store: FileSessionStore, events: RuntimeEventRecorder, inflight: InflightTracker) -> TurnService:
    return TurnService(thread_store, session_store, events, inflight, now_iso=lambda: FIXED_TIME)


@pytest.fixture
def compactor() -> ContextCompactor:
    return ContextCompactor(soft_threshold=100, hard_threshold=500)


@pytest.fixture
def model_client() -> FakeModelClient:
    return FakeModelClient(text_chunks=["Hello, KUN!"])


@pytest.fixture
def tool_host() -> FakeToolHost:
    return FakeToolHost(
        tools=[
            {"name": "read", "description": "Read file", "inputSchema": {}, "toolKind": "tool_call"},
            {"name": "bash", "description": "Run command", "inputSchema": {}, "toolKind": "command_execution"},
        ],
        results={"read": "file content", "bash": "command output"},
    )


@pytest.fixture
def loop_opts(
    thread_store: FileThreadStore,
    session_store: FileSessionStore,
    model_client: FakeModelClient,
    tool_host: FakeToolHost,
    usage: UsageService,
    events: RuntimeEventRecorder,
    turn_svc: TurnService,
    inflight: InflightTracker,
    compactor: ContextCompactor,
) -> AgentLoopOptions:
    return AgentLoopOptions(
        thread_store=thread_store,
        session_store=session_store,
        model=model_client,
        tool_host=tool_host,
        usage=usage,
        events=events,
        turns=turn_svc,
        inflight=inflight,
        compactor=compactor,
        now_iso=lambda: FIXED_TIME,
    )


async def _setup_thread_and_turn(thread_store: FileThreadStore, turn_svc: TurnService) -> tuple[str, str]:
    th = {
        "id": "th1",
        "title": "Test Thread",
        "workspace": "/tmp/test",
        "model": "fake-model",
        "mode": "agent",
        "status": "idle",
        "approvalPolicy": "auto",
        "sandboxMode": "workspace-write",
        "relation": "primary",
        "costBudgetWarningSent": False,
        "createdAt": FIXED_TIME,
        "updatedAt": FIXED_TIME,
        "turns": [],
    }
    await thread_store.upsert(th)
    result = await turn_svc.start_turn("th1", "hello")
    return "th1", result["turnId"]


# ═══════════════════════════════════════════════════════════════════════════════
# Basic AgentLoop tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentLoopBasic:
    @pytest.mark.asyncio
    async def test_single_turn_text_completion(
        self, loop_opts: AgentLoopOptions, thread_store: FileThreadStore, turn_svc: TurnService
    ) -> None:
        loop = AgentLoop(loop_opts)
        thread_id, turn_id = await _setup_thread_and_turn(thread_store, turn_svc)

        status = await loop.run_turn(thread_id, turn_id)
        assert status == "completed"

        # Turn should be finished
        turn = await turn_svc.get_turn(thread_id, turn_id)
        assert turn is not None
        assert turn["status"] == "completed"

    @pytest.mark.asyncio
    async def test_emits_pipeline_events(
        self, loop_opts: AgentLoopOptions, thread_store: FileThreadStore, turn_svc: TurnService, bus: EventBus
    ) -> None:
        loop = AgentLoop(loop_opts)
        thread_id, turn_id = await _setup_thread_and_turn(thread_store, turn_svc)

        await loop.run_turn(thread_id, turn_id)

        events_list = bus.history(thread_id)
        kinds = [e["kind"] for e in events_list]
        assert "pipeline_stage" in kinds
        assert "turn_completed" in kinds

    @pytest.mark.asyncio
    async def test_emits_text_delta(
        self, loop_opts: AgentLoopOptions, thread_store: FileThreadStore, turn_svc: TurnService, bus: EventBus
    ) -> None:
        loop = AgentLoop(loop_opts)
        thread_id, turn_id = await _setup_thread_and_turn(thread_store, turn_svc)

        await loop.run_turn(thread_id, turn_id)

        events_list = bus.history(thread_id)
        assert any(e.get("kind") == "assistant_text_delta" for e in events_list)

    @pytest.mark.asyncio
    async def test_persists_assistant_item(
        self, loop_opts: AgentLoopOptions, thread_store: FileThreadStore, turn_svc: TurnService, session_store: FileSessionStore
    ) -> None:
        loop = AgentLoop(loop_opts)
        thread_id, turn_id = await _setup_thread_and_turn(thread_store, turn_svc)

        await loop.run_turn(thread_id, turn_id)

        items = await session_store.load_items(thread_id)
        kinds = [i["kind"] for i in items]
        assert "assistant_text" in kinds


class TestAgentLoopTools:
    @pytest.mark.asyncio
    async def test_tool_call_dispatch(
        self, loop_opts: AgentLoopOptions, thread_store: FileThreadStore, turn_svc: TurnService, session_store: FileSessionStore
    ) -> None:
        """Agent loop should dispatch tool calls from the model and persist results."""
        loop_opts.model = FakeModelClient(
            text_chunks=["Let me check..."],
            tool_calls=[{"id": "call_1", "name": "read", "arguments": {"path": "test.txt"}}],
        )
        loop = AgentLoop(loop_opts)
        thread_id, turn_id = await _setup_thread_and_turn(thread_store, turn_svc)

        status = await loop.run_turn(thread_id, turn_id)
        assert status == "completed"

        items = await session_store.load_items(thread_id)
        kinds = [i["kind"] for i in items]
        assert "tool_call" in kinds
        assert "tool_result" in kinds

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(
        self, loop_opts: AgentLoopOptions, thread_store: FileThreadStore, turn_svc: TurnService, session_store: FileSessionStore
    ) -> None:
        # Use text-only after tool dispatch — avoids infinite loop from
        # FakeModelClient always returning tools.
        loop_opts.model = FakeModelClient(
            text_chunks=["Checking..."],
            tool_calls=[
                {"id": "call_1", "name": "read", "arguments": {"path": "a.txt"}},
                {"id": "call_2", "name": "read", "arguments": {"path": "b.txt"}},
            ],
        )
        loop = AgentLoop(loop_opts)
        thread_id, turn_id = await _setup_thread_and_turn(thread_store, turn_svc)

        await loop.run_turn(thread_id, turn_id)

        items = await session_store.load_items(thread_id)
        tool_call_items = [i for i in items if i["kind"] == "tool_call"]
        tool_result_items = [i for i in items if i["kind"] == "tool_result"]
        # At least the first batch of 2 tool calls + 2 results should exist.
        # (The loop may call model_step again, producing more.)
        assert len(tool_call_items) >= 2
        assert len(tool_result_items) >= 2

    @pytest.mark.asyncio
    async def test_tool_error_handling(
        self, loop_opts: AgentLoopOptions, thread_store: FileThreadStore, turn_svc: TurnService, session_store: FileSessionStore
    ) -> None:
        loop_opts.model = FakeModelClient(
            tool_calls=[{"id": "call_1", "name": "bash", "arguments": {"command": "rm -rf /"}}],
        )
        loop_opts.tool_host = FakeToolHost(
            tools=[{"name": "bash", "description": "Run", "inputSchema": {}, "toolKind": "command_execution"}],
            error_tools={"bash"},
            results={"bash": "Permission denied"},
        )
        loop = AgentLoop(loop_opts)
        thread_id, turn_id = await _setup_thread_and_turn(thread_store, turn_svc)

        await loop.run_turn(thread_id, turn_id)

        items = await session_store.load_items(thread_id)
        results = [i for i in items if i["kind"] == "tool_result"]
        assert len(results) >= 1
        assert any(r.get("isError") for r in results)


class TestAgentLoopGates:
    @pytest.mark.asyncio
    async def test_interrupt_stops_loop(
        self, loop_opts: AgentLoopOptions, thread_store: FileThreadStore, turn_svc: TurnService
    ) -> None:
        """Interrupting a turn before it starts should abort."""
        # Interrupt before the loop even runs — the token is already created
        # in start_turn.
        loop = AgentLoop(loop_opts)
        thread_id, turn_id = await _setup_thread_and_turn(thread_store, turn_svc)

        await turn_svc.interrupt_turn(thread_id, turn_id)
        status = await loop.run_turn(thread_id, turn_id)
        assert status == "aborted"

    @pytest.mark.asyncio
    async def test_model_error_propagates(
        self, loop_opts: AgentLoopOptions, thread_store: FileThreadStore, turn_svc: TurnService, bus: EventBus
    ) -> None:
        loop_opts.model = FakeModelClient(error="model down", error_code="MODEL_ERROR")
        loop = AgentLoop(loop_opts)
        thread_id, turn_id = await _setup_thread_and_turn(thread_store, turn_svc)

        status = await loop.run_turn(thread_id, turn_id)
        # Model errors in the loop stop the turn with a failed status
        # (loop.py: stop_reason == "error" → "failed")
        assert status == "failed"

        events_list = bus.history(thread_id)
        assert any(e.get("kind") == "error" for e in events_list)


class TestAgentLoopCompaction:
    @pytest.mark.asyncio
    async def test_compaction_triggers_on_large_history(
        self, loop_opts: AgentLoopOptions, thread_store: FileThreadStore, turn_svc: TurnService,
        session_store: FileSessionStore, bus: EventBus,
    ) -> None:
        """Compaction should trigger when history is large."""
        loop_opts.compactor = ContextCompactor(soft_threshold=10, hard_threshold=20)
        loop = AgentLoop(loop_opts)
        thread_id, turn_id = await _setup_thread_and_turn(thread_store, turn_svc)

        # Add lots of history
        for i in range(10):
            await session_store.append_item(thread_id, {
                "id": f"old_{i}", "turnId": "old_turn", "threadId": thread_id,
                "role": "user", "status": "completed", "kind": "user_message",
                "createdAt": FIXED_TIME, "text": f"long message number {i} " + ("x" * 100),
            })

        await loop.run_turn(thread_id, turn_id)

        items = await session_store.load_items(thread_id)
        kinds = [i["kind"] for i in items]
        # The compactor must have folded the old history into a summary item
        assert "compaction" in kinds, f"expected a compaction item, got kinds: {kinds}"

        events_list = bus.history(thread_id)
        assert any(
            e.get("kind") == "compaction_completed" for e in events_list
        ), "compaction_completed event should be recorded"


class TestAgentLoopToolStorm:
    @pytest.mark.asyncio
    async def test_identical_calls_suppressed(
        self, loop_opts: AgentLoopOptions, thread_store: FileThreadStore, turn_svc: TurnService, session_store: FileSessionStore
    ) -> None:
        """Repeated identical tool calls should be suppressed."""
        loop_opts.tool_storm = {"enabled": True, "windowSize": 4, "threshold": 2}
        loop_opts.model = FakeModelClient(
            text_chunks=["Try again..."],
            tool_calls=[
                {"id": "call_1", "name": "read", "arguments": {"path": "same.txt"}},
                {"id": "call_2", "name": "read", "arguments": {"path": "same.txt"}},
                {"id": "call_3", "name": "read", "arguments": {"path": "same.txt"}},
            ],
        )
        loop = AgentLoop(loop_opts)
        thread_id, turn_id = await _setup_thread_and_turn(thread_store, turn_svc)

        await loop.run_turn(thread_id, turn_id)

        items = await session_store.load_items(thread_id)
        results = [i for i in items if i["kind"] == "tool_result"]
        # First executes, second suppressed, third suppressed
        suppressed = [r for r in results if "suppressed" in str(r.get("output", ""))]
        assert len(suppressed) >= 1


class TestAgentLoopUsage:
    @pytest.mark.asyncio
    async def test_usage_accumulated(
        self, loop_opts: AgentLoopOptions, thread_store: FileThreadStore, turn_svc: TurnService, usage: UsageService
    ) -> None:
        loop_opts.model = FakeModelClient(
            text_chunks=["Done"],
            usage={"promptTokens": 100, "completionTokens": 50, "totalTokens": 150},
        )
        loop = AgentLoop(loop_opts)
        thread_id, turn_id = await _setup_thread_and_turn(thread_store, turn_svc)

        await loop.run_turn(thread_id, turn_id)

        snap = usage.for_thread(thread_id)
        assert snap.get("promptTokens", 0) >= 100


class TestAgentLoopUserRoots:
    """Issue #821: directories the user mentions reach the tool host context."""

    @pytest.mark.asyncio
    async def test_user_mentioned_roots_reach_tool_context(
        self,
        loop_opts: AgentLoopOptions,
        thread_store: FileThreadStore,
        turn_svc: TurnService,
        tmp_path: Path,
    ) -> None:
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        # The thread workspace is tmp_path/"ws" so the mentioned dir sits
        # OUTSIDE it — an inside mention would be skipped as already legal.
        thread = {
            "id": "th1",
            "title": "Test Thread",
            "workspace": str(tmp_path / "ws"),
            "model": "fake-model",
            "mode": "agent",
            "status": "idle",
            "approvalPolicy": "auto",
            "sandboxMode": "workspace-write",
            "relation": "primary",
            "costBudgetWarningSent": False,
            "createdAt": FIXED_TIME,
            "updatedAt": FIXED_TIME,
            "turns": [],
        }
        await thread_store.upsert(thread)
        result = await turn_svc.start_turn("th1", f"处理 XX，结果输出到 {out_dir}，谢谢")
        turn_id = result["turnId"]

        loop_opts.model = FakeModelClient(
            text_chunks=["Let me check..."],
            tool_calls=[{"id": "call_1", "name": "read", "arguments": {"path": "test.txt"}}],
        )
        loop = AgentLoop(loop_opts)
        status = await loop.run_turn("th1", turn_id)
        assert status == "completed"

        assert loop_opts.tool_host.calls, "expected at least one tool dispatch"
        context = loop_opts.tool_host.calls[-1][1]
        roots = [str(Path(r)) for r in context.user_mentioned_roots]
        assert str(out_dir.resolve()) in roots
