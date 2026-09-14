# #646-v2：Task Plan Card 重构——设计规格（GPT 第二轮评审拍板）

> 状态：**架构定稿**（2026-08-15 GPT 评审）——#684 冻结为技术验证，本方案为 9 月演示方向
> 核心转变：#646 从「AI 如何请求权限」→「AI 如何与用户协作」（任务计划 → 执行 → 高风险节点确认）

---

## 1. 三层模型（最终架构）

```
                用户选择 Mode（体验层）
                      │
                      ▼
        Agent Planning Layer（#646）
        「你要做什么？」→ Task Plan Card
                      │
                      ▼
        Execution Policy（审批设置）
        「你允许它怎么做？」
                      │
                      ▼
        Runtime Guard（危险动作检查）
        「真的执行这个危险动作时再确认」
                      │
                      ▼
                Tool Execute
```

| 东西 | 负责 |
|---|---|
| Mode | 用户希望的 AI 自主程度 |
| **Plan Card（#646）** | 任务开始前**一次**决策 |
| Approval（审批设置） | 安全权限 |
| **Action Card** | 危险动作（上传/支付/删除/外发）**最后**确认 |

## 2. Mode 重定义

| 现在 | 建议名 | 行为 |
|---|---|---|
| Plan | **规划模式** | 只给方案：聊天 → Plan Card → 方案 → 停止；禁止文件修改/网络副作用/上传 |
| 手动 | **逐步模式**（高级） | 开发者：每个危险动作都审批（[允许一次]） |
| 允许编辑 | **协作模式**（默认） | Claude Code 式：计划卡一次 + 真正外发一次，普通文件不弹 |
| 自动 | **自主模式** | 用户明确选择自动=授权最大自治；但危险边界仍在（支付/删目录/上传敏感数据禁止）——**auto ≠ root** |

## 3. 两种卡

### A. Plan Card（主要）——任务开始前一次

**触发**（不是工具，是 Agent 判断）：
- `task_complexity > threshold` / 预计多工具调用 / Skill 执行 / 外部资源 / 超过 30 秒

**模型调用**：新增 `ask_user_plan_confirm` 工具（**不污染 ask_user_confirm_card**）：

```json
{
  "title": "生成 MOF 实验报告",
  "goal": "整理 5 篇论文并生成 Workflow",
  "steps": [
    { "name": "论文检索", "tools": ["web_search"] },
    { "name": "生成报告", "tools": ["write_file"] },
    { "name": "上传 Qraft", "tools": ["upload"] }
  ],
  "permissions": ["network_read", "workspace_write", "external_upload"]
}
```

**前端形态**（不是审批弹窗，是 AI 助手展示计划）：
```
┌──────────────────────────────┐
│ 📋 准备执行任务               │
│ 生成 MOF 实验报告             │
│                              │
│ 执行计划:                     │
│  ✓ 搜索相关论文               │
│  ✓ 分析实验参数               │
│  ✓ 创建报告                   │
│  ○ 上传 Qraft                 │
│                              │
│ 需要权限:                     │
│  🌐 网络访问 · 📄 创建文件 · ☁ 外部上传 │
│ 预计: 3-5 分钟                │
│                              │
│              [开始执行]       │
└──────────────────────────────┘
```

### B. Action Confirmation Card（少量）——危险动作

只出现于：**上传 / 支付 / 删除 / 外发**

```
⚠ 即将上传数据
文件: workflow.json · 23 KB · 目标: Qraft
[取消] [确认上传]
```

（这是现有 ask_user_confirm_card 的职责——保留）

## 4. TaskState 状态机

```
PLANNING → WAIT_CONFIRM → RUNNING → WAIT_DANGEROUS_ACTION → COMPLETED
                              │                                  │
                              └── CANCELLED ←───────────────────┘
```

流程：
```
User request → Agent planning → ask_user_plan_confirm → WAIT_CONFIRM
→ User approve → execute → dangerous action? → yes → Action Confirm → continue
→ COMPLETED / CANCELLED
```

## 5. 审批设置 UI 重构

- **普通用户**：只有「AI 自主程度」单选（规划/协作/自动）——隐藏高级设置
- **高级设置展开**（「高级安全控制」）：
  - 命令执行: [需要确认]
  - 文件修改: [需要确认]
  - 网络访问: [允许]
  - 危险操作: [始终确认]
- 命名：「审批绕过模式」→「自主执行策略」
- **白名单 → 允许规则**（工具+场景，非 url 清单）：
  ```
  科研资料访问: web_search/web_fetch · 范围: 学术网站 · 有效: 本会话
  ```

## 6. 代码落点（Phase 分步）

### Phase 1（9 月演示必须）
| 项 | 实现 |
|---|---|
| `ask_user_plan_confirm` 工具 | 新增（schema：title/goal/steps/permissions；resolver 复用 user_input_gate） |
| TaskState | 新增（task 级状态机，turn/task 上下文） |
| Plan Card 前端 | 新组件 PlanCard（计划+权限清单+开始执行）+ 状态进度（Planning→Running→Done） |
| 提示词 | 注入「复杂任务先弹任务计划卡」 |
| 保留 | ask_user_confirm_card（危险动作卡）、现有 Safety Approval 不动 |

### Phase 2
| 项 | 实现 |
|---|---|
| collab_policy 重构 | `tool → confirm` 改为 `task context → confirm`（单工具不再弹，危险动作清单驱动） |
| Runtime Guard | 危险动作（upload/delete/pay/destructive）最后确认 |
| 模式联动 | 协作=默认；自主=危险边界内最大自治 |

## 7. 冻结与存档

- **#684**：冻结为技术验证（逐工具 gate 的实现记录）——不再演进，合并与否由 owner 定
- **#646-v2**：新分支 `feature/646-v2-plan-card`（基于最新 develop，含 #711/#684 rebase 成果）

## 8. 9 月演示体验（验收标准）

普通用户：
```
输入任务 → 计划卡一次 → 执行（进度面板）→ 上传时一次 → 完成
```
高级用户（逐步模式）：每一步确认
自动用户：几乎无打断
