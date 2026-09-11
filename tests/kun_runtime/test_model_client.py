"""Phase 5 tests — ModelClient adapter and FakeModelClient."""

from __future__ import annotations

import pytest

from miqi.kun_runtime.model_client import (
    FakeModelClient,
    MiQiModelClient,
    ModelRequest,
    ModelToolSpec,
    _build_messages,
    _build_tools,
    _item_to_message,
)
from miqi.providers.base import LLMProvider, LLMResponse, ToolCallRequest

# ═══════════════════════════════════════════════════════════════════════════════
# FakeModelClient tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFakeModelClient:
    @pytest.mark.asyncio
    async def test_no_tools_text_only(self) -> None:
        client = FakeModelClient(text_chunks=["Hello, world!"])
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake")
        chunks = [c async for c in client.stream(req)]
        kinds = [c.kind for c in chunks]
        assert "assistant_text_delta" in kinds
        assert "completed" in kinds
        assert chunks[-1].stopReason == "stop"

    @pytest.mark.asyncio
    async def test_with_tool_calls(self) -> None:
        client = FakeModelClient(
            text_chunks=["Let me check..."],
            tool_calls=[{"id": "call_1", "name": "read", "arguments": {"path": "test.txt"}}],
        )
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake")
        chunks = [c async for c in client.stream(req)]
        kinds = [c.kind for c in chunks]
        assert "tool_call_complete" in kinds
        assert chunks[-1].stopReason == "tool_calls"

    @pytest.mark.asyncio
    async def test_with_reasoning(self) -> None:
        client = FakeModelClient(
            reasoning_chunks=["Let me think..."],
            text_chunks=["Answer"],
        )
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake")
        chunks = [c async for c in client.stream(req)]
        kinds = [c.kind for c in chunks]
        assert "assistant_reasoning_delta" in kinds
        assert "assistant_text_delta" in kinds

    @pytest.mark.asyncio
    async def test_with_usage(self) -> None:
        client = FakeModelClient(
            text_chunks=["Done"],
            usage={"promptTokens": 100, "completionTokens": 50, "totalTokens": 150},
        )
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake")
        chunks = [c async for c in client.stream(req)]
        usage_chunks = [c for c in chunks if c.kind == "usage"]
        assert len(usage_chunks) == 1
        assert usage_chunks[0].usage == {"promptTokens": 100, "completionTokens": 50, "totalTokens": 150}

    @pytest.mark.asyncio
    async def test_error(self) -> None:
        client = FakeModelClient(error="provider down", error_code="API_ERROR")
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake")
        chunks = [c async for c in client.stream(req)]
        assert len(chunks) == 1
        assert chunks[0].kind == "error"
        assert chunks[0].message == "provider down"

    @pytest.mark.asyncio
    async def test_records_requests(self) -> None:
        client = FakeModelClient(text_chunks=["OK"])
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake", temperature=0.1)
        async for _ in client.stream(req):
            pass
        assert len(client._requests) == 1
        assert client._requests[0].thread_id == "th1"

    @pytest.mark.asyncio
    async def test_multiple_text_chunks(self) -> None:
        client = FakeModelClient(text_chunks=["Part 1 ", "Part 2 ", "Part 3"])
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake")
        chunks = [c async for c in client.stream(req)]
        text_chunks = [c.text for c in chunks if c.kind == "assistant_text_delta"]
        assert text_chunks == ["Part 1 ", "Part 2 ", "Part 3"]


# ═══════════════════════════════════════════════════════════════════════════════
# Message conversion tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestMessageConversion:
    def test_user_message_item(self) -> None:
        item = {"id": "u1", "kind": "user_message", "text": "hello"}
        msg = _item_to_message(item)
        assert msg is not None
        assert msg["role"] == "user"
        assert msg["content"] == "hello"

    def test_assistant_text_item(self) -> None:
        item = {"id": "a1", "kind": "assistant_text", "text": "response"}
        msg = _item_to_message(item)
        assert msg is not None
        assert msg["role"] == "assistant"
        assert msg["content"] == "response"

    def test_tool_call_item(self) -> None:
        item = {
            "id": "tc1", "kind": "tool_call",
            "toolName": "read", "callId": "call_1",
            "arguments": {"path": "test.txt"},
        }
        msg = _item_to_message(item)
        assert msg is not None
        assert msg["role"] == "assistant"
        assert msg["tool_calls"][0]["function"]["name"] == "read"

    def test_tool_result_item(self) -> None:
        item = {
            "id": "tr1", "kind": "tool_result",
            "toolName": "read", "callId": "call_1",
            "output": "file content",
        }
        msg = _item_to_message(item)
        assert msg is not None
        assert msg["role"] == "tool"
        assert msg["content"] == "file content"

    def test_error_item(self) -> None:
        item = {"id": "e1", "kind": "error", "message": "something failed"}
        msg = _item_to_message(item)
        assert msg is not None
        assert msg["role"] == "system"
        assert "failed" in msg["content"]

    def test_compaction_item(self) -> None:
        item = {"id": "c1", "kind": "compaction", "summary": "Earlier conversation"}
        msg = _item_to_message(item)
        assert msg is not None
        assert msg["role"] == "system"
        assert msg["content"] == "Earlier conversation"


class TestBuildMessages:
    def test_empty_request(self) -> None:
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake")
        msgs = _build_messages(req)
        assert msgs == []

    def test_system_prompt(self) -> None:
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake", system_prompt="You are helpful.")
        msgs = _build_messages(req)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"

    def test_context_instructions(self) -> None:
        req = ModelRequest(
            thread_id="th1", turn_id="t1", model="fake",
            system_prompt="You are helpful.",
            context_instructions=["Skill: code-review"],
        )
        msgs = _build_messages(req)
        assert len(msgs) == 1
        assert "Skill: code-review" in msgs[0]["content"]

    def test_history_items(self) -> None:
        req = ModelRequest(
            thread_id="th1", turn_id="t1", model="fake",
            history=[
                {"id": "u1", "kind": "user_message", "text": "hello"},
                {"id": "a1", "kind": "assistant_text", "text": "hi"},
            ],
        )
        msgs = _build_messages(req)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_mode_instruction(self) -> None:
        req = ModelRequest(
            thread_id="th1", turn_id="t1", model="fake",
            system_prompt="Base prompt",
            mode_instruction="Plan mode enabled.",
        )
        msgs = _build_messages(req)
        assert "Plan mode enabled" in msgs[0]["content"]


class TestBuildTools:
    def test_converts_tool_specs(self) -> None:
        specs = [
            ModelToolSpec(
                name="read",
                description="Read a file",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            ),
        ]
        result = _build_tools(specs)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "read"

    def test_empty_tools(self) -> None:
        assert _build_tools([]) == []


# ═══════════════════════════════════════════════════════════════════════════════
# MiQiModelClient integration test (pseudo-streaming with FakeProvider)
# ═══════════════════════════════════════════════════════════════════════════════


class FakeProvider(LLMProvider):
    """A provider that returns configurable responses."""

    def __init__(
        self,
        content: str = "",
        reasoning: str | None = None,
        tool_calls: list[dict] | None = None,
        usage: dict | None = None,
        finish_reason: str = "stop",
        raise_error: Exception | None = None,
    ):
        super().__init__()
        self._content = content
        self._reasoning = reasoning
        self._raw_tool_calls = tool_calls or []
        self._usage = usage
        self._finish_reason = finish_reason
        self._raise_error = raise_error

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        if self._raise_error:
            raise self._raise_error
        return LLMResponse(
            content=self._content,
            tool_calls=[ToolCallRequest(**tc) for tc in self._raw_tool_calls],
            finish_reason=self._finish_reason,
            usage=self._usage or {},
            reasoning_content=self._reasoning,
        )

    def get_default_model(self) -> str:
        return "fake-model"


class TestMiQiModelClient:
    @pytest.mark.asyncio
    async def test_text_only(self) -> None:
        provider = FakeProvider(content="Hello from MiQi!")
        client = MiQiModelClient(provider)
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake")
        chunks = [c async for c in client.stream(req)]
        kinds = [c.kind for c in chunks]
        assert "assistant_text_delta" in kinds
        assert "completed" in kinds
        text = next(c.text for c in chunks if c.kind == "assistant_text_delta")
        assert text == "Hello from MiQi!"

    @pytest.mark.asyncio
    async def test_with_reasoning(self) -> None:
        provider = FakeProvider(content="Answer", reasoning="Hmm...")
        client = MiQiModelClient(provider)
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake")
        chunks = [c async for c in client.stream(req)]
        reasoning = [c for c in chunks if c.kind == "assistant_reasoning_delta"]
        assert len(reasoning) == 1
        assert reasoning[0].text == "Hmm..."

    @pytest.mark.asyncio
    async def test_with_tool_calls(self) -> None:
        provider = FakeProvider(
            content="Let me read that.",
            tool_calls=[{"id": "call_1", "name": "read", "arguments": {"path": "a.txt"}}],
        )
        client = MiQiModelClient(provider)
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake")
        chunks = [c async for c in client.stream(req)]
        tc_chunks = [c for c in chunks if c.kind == "tool_call_complete"]
        assert len(tc_chunks) == 1
        assert tc_chunks[0].toolName == "read"
        complete = [c for c in chunks if c.kind == "completed"]
        assert complete[0].stopReason == "tool_calls"

    @pytest.mark.asyncio
    async def test_with_usage(self) -> None:
        provider = FakeProvider(
            content="Done",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )
        client = MiQiModelClient(provider)
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake")
        chunks = [c async for c in client.stream(req)]
        usage = [c for c in chunks if c.kind == "usage"]
        assert len(usage) == 1
        assert usage[0].usage["promptTokens"] == 100

    @pytest.mark.asyncio
    async def test_provider_error(self) -> None:
        provider = FakeProvider(raise_error=RuntimeError("connection refused"))
        client = MiQiModelClient(provider)
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake")
        chunks = [c async for c in client.stream(req)]
        assert len(chunks) == 1
        assert chunks[0].kind == "error"
        assert "connection refused" in chunks[0].message

    @pytest.mark.asyncio
    async def test_api_error_finish_reason(self) -> None:
        provider = FakeProvider(content="API Error", finish_reason="error")
        client = MiQiModelClient(provider)
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake")
        chunks = [c async for c in client.stream(req)]
        assert chunks[0].kind == "error"

    @pytest.mark.asyncio
    async def test_passes_tools_to_provider(self) -> None:
        provider = FakeProvider(content="OK")
        client = MiQiModelClient(provider)
        req = ModelRequest(
            thread_id="th1", turn_id="t1", model="fake",
            tools=[ModelToolSpec(name="read", description="Read file", input_schema={"type": "object"})],
        )
        chunks = [c async for c in client.stream(req)]
        kinds = [c.kind for c in chunks]
        assert "completed" in kinds  # didn't crash, provider handled the tools arg


class RecordingProvider(FakeProvider):
    """FakeProvider 子类：记录实际发给 provider.chat() 的 messages 列表。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.received: list[dict] | None = None

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        self.received = messages
        return await super().chat(messages, tools=tools, model=model,
                                  max_tokens=max_tokens, temperature=temperature)


# ═══════════════════════════════════════════════════════════════════════════════
# KUN pre-send guard 结构完整性（issue #753 同类：孤儿 tool → API 400）
# ═══════════════════════════════════════════════════════════════════════════════


class TestKunPreSendGuard:
    @staticmethod
    def _pair_history() -> list[dict]:
        """user → assistant(tool_call, 大 arguments) → tool_result(小) 成对序列，
        assistant 体积大保证「单独弹掉一个 assistant 就跌破限额」可触发。"""
        history: list[dict] = []
        for k in range(30):
            history.append({"id": f"u{k}", "kind": "user_message",
                            "text": f"用户消息{k} " + "好" * 400})
            history.append({"id": f"tc{k}", "kind": "tool_call", "summary": None,
                            "callId": f"c{k}", "toolName": "exec",
                            "arguments": {"data": "好" * 600}})
            history.append({"id": f"tr{k}", "kind": "tool_result", "callId": f"c{k}",
                            "toolName": "exec", "output": "结" * 30})
        history.append({"id": "ulast", "kind": "user_message", "text": "最后一个用户消息"})
        return history

    @staticmethod
    def _assert_structurally_valid(msgs: list[dict]) -> None:
        """无孤儿 tool（每个 tool 前必须有包含其 id 的 assistant tool_calls）、
        无未响应 tool_calls（与主 runtime 回归测试同一校验）。"""
        pending: set[str] = set()
        for m in msgs:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                pending.update(tc["id"] for tc in m["tool_calls"])
            elif m.get("role") == "tool":
                assert m.get("tool_call_id") in pending, f"孤儿 tool: {m.get('tool_call_id')}"
                pending.discard(m.get("tool_call_id"))
        assert not pending

    @pytest.mark.asyncio
    async def test_trim_orphan_tool_is_pruned_before_provider(self, monkeypatch) -> None:
        """回归（#753 同类）：KUN pre-send trim 逐条弹出可能留下孤儿 tool，
        发送给 provider 前必须成对裁剪。"""
        from miqi.kun_runtime.context_estimator import estimate_tokens as est_fn

        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake",
                           system_prompt="系统提示", history=self._pair_history())
        built = _build_messages(req)

        # 找一个「恰好弹出 a0、t0、a1 后跌破限额」的限额 L：
        # 此时 t1(c1) 的 assistant 已被删而 tool 残留 → 孤儿。
        # 注意：真实 trim 逐条弹出 a0→t0→a1（跳过 user 消息），第三次弹的是
        # a1 而非 index 1 处（此时是 u1）——按 tool_call_id 精确构造
        # after2/after3（CodeRabbit #771 评审：索引 pop(1) 会因列表移位删错消息）。
        def _asst_id(m: dict) -> str | None:
            tcs = m.get("tool_calls") or []
            return tcs[0]["id"] if tcs else None

        after2 = [
            m for m in built
            if _asst_id(m) != "c0" and m.get("tool_call_id") != "c0"
        ]  # 删掉 a0 + t0
        after3 = [m for m in after2 if _asst_id(m) != "c1"]  # 再删掉 a1
        e2 = est_fn(str(after2))
        e3 = est_fn(str(after3))
        assert e2 > e3
        safe_limit = (e2 + e3) // 2
        monkeypatch.setattr(
            "miqi.kun_runtime.context_estimator.get_safe_context_limit",
            lambda model: safe_limit,
        )

        provider = RecordingProvider(content="done")
        client = MiQiModelClient(provider)
        chunks = [c async for c in client.stream(req)]
        assert any(c.kind == "completed" for c in chunks)
        msgs = provider.received
        assert msgs is not None
        assert len(msgs) < len(built)  # trim 确实发生了
        assert len(msgs) == len(after3) - 1  # 恰好裁掉那 1 条孤儿 tool
        self._assert_structurally_valid(msgs)
        assert not any(
            m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in msgs
        )

    @pytest.mark.asyncio
    async def test_prunes_orphan_even_without_trim(self) -> None:
        """未超限也执行结构裁剪（#761 教训）：畸形组可独立于超限存在。"""
        history = [
            {"id": "u1", "kind": "user_message", "text": "hi"},
            {"id": "tc1", "kind": "tool_call", "summary": None, "callId": "c1",
             "toolName": "exec", "arguments": {}},
            {"id": "tr1", "kind": "tool_result", "callId": "c1", "toolName": "exec",
             "output": "ok"},
            {"id": "orphan", "kind": "tool_result", "callId": "c9", "toolName": "exec",
             "output": "no assistant"},  # 孤儿 tool_result
            {"id": "u2", "kind": "user_message", "text": "again"},
        ]
        provider = RecordingProvider(content="done")
        client = MiQiModelClient(provider)
        req = ModelRequest(thread_id="th1", turn_id="t1", model="fake",
                           system_prompt="系统提示", history=history)
        chunks = [c async for c in client.stream(req)]
        assert any(c.kind == "completed" for c in chunks)
        msgs = provider.received
        assert msgs is not None
        # 默认限额下 est 极小 → 无超限 trim，但孤儿 tool_result 仍被丢弃
        assert not any(
            m.get("role") == "tool" and m.get("tool_call_id") == "c9" for m in msgs
        )
        # 完整 c1 组不受影响
        assert any(
            m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in msgs
        )
        self._assert_structurally_valid(msgs)
