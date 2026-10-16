"""2026-10-16 实锤回归:面板派发的修复子代理作业,其回合 workspace 必须来自会话。

现场:桥日志 56 条「SURE hook ingest 退出码 5 (未写入)」——修复子代理的每个
工具事件(Pre+Post)全部落空。根因:`run_agent_job` 从模型 provider 上
`getattr(self._provider, "workspace", Path("."))` 取工作区——模型 provider
没有该属性(见 miqi/providers/base.py),退回 `Path(".")`;序列化进 SURE 事件
就是相对根 `"."`,SURE 按纪律整体拒收,原文:

    The project directory was given as ".", which is not an absolute path.

于是 AI 干活的工具级证据一条都进不了库(声称核查因此无据可依)。
"""

from types import SimpleNamespace

import pytest


class _ProviderWithoutWorkspace:
    """模型 provider:有 get_default_model,没有 workspace(与真实 provider 一致)。"""

    def get_default_model(self) -> str:
        return "test-model"

    async def chat(self, *args, **kwargs):  # pragma: no cover - 不应被真的调用
        raise AssertionError("回归测试不应真的发起模型调用")


def _runner(tmp_path, **overrides):
    from miqi.runtime.turn_runner import TurnRunner

    kwargs = dict(
        provider=_ProviderWithoutWorkspace(),
        tool_runtime=None,
        context_runtime=None,
        event_emitter=None,
        max_iterations=1,
        capability_resolver=None,
        ledger_runtime=None,
        hooks=None,
        workspace=tmp_path,
    )
    kwargs.update(overrides)
    return TurnRunner(**kwargs)


@pytest.mark.asyncio
async def test_agent_job_turn_workspace_comes_from_the_runner(tmp_path):
    from miqi.runtime.turn_runner import TurnResult

    runner = _runner(tmp_path)
    captured: dict = {}

    async def _fake_run(**kwargs):
        captured["turn"] = kwargs["turn"]
        return TurnResult(final_content="done", messages=[], tools_used=[])

    runner.run = _fake_run  # type: ignore[method-assign]

    job = SimpleNamespace(
        job_id="j1",
        agent_type="code-agent",
        thread_id="t1",
        task="fix it",
        user_roots=None,
    )
    await runner.run_agent_job(job)

    turn = captured["turn"]
    assert str(turn.workspace) == str(tmp_path), (
        f"作业回合 workspace 应来自 TurnRunner 注入({tmp_path}),实际 {turn.workspace!r}"
    )
    assert str(turn.workspace) != ".", "绝不能是相对根 '.'(会被 SURE 整体拒收)"


@pytest.mark.asyncio
async def test_agent_job_without_injected_workspace_keeps_a_sane_default(tmp_path):
    """未注入 workspace(旧构造方/测试替身)时维持既有缺省,不炸。"""
    from miqi.runtime.turn_runner import TurnResult

    runner = _runner(tmp_path, workspace=None)
    captured: dict = {}

    async def _fake_run(**kwargs):
        captured["turn"] = kwargs["turn"]
        return TurnResult(final_content="done", messages=[], tools_used=[])

    runner.run = _fake_run  # type: ignore[method-assign]

    job = SimpleNamespace(
        job_id="j2", agent_type="code-agent", thread_id="t2", task="t", user_roots=None
    )
    await runner.run_agent_job(job)
    assert captured["turn"].workspace is not None
