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

**与执行策略的关系（#1102）**：`check()` 的顺序是 `deny_patterns` → `INTERACTIVE_CONFIRM_TOOLS` → `_action_guard` → `bypass_approval` → `force_approval` → 其余分类审批。

- **`bypass_approval` 不跳过 guard**。auto（和 plan）模式由执行策略**自动**置位该标志（`task_runner.py:559-589` 是桌面主路径；`turn_runner.py` 那份在 `run_agent_job` 里、`execution_policy` 硬编码 `"edit"`，实际不可达）。用户在选择「自动」时授权的是「普通动作免确认」，不是「高危动作免兜底」——见 `docs/design-646-v2-plan-card.md:43`「auto ≠ root，危险边界仍在」。曾有一版实现把 bypass 短路放在 guard 之前，等于 auto 下 guard 永不执行。
- **「纯手动」（`force_approval` 且非 `bypass_approval`）下 guard 主动让位**：那里每个动作本来就要确认，两张卡对同一个动作没有增量安全性；而且 guard 卡面「同类动作不再逐一询问」的会话缓存语义在 manual 下并不成立（guard 弃权后 `force_approval` 会再次拦下），叠加反而让卡面文案失真。guard 在代码里之所以位于 `force_approval` **之前**，只是被「必须早于 `bypass_approval`」这条约束逼出来的位置，不是要抢手动模式的确认。
- **bypass 仍优先于 force，但不得借道 force 跳过 guard**：两个标志理论上互斥（没有任何模式同时置位）。若同时置位，普通动作照旧由 `bypass_approval` 放行（`test_ep_bypass_wins_over_force` / `test_bypass_beats_force` 用的是 `exec`，risk=5，guard 本就弃权）；而**高危动作仍会被 guard 拦下**。让位条件因此写的是「force 且非 bypass」而不是「force」——后者会让 bypass 经由 force 这个入口重新绕开 guard，正是 #1102 要堵的语义。
- **guard 覆盖面的真实边界**：`should_confirm_action` 只认 `TOOL_RISK` 里 risk>=10 的名字，而其中真实注册的工具目前**只有 `spawn`**（`upload` / `delete_file` / `send_message` / `payment` 等无对应工具类；真实外发工具是 `message`，risk=2 进不了 guard）。所以顺序修好之后，auto 下 guard 实际能拦到的也只有 `spawn`——其余高危通路（如 `exec` 跑 `upload_run.py`）不在 guard 覆盖内，那是 #1101 的议题。

**上界**：`_MAX_ACTION_GUARD_CONFIRMED = 512`，缓存超限清空重建（防长会话无界增长）。清空的最坏后果是**多弹卡**，方向安全——只可能多问，不可能少问。
