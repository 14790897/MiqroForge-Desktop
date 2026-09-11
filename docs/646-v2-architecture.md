# MiQi Agent Task Lifecycle 架构文档（#646-v2 v3.3 最终版）

> 日期：2026-08-19 · 分支：feature/646-v2-plan-card（PR #719）
> 设计来源：ChatGPT 三轮拍板（v3→v3.1→v3.2→v3.3）+ Grok Build todo 系统研究
> 定位：**Agent Task Lifecycle Protocol v1**——不是"计划卡组件"，是 MiQi 的任务层基础设施

---

## 一、核心模型（五个对象，职责严格分离）

```text
PlanSnapshot = 用户批准事实（authority）——不可变
TodoState    = Agent 语义状态（execution state）——可变，模型/harness 写入
ToolEvent    = Runtime 事实（factual）——工具真实执行结果
Timeline     = TodoState 的 projection——不拥有独立状态
ActionGuard  = 安全事实——看真实 ToolResult，不信 Todo 的 completed
```

**五条决策记录（挡架构漂移）**：
1. Todo ≠ Authority（Agent 报告的状态，不是安全/执行事实来源）
2. Plan ≠ Todo（PlanSnapshot 静态批准语义；TodoState 动态执行状态）
3. Todo ≠ Scope Expansion（辅助步骤允许；目标/核心步骤/数据范围/副作用必须重新确认）
4. Tool Events ≠ Todo（Runtime 事实 vs 语义描述——同入 TodoState，source 区分）
5. Timeline ≠ State（projection，不拥有独立任务状态）

## 二、架构图

```text
User → Mode
   ↓
Task（AgentRunContext——一次工作流执行实例，非 session）
   ├── PlanSnapshot（不可变）→ PlanCard → User Confirm（冻结）
   ├── TodoState（可变）→ Timeline（projection）
   │       ▲
   │  todo_write（model） / ToolEvent→observed（harness 兜底）
   ├── Permission/Safety
   └── ActionGuard → Execute（看真实 ToolResult）
```

## 三、数据模型（miqi/runtime/task_objects.py）

```python
TodoStatus = queued | in_progress | blocked | completed | cancelled
# QUEUED：已批准等待 Agent 开始（解决"确认后没动静"UX）

TodoItem { id, content, status, kind: plan|auxiliary|observed, source: model|harness,
           blocked_reason? }
# kind/source 是内部协议——前端 DTO 只暴露 {id, title, status}

TodoState { run_id, revision, items }
# revision 单调递增（并发/重复结果/stale 防护）

PlanSnapshot { plan_id, goal, steps[(id, content)], approved_scope, plan_version }
ApprovedScope { sources, artifacts[{type,name}], external_actions[{provider,operation}] }
# 结构化 scope——mutation detection 可比较（非字符串匹配）

AgentRunContext { run_id, session_key, plan_snapshot, todo_state, action_history }
```

## 四、状态机（transition validator）

```text
QUEUED → IN_PROGRESS → COMPLETED
        ↘ BLOCKED ⇄ IN_PROGRESS
任何状态 → CANCELLED
禁止：COMPLETED → IN_PROGRESS（除非人工）
```

## 五、todo_write 工具（模型进度协议）

```python
todo_write(todos: [TodoPatch], merge=True) → {status, summary, revision, rejected?}
# TodoPatch = {id, status} | {id, content, kind:"auxiliary", status?}
# 规则：
#  - merge 增量（只发变化）+ 自动升级（全指向已有 id 的状态翻转）
#  - plan item 只允许 status transition（禁删/改 content）
#    → error-as-output：PLAN_MUTATION_REQUIRES_CONFIRMATION + suggestion
#  - transition validator
#  - revision += 1
#  - summary_for_prompt（结构化：total/completed/in_progress/pending/blocked）
```

## 六、执行数据流（turn_runner）

```text
模型首轮 tool_calls
  → TaskPolicy.should_plan_confirm（复杂度：阶段跨类/artifact/skill；纯读不弹）
  → 弹 PlanCard → 用户确认
  → PlanSnapshot 冻结（步骤 slugify 稳定 id）+ TodoState 初始化（plan/QUEUED）
  → system 注入：已批准步骤 id 清单 + "用 todo_write 维护进度"（SHOULD 非 MUST）
  → 模型 todo_write 更新 / harness 工具事件写 observed（兜底——不允许空白）
  → todo_state 事件推前端（DTO 隔离）
  → ActionGuard（上传/删除/支付永远确认——看真实 ToolResult）
```

## 七、model-preferred, harness-backed（两级进度）

```text
Primary: TodoState（模型 todo_write——semantic）
Fallback: ToolCallBegin/End → TodoState observed 条目（source=harness——factual 兜底）
Timeline 永远只读 TodoState.items（单源——UI 无两个列表）
```

## 八、Frozen Plan + Mutable Todo 边界

```text
Todo 可以：✓ 改状态 ✓ 加辅助步骤(kind=auxiliary) ✓ cancel ✓ block(带 reason)
Todo 不可以：✗ 改用户批准的目标 ✗ 偷偷扩大数据范围 ✗ 新增外部副作用
            ✗ 代替 ActionGuard ✗ 代替 ToolResult
模型改 plan 步骤 content → 拒绝（PLAN_MUTATION_REQUIRES_CONFIRMATION → 重新 PlanCard）
```

## 九、实现状态（2026-08-19）

| 组件 | 文件 | 状态 |
|---|---|---|
| 数据模型 | `miqi/runtime/task_objects.py` | ✅ 10 测试 |
| todo_write 工具 | `miqi/agent/tools/todo_write.py` | ✅ 7 测试 |
| 确认冻结+初始化+注入 | `miqi/runtime/turn_runner.py` | ✅ 集成 6 测试 |
| todo_write 拦截（运行时） | `turn_runner.py` + `tool_registry_factory.py` | ✅ |
| observed 兜底（单源） | `turn_runner.py`（Begin/End 写） | ✅ |
| todo_state 事件（DTO） | `turn_runner.py` `_emit_todo_state` | ✅ 后端 |
| Timeline 前端投影 | 前端（display=todo_state 订阅） | ⏳ 待 UI 设计方向 |
| plan mutation 自动检测 | P1 | ⏳ |
| modify → Plan v2 reconcile | P1 | ⏳ |
| Task 持久化/跨 session | P2 | ⏳ |

**验证**：后端 376 passed + execution 413 passed（1 预存在 bwrap 无关失败）
+ E2E 3 项全过（plan card / auto timeline）

## 十、9 月演示闭环

```text
用户：帮我完成 MOF 调研并上传 Qraft
→ PlanCard（目标/步骤 1-4/权限 网络+文件+上传）
→ 用户确认 → PlanSnapshot 冻结 + TodoState 初始化（QUEUED ×4）
→ 注入 "Maintain progress using todo_write"（附步骤 id 清单）
→ 模型 todo_write([1:in_progress]) → Timeline ⟳ 搜集论文资料
→ 执行 → todo_write([1:completed, 2:in_progress]) → Timeline ✓⟳
→ ActionGuard：即将上传 Qraft → 确认（看真实上传结果）
→ 完成
```

## 十一、P1/P2（冻结清单）

- P1：verification（completed 需 ToolResult 证据）/ plan mutation 自动检测 /
  modify → PlanSnapshot v2 → Todo reconcile / Task 一级对象 + task-level persistence
- P2：跨 session / global registry / priority scheduler / todo search / 复杂 reconcile
