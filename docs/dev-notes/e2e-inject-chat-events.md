---
name: e2e-inject-chat-events
description: "E2E 测试前端聊天渲染契约可用主进程 webContents.send('chat:progress') 注入事件，需挂起 mock 保持回合 in-flight"
type: project
---

前端聊天渲染契约的 E2E 不必走真实 LLM/沙箱：从 Playwright 的 `electronApp.evaluate` 里用 `BrowserWindow.getAllWindows().find(w => w.getTitle() === 'MiQroForge Desktop').webContents.send('chat:progress', payload)` 注入后端形状的 progress 事件即可（preload 原样转发，无校验）。

关键坑：
1. **前端只在回合进行中注册 chat 事件监听**（`unsubsRef.current = [unsubProgress...]` 在 handleSend 内注册）——空闲状态注入无人接收。解法：`scripts/mock_hang.py`（永不响应的 provider mock）让真实 send 后回合保持 in-flight，再注入。
2. **`createNewConversation(page)` 返回会话 TITLE 不是 session_key**——progress 事件按 session_key 过滤（`data.session_key` 必须等于 `currentSessionRef.current`），要用 `window.miqi.sessions.list()` 按标题匹配或取 created_at 最新的拿真实 key。
3. danger 色断言：红框文字色 `rgb(255, 97, 97)`（暗色 #ff6161）/ `rgb(192, 64, 64)`（亮色 #c04040），遍历元素 getComputedStyle 比对即可。
4. ToolErrorEvent 载荷形状：`{event: 'ToolErrorEvent', data: {turn_id, tool_name, tool_call_id, message, recoverable}, session_key}`（loop.py 泛化分支 asdict 后转发）。

实例见 `apps/desktop/tests/e2e/tool-error-neutral.spec.ts`（#921）。相关：[confirm-card-issue-714-fix](confirm-card-issue-714-fix.md)、[macos-ci-local-mock-server](macos-ci-local-mock-server.md)
