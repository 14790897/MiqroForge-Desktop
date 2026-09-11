"""Legacy (desktop) path tests for user-mentioned output dirs (issue #821).

The desktop bridge runs RuntimeSession → TurnRunner → ToolRuntime →
ToolOrchestrator (not the KUN loop), so the auto-sensed roots must flow:
  TurnRunner (extract from user_content, refresh on steering)
    → TurnContext.user_mentioned_roots
    → ToolRuntime → ToolExecutionContext.user_mentioned_roots
    → ToolOrchestrator._execute_in_sandbox → `_user_roots` kwargs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.execution.hook_runtime import HookOutcome
from miqi.execution.orchestrator import (
    ToolExecutionContext,
    ToolOrchestrator,
)
from miqi.execution.permission_engine import (
    PermissionDecision,
    PermissionVerdict,
)
from miqi.providers.base import LLMResponse, LLMStreamEvent
from miqi.runtime.tool_runtime import ToolRuntime
from miqi.runtime.turn_runner import TurnRunner


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
        return [*messages, {"role": "assistant", "content": content}]

    def trim_for_model(self, messages: Any, model: str) -> Any:
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


def _make_runner() -> TurnRunner:
    return TurnRunner(
        provider=_FakeProvider(),
        tool_runtime=_FakeToolRuntime(),
        context_runtime=_FakeContextRuntime(),
        event_emitter=_FakeEventEmitter(),
        max_iterations=3,
    )


# ── TurnRunner: extraction ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_runner_extracts_user_mentioned_roots(tmp_path: Path) -> None:
    """A dir the user names in their message lands on turn.user_mentioned_roots."""
    ws = tmp_path / "ws"
    ws.mkdir()
    out = tmp_path / "Desktop_out"
    out.mkdir()
    runner = _make_runner()

    turn = SimpleNamespace(
        turn_id="turn-1",
        thread_id="thread-1",
        model="default",
        temperature=0.1,
        max_tokens=100,
        workspace=ws,
    )
    await runner.run(
        turn=turn,
        user_content=f"结果输出到 {out}，谢谢",
        system_prompt="sys",
        tools=None,
    )
    assert turn.user_mentioned_roots == [out.resolve()]


@pytest.mark.asyncio
async def test_turn_runner_skips_workspace_covered_mentions(tmp_path: Path) -> None:
    """A mention already inside the workspace adds no root."""
    ws = tmp_path / "ws"
    ws.mkdir()
    sub = ws / "sub"
    sub.mkdir()
    runner = _make_runner()

    turn = SimpleNamespace(
        turn_id="turn-1",
        thread_id="thread-1",
        model="default",
        temperature=0.1,
        max_tokens=100,
        workspace=ws,
    )
    await runner.run(
        turn=turn,
        user_content=f"输出到 {sub}",
        system_prompt="sys",
        tools=None,
    )
    assert turn.user_mentioned_roots == []


@pytest.mark.asyncio
async def test_turn_runner_refreshes_roots_on_steering(tmp_path: Path) -> None:
    """A steering message naming a new output dir refreshes the roots."""
    ws = tmp_path / "ws"
    ws.mkdir()
    first = tmp_path / "first_out"
    first.mkdir()
    second = tmp_path / "second_out"
    second.mkdir()
    steer_queue: asyncio.Queue = asyncio.Queue()
    steer_queue.put_nowait({"content": f"改放到 {second} 里"})
    runner = _make_runner()

    turn = SimpleNamespace(
        turn_id="turn-1",
        thread_id="thread-1",
        model="default",
        temperature=0.1,
        max_tokens=100,
        workspace=ws,
    )
    await runner.run(
        turn=turn,
        user_content=f"结果输出到 {first}",
        system_prompt="sys",
        tools=None,
        steer_queue=steer_queue,
    )
    assert first.resolve() in turn.user_mentioned_roots
    assert second.resolve() in turn.user_mentioned_roots


# ── ToolRuntime: context propagation ─────────────────────────────────────────


class _CapturingOrchestrator:
    def __init__(self) -> None:
        self.last_ctx: ToolExecutionContext | None = None

    async def execute(self, ctx: ToolExecutionContext) -> ToolExecutionContext:
        self.last_ctx = ctx
        ctx.result = "ok"
        return ctx


@pytest.mark.asyncio
async def test_tool_runtime_passes_user_roots_to_context(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    orch = _CapturingOrchestrator()
    runtime = ToolRuntime(orchestrator=orch)

    turn = SimpleNamespace(
        turn_id="turn-1",
        thread_id="thread-1",
        agent_metadata=SimpleNamespace(name="main"),
        client_id="",
        session_id="",
        user_mentioned_roots=[out.resolve()],
    )
    call = SimpleNamespace(name="write_file", id="call-1", arguments={"path": "x"})
    await runtime.execute_one(turn, call)

    assert orch.last_ctx is not None
    assert orch.last_ctx.user_mentioned_roots == [str(out.resolve())]


# ── Orchestrator: `_user_roots` kwarg injection ──────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_injects_user_roots_kwargs(tmp_path: Path) -> None:
    """ToolOrchestrator passes ctx.user_mentioned_roots as `_user_roots`."""
    out = tmp_path / "out"
    out.mkdir()
    components: dict[str, Any] = {
        "permission_engine": MagicMock(),
        "sandbox_engine": MagicMock(),
        "hook_runtime": MagicMock(),
        "tool_registry": MagicMock(),
        "event_emitter": MagicMock(),
    }
    components["permission_engine"].check = AsyncMock(
        return_value=PermissionDecision(
            verdict=PermissionVerdict.ALLOW,
            category="file_write",
            description="ok",
            allow_permanent=False,
        )
    )
    components["sandbox_engine"].select = AsyncMock(
        return_value=MagicMock(
            sandbox_type="none",
            filesystem_policy=MagicMock(),
            network_policy="allow_all",
        )
    )
    components["hook_runtime"].run = AsyncMock()
    components["hook_runtime"].run_with_outcome = AsyncMock(
        return_value=HookOutcome.continue_()
    )
    components["event_emitter"].emit = AsyncMock()

    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="Successfully wrote 5 bytes")
    components["tool_registry"].get.return_value = tool_mock

    orch = ToolOrchestrator(
        permission_engine=components["permission_engine"],
        sandbox_engine=components["sandbox_engine"],
        hook_runtime=components["hook_runtime"],
        tool_registry=components["tool_registry"],
        event_emitter=components["event_emitter"],
    )

    ctx = ToolExecutionContext(
        tool_name="write_file",
        tool_call_id="call-1",
        arguments={"path": str(out / "r.md"), "content": "hi"},
        turn_id="turn-1",
        thread_id="thread-1",
        agent_type="main",
        session_id="desktop:test",
        user_mentioned_roots=[str(out.resolve())],
    )
    result = await orch.execute(ctx)

    assert "Successfully wrote" in (result.result or "")
    kwargs = tool_mock.execute.call_args.kwargs
    assert kwargs["_user_roots"] == [str(out.resolve())]


@pytest.mark.asyncio
async def test_orchestrator_skips_user_roots_when_empty(tmp_path: Path) -> None:
    """No user roots on ctx → no `_user_roots` kwarg injected."""
    components: dict[str, Any] = {
        "permission_engine": MagicMock(),
        "sandbox_engine": MagicMock(),
        "hook_runtime": MagicMock(),
        "tool_registry": MagicMock(),
        "event_emitter": MagicMock(),
    }
    components["permission_engine"].check = AsyncMock(
        return_value=PermissionDecision(
            verdict=PermissionVerdict.ALLOW,
            category="file_write",
            description="ok",
            allow_permanent=False,
        )
    )
    components["sandbox_engine"].select = AsyncMock(
        return_value=MagicMock(
            sandbox_type="none",
            filesystem_policy=MagicMock(),
            network_policy="allow_all",
        )
    )
    components["hook_runtime"].run = AsyncMock()
    components["hook_runtime"].run_with_outcome = AsyncMock(
        return_value=HookOutcome.continue_()
    )
    components["event_emitter"].emit = AsyncMock()

    tool_mock = MagicMock()
    tool_mock.execute = AsyncMock(return_value="Successfully wrote 5 bytes")
    components["tool_registry"].get.return_value = tool_mock

    orch = ToolOrchestrator(
        permission_engine=components["permission_engine"],
        sandbox_engine=components["sandbox_engine"],
        hook_runtime=components["hook_runtime"],
        tool_registry=components["tool_registry"],
        event_emitter=components["event_emitter"],
    )

    ctx = ToolExecutionContext(
        tool_name="write_file",
        tool_call_id="call-1",
        arguments={"path": "/tmp/x.txt", "content": "hi"},
        turn_id="turn-1",
        thread_id="thread-1",
        agent_type="main",
        session_id="desktop:test",
    )
    await orch.execute(ctx)

    kwargs = tool_mock.execute.call_args.kwargs
    assert "_user_roots" not in kwargs
