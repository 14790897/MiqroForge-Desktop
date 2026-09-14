# #646-v2 设计基线 v3.3：Agent Task Lifecycle Protocol v1（最终拍板，2026-08-18）

> ChatGPT 最终评审（8.8/10 + 7 项修正）。冻结为实施基线。
> **定位**：不是"PlanCard 功能"——是 **Agent Task Lifecycle Protocol v1**
> （模型与 harness 之间的进度状态协议）。

---

## 一、最终 7 项修正

| # | 修正 | 落地 |
|---|---|---|
| 1 | ExecutionContext → **AgentRunContext**（避免与 RuntimeContext/AgentContext 混淆） | `run_id` + session_key + plan_snapshot + todo_state + action_history |
| 2 | **source/kind 保留后端，不给 UI** | 前端 DTO 只暴露 `{id, title, status}`——不暴露内部实现 |
| 3 | todo_write **SHOULD 不是 MUST** | 复杂任务（PlanCard 已弹 = 复杂）注入提示；ActionGuard-only 任务不需要 |
| 4 | **TodoStatus 加 QUEUED** | 已批准等待 Agent 开始（解决"确认后没动静"UX） |
| 5 | **Scope 结构化**（非字符串） | `{provider:"qraft", operation:"upload"}`——mutation detection 可比较 |
| 6 | **merge 禁止删除/rename** | plan item 只允许 status transition；auxiliary 可增可 cancel |
| 7 | **transition validator** | QUEUED→IN_PROGRESS→COMPLETED；IN_PROGRESS↔BLOCKED；任何→CANCELLED；禁止 COMPLETED→IN_PROGRESS |

## 二、最终数据模型

```python
@dataclass
class PlanStep:
    id: str
    content: str

@dataclass
class ApprovedScope:                      # 结构化（非字符串列表）
    sources: list[str]                    # ["academic papers"]
    artifacts: list[ArtifactRef]          # [{"type":"document","name":"report.docx"}]
    external_actions: list[ExternalAction]  # [{"provider":"qraft","operation":"upload"}]

@dataclass
class PlanSnapshot:                       # immutable（用户批准事实）
    plan_id: str
    goal: str
    steps: list[PlanStep]
    approved_scope: ApprovedScope
    plan_version: int = 1
    approved_at: str
    approved_by: str = "user"

@dataclass
class TodoItem:
    id: str
    content: str
    status: str   # queued/in_progress/blocked/completed/cancelled
    kind: str     # plan | auxiliary | observed（后端）
    source: str   # model | harness（后端——不给 UI）

@dataclass
class TodoState:
    run_id: str
    revision: int = 0
    items: list[TodoItem]

@dataclass
class AgentRunContext:                    # 一次 Agent 工作流执行实例（非 session）
    run_id: str
    session_key: str
    plan_snapshot: PlanSnapshot | None
    todo_state: TodoState
    action_history: list
```

## 三、状态机（transition validator——P0 必须）

```text
QUEUED → IN_PROGRESS → COMPLETED
        ↘ BLOCKED ⇄ IN_PROGRESS
任何状态 → CANCELLED
禁止：COMPLETED → IN_PROGRESS（除非人工）
```

## 四、todo_write 契约（P0 前置——先冻结协议）

```python
todo_write(todos: [TodoPatch], merge: bool = True) -> TodoWriteResult
# TodoPatch = {id, status} | {id, content, kind:"auxiliary", status?}
# 规则：
#  - merge 默认 + 自动升级（全指向已有 id 的状态翻转）
#  - plan item：只允许 status transition（禁止 delete/rename/改 content）
#  - plan item 改 content → error-as-output：
#      {"status":"rejected","reason":"PLAN_MUTATION_REQUIRES_CONFIRMATION",
#       "suggestion":"ask_user_plan_confirm"}
#  - transition 校验（validator）
#  - revision += 1
#  - summary 回传（total/completed/in_progress/pending/blocked——无 source/kind）
```

## 五、提示词注入（SHOULD——按复杂度）

```text
PlanCard 确认后（复杂任务）：
  "Your approved plan has been initialized.
   Maintain progress using todo_write."
模型第一轮：todo_write([{id, status:"in_progress"}]) 再执行（SHOULD——不强制；
模型不调则 harness ToolEvent 写入 kind=observed/source=harness）
```

## 六、Timeline（projection——单源）

```text
TodoState → Timeline（前端只拿 {id, title, status}）
ToolEvent → TodoState（kind=observed, source=harness——后端单写入）
```

## 七、P0 实施顺序（todo contract 前置）

| 步骤 | 内容 |
|---|---|
| 1 | **数据模型**（PlanSnapshot/ApprovedScope/TodoState/AgentRunContext）|
| 2 | **todo_write contract**（merge/拒绝闸/transition validator/summary——先冻结协议）|
| 3 | **Plan confirm → PlanSnapshot + TodoState 初始化**（plan-kind + QUEUED）|
| 4 | **Timeline projection**（读 TodoState——前端 DTO 隔离）|
| 5 | **ToolEvent fallback**（kind=observed 单源写入）|
| 6 | **Prompt injection**（SHOULD）|

## 八、9 月演示闭环

```text
用户：帮我完成 MOF 调研并上传 Qraft
→ PlanCard（目标/步骤 1-4/权限 网络+文件+上传）
→ 用户确认 → PlanSnapshot 冻结 + TodoState 初始化（QUEUED ×4）
→ 注入 "Maintain progress using todo_write"
→ 模型 todo_write([1:in_progress]) → Timeline ⟳ 搜集论文资料
→ 执行 → todo_write([1:completed, 2:in_progress]) → Timeline ✓⟳
→ ActionGuard：即将上传 Qraft → 确认
→ 完成
```

## 九、P1（冻结）

- verification（completed 需 ToolResult 证据：declared/observed/verified）
- plan mutation detection 自动化（approved_scope 比对）
- modify → PlanSnapshot v2 → Todo reconcile
- Task 一级对象 + task-level persistence
- COMPLETED→IN_PROGRESS 人工恢复

## 十、P2（不做）

- 跨 session / global registry / priority / scheduler / todo search / 复杂 reconcile engine

---

## 决策记录（累计 7 条）

1. Todo ≠ Authority（Agent 报告的状态，不是安全/执行事实）
2. Plan ≠ Todo（静态批准语义 vs 动态执行状态）
3. Todo ≠ Scope Expansion（辅助步骤允许；目标/范围/副作用必须重新确认）
4. Tool Events ≠ Todo（事实 vs 语义——同入 TodoState，source 区分）
5. Timeline ≠ State（projection）
6. P0 ≠ 任务管理系统（AgentRunContext 够用；Task/Persistence P1）
7. **source/kind 是内部协议，不是 UI 概念**（前端只见 {id, title, status}）
