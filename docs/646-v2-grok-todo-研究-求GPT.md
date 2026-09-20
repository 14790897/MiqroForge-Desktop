# Grok Build todo 系统研究 & #646-v2 吸收建议（2026-08-18，求 ChatGPT 评审）

> 研究来源：`D:\Desktop\duibi\grok\grok-build`（Grok Build = xAI 终端 AI coding agent
> 的 Rust 源码，crates/codegen/xai-grok-tools/.../grok_build/todo 1007 行 + scheduler 440 行）。
> 用户指示：这个方面是**能力本身的拓展**，不是 UI。
> 问题：我们的 PlanCard/Timeline 步骤列表"像 todo"——Grok 的 todo 系统值得吸收什么？

---

## 一、Grok todo 系统核心设计

### 1. todo_write 工具（模型主动维护任务列表）

```rust
/// 描述（工具原文）：
/// "Create and manage a structured task list. The user sees this list live —
/// it is your primary way to show progress.
/// Use for any task with 3+ steps. Skip for trivial single-step work."
```
- **模型是 todo 的所有者**：创建/更新/标记进度都由模型调 `todo_write` 完成
- **用户实时可见**：列表是模型展示进度的主要方式（不是聊天文本）
- **3+ 步骤才用**（小任务跳过——与我们的复杂度阈值同理）
- **is_read_only: true**（写 todo 不算危险操作——不触发审批/确认）

### 2. merge 增量更新（最有价值的设计）

```rust
pub struct TodoWriteInput {
    pub merge: bool,   // 默认 true
    pub todos: Vec<TodoUpdate>,  // {id, content?, status?}
}
```
- 模型**只发变化项**：翻转状态 = `{id, status}`（content 可省略）
- **自动升级**：模型忘写 `merge:true` 但意图明显（所有更新项都指向已有 id 且无 content）
  → 自动按 merge 处理（容错）
- 整体替换 = 显式 `merge:false`

### 3. 持久化资源

```rust
#[derive(Default, Serialize, Deserialize)]
pub struct TodoState { todos: IndexMap<TodoId, TodoItem> }
crate::register_resource!("grok_build", "Todo", TodoState);
```
- 跨调用/跨回合存活（serde 持久化）——不是 UI 状态
- IndexMap = **有序**（id 顺序即展示顺序）

### 4. TodoItem 结构

```rust
pub struct TodoItem {
    pub content: String,
    pub priority: TodoPriority,   // high / medium(默认) / low
    pub status: TodoStatus,       // pending / in_progress / completed / cancelled
    pub meta: Option<serde_json::Value>,  // 附加 JSON
}
```

### 5. summary_for_prompt（上下文管理）

- 工具结果回传的是 **summarize_todo_state 摘要**（不是整个列表）
- 防长任务上下文膨胀（模型不需要每次都看全部 todo）

### 6. 错误处理

- 重复 ID → `TodoWriteOutput::DuplicateId("...")`（**error-as-output**——
  模型可区分业务错误与基础设施错误）

---

## 二、Grok scheduler（调度器——附带发现）

`ScheduledTask`：interval_secs + prompt + recurring + durable + expires_at（循环任务
7 天过期）+ last_subagent_id（任务跑在子代理）+ **iterations_since_fresh** +
`LOOP_FRESH_CHAIN_EVERY = 10`（**每 10 次迭代开新 transcript**——防上下文膨胀）。
Actor + mpsc 命令通道（Create/Update/Delete/List）。

→ 与我们 #646 无关（那是定时/循环任务），但 "fresh transcript" 思路可参考长任务。

---

## 三、与 #646-v2 现状对比

| 维度 | Grok todo | 我们（#646-v2） | 差距 |
|---|---|---|---|
| 步骤/列表所有者 | **模型**（todo_write 工具主动维护） | **harness**（从工具序列一次性生成） | 模型不知道自己"宣称"的步骤；进度更新靠 harness 猜（ToolCall 事件） |
| 更新方式 | **merge 增量**（只发变化） | 无（modify 循环 = 重发全部） | 费 token、易错 |
| 结果回传 | **summary**（摘要） | 全量 | 长任务上下文膨胀 |
| 持久化 | 资源级（跨会话） | 会话内 UI 状态 | 任务列表不持久 |
| 优先级 | high/medium/low | 无 | 可加 |
| 3+ 步骤门槛 | 有 | 有（复杂度阈值） | 一致 ✓ |
| 状态 4 态 | pending/in_progress/completed/cancelled | 同 | 一致 ✓ |
| 只读 | is_read_only: true（不审批） | 确认类工具 ALLOW ✓ | 一致 ✓ |

---

## 四、吸收方案（三档，请拍板）

### 方案 A（推荐）：todo_write 工具 + 模型主动进度

**做法**：
1. 新增 `todo_write` 工具（Grok 语义：merge 增量 / 3+ 步骤门槛 / is_read_only / summary 回传）
2. **PlanCard 确认后**：harness 不再从工具序列生成步骤——改为**引导模型用 todo_write 建立步骤**（提示词注入："确认计划后先用 todo_write 列出步骤，每完成一步标记 in_progress/completed"）
3. **Timeline 数据源切换**：从 harness 静态步骤 → todo 状态（模型更新即反映——进度**由模型负责**（准确），harness 只管安全边界）
4. merge 语义：modify 循环 = 模型只发变化的步骤

**价值**：进度准确（模型知道自己在干嘛）；省 token（增量更新+摘要）；任务列表可持久化（后续）
**风险**：模型不可靠（忘调/调错）→ harness 仍需兜底（现有 ToolCall 事件进度保留为 fallback）；todo 与 PlanCard 步骤一致性需校验

### 方案 B（轻）：只吸收 merge + summary

现有 harness 步骤更新改 merge 语义（只发变化）+ 工具结果摘要化。不做模型主动维护。
**价值**：省 token、上下文管理；改动小
**风险**：进度仍靠 harness 猜（模型不准确）

### 方案 C（完整）：A + 持久化

方案 A + TodoState 注册为持久化资源（跨会话存活——右侧"任务资产"面板显示 todo）。
**价值**：任务列表可追溯/跨会话
**风险**：会话系统改造（大）；与现有任务（sessions）系统关系需设计

---

## 五、待 ChatGPT 评审的问题

1. **方案选哪个**？A / B / C？
2. A 方案中"模型主动 vs harness 兜底"的边界：进度展示以 todo 为准（模型负责），
   但模型忘调 todo_write 时 Timeline 显示什么？（空？还是 fallback 到工具事件？）
3. todo_write 与现有 ask_user_plan_confirm/PlanCard 的关系：
   - PlanCard 确认后模型立刻 todo_write 建立列表（两阶段：确认 → 列表）？
   - 还是 PlanCard 步骤本身就是 todo 的初始值（harness 把计划步骤预填进 todo，
     模型后续增量更新）？——**后者更像 Grok**（列表一直在，模型更新）
4. summary_for_prompt 的摘要格式（模型回传什么：`3/5 完成 · 进行中: 生成报告`？）
5. todo 与 Timeline 合并还是并行？（Timeline = todo 的 UI 渲染？——若是，Timeline
   组件不再需要 stepStatus 事件驱动——直接读 todo 状态）
6. modify 循环与 merge：用户改计划 → 模型 merge 更新 todo（只发变化）→ PlanCard
   重渲染？——需要 todo 变更事件（或 PlanCard 读同一 todo 状态）
7. 优先级（high/medium/low）要不要？（我们 PlanCard 无优先级概念）
8. 持久化（方案 C）与现有 sessions 系统（任务列表）的归属：todo 挂在 session 下？
   还是独立资源？（Grok 是全局资源——跨任务共享？）
