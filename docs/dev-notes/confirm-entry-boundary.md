---
name: confirm-entry-boundary
description: #646-v2 确认入口边界——危险动作/计划/结构化选择/系统兜底四个入口的分工、指令注入机制与三处代码锚点
type: project
---

# 确认入口边界（#646-v2）

模型侧只能通过**工具**触发确认；选错工具 = 用户看到错误的卡片，或干脆看不到卡。

## 入口边界表

| 场景 | 入口 | 说明 |
|---|---|---|
| 危险动作（upload / delete / payment / 高危 exec） | `request_action_confirmation` | 模型侧**唯一**入口，独立 ActionCard（目标/文件/大小/指纹） |
| 任务启动计划 | `ask_user_plan_confirm` | PlanCard（"你准备干什么"）；harness 达阈值也会强制弹 |
| 非危险的结构化选择 | `ask_user_confirm_card` | 通用选项卡（choices / 超时） |
| 系统兜底（模型没弹卡） | `permission_engine._action_guard` | **非模型入口**：按 permission_profile 拦截 |

两个确认工具同时列入"不可并行"与"风暴豁免"名单（阻塞型人机握手不得并行；反复弹同一张卡不该被熔断抑制）。

## 注入机制

共享助手 `miqi/agent/tools/confirm_instructions.py` 持有确认类工具文案，按**工具名逐项注入**系统提示词。新增/改名一个确认工具必须同步改这里，否则模型收到的是别的工具的说明。

## 三处代码锚点

- `miqi/runtime/agent_registry.py` — main agent `available_tools` allowlist：**不在名单里的工具，模型永远够不到**（本轮根因）
- `miqi/agent/tools/confirm_instructions.py` — 确认类指令注入
- `miqi/execution/permission_engine.py:151` `_action_guard` — 系统侧兜底

## 教训

**e2e mock 不校验工具清单，不能当作"暴露已生效"的证据**：mock 只回放预设的 tool_calls，allowlist 漏了工具照样全绿。暴露类改动必须用单测断言 `available_tools` 成员（见 `tests/runtime/test_agent_registry.py::test_main_agent_exposes_request_action_confirmation`）。

相关：[confirm-card-two-runtime-map](confirm-card-two-runtime-map.md)
