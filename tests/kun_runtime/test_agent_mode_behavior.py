"""Behavioral tests for Agent Reasoning Modes (issue #680).

Prove the mode actually changes the runtime behavior, not just the UI:
- fast: max_tokens=2048, fast prompt injected, decision loop capped at 3 rounds
- think: max_tokens=8192, no prompt injection, unlimited rounds (current behavior)
- default (no mode) = fast (默认极速版, user decision)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from miqi.agent.agent_mode import FAST_PROMPT
from miqi.kun_runtime.cancellation import InflightTracker
from miqi.kun_runtime.compactor import ContextCompactor
from miqi.kun_runtime.event_bus import EventBus
from miqi.kun_runtime.event_recorder import RuntimeEventRecorder
from miqi.kun_runtime.loop import AgentLoop, AgentLoopOptions
from miqi.kun_runtime.model_client import FakeModelClient, ModelRequest, ModelStreamChunk
from miqi.kun_runtime.stores import FileSessionStore, FileThreadStore
from miqi.kun_runtime.tool_host import FakeToolHost
from miqi.kun_runtime.turn_service import TurnService
from miqi.kun_runtime.usage import UsageService

_FIXED = "2026-08-18T00:00:00.000Z"


class CapturingModel(FakeModelClient):
    """FakeModelClient that records every ModelRequest it sees."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests: list[ModelRequest] = []

    async def stream(self, request):
        self.requests.append(request)
        async for chunk in super().stream(request):
            yield chunk


def _build_runtime(tmp_path: Path, model: CapturingModel, mode: str | None) -> AgentLoop:
    data_dir = tmp_path / "data"
    thread_store = FileThreadStore(data_dir)
    session_store = FileSessionStore(data_dir)
    bus = EventBus()
    events = RuntimeEventRecorder(bus, now_iso=lambda: _FIXED)
    inflight = InflightTracker()
    turns = TurnService(thread_store, session_store, events, inflight, now_iso=lambda: _FIXED)
    tool_host = FakeToolHost(
        tools=[{"name": "web_search", "description": "Search", "inputSchema": {}, "toolKind": "tool_call"}],
        results={"web_search": "search results"},
    )

    loop = AgentLoop(
        AgentLoopOptions(
            thread_store=thread_store,
            session_store=session_store,
            model=model,
            tool_host=tool_host,
            usage=UsageService(),
            events=events,
            turns=turns,
            inflight=inflight,
            compactor=ContextCompactor(soft_threshold=100, hard_threshold=500),
            now_iso=lambda: _FIXED,
        )
    )
    return loop


async def _mkthread(loop: AgentLoop, thread_id: str, mode: str | None) -> None:
    thread = {"id": thread_id, "turns": [], "metadata": {"mode": mode} if mode else {}}
    await loop._opts.thread_store.upsert(thread)


@pytest.mark.asyncio
async def test_fast_mode_request_params(tmp_path: Path) -> None:
    """fast → max_tokens 2048 + fast prompt injected into system prompt."""
    model = CapturingModel(text_chunks=["快速回答"])
    loop = _build_runtime(tmp_path, model, mode="fast")
    await _mkthread(loop, "t-fast", "fast")

    turn = await loop._opts.turns.start_turn("t-fast", "hello")
    status = await loop.run_turn("t-fast", turn["turnId"])

    assert status == "completed"
    assert model.requests, "model should have been called"
    req = model.requests[0]
    assert req.max_tokens == 2048
    assert FAST_PROMPT in req.system_prompt
    assert "极速回答" in req.system_prompt


@pytest.mark.asyncio
async def test_think_mode_request_params(tmp_path: Path) -> None:
    """think → max_tokens 8192 + depth-guidance prompt injected."""
    model = CapturingModel(text_chunks=["深度回答"])
    loop = _build_runtime(tmp_path, model, mode="think")
    await _mkthread(loop, "t-think", "think")

    turn = await loop._opts.turns.start_turn("t-think", "hello")
    status = await loop.run_turn("t-think", turn["turnId"])

    assert status == "completed"
    assert model.requests
    req = model.requests[0]
    assert req.max_tokens == 8192
    assert "深度研究" in req.system_prompt
    assert "极速回答" not in req.system_prompt


@pytest.mark.asyncio
async def test_fast_mode_caps_decision_rounds(tmp_path: Path) -> None:
    """fast: a model that keeps requesting tools stops after 3 rounds (fuse)."""

    class ToolLoopModel(CapturingModel):
        def __init__(self):
            super().__init__(text_chunks=[])
            self._calls = 0

        async def stream(self, request):
            self._calls += 1
            self.requests.append(request)
            yield ModelStreamChunk(
                kind="tool_call_complete",
                callId=f"call_{self._calls}",
                toolName="web_search",
                arguments={"query": "q"},
            )
            yield ModelStreamChunk(kind="completed", stopReason="tool_calls")

    model = ToolLoopModel()
    loop = _build_runtime(tmp_path, model, mode="fast")
    await _mkthread(loop, "t-fuse", "fast")

    turn = await loop._opts.turns.start_turn("t-fuse", "search")
    status = await loop.run_turn("t-fuse", turn["turnId"])

    # 3 decision rounds max (fuse), then the loop gives up gracefully.
    assert model._calls <= 3, f"fast mode must cap rounds, got {model._calls}"
    assert status == "completed"


@pytest.mark.asyncio
async def test_mode_defaults_to_fast(tmp_path: Path) -> None:
    """Thread with no mode → fast (默认极速版, user decision)."""
    model = CapturingModel(text_chunks=["ok"])
    loop = _build_runtime(tmp_path, model, mode=None)
    await _mkthread(loop, "t-none", None)

    turn = await loop._opts.turns.start_turn("t-none", "hello")
    await loop.run_turn("t-none", turn["turnId"])

    assert model.requests
    assert model.requests[0].max_tokens == 2048  # default = fast
