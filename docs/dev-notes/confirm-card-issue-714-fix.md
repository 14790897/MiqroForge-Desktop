---
name: confirm-card-issue-714-fix
description: "issue #714 修复要点 — 最终方案为同回合并发确认卡排队串行（PR #718）；#716 的拒绝方案被用户否定并替换"
type: project
---

issue #714（确认卡堆叠/僵尸卡）分两个 PR：

**#716（已合入，部分被 #718 替换）**：前端 `resolved:false` → backendReleased（不恢复 pending，消除点击后反弹的僵尸卡循环，前端 IPC 异常恢复时需先删 resolved 副本避免双渲染）+ KUN 复核（dispatch 串行 + loop finally 对超时/取消/异常均发 resolved 事件）。gate 的"同回合并发拒绝"方案被用户否定——真实 LLM 一次发 3 张卡时后 2 张被自动取消，用户失去确认机会。

**#718（最终方案）：同回合并发确认卡排队串行**：
- `UserInputGate.request()` 增加 `on_pending` 异步回调：每回合 FIFO 槽位队列（`_turn_queues`，**新队列必须预置一个槽位 token**，否则第一个 `queue.get()` 空队列永久阻塞——踩过坑）；请求获取槽位、注册 pending 后才触发 on_pending；释放时 `put_nowait(None)` + `queue.empty()` 时清理字典项
- KUN loop 与 legacy resolver 的 `user_input_requested` 宣告（item 应用/事件/waiting_for_user 状态）全部移入 `on_pending`——排队中的卡不渲染，前端任意时刻只看到一张卡
- 前端 backendReleased 保留作超时/回合终止竞态兜底

**测试布局**：
- `TestGateTurnQueue`（同回合并发排队不堆叠、前一卡释放后第二张自动挂起获独立 slot、on_pending 仅在真正挂起时触发、跨回合并发允许）+ `test_concurrent_cards_emit_one_at_a_time`（legacy 并发双卡：第二张事件在第一张 resolve 后才发出）
- E2E 双卡用例（mock "双卡"分支）：第二张 `toBeHidden` 排队断言 → 取消第一张 → 第二张弹出 → 两张均正常取消、无"后端已释放" → 回合完成；负向验证（gate 临时恢复拒绝逻辑）确认该用例失败
- E2E 断言注意：卡片取消后标题出现在 resolved 折叠区（cardArea 范围内仍可见）——断言"离开 pending"应查 resolvedArea 可见 + `等待你的选择` count，而非 cardArea hidden

**踩坑**：
- mock 循环变量遮蔽：`for tc in m["tool_calls"]` 遮蔽同名 `tc()` helper → `TypeError: 'dict' object is not callable`（修复：改名 call）
- playwright webServer 3458 端口残留 → taskkill 后重试；长页面 fullPage 截图 30s 超时 → 加 timeout: 60_000；双 worker 并行两 Electron 实例 sendMessage 易 flake → `--workers=1`
- `*.png` 被 gitignore，截图 `git add -f` 入 `docs/screenshots/<feature>/`，PR body 用 `https://github.com/14790897/MiqroForge-Desktop/raw/<branch>/docs/screenshots/...` 引用

相关：[confirm-card-two-runtime-map](confirm-card-two-runtime-map.md)、[legacy-main-path-only](legacy-main-path-only.md)
