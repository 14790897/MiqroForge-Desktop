---
name: legacy-main-path-only
description: 用户明确要求（2026-09-05 更正）：新功能只做 legacy 桌面主路径（miqi/runtime/）——KUN runtime 未接入主执行路径，不作为开发目标
type: feedback
---

用户指示（2026-09-05 更正）：**新功能只做 legacy 路径**（`miqi/runtime/` 桌面主路径）。

背景：项目存在两套运行时——legacy 路径（miqi/runtime/task_runner.py + TurnRunner + ToolRegistry + miqi/agent/user_input_resolver.py 的桌面 resolver 桥）和 KUN runtime（miqi/kun_runtime/，AgentLoop + gate）。桌面 chat.send 实际一直走 legacy 路径；KUN 的循环引擎 AgentLoop 没有任何实例化调用，未接入主执行路径（见 [confirm-card-two-runtime-map](confirm-card-two-runtime-map.md) 的 2026-09-02 复核）。2026-08-15 曾指示"新功能只做 KUN、legacy 不再维护"，2026-09-05 更正为相反方向：只做 legacy。

**Why:** KUN runtime 始终未接入主执行路径，桌面实际主路径是 miqi/runtime/——只做 legacy 才能保证新功能真实生效。

**How to apply:**
- 新功能/新修复默认只做 legacy 主路径（miqi/runtime/task_runner.py、turn_runner.py、agent/user_input_resolver.py、agent/tools 的 resolver 注入）
- KUN runtime（miqi/kun_runtime/）未接入主执行路径，不作为新功能开发目标（被主路径复用的组件除外，如 user_input_resolver.py 借用的 UserInputGate 共享确认门）
- 涉及"桌面路径 vs KUN 路径"的讨论时，以 legacy 桌面主路径为准；相关背景见 [confirm-card-two-runtime-map](confirm-card-two-runtime-map.md)
