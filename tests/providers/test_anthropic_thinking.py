# -*- coding: utf-8 -*-
"""回归：AnthropicProvider._parse_response 必须把 thinking 块搬进 reasoning_content。

背景（实机复现）：平台 AI 网关是 Anthropic Messages 兼容，实机返回
``block types: ['thinking', 'text']``；而 ``_parse_response`` 只认 ``text`` /
``tool_use``，thinking 被整块丢弃 → ``LLMResponse.reasoning_content`` 为空 →
turn_runner（``response.reasoning_content or 累积 deltas``）拿不到思考内容 →
前端 ThinkBlock ``if (!reasoning && !children) return null`` 整体不渲染，
表现为"思考过程不显示"。

口径对齐 Hermes：``agent/chat_completion_helpers.py`` 的 ``thinking_delta`` →
``fire_reasoning_delta``（thinking 文本走 reasoning 通道）。
"""
from types import SimpleNamespace

from miqi.providers.anthropic_provider import AnthropicProvider


def _provider() -> AnthropicProvider:
    return AnthropicProvider(
        api_key="test-key",
        api_base="http://127.0.0.1:9",
        default_model="deepseek/deepseek-v4-flash",
    )


def _response(blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, usage=None)


def test_thinking_block_fills_reasoning_content():
    out = _provider()._parse_response(
        _response(
            [
                SimpleNamespace(type="thinking", thinking="先算 17*20，再加 17*3。"),
                SimpleNamespace(type="text", text="391"),
            ]
        )
    )
    assert out.reasoning_content == "先算 17*20，再加 17*3。"
    assert out.content == "391"


def test_multiple_thinking_blocks_join_and_redacted_is_ignored():
    out = _provider()._parse_response(
        _response(
            [
                SimpleNamespace(type="thinking", thinking="A"),
                SimpleNamespace(type="redacted_thinking", data="opaque"),
                SimpleNamespace(type="thinking", thinking="B"),
                SimpleNamespace(type="text", text="ok"),
            ]
        )
    )
    assert out.reasoning_content == "A\nB"


def test_no_thinking_keeps_reasoning_none():
    out = _provider()._parse_response(_response([SimpleNamespace(type="text", text="hi")]))
    assert out.reasoning_content is None


def test_thinking_with_tool_use_keeps_tool_call_and_finish_reason():
    out = _provider()._parse_response(
        _response(
            [
                SimpleNamespace(type="thinking", thinking="需要先查资料"),
                SimpleNamespace(type="tool_use", id="t1", name="web_search", input={"query": "MOF-5"}),
            ],
            stop_reason="tool_use",
        )
    )
    assert out.reasoning_content == "需要先查资料"
    assert [c.name for c in out.tool_calls] == ["web_search"]
    assert out.finish_reason == "tool_calls"
