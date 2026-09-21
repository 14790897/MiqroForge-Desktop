# 计划卡（plan）改造方案 —— 现状全量 + 待评审

> 日期：2026-09-15　分支：`feature/646-v2-ui-workbuddy`（PR #1071，未合并）
> 需求原话：「这个里面有一个叫 plan 的，就是在每一个指令性任务下达后有一个意图识别好像会触发，就会让你选择……什么的。然后这个的调整方案和 UI 都要改，很急，可以参考 hermes，因为他的代码是开源的。」
> 本文所有"现状"均为**代码级核实**（分支上的真实代码 + 行号），标注「待实机」的除外。

---

## 一、先回答一句：现在到底有没有"意图识别"？

**没有。** 代码里不存在任何意图识别/意图分类组件（全仓 grep「意图 / intent」无业务命中）。
"会不会弹计划卡"由两条**隐藏规则**决定，用户无法预期：

| 路径 | 什么时候弹 | 卡片内容从哪来 |
|---|---|---|
| A. 模型主动 | 模型自己决定调用 `ask_user_plan_confirm`（提示词要求"多步任务开始前调用"） | **模型自己写的** title/goal/steps |
| B. harness 闸门 | 每轮结束后累计工具调用算「复杂度评分」≥4 就**强制弹** | **不是任务的真实计划**——由「工具名→中文标签表」拼出来的模板 |

所以"好像会触发"的观感是对的：**它既不是抽签，也不是理解任务，而是一条分数公式。**

---

## 二、现状（代码事实）

### 2.1 触发：harness 复杂度闸门（"每个指令性任务都弹"的真正来源）

`miqi/runtime/turn_runner.py:716-800`：模型输出后、**第一个工具执行前**判定：

```python
# miqi/execution/task_policy.py:233
COMPLEXITY_THRESHOLD = 4

# task_policy.py:236 complexity_score()
#   工具调用次数 ≥6 → +2 ；≥3 → +1
#   跨 ≥2 个阶段（READ/WRITE/EXEC/EXTERNAL）→ +2
#   会产出文件（任一工具风险≥2：写/执行/外部）→ +2
#   用了 skill → +3
# 评分 ≥4 且模式为 edit → 强制弹卡（turn_runner.py:761 → _harness_plan_confirm L1236）
# auto 模式 → 不弹卡，改发非阻塞 Timeline（task_policy.py:284 / turn_runner.py:759）
# plan 模式 → 不弹（should_plan_confirm 首行直接 return False）
```

**举例（为什么感觉"每个任务都弹"）**：
「搜资料 + 写报告」＝ 调用≥3（+1）＋ 跨 READ/WRITE 两阶段（+2）＋ 有产物（+2）＝ **5 ≥ 4 → 弹**。
只要任务"多步 + 有产物"，几乎必弹。

### 2.2 卡片内容（harness 路径）：工具标签拼模板

`task_policy.py:300 plan_card_steps()` 把工具名翻译成中文标签当步骤：

```
web_search → "搜集资料"
create_doc → "生成文档"
qraft_upload → "上传到 Qraft"
```

→ 同一个任务，永远得到同几条通用标签。**这就是"计划非常公式套路"的来源**（8-30 已批，至今未修）。
模型路径（A）的卡片内容是模型写的，相对真实——但两条路径**外观一模一样**，用户分不出来。

### 2.3 现有卡片 UI（截图实拍）

```
📋 生成 MOF-5 实验报告                                 等待你的决定
   搜索论文并生成报告
   ┌────────────────────────────────────┐
   │ ○ 搜集论文资料                       │  ← 浅灰大容器 + 子项行（WorkBuddy 风）
   │ ○ 创建实验报告                       │
   │ ○ 上传到 Qraft                       │
   └────────────────────────────────────┘
   涉及 网络 工作区 外部
   [按当前方案执行 →]   [调整方案]   [取消]
```

- 位置：消息流内（挂在工具链里，非弹窗）
- 确认后：卡片转「执行中」+ 步骤打勾（Timeline 驱动）
- 有卡等待时：底部输入框隐藏（旧定稿）

### 2.4 「调整方案」流程 —— 现状核实（2026-09-15 二次核对，修正初版判断）

**先纠正初版 md 的一个误判**：初版说"harness 路径会把调整文本丢掉"。二次核对代码后确认——
`miqi/runtime/services.py:255` 桌面端构造的是 **`CollaborativeTurnRunner`**（`miqi/runtime/collaborative_turn_runner.py`），
它已经实现了「调整意见 → 同轮重新规划」：`_harness_plan_confirm` 里把 `choice_label` 存进
`turn._plan_adjustment_pending`，外层 `run()` 看到后在**同一回合**追加一轮 provider 调用，并注入：

```
【用户调整后的任务约束】
{adjustment}
请严格基于这条约束重新规划，不要执行之前被否决的方案。
```

**真正会丢文本的只有 base `TurnRunner`**（`miqi/runtime/turn_runner.py:773-786`）——`modify` 时直接返回空 TurnResult 结束回合。
桌面端不走它，但它仍是 CLI/其他入口的路径，需要对齐。

**剩下两个真实问题（收窄后）**：
- **P3a 前端冗余**：调整提交后前端仍会 `lastAdjustAt → adjustHint → 聚焦底部输入框 + 提示词`
  （`ChatConsole.tsx:3002-3022`）——与"同轮自动重规划"重复，且与"有卡等待时输入框隐藏"的设计冲突（用户已拍板：藏）。
- **P3b 文案**：卡片"已调整"提示与真实行为需要一致（自动重规划中，不用你再输）。

**模型路径**（模型主动调 `ask_user_plan_confirm`）：`build_result` 把 `choice_label` 作为 tool result 回给模型 → 同轮重规划 ✓ 一致。

**结论**：Q3（自动重规划）在桌面端**已经成立**；本次要做的是**去掉前端冗余聚焦** + **base runner 对齐**，而不是新建机制。

### 2.5 相关文件清单

| 层 | 文件 | 内容 |
|---|---|---|
| 触发 | `miqi/runtime/turn_runner.py:716-800 / 1236-1316` | harness 闸门、弹卡、Timeline 事件 |
| 策略 | `miqi/execution/task_policy.py:233-340` | 复杂度评分 / 模板步骤 / 权限推断 |
| 工具 | `miqi/agent/tools/ask_user_plan_confirm.py` | 模型主动路径（含提示词） |
| 前端 | `apps/desktop/.../components/PlanCard.tsx`（302 行） | 卡片本体 |
| 前端 | `.../ConfirmCardArea.tsx` / `contexts/UserInputContext.tsx` / `ChatConsole.tsx:2995-3025` | 接线 / 调整焦点 |
| 测试 | `tests/kun_runtime/test_harness_plan_gate.py`、`apps/desktop/tests/e2e/plan-card.spec.ts` | 单测 / E2E |

---

## 三、Hermes 对照（本机源码实测，非道听途说）

源码路径：`~/AppData/Local/hermes/hermes-agent`

| 维度 | Hermes 的做法 | 对我们的启示 |
|---|---|---|
| 计划的作者 | **模型自己**：`tools/todo_tool.py`（模型写 id/content/status，每次调用返回全量清单） | 计划内容必须来自模型，不能是 harness 用工具标签拼模板（治 P2） |
| 触发 | **没有 harness 计划闸门**：计划只是模型的工作清单；"要用户拍板"走 `clarify` 提问；危险动作走审批 | "展示计划"与"阻塞确认"是两件事，可分开（治 P1/P4） |
| 呈现 | **非阻塞、常驻**：输入框上方 status stack（分组折叠「清单 X/Y」+ ✓ 打勾），侧栏会话卡同步 X/Y 进度，完成后淡出 | 进度应"常驻但低调"，而不是必须点的一张大卡 |
| 阻塞点 | 只有两处：**危险工具审批**（approval.tsx：once/session/always + deny）与**提问**（clarify） | 多步任务本身不阻塞；只有外部动作/必须拍板才阻塞 |
| 计划模式 | `/plan`＝写一份 md 计划到磁盘、**不执行** | "先给计划不执行"是显式模式，不是自动打断 |

---

## 四、问题清单（本次建议要改的）

| # | 问题 | 证据 | 用户可感知的后果 |
|---|---|---|---|
| **P1** | 触发过频/不可预期 | 复杂度≥4 即弹（§2.1） | "每个指令性任务都弹" |
| **P2** | harness 路径计划内容是模板 | `plan_card_steps()`（§2.2） | "计划非常公式套路" |
| **P3** | 调整方案双输入（harness 路径丢文本） | `_harness_plan_confirm` 只取 choice_id（§2.4） | 用户白写一遍 |
| **P4** | 没有意图识别；弹不弹由隐藏分数+模式决定 | §2.1 | "好像会触发"、无法预期 |
| **P5** | UI：卡内三按钮+权限行+灰块层级重；执行中与 Timeline 分工不清 | 截图 | "UI 都要改" |
| **P6** | PR #1071：CI 3 红（validate / electron-e2e / macos-e2e）+ CodeRabbit 14 条 + **PR 描述被截断**（仅 171 字符） | `gh pr checks 1071` | 无法合并 |

---

## 五、改造方案

### 5.1 目标（一句话）

**计划的作者换成模型；弹不弹、为什么弹做成显式可预期；"调整方案"只留一个入口、一次输入；UI 按 Hermes 的"计划内嵌 + 常驻进度"重排。**

### 5.2 方案 A（推荐）

**① 计划内容 = 模型产出（治 P2）**
- 保留 `ask_user_plan_confirm` 作为唯一"计划作者"；提示词强化：复杂任务的第一个动作就是调用它给出计划（标题 / 目标 / 3-8 步用户语言步骤）
- **删除 `plan_card_steps()` 模板兜底**：harness 闸门不得再自己拼内容

**② 触发显式化（治 P1/P4）**
- 弹卡只保留两类触发：
  - a) 模型主动给出计划（正常路径）
  - b) 任务里出现**外部动作**（风险≥10：上传/删除/支付/外发/启动进程）——这种必须用户拍板
- 纯本地多步任务（搜索+写报告）：**不再用大卡打断**，改为常驻 Timeline 进度（即现在 auto 模式的形态，推广到 edit 模式）
- 弹卡时给一句**"为什么现在要你拍板"**（例："接下来会把结果上传到 Qraft"）

**③ 调整方案单入口（治 P3）**
- 卡内输入框保留（唯一入口）；提交后**把调整文本直接作为下一轮用户输入发给 Agent**（`_harness_plan_confirm` 返回并回灌 choice_label），不再聚焦底部输入框、不再要用户输第二遍

**④ UI 按 Hermes 呈现重排（治 P5，详见 5.4）**

**⑤ 顺带修 PR #1071（治 P6）**：补 PR 描述（只追加，不整体替换）、CI 三红逐项处理

### 5.3 方案 B（保守备选）

不动触发频率，只治 P2/P3/P5：
- harness 闸门触发时**追加一轮"请先给出你的计划"**再弹卡（内容变成模型的）；
- 调整方案单入口；
- UI 微调。

### 5.4 UI 规格（方案 A 版）

```
[图标] 生成 MOF-5 实验报告                          执行中 · 2/3
       搜索论文并生成报告
   ✓  搜集论文资料
   ⟳  创建实验报告
   ○  上传到 Qraft
   ─────────────────────────────────────────────
   涉及 网络 工作区 外部
   [按当前方案执行 →]   调整方案   取消            ← 等待态（执行中收起按钮）
   ┌─ 调整方案（唯一输入入口）───────────────┐
   │ 你希望怎么调整？                        │
   │ [………………………………]  提交调整 →          │
   └──────────────────────────────────────┘
```

- 等待态：状态行「等待你的决定」；执行态：状态行「执行中 · X/Y」+ 步骤实时打勾
- 权限摘要保留，但降为一行的低调小字
- 视觉细节（间距/圆角/配色）**会先出 HTML 原型给你挑**，再改代码

---

## 六、待确认（3 个问题，可直接回）

- **Q1 触发口径**：9 月演示脚本需要"每个复杂任务都弹一张计划卡"吗？
  - 若需要 → 触发保持"多步+有产物"，但**换模型内容 + 说明为什么弹**；
  - 若不需要 → 按方案 A：本地多步任务不打断，只外部动作弹。
- **Q2 有卡等待时**，底部输入框：保持隐藏（现状），还是像 Hermes 一样保持可用（可以边看计划边打字）？
- **Q3 「调整方案」提交后**：直接重新规划（方案 A 的 ③），还是仍要用户自己在输入框确认发送（现状）？

---

## 七、工作量与顺序（确认后立即开工）

1. 后端：触发口径 + 内容来源 + 调整回灌（`task_policy.py` / `turn_runner.py`）+ 单测更新
2. 前端：PlanCard / Timeline（`PlanCard.tsx` / `Timeline.tsx`）
3. 测试：`test_harness_plan_gate.py` + `plan-card.spec.ts` E2E + 截图
4. PR #1071：补描述 + CI 收敛

> 工作副本已就位：`D:\Desktop\all_code\miqi-646-plan-ui`（分支 `feature/646-v2-ui-workbuddy`）。

---

## 八、Hermes / Claude Code 实测学习结论（2026-09-15）

**Claude Code**（本机 2.1.257 二进制字符串实测）：
- `Plan mode is active. …you MUST NOT make any edits, run any non-readonly tools…`（plan 模式只读，除计划文件）
- `ExitPlanMode inherently requests user approval of your plan.`（**计划由模型提交、审批由用户完成**）
- `ExitPlanMode again to resubmit it for approval.`（用户反馈后 → 模型改计划 → **重新提交**；这也就是我们的"调整方案→重规划"闭环）
- `Plan mode is active, so a goal cannot be proposed yet. Keep planning; propose the goal after the plan is approved.`

**Hermes**（本机源码实测）：
- `tools/clarify_tool.py`：结构化提问 ≤4 选项（自动加 "(Other)"）+ 批次 ≤5 问 + 首选项标 `(Recommended)` + **超时兜底**：`The user did not provide a response within the time limit. Use your best judgement to make the choice and proceed.`
- `tools/todo_tool.py` + `store/todos.ts` + `composer/status-stack`：计划/清单由**模型**写、非阻塞常驻（输入框上方 X/Y），完成即淡出
- `blueprints.py` = 技能+定时任务，与计划 UX 无关（避免误抄）

**据此落地的 3 条**：
1. **计划内容一律由模型产出**——闸门不再用工具标签拼模板（治 P2），模型仍不给才退回策略卡兜底；
2. **调整＝重新提交**（Claude Code 语义）——桌面端已有同轮重规划，去掉前端"再输一遍"的冗余聚焦；
3. **超时要有明确语义**（作业项：现为 300s，落到"按最佳判断继续/明确告知"之间需拍板，Hermes 是继续 + 告知）。

## 九、本轮落地记录（2026-09-15，commit `0cb9c7c5`）

- `miqi/runtime/turn_runner.py`：计划闸门改为「先请模型给计划（`_plan_request_sent` 一次性 → `_plan_request_pending`）」；
- `miqi/runtime/collaborative_turn_runner.py`：新增 plan-request 分支（追加一轮注入计划请求）+ `supports_plan_replan` 标记；
- `apps/desktop/.../ChatConsole.tsx`：删除 `lastAdjustAt → adjustHint → 聚焦输入框` 链路（有卡等待时输入框保持隐藏）；
- `tests/runtime/test_collaborative_turn_runner.py`：+2 用例（计划请求轮、标记位）；全量 `tests/runtime|execution|agent|kun_runtime` **2816 passed**，ruff 全过。
- 待办：UI 微调（状态行/进度/权限降噪，原型见 `docs/ui-prototype-plan-card-2026-09-15.html`）、E2E 截图、CI 收敛。

## 八、决策点（供评审）：计划卡超时语义

现状：`_harness_plan_confirm` 等待 300s，超时按「取消」处理，回文案「已取消任务」（turn_runner.py）。

对照：Claude Code 的计划审批是**无限等待**（用户不答就一直挂着，可用 Ctrl+C 中断）；Hermes 的 clarify 是**超时后按最佳判断继续**（提示词原文：The user did not provide a response within the time limit. Use your best judgement to make the choice and proceed.）。

建议：**保持 300s + 取消**。计划卡是执行前的安全闸门，「超时自动执行」的风险高于「超时取消」；但把文案从「已取消任务」改为「等待超时，已取消；重新发送可继续」，让用户明确知道发生了什么，而不是像"任务莫名消失"。

待外部评审确认后落地文案（改动面：turn_runner.py 超时分支一处）。

## 九、更正（2026-09-15 晚）：闸门 nudge 已退回——计划内容不走「先问模型」

**决定**：§七 的方案要点 ①「计划内容 = 模型产出（闸门先请模型用 `ask_user_plan_confirm` 给计划）」**已撤回**（commit `589afd10`）。

**原因（CI 实证，非推测）**

- `edd8aa23` 的 electron-e2e / macos-e2e 中，`subagent-spawn` / `system-install-card-real-llm` / `write-authorization` / `guard-issue-811-real-llm` **全部超时**；日志显示工具调用停在「等待你的确认…」，spec 期待的卡始终不出现（`approvePlanCardIfAny: 计划卡未出现（继续等）`）。
- 机制：闸门改为「先向模型索要计划」后，**e2e 的模型是脚本化 mock**（不会调用这个新工具）→ 回合永远推不动 → 流程卡死。
- 且该改动**触碰了工具调用环节**，与用户边界（本次只改 plan 卡片 UI）冲突。

**当前行为（= 2026-08-10 起既有行为，未变）**

- 闸门命中 → `_harness_plan_confirm` 弹策略卡（步骤由 `task_policy.plan_card_steps()` 按工具标签生成）；
- 模型**主动**调用 `ask_user_plan_confirm` 时，卡片内容仍是模型写的（两条路径并存，未变）；
- 「调整方案」仍按 §七 定稿：用户意见一次输入 → Collaborative 同轮重规划（`choice_label`）。

**结论**：计划内容的「模型产出」若要做，需另找不改变工具调用时序的入口（例如提高模型主动调用 `ask_user_plan_confirm` 的提示词权重），不能经闸门拦截。
