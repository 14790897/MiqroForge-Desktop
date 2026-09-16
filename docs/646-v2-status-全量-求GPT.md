# #646-v2 Task Plan Card 实施状态全量报告（2026-08-18，求 ChatGPT 评审）

> 本文档是 **#646-v2（Agent Task Planning System）当前实施状态**的完整清单：
> 已做的（含验证证据）/ 没做的 / 偏离的 / 待拍板的问题——请逐条评审。

---

## 一、背景（30 秒版）

- **#646** = 任务启动授权（Agent Task Planning System），**不是工具审批**。
- 你六轮评审拍板：三层模型（Mode → Agent Task Layer → Safety Layer → Action Guard）+ 两种卡（PlanCard 任务计划决策点 / ActionCard 危险动作确认）+ harness 强制（模型主动弹卡只是体验优化）。
- 当前分支 `feature/646-v2-plan-card`（PR #719 draft），19 个提交，全部推送。
- **E2E 全链路测试通过**（真实 Electron：PlanCard→开始执行→web_search→write_file→ActionCard→确认→完成）。

---

## 二、E2E 为什么用 mock（先回答这个）

| 层 | 真/假 | 说明 |
|---|---|---|
| LLM 模型 | **mock**（mock_openai.py 确定性状态机） | E2E 每轮 3-4 分钟；真实模型结果不可复现→测试不稳定；确定性状态机保证"弹卡→点击→回传"每一步可预期；#711 的 confirm-card E2E 同为 mock 模式 |
| 卡片 UI/交互 | **真实** | PlanCard/ActionCard 真实组件 |
| 审批/权限引擎 | **真实** | 所以抓到「Unknown tool: ask_user_plan_confirm」审批拦截真 bug |
| 事件链路 bridge→main→renderer | **真实** | 所以抓到"工具执行了但卡没渲染"问题 |
| 真实模型链路 | `real-llm.spec.ts`（CI 跑，需 key） | 本地无 key |

**结论：mock 只替"模型智商"，不替"系统行为"。**

---

## 三、已做（含验证证据）

### 3.1 核心机制（全部按你的拍板）

| # | 拍板项 | 实施 | 验证证据 |
|---|---|---|---|
| 1 | #646 = 任务启动授权 | PlanCard 是"你准备干什么"决策点 | E2E 全链路 |
| 2 | harness 强制计划卡 | turn_runner 插桩：模型首轮 tool_calls 后累计判定，达阈值强制弹卡；模型主动弹过则不重复 | E2E 实测（模型主动弹路径）+ 集成测试 |
| 3 | 边界：纯读任务不弹 | should_plan_confirm 复杂度 <4 不弹（搜索/读论文不弹） | task_policy 6 用例 |
| 4 | 步骤用户语言 | TOOL_DESCRIPTION 语义化（web_search→"搜集论文资料"） | E2E 断言「无工具名」 |
| 5 | ActionCard 独立 | request_action_confirmation 工具 + 独立 UI（目标/文件/大小/指纹） | E2E（ActionCard 出现+确认上传） |
| 6 | 复杂度/风险分离 | complexity_score（工具≥3+1/Skill+3/>5min+2/产物+2/多阶段+2）与 risk_score（upload/delete/payment 10、exec 5、write 2）完全分离 | 单元测试 + 集成测试（跨轮多阶段判定） |
| 7 | delete 分级 | DELETE_TEMP（临时文件）放行 / DELETE_DESTRUCTIVE（目录/通配/递归/关键路径）确认 | task_policy 用例 |
| 8 | TaskState 冻结 | PLANNING/WAIT_USER_PLAN_CONFIRM/RUNNING/WAIT_ACTION_CONFIRM/COMPLETED | 单元测试 |
| 9 | Edit 模式文件自动放行 | permission_profile GRANULAR(file_write=never)——写文件不弹审批，exec/危险仍确认 | 单元测试 50 passed + 实测修复 |
| 10 | 确认类工具不触发审批 | permission_engine 6b 分支（ask_user_confirm_card/ask_user_plan_confirm/request_action_confirmation 直接 ALLOW） | E2E（Kimi 看截图定位的 bug 修复） |
| 11 | modify 循环 | PlanCard「修改计划」→ choice_id=modify → 模型重规划弹新卡 | 提示词第 4 条 + build_result |
| 12 | 确认后折叠 | PlanCard resolved 默认收起（思维列表式）+ 展开详情 | 组件测试 |
| 13 | 长任务不重复确认 | _plan_confirm_done 一次性；危险动作走 ActionCard | E2E |
| 14 | #684 冻结为技术验证 | 分支保留，代码作 Action Guard 参考 | — |

### 3.2 UI（Kimi k2.6 多模态评审，真机截图）
- **两轮评审已落地**：投影/圆角 14px/步骤列表独立背景块/按钮层级/高亮加深/去红边框改左侧色条（ActionCard）
- 步骤工具名泄漏（demo 页）已修复
- **当前 UI 状态**：PlanCard（等待/执行中/已完成三态 + 折叠）+ ActionCard（上传/支付/删除三态）

### 3.3 验证基线
- 后端：1987 passed 基线 + task_policy 6 + turn_runner 50 + permission_engine 38 + 集成测试（T5 auto 不弹通过；T3/T8 测试替身适配中）
- 前端：vitest 21 passed（PlanCard 3/ActionCard 3/ConfirmCard 15）+ tsc 0
- **E2E：plan-card.spec.ts 1 passed（1.4m 全链路）**；confirm-card.spec.ts 主链路 passed
- 截图管道：plan-shot/action-shot → plan-card-all.png / action-card-all.png / 真机 E2E 截图

---

## 四、拍板了但**没做**（3 项——诚实清单）

| # | 拍板项 | 当前状态 | 原因/影响 |
|---|---|---|---|
| 1 | **Auto 模式 TaskTimeline**（非阻塞展示，不叫 Card） | ❌ 未做——Auto 模式既不弹卡也不展示，黑箱 | 我优先做了机制正确性；9 月演示 Auto 链路需要 |
| 2 | **审批设置降级高级设置**（"自主执行策略"单选：规划/手动/允许编辑/自动；审批绕过改名） | ❌ 未做——设置 UI 还是旧的（审批设置独立页） | 产品结构项，用户可见度低 |
| 3 | **长任务进度实时推送**（ToolCallBegin/End → PlanCard 步骤打勾） | ⚠️ 半做——组件有 stepStatus 字段，**后端没推事件**，执行中步骤不实时更新 | 演示"执行动画"的关键 |

---

## 五、待拍板问题（每项带我的建议，请评审）

### Q1：跨轮复杂度判定——多阶段 +2 的粒度
现状：累计工具去重 + 跨轮（n_rounds≥2）多阶段 +2。「搜索 5 篇+生成报告」类任务（5+ 工具分 3 轮）→ 1+2+2=5 ≥4 弹卡 ✓。
**问题**：模型第一轮就调 5+ 工具（一轮完成）→ n_rounds=1 → 无多阶段分 → 1+2=3 <4 **不弹**——同任务不同弹卡结果。要不要把"首次 tool_calls ≥4"也视为多阶段（+2）？
**我的建议**：是——首轮 ≥4 工具说明任务本身复杂，与跨轮等价。

### Q2：弹卡时机（分轮执行）
现状：累计判定导致弹卡可能发生在**第 2-3 轮**（用户已看到部分搜索结果）。GPT 说的"Agent execution planning boundary"严格应在**第一个工具执行前**。
**问题**：模型分批规划时（首轮 1-2 个工具），无法预知总规模——提前弹会误伤小任务，延后弹会"先跑几步"。
**我的建议**：接受现状（累计判定+延后弹）——因为首轮弹（按预期规模）需要模型提供计划文本（两阶段方案，9 月不做）。两阶段确认后自然解决。

### Q3：ActionCard 的 sha256 从哪来
现状：request_action_confirmation 工具参数带 sha256（模型填写——**不可信**）。
**问题**：上传执行前谁算指纹？（工具两段式：先算后传 / harness 预读 / 演示先不显示）
**我的建议**：上传工具两段式（execution 前算 sha256 注入 ActionCard）——但工作量大；演示先显示"文件名+大小"不显示指纹，后续补。

### Q4：PlanCard+ActionCard 同 turn 时间线
现状：E2E 验证了"计划确认→执行→ActionCard"串行链路 ✓。
**问题**：模型在计划确认前就调了 upload 类工具（计划卡还没确认）——ActionCard 先弹？
**我的建议**：harness 计划卡未确认时，危险工具**排队**（等计划确认后再弹 ActionCard）——需要 harness 拦截危险工具执行。

### Q5：#729 两个体验问题（首字延迟 + 切 session 思考消失）
- 首字延迟：模型首轮完整思考后才输出首个 delta——建议"发送后立即'正在思考'占位 + 流式首字计时"
- 思考消失：切 session 重载后 reasoning 不恢复——后端已存（message_fields），前端重载路径待修
**问题**：这两个要不要纳入 #646-v2（演示前必修）？
**我的建议**：是——演示体验的关键。

### Q6：权限标签图标（Kimi P1）
Kimi：权限 pill 加图标+颜色区分风险（网络-蓝/文件-橙/上传-紫）。
**问题**：emoji 还是 lucide？（你之前 UI 偏好 emoji 非 lucide 单色）
**我的建议**：emoji（🌐📄⬆️），小改动。

### Q7：T3/T8 集成测试未完成
T3（累计弹卡）/T8（modify 循环）集成测试的测试替身还在适配真实 TurnRunner（fake provider/tools/context 接口补齐中）。T5（auto 不弹）已通过。
**问题**：继续补测试替身，还是先做 Auto Timeline（演示更优先）？

### Q8：Auto Timeline 的实现方式
后端事件推送（ToolCallBegin→步骤状态）vs 前端轮询。
**我的建议**：后端事件——已有 ToolCallBegin/End 事件，复用推 PlanCard/Timeline 状态。

### Q9：白名单场景化
你拍板"后续做"（工具+场景规则替代 URL 清单）——现在做还是等 #646-v2 合入？

### Q10：9 月演示最小闭环确认
Edit 模式：用户"帮我完成 MOF 调研并上传 Qraft" → 📋PlanCard（目标/步骤/权限/[开始执行]）→ 执行动画（✓搜索 ✓分析 ✓生成）→ ⚠ActionCard（上传+指纹）→ 成功。
**问题**：Auto 模式演示（Timeline）要不要同场演示？
**我的建议**：演示 Edit 闭环为主（已验证），Auto Timeline 作为彩蛋（若 Q8 做了）。

---

## 六、实测发现的问题链（供判断质量）

1. **Edit 文件审批仍弹**（用户实测）→ 根因：Phase 13 先 attach 默认 Profile → edit 分支 is None 跳过 → 已修
2. **「Unknown tool: ask_user_plan_confirm」审批拦截**（E2E）→ 根因：权限引擎 deny-by-default → 已修（确认类工具 ALLOW）
3. **计划卡步骤全空**（用户实测）→ 根因：ConfirmCardArea 映射 s.title vs s.name → 已修
4. **模型分批调工具 → 计划卡永不弹**（集成测试暴露）→ 根因：每轮单独判定 → 已修（turn 级累计+跨轮）
5. **mock 分支顺序**（plan 分支在 R1 后永远到不了）→ 已修
6. **web_search 在 E2E 弹审批**（真实用户模式不弹）→ E2E 环境差异，spec 自动批准；待确认是否 permission 配置问题

---

## 七、下一步建议顺序（等你拍板）

1. **Q8 Auto Timeline**（演示依赖）
2. **Q1 首轮 ≥4 工具 = 多阶段**（机制一致性，小改）
3. **Q5 #729 两体验问题**（演示体验）
4. **Q6 权限图标**（Kimi P1，小改）
5. **Q3 sha256 两段式**（安全完整性，可后置）
6. 审批设置 UI 降级（产品结构，可后置）
