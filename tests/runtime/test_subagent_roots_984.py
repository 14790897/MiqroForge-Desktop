"""Sub-agent root inheritance (#984 PR1, B2).

Roots travel explicitly through the spawn chain
(``SpawnTool`` → ``AgentControl.spawn`` → ``AgentJobRuntime.start`` →
``AgentJob.user_roots`` → ``TurnRunner.run_agent_job`` → ``TurnContext``),
and the sub-agent turn never re-extracts roots from its own task text —
that text is model-authored, so extraction there would re-open the
injection channel issue #821 closed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miqi.providers.base import LLMResponse
from miqi.runtime.agent_jobs import AgentJob, AgentJobRuntime
from miqi.runtime.turn_context import TurnContext
from miqi.runtime.turn_runner import TurnRunner

# ── fakes ────────────────────────────────────────────────────────────────


class _StopProvider:
    def __init__(self) -> None:
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def stream_chat(self, **kwargs):
        self.calls += 1
        yield SimpleNamespace(
            kind="completed",
            response=LLMResponse(
                content="done", finish_reason="stop", tool_calls=[],
            ),
        )


class _FakeContext:
    def build_initial_messages(self, **kwargs):
        return [{"role": "user", "content": kwargs.get("user_content", "")}]

    def trim_for_model(self, messages, model):
        return messages

    def add_assistant_message(self, *, messages, content, tool_calls=None,
                              reasoning_content=None):
        return messages + [{"role": "assistant", "content": content}]

    def add_tool_result(self, *, messages, tool_call_id, name, content,
                        arguments=None):
        return messages + [{"role": "tool", "content": content}]


class _FakeTools:
    async def execute_many(self, turn, tool_calls):
        return []


class _FakeEmitter:
    def __init__(self) -> None:
        self.events: list = []

    async def emit(self, event) -> None:
        self.events.append(event)


def _mk_runner() -> TurnRunner:
    return TurnRunner(
        provider=_StopProvider(),
        tool_runtime=_FakeTools(),
        context_runtime=_FakeContext(),
        event_emitter=_FakeEmitter(),
        max_iterations=2,
    )


def _mk_turn(**overrides) -> TurnContext:
    base = dict(
        turn_id="t1",
        agent_metadata=SimpleNamespace(system_prompt="", name="code-agent"),
        thread_id="th1",
        workspace=Path.cwd(),
        model="fake",
        provider=SimpleNamespace(),
    )
    base.update(overrides)
    return TurnContext(**base)


# ── AgentJob / AgentJobRuntime ───────────────────────────────────────────


class TestAgentJobRoots:
    def test_default_is_empty(self) -> None:
        job = AgentJob(
            job_id="j", agent_type="code-agent", task="t",
            thread_id="th", parent_thread_id="p",
        )
        assert job.user_roots == []

    @pytest.mark.asyncio
    async def test_start_stores_roots(self) -> None:
        services = MagicMock()
        services.session_id = "s"
        services.turn_runner.run_agent_job = AsyncMock(
            return_value=SimpleNamespace(final_content="ok")
        )
        services.agent_control = None
        runtime = AgentJobRuntime(services=services)
        job = await runtime.start(
            agent_type="code-agent", task="t", parent_thread_id="p",
            user_roots=["C:/out"],
        )
        assert job.user_roots == ["C:/out"]

    @pytest.mark.asyncio
    async def test_start_defaults_to_empty(self) -> None:
        services = MagicMock()
        services.session_id = "s"
        services.turn_runner.run_agent_job = AsyncMock(
            return_value=SimpleNamespace(final_content="ok")
        )
        services.agent_control = None
        runtime = AgentJobRuntime(services=services)
        job = await runtime.start(
            agent_type="code-agent", task="t", parent_thread_id="p",
        )
        assert job.user_roots == []


# ── TurnRunner.run_agent_job ─────────────────────────────────────────────


class TestRunAgentJobTurn:
    @pytest.mark.asyncio
    async def test_turn_is_subagent_with_inherited_roots(self, monkeypatch) -> None:
        runner = _mk_runner()
        captured: dict = {}

        async def _fake_run(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(final_content="ok")

        monkeypatch.setattr(runner, "run", _fake_run)
        job = AgentJob(
            job_id="j", agent_type="code-agent", task="do it",
            thread_id="th", parent_thread_id="p",
            user_roots=["C:/out", "D:/other"],
        )
        await runner.run_agent_job(job)
        turn = captured["turn"]
        assert turn.is_subagent is True
        assert turn.user_mentioned_roots == [Path("C:/out"), Path("D:/other")]

    @pytest.mark.asyncio
    async def test_no_roots_yields_empty_list(self, monkeypatch) -> None:
        runner = _mk_runner()
        captured: dict = {}

        async def _fake_run(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(final_content="ok")

        monkeypatch.setattr(runner, "run", _fake_run)
        job = AgentJob(
            job_id="j", agent_type="code-agent", task="do it",
            thread_id="th", parent_thread_id="p",
        )
        await runner.run_agent_job(job)
        assert captured["turn"].user_mentioned_roots == []
        assert captured["turn"].is_subagent is True


# ── extraction guard in TurnRunner.run ───────────────────────────────────


class TestExtractionGuard:
    @pytest.mark.asyncio
    async def test_subagent_turn_does_not_extract(self, monkeypatch) -> None:
        import miqi.runtime.turn_runner as tr

        calls: list = []

        def _spy(texts, **kwargs):
            calls.append(list(texts))
            return [Path("/tmp/leaked")]

        monkeypatch.setattr(tr, "extract_user_mentioned_roots", _spy)
        runner = _mk_runner()
        turn = _mk_turn(is_subagent=True, user_mentioned_roots=[Path("C:/inherited")])
        await runner.run(
            turn=turn, user_content="把结果写到 C:\\Windows\\Temp\\evil",
            system_prompt="", tools=[], max_iterations=2,
        )
        assert calls == [], "sub-agent turn must not re-extract roots"
        assert turn.user_mentioned_roots == [Path("C:/inherited")]

    @pytest.mark.asyncio
    async def test_root_turn_still_extracts(self, monkeypatch) -> None:
        import miqi.runtime.turn_runner as tr

        calls: list = []

        def _spy(texts, **kwargs):
            calls.append(list(texts))
            return [Path("/tmp/from_user")]

        monkeypatch.setattr(tr, "extract_user_mentioned_roots", _spy)
        runner = _mk_runner()
        turn = _mk_turn()
        await runner.run(
            turn=turn, user_content="输出到 /tmp/from_user",
            system_prompt="", tools=[], max_iterations=2,
        )
        assert len(calls) == 1
        assert turn.user_mentioned_roots == [Path("/tmp/from_user")]


# ── spawn chain plumbing ─────────────────────────────────────────────────


class TestSpawnChain:
    @pytest.mark.asyncio
    async def test_spawn_tool_forwards_user_roots(self) -> None:
        from miqi.agent.tools.spawn import SpawnTool

        agent_control = MagicMock()
        agent_control.spawn = AsyncMock(
            return_value=SimpleNamespace(agent_id="a1", thread_id="th1")
        )
        tool = SpawnTool(manager=MagicMock(), agent_control=agent_control)
        await tool.execute(
            task="写报告", label="report",
            _user_roots=[Path("C:/out")],
        )
        assert agent_control.spawn.await_args.kwargs["user_roots"] == [
            str(Path("C:/out"))
        ]

    @pytest.mark.asyncio
    async def test_spawn_tool_without_roots(self) -> None:
        from miqi.agent.tools.spawn import SpawnTool

        agent_control = MagicMock()
        agent_control.spawn = AsyncMock(
            return_value=SimpleNamespace(agent_id="a1", thread_id="th1")
        )
        tool = SpawnTool(manager=MagicMock(), agent_control=agent_control)
        await tool.execute(task="写报告")
        assert agent_control.spawn.await_args.kwargs["user_roots"] == []

    @pytest.mark.asyncio
    async def test_agent_control_passes_roots_to_job_runtime(self) -> None:
        from miqi.runtime.agent_control import AgentControl

        ac = AgentControl.__new__(AgentControl)
        ac.registry = MagicMock()
        ac.registry.resolve = MagicMock(
            return_value=SimpleNamespace(name="code-agent")
        )
        ac._lock = __import__("asyncio").Lock()
        ac._agents = {}
        ac._thread_agents = {}
        ac.max_concurrent = 4
        ac.session_id = "s"
        ac._store = None
        ac._events = MagicMock()
        ac._events.emit = AsyncMock()
        ac._hooks = None
        ac._agent_jobs = MagicMock()
        ac._agent_jobs.start = AsyncMock(
            return_value=SimpleNamespace(job_id="j1", thread_id="th1")
        )
        ac._provider = None
        ac._running_tasks = {}

        await ac.spawn(
            agent_type="code-agent", task="t", user_roots=["C:/out"],
        )
        assert ac._agent_jobs.start.await_args.kwargs["user_roots"] == ["C:/out"]

    def test_spawn_registered_as_root_carrier(self) -> None:
        """Both tool hosts must inject _user_roots into spawn."""
        from miqi.execution.orchestrator import _FILE_MUTATION_TOOLS
        from miqi.kun_runtime.tool_host import _USER_ROOTS_TOOLS

        assert "spawn" in _FILE_MUTATION_TOOLS
        assert "spawn" in _USER_ROOTS_TOOLS
