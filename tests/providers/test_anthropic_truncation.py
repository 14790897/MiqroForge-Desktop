"""#1094 S3：anthropic（网关）路径识别被输出上限截断的工具参数。

桌面生产链路是 网关 → AnthropicProvider，故该路径必须与 openai_provider S1
同口径打标：stop_reason=max_tokens + 参数为非空字符串 + 严格 json.loads 失败
⇒ ToolCallRequest.truncated=True（arguments 只是 json_repair 的残片打捞）。
"""

from types import SimpleNamespace

from miqi.providers.anthropic_provider import AnthropicProvider

# 非空、且严格 json.loads 必然失败的残片（模型在 content 中途被 max_tokens 砍断）
TRUNCATED_ARGS = '{"path": "/tmp/a.txt", "content": "hello wor'
VALID_ARGS = '{"path": "/tmp/a.txt", "content": "hello world"}'


def _provider() -> AnthropicProvider:
    """_parse_response 只用静态方法，无需走 __init__（避免真实凭证/客户端）。"""
    return AnthropicProvider.__new__(AnthropicProvider)


def _text_block(text: str = "先说明一下") -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(
    input_: object,
    name: str = "write_file",
    id_: str = "toolu_1",
) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def _response(blocks: list, stop_reason: str) -> SimpleNamespace:
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def test_max_tokens_with_broken_json_string_is_flagged() -> None:
    """① max_tokens + 残缺 JSON 串 → truncated=True，且 json_repair 打捞照旧。"""
    resp = _response([_tool_use_block(TRUNCATED_ARGS)], "max_tokens")

    out = _provider()._parse_response(resp)

    assert out.finish_reason == "length"
    assert len(out.tool_calls) == 1
    call = out.tool_calls[0]
    assert call.truncated is True
    # json_repair 行为原样保留：残片仍被打捞成 dict 供上层展示/拒执说明
    assert isinstance(call.arguments, dict)


def test_max_tokens_with_valid_json_string_is_not_flagged() -> None:
    """② max_tokens 但参数是合法 JSON 串 → 未截断（模型只是恰好用完预算）。"""
    resp = _response([_tool_use_block(VALID_ARGS)], "max_tokens")

    out = _provider()._parse_response(resp)

    assert out.finish_reason == "length"
    assert out.tool_calls[0].truncated is False
    assert out.tool_calls[0].arguments == {
        "path": "/tmp/a.txt",
        "content": "hello world",
    }


def test_tool_use_stop_reason_with_broken_json_string_is_not_flagged() -> None:
    """③ stop_reason=tool_use + 残缺串 → 非 length，不能误标。"""
    resp = _response([_tool_use_block(TRUNCATED_ARGS)], "tool_use")

    out = _provider()._parse_response(resp)

    assert out.finish_reason == "tool_calls"
    assert out.tool_calls[0].truncated is False


def test_dict_input_is_not_flagged() -> None:
    """④ input 已是 dict（SDK 侧解析完成）→ 无"严格解析失败"可言，不标。"""
    resp = _response(
        [_tool_use_block({"path": "/tmp/a.txt", "content": "hi"})],
        "max_tokens",
    )

    out = _provider()._parse_response(resp)

    assert out.tool_calls[0].truncated is False


def test_empty_string_input_is_not_flagged() -> None:
    """空串参数窗口不属本修复范围（工具名已出、参数空串→按 {} 执行）。"""
    resp = _response([_tool_use_block("")], "max_tokens")

    out = _provider()._parse_response(resp)

    assert out.tool_calls[0].truncated is False
    assert out.tool_calls[0].arguments == {}


def test_text_block_mixed_in_does_not_interfere() -> None:
    """text block 混排：正文照旧拼接，只有被截断的那个 tool_use 被标记。"""
    resp = _response(
        [
            _text_block("我先写文件"),
            _tool_use_block(TRUNCATED_ARGS, id_="toolu_trunc"),
            _tool_use_block(VALID_ARGS, name="read_file", id_="toolu_ok"),
        ],
        "max_tokens",
    )

    out = _provider()._parse_response(resp)

    assert out.content == "我先写文件"
    flags = {(tc.id, tc.truncated) for tc in out.tool_calls}
    assert flags == {("toolu_trunc", True), ("toolu_ok", False)}


def test_end_turn_with_broken_json_string_is_not_flagged() -> None:
    """stop_reason=end_turn + 残缺串 → finish_reason=stop，同样不该误标。"""
    resp = _response([_tool_use_block(TRUNCATED_ARGS)], "end_turn")

    out = _provider()._parse_response(resp)

    assert out.finish_reason == "stop"
    assert out.tool_calls[0].truncated is False
