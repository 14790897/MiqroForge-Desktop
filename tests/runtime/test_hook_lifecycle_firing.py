"""Lifecycle hook firing tests for Task 51.3.

Verifies that TurnRunner, ContextRuntime, RuntimeSession, and AgentControl fire
their assigned lifecycle hook points.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from miqi.execution.hook_runtime import (
    HookOutcome,
    HookPoint,
    HookRegistration,
    HookRuntime,
    LifecycleHookContext,
)
from miqi.providers.base import LLMResponse, LLMStreamEvent
from miqi.runtime.agent_control import AgentControl
from miqi.runtime.agent_registry import AgentRegistry
from miqi.runtime.context_runtime import ContextRuntime
from miqi.runtime.session import RuntimeSession
from miqi.runtime.turn_runner import TurnRunner


@dataclass
class _HookRecorder:
    """Helper that captures every fired lifecycle hook context."""

    calls: list[tuple[HookPoint, LifecycleHookContext]] = field(
        default_factory=list,
    )

    def callback(self, point: HookPoint):
        async def _cb(ctx: LifecycleHookContext) -> HookOutcome | None:
            self.calls.append((point, ctx))
            return None

        return _cb

    def blocking_callback(self, point: HookPoint):
        async def _cb(ctx: LifecycleHookContext) -> HookOutcome:
            self.calls.append((point, ctx))
            return HookOutcome.block("test block")

        return _cb

    def points(self) -> set[HookPoint]:
        return {p for p, _ in self.calls}


# ── TurnRunner tests ──────────────────────────────────────────────────────────

class _FakeContextRuntime:
    def build_initial_messages(
        self,
        *,
        turn: Any,
        user_content: str,
        system_prompt: str,
        history: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    def add_assistant_message(
        self,
        *,
        messages: list[dict[str, Any]],
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]:
        item: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            item["tool_calls"] = tool_calls
        if reasoning_content:
            item["reasoning_content"] = reasoning_content
        return [*messages, item]

    def trim_for_model(self, messages, model):
        return messages


class _FakeToolRuntime:
    async def execute_many(self, turn: Any, tool_calls: list[Any]) -> list[Any]:
        return []


class _FakeProvider:
    async def stream_chat(self, **kwargs: Any):
        yield LLMStreamEvent(
            kind="completed",
            response=LLMResponse(content="hello"),
        )


class _FakeEventEmitter:
    async def emit(self, event: Any) -> None:
        pass


@pytest.mark.asyncio
async def test_turn_runner_fires_lifecycle_hooks() -> None:
    hooks = HookRuntime()
    recorder = _HookRecorder()
    for point in (
        HookPoint.PROMPT_SUBMIT,
        HookPoint.TURN_START,
        HookPoint.TURN_END,
    ):
        hooks.register(
            HookRegistration(point, "*", recorder.callback(point))
        )

    runner = TurnRunner(
        provider=_FakeProvider(),
        tool_runtime=_FakeToolRuntime(),
        context_runtime=_FakeContextRuntime(),
        event_emitter=_FakeEventEmitter(),
        max_iterations=3,
        hooks=hooks,
    )

    turn = SimpleNamespace(
        turn_id="turn-1",
        thread_id="thread-1",
        model="default",
        temperature=0.1,
        max_tokens=100,
    )

    result = await runner.run(
        turn=turn,
        user_content="hi",
        system_prompt="sys",
        tools=None,
    )

    assert result.final_content == "hello"
    assert recorder.points() >= {
        HookPoint.PROMPT_SUBMIT,
        HookPoint.TURN_START,
        HookPoint.TURN_END,
    }

    # PROMPT_SUBMIT should carry the user content.
    prompt_ctx = next(
        ctx for p, ctx in recorder.calls if p == HookPoint.PROMPT_SUBMIT
    )
    assert prompt_ctx.data.get("user_content") == "hi"


@pytest.mark.asyncio
async def test_turn_runner_skips_hooks_when_none() -> None:
    runner = TurnRunner(
        provider=_FakeProvider(),
        tool_runtime=_FakeToolRuntime(),
        context_runtime=_FakeContextRuntime(),
        event_emitter=_FakeEventEmitter(),
        max_iterations=3,
    )

    turn = SimpleNamespace(
        turn_id="turn-1",
        thread_id="thread-1",
        model="default",
        temperature=0.1,
        max_tokens=100,
    )

    result = await runner.run(
        turn=turn,
        user_content="hi",
        system_prompt="sys",
        tools=None,
    )
    assert result.final_content == "hello"


# ── ContextRuntime tests ──────────────────────────────────────────────────────

async def _build_large_messages(count: int = 30) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "system prompt"},
    ]
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": "x" * 4000})
    return messages


@pytest.mark.asyncio
async def test_context_runtime_fires_compact_hooks() -> None:
    llm_calls: list[tuple[list[dict[str, Any]], str]] = []

    async def fake_llm(msgs: list[dict[str, Any]], model: str) -> str:
        llm_calls.append((msgs, model))
        return "summary"

    hooks = HookRuntime()
    recorder = _HookRecorder()
    hooks.register(
        HookRegistration(
            HookPoint.PRE_COMPACT, "*", recorder.callback(HookPoint.PRE_COMPACT)
        )
    )
    hooks.register(
        HookRegistration(
            HookPoint.POST_COMPACT, "*", recorder.callback(HookPoint.POST_COMPACT)
        )
    )

    runtime = ContextRuntime(
        llm_call_fn=fake_llm,
        context_limit_chars=16_000,
        compression_threshold_chars=0,
        hooks=hooks,
    )

    messages = await _build_large_messages()
    compressed = await runtime.compress_messages(
        messages, model="default", session_id="sess-1"
    )

    assert HookPoint.PRE_COMPACT in recorder.points()
    assert HookPoint.POST_COMPACT in recorder.points()
    assert len(llm_calls) == 1
    # The compressor should have mutated/shortened the message list.
    assert len(compressed) < len(messages)


@pytest.mark.asyncio
async def test_pre_compact_block_skips_compression() -> None:
    llm_calls: list[tuple[list[dict[str, Any]], str]] = []

    async def fake_llm(msgs: list[dict[str, Any]], model: str) -> str:
        llm_calls.append((msgs, model))
        return "summary"

    hooks = HookRuntime()
    recorder = _HookRecorder()
    hooks.register(
        HookRegistration(
            HookPoint.PRE_COMPACT,
            "*",
            recorder.blocking_callback(HookPoint.PRE_COMPACT),
        )
    )
    hooks.register(
        HookRegistration(
            HookPoint.POST_COMPACT, "*", recorder.callback(HookPoint.POST_COMPACT)
        )
    )

    runtime = ContextRuntime(
        llm_call_fn=fake_llm,
        context_limit_chars=16_000,
        compression_threshold_chars=0,
        hooks=hooks,
    )

    messages = await _build_large_messages()
    compressed = await runtime.compress_messages(
        messages, model="default", session_id="sess-1"
    )

    assert HookPoint.PRE_COMPACT in recorder.points()
    assert HookPoint.POST_COMPACT in recorder.points()
    # Block should prevent the LLM (and actual compression) from running.
    assert len(llm_calls) == 0
    assert compressed == messages


@pytest.mark.asyncio
async def test_context_runtime_skips_hooks_when_none() -> None:
    async def fake_llm(msgs: list[dict[str, Any]], model: str) -> str:
        return "summary"

    runtime = ContextRuntime(
        llm_call_fn=fake_llm,
        context_limit_chars=16_000,
        compression_threshold_chars=0,
    )
    messages = await _build_large_messages()
    result = await runtime.compress_messages(messages, model="default")
    assert isinstance(result, list)


# ── RuntimeSession tests ──────────────────────────────────────────────────────

class _FakeThreadRuntime:
    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def get_thread(self, thread_id: str) -> Any | None:
        return None

    async def create_thread(
        self, *, thread_id: str, title: str
    ) -> Any:
        return SimpleNamespace(thread_id=thread_id)


class _FakeHistoryRuntime:
    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass


class _FakeLedgerRuntime:
    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass


def _make_session_services(hooks: HookRuntime) -> Any:
    return SimpleNamespace(
        session_id="sess:test",
        workspace=Path.cwd(),
        hooks=hooks,
        session_state=SimpleNamespace(active_thread_id="sess:test:default"),
        history_runtime=_FakeHistoryRuntime(),
        thread_runtime=_FakeThreadRuntime(),
        ledger_runtime=_FakeLedgerRuntime(),
        model_settings=SimpleNamespace(
            model="default",
            temperature=0.1,
            max_tokens=100,
            max_tool_result_chars=1000,
            context_limit_chars=100_000,
        ),
        bus=None,
        provider=None,
        event_emitter=_FakeEventEmitter(),
        tool_registry=None,
        orchestrator=None,
        agent_registry=None,
        agent_control=None,
        tool_runtime=None,
        context_runtime=None,
        turn_runner=None,
        plugin_manager=None,
        agent_jobs=None,
        capability_resolver=None,
        mcp_runtime=None,
        replay_runtime=None,
    )


@pytest.mark.asyncio
async def test_session_fires_start_and_stop_hooks() -> None:
    hooks = HookRuntime()
    recorder = _HookRecorder()
    for point in (
        HookPoint.SESSION_START,
        HookPoint.SESSION_END,
        HookPoint.STOP,
    ):
        hooks.register(
            HookRegistration(point, "*", recorder.callback(point))
        )

    services = _make_session_services(hooks)
    session = RuntimeSession(services=services, hooks=hooks)

    await session.start()
    await session.stop()

    assert HookPoint.SESSION_START in recorder.points()
    assert HookPoint.SESSION_END in recorder.points()
    assert HookPoint.STOP in recorder.points()


# ── AgentControl tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_control_fires_subagent_start() -> None:
    hooks = HookRuntime()
    recorder = _HookRecorder()
    hooks.register(
        HookRegistration(
            HookPoint.SUBAGENT_START,
            "*",
            recorder.callback(HookPoint.SUBAGENT_START),
        )
    )

    control = AgentControl(
        session_id="sess:test",
        registry=AgentRegistry(),
        event_emitter=_FakeEventEmitter(),
        workspace=Path.cwd(),
        hooks=hooks,
    )

    agent = await control.spawn(
        agent_type="code-agent",
        task="do something",
    )

    assert HookPoint.SUBAGENT_START in recorder.points()
    assert agent.agent_id
    assert agent.thread_id


class _SubagentProvider:
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        return LLMResponse(content="done")


@pytest.mark.asyncio
async def test_agent_control_fires_subagent_end() -> None:
    hooks = HookRuntime()
    recorder = _HookRecorder()
    hooks.register(
        HookRegistration(
            HookPoint.SUBAGENT_END,
            "*",
            recorder.callback(HookPoint.SUBAGENT_END),
        )
    )

    control = AgentControl(
        session_id="sess:test",
        registry=AgentRegistry(),
        event_emitter=_FakeEventEmitter(),
        workspace=Path.cwd(),
        provider=_SubagentProvider(),
        hooks=hooks,
    )

    agent = await control.spawn(
        agent_type="code-agent",
        task="do something",
    )
    # Direct path schedules a background task; wait for it to finish.
    task = control._running_tasks.get(agent.agent_id)
    if task is not None:
        await task

    assert HookPoint.SUBAGENT_END in recorder.points()
