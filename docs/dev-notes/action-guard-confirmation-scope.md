---
name: action-guard-confirmation-scope
description: "Action Guard 确认范围口径 — 判定看参数、缓存按 thread+tool、卡面必须明示；参数摘要方案已否（产品拍板 2026-09-16）"
type: project
---

Action Guard（`miqi/execution/permission_engine.py::_action_guard`）的确认范围，三条不变式：

1. **判定看参数**：是否需确认由 `task_policy.should_confirm_action(tool_name, arguments)` 逐次判定——风险分 + 敏感路径 + 破坏性删除判定都吃 arguments。同一次工具调用是否危险，永远按当次参数算。
2. **缓存按 thread+tool**：`_action_guard_confirmed` 的键是 `f"{thread_id}:{tool_name}"`，**不看参数**。同一 thread 内同一工具确认一次后，后续同类动作不再逐一询问；跨 thread / 换工具各自弹卡。放行通道还有 family 级跳过（`task_policy.action_family` × `ctx.action_confirmed_families`，模型侧 ActionCard 已确认时避免双卡）。
3. **明示义务**：卡面 message 必须写明「（确认后本对话内同类动作将不再逐一询问）」。缓存语义变了而文案不变，就是在用户不知情下扩大授权——文案与键控口径必须同时改。

**决策记录（产品拍板 2026-09-16）**：曾评估「安全参数摘要（thread+tool+args digest）」方案——即参数变化就重新确认，授权更贴合单次动作。因摩擦未采纳（同一 thread 内连续上传/删除会反复弹卡），最终采用更宽松的 thread+tool 口径；安全性由此前提下移给「判定看参数」+「卡面明示」。

**边界**：`_action_guard_confirmed` 是安全层兜底缓存，≠ `session_allowlist` / `permanent_allowlist`（用户显式「允许并记住」：`PermissionEngine._make_key` 键控、参数变了 key 就变；写入侧在 `kun_runtime/loop.py::_remember_key`，`allow_remember_choice=True` 才走）。两套机制别混改。

**上界**：`_MAX_ACTION_GUARD_CONFIRMED = 512`，缓存超限清空重建（防长会话无界增长）。清空的最坏后果是**多弹卡**，方向安全——只可能多问，不可能少问。
