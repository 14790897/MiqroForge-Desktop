"""Tests for streaming delta and tool lifecycle ledger recording (Phase 24.4)."""

import pytest


@pytest.mark.asyncio
async def test_streaming_deltas_are_recorded_in_ledger(fake_config, tmp_path):
    from miqi.protocol.commands import UserMessage
    from miqi.protocol.events import TurnCompleteEvent
    from miqi.providers.base import LLMResponse, LLMStreamEvent
    from miqi.runtime.session import RuntimeSession

    class Provider:
        def get_default_model(self):
            return "test-model"

        async def chat(self, **kwargs):
            return LLMResponse(content="hello", finish_reason="stop")

        async def stream_chat(self, **kwargs):
            yield LLMStreamEvent(kind="content_delta", delta="hel")
            yield LLMStreamEvent(kind="content_delta", delta="lo")
            yield LLMStreamEvent(
                kind="completed",
                response=LLMResponse(content="hello", finish_reason="stop"),
            )

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=Provider(),
        session_id="sess-ledger-delta",
        workspace=tmp_path,
    )
    await runtime.start()
    try:
        await runtime.submit(UserMessage(content="hi", thread_id="thread-delta"))
        while True:
            event = await runtime.next_event(timeout=2)
            if isinstance(event, TurnCompleteEvent):
                break

        items = await runtime.services.ledger_runtime.load_items("thread-delta")
        deltas = [item for item in items if item.item_type == "assistant_delta"]

        assert [item.content for item in deltas] == ["hel", "lo"]
        assert [item.payload["index"] for item in deltas] == [0, 1]
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_tool_calls_are_recorded_in_ledger(fake_config, tmp_path):
    from miqi.protocol.commands import UserMessage
    from miqi.protocol.events import TurnCompleteEvent
    from miqi.providers.base import LLMResponse, LLMStreamEvent, ToolCallRequest
    from miqi.runtime.session import RuntimeSession

    class Provider:
        def __init__(self):
            self.calls = 0

        def get_default_model(self):
            return "test-model"

        async def chat(self, **kwargs):
            return LLMResponse(content="done", finish_reason="stop")

        async def stream_chat(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield LLMStreamEvent(
                    kind="completed",
                    response=LLMResponse(
                        content="",
                        finish_reason="tool_calls",
                        tool_calls=[
                            ToolCallRequest(
                                id="call-1",
                                name="read_file",
                                arguments={"path": "missing.txt"},
                            )
                        ],
                    ),
                )
            else:
                yield LLMStreamEvent(
                    kind="completed",
                    response=LLMResponse(content="done", finish_reason="stop"),
                )

    runtime = RuntimeSession.create(
        config=fake_config,
        provider=Provider(),
        session_id="sess-ledger-tools",
        workspace=tmp_path,
    )
    await runtime.start()
    try:
        await runtime.submit(UserMessage(content="read file", thread_id="thread-tools"))
        while True:
            event = await runtime.next_event(timeout=2)
            if isinstance(event, TurnCompleteEvent):
                break

        items = await runtime.services.ledger_runtime.load_items("thread-tools")
        types = [item.item_type for item in items]

        assert "tool_call_started" in types
        assert "tool_call_completed" in types
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_mcp_download_turn_ledger_and_model_never_see_base64(fake_config, tmp_path):
    """#975 全链断言：真实 TurnRunner 一轮下载后，模型消息 / ledger / 事件
    都只见摘要，base64 绝不出现在 ctx.result 之后的任何出口。"""
    import base64
    import hashlib
    import json
    from types import SimpleNamespace

    from mcp import types as mcp_types

    from miqi.agent.tools.mcp import MCPToolWrapper
    from miqi.protocol.commands import UserMessage
    from miqi.protocol.events import TurnCompleteEvent
    from miqi.providers.base import LLMResponse, LLMStreamEvent, ToolCallRequest
    from miqi.runtime.session import RuntimeSession

    # decoded ≈ 1.6 MiB 事故级负载
    payload_bytes = bytes((i * 7 + 3) % 256 for i in range(int(1.6 * 1024 * 1024)))
    payload_b64 = base64.b64encode(payload_bytes).decode()
    payload_sha = hashlib.sha256(payload_bytes).hexdigest()
    response_json = json.dumps(
        {
            "name": "secret.cube",
            "size_bytes": len(payload_bytes),
            "sha256": payload_sha,
            "content_base64": payload_b64,
        }
    )

    class FakeMcpSession:
        async def call_tool(self, name, arguments=None, progress_callback=None):
            return SimpleNamespace(
                isError=False,
                structuredContent=None,
                content=[mcp_types.TextContent(type="text", text=response_json)],
            )

    class Provider:
        def __init__(self):
            self.seen_messages: list = []
            self.calls = 0

        def get_default_model(self):
            return "test-model"

        async def chat(self, **kwargs):
            self.seen_messages.append(kwargs.get("messages", []))
            return LLMResponse(content="done", finish_reason="stop")

        async def stream_chat(self, **kwargs):
            self.calls += 1
            self.seen_messages.append(kwargs.get("messages", []))
            if self.calls == 1:
                yield LLMStreamEvent(
                    kind="completed",
                    response=LLMResponse(
                        content="",
                        finish_reason="tool_calls",
                        tool_calls=[
                            ToolCallRequest(
                                id="dl-1",
                                name="mcp_miqroforge_download_file",
                                arguments={"name": "secret.cube"},
                            )
                        ],
                    ),
                )
            else:
                yield LLMStreamEvent(
                    kind="completed",
                    response=LLMResponse(content="done", finish_reason="stop"),
                )

    provider = Provider()
    # mcp 工具默认需审批：本测试旁路审批（真实用户侧由现有审批生命周期把关，
    # 不在 #975 范围内）；permanent_approvals 一并写入以贴合桌面配置形态。
    fake_config.agents.permanent_approvals.extend(
        ["mcp_miqroforge_download_file", "mcp_*"]
    )
    fake_config.approvals.bypass_all = True
    fake_config.agents.command_approval.enabled = False
    runtime = RuntimeSession.create(
        config=fake_config,
        provider=provider,
        session_id="sess-975-download",
        workspace=tmp_path,
    )
    await runtime.start()
    try:
        tool_def = SimpleNamespace(
            name="download_file",
            description="download remote artifact",
            inputSchema={"type": "object", "properties": {}},
        )
        wrapper = MCPToolWrapper(
            FakeMcpSession(), "miqroforge", tool_def,
            tool_timeout=5, progress_interval=0, base_workspace=tmp_path,
        )
        runtime.services.tool_registry.register(wrapper)

        await runtime.submit(UserMessage(content="download it", thread_id="thread-975"))
        for _ in range(60):
            event = await runtime.next_event(timeout=1)
            if isinstance(event, TurnCompleteEvent):
                break

        # 1) 落盘成功且 sha 一致（事故路径端到端）
        target = tmp_path / ".miqi" / "downloads" / "secret.cube"
        assert target.read_bytes() == payload_bytes

        # 2) ledger tool_call_completed 只含摘要：base64 与原始负载绝不出现在任何
        #    一个 ledger item 的 payload/result 里
        items = await runtime.services.ledger_runtime.load_items("thread-975")
        completed = [it for it in items if it.item_type == "tool_call_completed"]
        assert completed, "ledger 缺少 tool_call_completed"
        all_items = [
            {"payload": it.payload, "content": it.content}
            for it in items
            if isinstance(it.payload, dict)
        ]
        joined = json.dumps(all_items, ensure_ascii=False)
        assert payload_b64 not in joined
        result_payload = json.loads(completed[0].payload["result"])
        assert result_payload["type"] == "download_artifact"
        assert result_payload["sha256"] == payload_sha

        # 3) 模型可见消息（第二次 chat 的 tool role 内容）只含摘要
        tool_message_contents = []
        for msgs in provider.seen_messages:
            for m in msgs:
                if isinstance(m, dict) and m.get("role") == "tool":
                    tool_message_contents.append(m.get("content", ""))
        assert tool_message_contents, "模型未收到 tool 消息"
        for content in tool_message_contents:
            assert payload_b64 not in content
            assert content.strip().startswith('{"type": "download_artifact"')
    finally:
        await runtime.stop()
