---
name: 1094-truncated-tool-args-refusal
description: #1094 输出上限截断的工具参数——provider 标记 ToolCallRequest.truncated + legacy 拒执与回显防孤儿 + 纯文本截断留痕
type: project
---

2026-09-16，#1094（分支 `fix/1094-max-tokens-truncation`）修「模型单次输出被
`max_tokens` 砍断 → 工具参数只剩半截 JSON → 被 `json_repair` 打捞后**照常执行**」。
本文记录**怎么标、谁拒执、哪些情况仍不覆盖**。

## 病根：json_repair 把截断伪装成"能解析"

工具参数的容错链两层都做了 repair：

- `openai_provider._parse_tool_call_arguments`：严格 `json.loads` 失败 → `json_repair.loads` 兜底；
- `anthropic_provider._parse_response`：字符串 `input` 走同样的 `json.loads` → `json_repair` 路径。

repair 对「模型手滑写了单引号」是救命的，对「被 `max_tokens` 砍在半句」则是**危险打捞**：
半截路径、半截 SQL、半截文件内容都会变成一个"看起来合法"的 dict，然后真实执行。
所以判据不是"能不能解析"，而是**严格解析是否失败 + 本轮是否被输出上限截断**。

## 三处改动

| 位置 | 改动 | 对应 |
|---|---|---|
| `miqi/providers/base.py` | `ToolCallRequest` 增字段 `truncated: bool = False` | 字段定义（**不是** `ToolCall` 类，仓库无此类） |
| `miqi/providers/openai_provider.py` | `_args_strict_ok` + `_parse_response`/流式路径打标 | S1 |
| `miqi/providers/anthropic_provider.py` | 同口径 `_args_strict_ok` + `_parse_response` 打标 | S3 |
| `miqi/runtime/turn_runner.py` | 拒执 + 回显防孤儿 + 纯文本截断留痕 | S2 |

**标记口径（两条链路逐字一致）**：

```python
truncated = (
    finish_reason == "length"            # anthropic 侧：stop_reason == "max_tokens"
    and isinstance(raw_args, str)        # dict = SDK 已解析完，无"截断"可言
    and bool(raw_args)                   # 空串窗口不标（见"不覆盖"）
    and not _args_strict_ok(raw_args)    # 严格 json.loads 失败
)
```

`_args_strict_ok` 只对**非空字符串**要求严格解析通过，其余（`None` / dict / 空串）一律视为可接受。
打标时补 warning（`tool args truncated by output cap ...`，含工具名）：**anthropic 侧**另带参数串前
200 字符；**openai 侧**（`chat` 与流式路径各一条）只列被标记调用的工具名。
**`json_repair` 行为一行没动**——残片仍被打捞进 `arguments`，供上层展示和拒执文案使用。

### 为什么 anthropic 那条是桌面生产路径

本机桌面走 **网关 → `AnthropicProvider`**，S1 只补了 `openai_provider`，
所以网关路径此前完全没标记：桌面上的截断调用依旧会执行。
anthropic 侧另有一处结构性坑：`stop_map`/`finish_reason` 原本在 `for block in response.content`
循环**之后**才算，而 `tool_use` 分支在循环内就要用 `finish_reason` 判截断——
必须把 `stop_map` 与 `finish_reason` 上提到循环之前。`stream_chat` 只转发 `chat()`，无需改。

### legacy 侧：拒执 + 回执必须成对

`turn_runner` 在预算跳过（`_budget_skip_reason`）之后、真正执行之前过滤：

- `truncated=True` 的调用**不进执行列表**，改为塞一条合成结果
  （`OrchestrationResult.TOOL_ERROR`），文案含**真实 `max_tokens` 数值**并建议
  「分片写入 / 先写文件再传路径」；同时记 warning。
- **回显防孤儿**：被拒的调用仍要进 assistant 的 `tool_calls`（`_echo_calls`），
  否则它的拒绝 `tool_result` 会在发送前被孤儿清理剪掉，模型永远不知道为什么没执行——
  这与 #753 是同一类配对问题。`tools_used` 只记**真正执行**的（`_refused_ids` 排除）。
- **单轮混合**（审计 F6）：同一轮 1 枚截断 + 1 枚正常，互不牵连——正常的照常执行，
  截断的拒执；用例 `test_turn_runner_mixed_round_runs_only_intact_call` 锁住这一点。

### 纯文本截断：只留痕，不拦

工具调用被截断有拒执动作，**纯文本**被 `length` 截断则无事可拒——它是终答，
控制流上只能放行。此前这种情况**零留痕**（审计 F3）：用户看到半句话，
日志里查不到任何线索。现在在 `if not response.has_tool_calls:` 分支入口记 warning：

```
turn_runner: 输出被 max_tokens 截断（纯文本，无工具调用），内容不完整 (max_tokens=…) turn=…
```

**只加日志，不动控制流。** 落点注意：这条不能加在工具过滤逻辑附近——无工具调用的
响应在 `if not response.has_tool_calls:` 就走向终答返回了，工具分支根本不可达
（初版就踩了这个坑，测试直接抓到）。

## 不覆盖（已知缺口）

1. **空串参数窗口**：工具名已出、参数是空串 → `arguments` 落成 `{}`、照常执行，
   随后通常以「必填参数缺失」报错收场。这一窗口**不标截断**（判据明确要求非空串）。
2. **桌面横幅 / 技能侧自适应分片**：本轮只做"标记 + 拒执 + 留痕"，没有 UI 提示，
   也没有让 skill 自动改成分片写入——都是 follow-up。
3. **KUN runtime 不在本修复范围**：KUN 未接入主执行路径（见
   [legacy-main-path-only](legacy-main-path-only.md)），本次只改 legacy 主路径。

## 验证

- `tests/providers/test_anthropic_truncation.py`：`max_tokens`+残缺串→`True`；
  合法 JSON 串→`False`；`tool_use`/`end_turn`+残缺串→`False`；dict input→`False`；
  空串→`False`；text block 混排不干扰。
- `tests/runtime/test_turn_runner.py`：S2 拒执/回显、S3 混合轮次、纯文本截断 warning。

**How to apply:** 给 provider 加工具参数容错时，先想「这串是模型手滑，还是被输出上限砍的」——
`json_repair` 只该救前者，后者必须让**运行时**知道，因为 provider 层无权拒执。
新增任何"拒执/跳过"分支时，记得同时回答两个问题：**回执是否成对回注**（否则孤儿被剪，
模型学不到）、**`tools_used` 是否只记真正执行的**（否则统计与审计一起失真）。
