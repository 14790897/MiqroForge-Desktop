"""#981 接线：子智能体的 spawn / 完成事件必须投给创建它的客户端。

渲染层的线程 tab 由 `IPC_EVENTS.AGENT_SPAWNED`（`agent:spawned`）建立
（ChatConsole 的 `agents.onSpawned` 是 `addThreadTab` 的唯一调用点），但这条
事件此前**没有任何产出方**：Python 侧发的是 `sub_agent_spawned`，主进程的
chat 事件转发白名单里也没有它 —— 于是 `threadState.tabs` 永远是 `['main']`，
tab 栏（`threads.length > 1` 才渲染）永远不出现。

本文件锁住 drain 侧的转投（事件名必须与渲染层的 IPC 名一致，否则主进程按名推
通道时推不出来）：

  sub_agent_spawned   → `agent:spawned`   （带 session_key，渲染层按会话过滤 tab）
  sub_agent_completed → `agent:completed`

走的必须是 `emit_client_event`（按 client 直投）而不是 `emit_event`（按会话
订阅扇出）：子智能体的完成事件常常在主回合结束之后才到，那时订阅与 chat.send
请求都已消失，只有直投通路收得到。
"""

import pytest

from miqi.bridge.loop import BridgeRuntimeLoop
from miqi.protocol.events import (
    SubAgentCompletedEvent,
    SubAgentSpawnedEvent,
    TurnCompleteEvent,
)


class _FakeAppServer:
    def __init__(self):
        self.emitted: list[tuple[str, object, str]] = []
        self.client_emitted: list[tuple[str, str, dict]] = []

    async def emit_event(self, session_id, event_type, data, request_id=None):
        self.emitted.append((event_type, data, session_id))

    async def emit_client_event(self, client_id, event_type, data, request_id=None):
        self.client_emitted.append((client_id, event_type, dict(data)))


class _BoomAppServer(_FakeAppServer):
    async def emit_client_event(self, client_id, event_type, data, request_id=None):
        raise RuntimeError("sink gone")


class _FakeRuntime:
    """按顺序吐出给定事件；吐完返回 None（drain 随即收尾）。"""

    def __init__(self, events):
        self._events = list(events)

    async def next_event(self, timeout=None):
        return self._events.pop(0) if self._events else None


def _drain_kwargs(runtime):
    return dict(
        request_id="req1",
        runtime=runtime,
        thread_id="sess",
        session_id="c:sess",
        client_id="c",
        session_key="sess",
    )


@pytest.mark.asyncio
async def test_spawned_event_is_forwarded_as_agent_spawned():
    loop = BridgeRuntimeLoop(send_func=lambda msg: None)
    app = _FakeAppServer()
    loop._app_server = app
    runtime = _FakeRuntime(
        [
            SubAgentSpawnedEvent(
                parent_turn_id="t1",
                sub_agent_id="a1",
                sub_thread_id="sub-a1",
                agent_type="code-agent",
                task_label="汇总 sales.csv",
            ),
            TurnCompleteEvent(turn_id="t1", thread_id="sess", outcome="success"),
        ]
    )

    await loop._drain_chat_events(**_drain_kwargs(runtime))

    forwarded = [e for e in app.client_emitted if e[1] == "agent:spawned"]
    assert len(forwarded) == 1, f"spawn 必须转投一次，实际 {app.client_emitted}"
    client_id, _event_type, data = forwarded[0]
    assert client_id == "c"
    assert data["sub_agent_id"] == "a1"
    assert data["sub_thread_id"] == "sub-a1"
    assert data["agent_type"] == "code-agent"
    assert data["task_label"] == "汇总 sales.csv"
    assert data["session_key"] == "sess", "渲染层按会话过滤 tab，必须带上 session_key"


@pytest.mark.asyncio
async def test_completed_event_is_forwarded_as_agent_completed():
    loop = BridgeRuntimeLoop(send_func=lambda msg: None)
    app = _FakeAppServer()
    loop._app_server = app
    runtime = _FakeRuntime(
        [
            SubAgentCompletedEvent(
                sub_agent_id="a1",
                sub_thread_id="sub-a1",
                outcome="success",
                summary="已生成待办清单",
            ),
            TurnCompleteEvent(turn_id="t1", thread_id="sess", outcome="success"),
        ]
    )

    await loop._drain_chat_events(**_drain_kwargs(runtime))

    forwarded = [e for e in app.client_emitted if e[1] == "agent:completed"]
    assert len(forwarded) == 1, f"complete 必须转投一次，实际 {app.client_emitted}"
    _client_id, _event_type, data = forwarded[0]
    assert data["sub_thread_id"] == "sub-a1"
    assert data["outcome"] == "success"
    assert data["summary"] == "已生成待办清单"
    assert data["session_key"] == "sess"


@pytest.mark.asyncio
async def test_forwarding_failure_does_not_break_the_turn():
    """投递失败不能让回合挂掉：tab 是增强，不是主流程。"""
    loop = BridgeRuntimeLoop(send_func=lambda msg: None)
    loop._app_server = _BoomAppServer()
    runtime = _FakeRuntime(
        [
            SubAgentSpawnedEvent(
                parent_turn_id="t1",
                sub_agent_id="a1",
                sub_thread_id="sub-a1",
                agent_type="code-agent",
                task_label="x",
            ),
            TurnCompleteEvent(turn_id="t1", thread_id="sess", outcome="success"),
        ]
    )

    # 不抛即通过：drain 正常收尾（终态由 _send 发出）
    await loop._drain_chat_events(**_drain_kwargs(runtime))
