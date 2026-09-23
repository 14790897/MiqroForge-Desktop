---
name: confirm-card-two-runtime-map
description: ask_user_confirm_card 确认卡功能的双路径实现地图——KUN 与 legacy 各有哪些组件、真实流程的 thread_id≠session_key 等关键事实
type: project
---

确认卡功能（issue #646 / PR #711）的实现分布在两套运行时：

**KUN 路径**（`miqi/kun_runtime/`）：loop.py 的 `await_user_input` 闭包（waiting_for_user 回合状态、事件键 camelCase：inputId/timeoutSeconds/allowRememberChoice）、user_input_gate.py（按 input_id keyed 的 pending 请求，`request()` 必须收到已宣告的 input_id 否则桌面 resolve miss）、tool_host.py 的 collab gate 拦截（`_execute_user_confirm`）、contracts.py 的 UserInputItem（snake_case 字段）/UserInputRequestedEvent（camelCase 字段）。

**legacy 桌面路径**（桌面主路径，新功能只做这条，见 [legacy-main-path-only](legacy-main-path-only.md)）：task_runner.py（contextvar 注入 thread/turn + 提示词注入 ASK_USER_CONFIRM_INSTRUCTION）、agent/user_input_resolver.py（模块级 `_emitters` 按 session_key 注册 + `_thread_sessions` thread→session 映射 + `_gate` 共享实例）、bridge/loop.py（drain task 注册 emitter、userInput.resolve handler 带会话鉴权）、前端 UserInputContext/ConfirmCard。

**关键事实**（踩过的坑）：
- 真实流程的 `thread_id` 是 threads.start 铸造的 id（如 `thread-xxx`），≠ session_key（`desktop:default`）——emitter/鉴权必须经 thread→session 映射
- 桌面 E2E 用 scripts/mock_openai.py（SSE 状态机，随机端口 + stdout 就绪判定；macOS CI 需 skip，见 [macos-ci-local-mock-server](macos-ci-local-mock-server.md)）
- E2E 目录：apps/desktop/tests/e2e/confirm-card.spec.ts（mock 五轮）、confirm-card-real-llm.spec.ts（真实 LLM）

**2026-09-02 复核（重要更正）**：KUN 移植引擎（miqi/kun_runtime/）的循环引擎 AgentLoop **没有任何实例化调用**，未接入主执行路径。当前桌面聊天主路径是 miqi/runtime/ 的 RuntimeSession → TaskRunner → TurnRunner（回合执行器自实现模型-工具循环）。kun_runtime 唯一的外部消费者是 agent/user_input_resolver.py（借用 UserInputGate 作为共享确认门 + lazy import loop._remember_key）。写产品/技术文档时不要把「KUN runtime」写成已启用路径。
