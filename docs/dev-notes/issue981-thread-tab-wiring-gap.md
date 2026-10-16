---
name: issue981-thread-tab-wiring-gap
description: "子线程 tab 原本无生产者（agent:spawned 无人发送）；已在 PR #1218 接线（d7701d04），合并前 develop 仍走不到"
type: project
---


**原缺口（2026-09-29 核查）**：`agent:spawned` / `agent:completed` 只有订阅方，主进程从不发送；后端真实事件 `sub_agent_spawned`（miqi/runtime/agent_control.py）在 bridge drain 里只落进「其余事件当 progress 透出」兜底，渲染层无消费者。→ tab 列表恒为 `['main']`，#981「切任务不中断」的用户路径不可达，其 e2e `issue-981-task-parallel.spec.ts` 靠注入 `agent:spawned` 构造场景。

**现状（2026-09-29 下午）**：维护者选定方案 a，接线已在 PR #1218 完成，待合并：

- `d7701d04` 转发 `sub_agent_spawned/completed` → `agent:spawned/completed`（走 emit_client_event 孤儿通路，带 session_key）；
- `fed3af48` 这两个事件不再当 progress 透出（去掉聊天里的裸事件名噪声）；
- `d9139102` 按所属会话落盘（后台会话的子智能体也留 tab）；
- 测试：macOS 守卫（a97f50a2）、真模型+真子智能体+零注入 e2e（c5e21a33，已标注「不构成回归判别」）、mock 注入版仍是回归判别用例；
- CI 在 head `d9139102` 上全绿（含 electron-e2e / macos-e2e / wsl-e2e）。

**How to apply:** 判断「tab 能不能出现」前先看 PR #1218 是否已合入 develop——合并前 develop 上仍是走不到的状态。相关 [e2e-inject-chat-events](e2e-inject-chat-events.md)、[verify-fix-in-real-app-not-just-mocks](verify-fix-in-real-app-not-just-mocks.md)、[plain-language-communication](plain-language-communication.md)。
