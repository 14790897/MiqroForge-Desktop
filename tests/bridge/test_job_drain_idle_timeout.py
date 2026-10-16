"""作业事件 drain 的空闲上限(2026-10-16 用户机实测回归)。

现场:面板派发的修复子代理在模型调用里静默 600s(网关非流式回退,已知可达
值),作业 drain 沿用聊天同款空闲上限(600s)中途收摊;此后会话事件队列
(4096 有界)没有消费者——作业一旦恢复输出,填满即冻死,正是这条 drain
当初要防的形态。修复:作业 drain 带自己的、长于看门狗兜底阈值(900s)的
空闲上限;此处按仓库"阈值政策测试"的写法把这条约束钉住。
"""

import asyncio

import pytest


class _CaptureSend:
    def __init__(self):
        self.messages: list[dict] = []

    def send(self, data: dict) -> None:
        self.messages.append(data)


class _FakeAppServer:
    def __init__(self):
        self.subscribed: list[tuple[str, str]] = []

    def subscribe(self, client_id, session_id):
        self.subscribed.append((client_id, session_id))


def test_the_job_drain_idle_cap_outlasts_any_legal_job_silence():
    """作业 drain 的空闲上限必须长于"合法静默"的最坏情形。

    合法静默的边界是 agent_jobs 看门狗兜底阈值(实测最坏的一次网关非流式
    回退可达 600s,阈值 15×60s=900s 之内的静默都算合法)——上限低于它,
    慢但正干活的作业就会在中途丢掉事件消费者(4096 队列冻死形态)。
    """
    import inspect

    from miqi.bridge.loop import (
        CHAT_DRAIN_IDLE_TIMEOUT_SECONDS,
        JOB_DRAIN_IDLE_TIMEOUT_SECONDS,
    )
    from miqi.runtime.agent_jobs import AgentJobRuntime

    params = inspect.signature(AgentJobRuntime.__init__).parameters
    watchdog = params["stall_interval"].default * params["stall_samples"].default
    assert JOB_DRAIN_IDLE_TIMEOUT_SECONDS > watchdog, (
        f"job drain idle cap ({JOB_DRAIN_IDLE_TIMEOUT_SECONDS:.0f}s) does not outlast "
        f"the watchdog's legal-silence bound ({watchdog:.0f}s): a working-but-slow job "
        "would lose its event consumer mid-turn (the 4096-queue freeze the drain exists "
        "to prevent)"
    )
    assert JOB_DRAIN_IDLE_TIMEOUT_SECONDS > CHAT_DRAIN_IDLE_TIMEOUT_SECONDS, (
        "job drains must carry their own, longer cap than chat drains — the chat cap "
        "is a deliberate per-request UX timeout and is far shorter than a job's legal "
        "model wait"
    )


@pytest.mark.asyncio
async def test_job_drain_registers_with_the_job_idle_cap():
    """面板派发的作业 drain 注册时必须带作业自己的空闲上限(不是聊天同款),
    且沿用合成 request_id 与终态静音约定。"""
    from miqi.bridge.loop import JOB_DRAIN_IDLE_TIMEOUT_SECONDS, BridgeRuntimeLoop

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=lambda *a, **k: None,
    )
    loop._app_server = _FakeAppServer()

    captured: dict = {}

    async def spy(**kwargs):
        captured.update(kwargs)

    loop._drain_chat_events = spy  # type: ignore[method-assign]

    await loop._register_agent_job_drain(
        runtime=object(),
        thread_id="t1",
        session_id="s1",
        session_key="k1",
        client_id="c1",
        agent_id="agent-x",
    )
    # 同步面:终态静音预置 + 订阅补齐
    assert "sure-job-agent-x" in loop._terminal_sent
    assert loop._app_server.subscribed == [("c1", "s1")]

    await asyncio.sleep(0)  # 让 drain 任务(此处为 spy)跑起来
    assert captured["request_id"] == "sure-job-agent-x"
    assert captured["idle_timeout"] == JOB_DRAIN_IDLE_TIMEOUT_SECONDS
    assert captured["session_id"] == "s1"
