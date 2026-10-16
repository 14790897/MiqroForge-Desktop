---
name: e2e-real-llm-hard-fail-rerun
description: "electron-e2e 的 real-LLM 用例偶发「全重试失败」阻塞 PR（回合超时/模型行为抖动）——处理=重跑失败 job，跟踪 issue #1193"
type: project
---


`electron-e2e`（Linux CI）里依赖真实 LLM 的用例会偶发连续 3 次尝试全败 → job failure、阻塞 PR。**当前处理方式：`gh run rerun <run-id> --failed`，实测都能通过**（不要为这类失败改代码）。同一天可能落在不同用例：issue-879-web-sources、full-electron 多轮记忆、guard-issue-811、issue-1061 产物账本。

**Why:** 2026-09-21～22 单日 4 例实测。失败形态固定两类：① `waitForResponseComplete: 回合在 120000ms/240000ms 内没有结束`；② 断言型（模型没按预期产出，如 web_search 未 emit 来源、回复文本不匹配正则）。与提交无关（同一提交另一次运行 success）。仓库 flaky 汇总步骤只覆盖「首败重试通过」，这类硬失败只出现在 Playwright 失败清单里。

**How to apply:** 撞上 electron-e2e 硬失败，先看失败用例是不是 real-LLM 类——是就直接重跑失败的 job，观察是否转绿；反复失败再按真实回归排查。收敛方向（超时/降级策略）跟踪 issue #1193；既有结论见 #1107（4 workers 非根因）。
