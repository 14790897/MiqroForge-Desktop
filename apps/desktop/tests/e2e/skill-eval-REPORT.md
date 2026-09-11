# MiQroForge 自带 Skills 精确调用量化评估报告（含优化前后对比）

- 评估日期：2026-08-17
- 评估对象：MiQroForge Desktop 真实 agent（Electron 全链路 + 真实 LLM `deepseek-v4-flash`，runtime 温度 0.1）
- 评估脚本：[skill-invocation-eval.spec.ts](skill-invocation-eval.spec.ts)（Playwright E2E，可复现）
- 原始数据：`apps/desktop/test-reports/eval-history/`（baseline 结果在首版报告中，v2 完整数据在 `v2-final-14cases.json`）

## 一、方法

用 **14 条刻意不含技能名的直接自然语言提示词**（"帮我做个PPT"式）驱动 agent，观察它能否精确调用对应的自带技能：

- 每个用例独立新会话（避免上下文串扰）；
- agent 主动发起的确认卡（`ask_user_confirm_card`，issue #646/#711）由 harness 自动点"确认"，模拟真实用户配合；
- 通过 `chat.onProgress` 采集回合内**每一次工具调用的工具名 + 完整参数**；
- 判定口径：**"加载技能"** = `skill_manage(view, name=X)` 或 `read_file(<X>/SKILL.md)`；**"命中"** = 期望技能被加载；
- 指标：召回率 = hit/全部；精确率 = hit/(hit+wrong_skill)；清单发现率 = 调用 `skill_manage(list)` 的用例占比。

运行命令：

```bash
cd apps/desktop && npm run build && \
PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test \
  --config=playwright.config.ts --project=electron \
  skill-invocation-eval.spec.ts --workers=1
```

环境变量：`MIQI_SKILL_EVAL_CASES=pptx,weather`（子集）、`MIQI_SKILL_EVAL_CASE_TIMEOUT_MIN`（默认 7）、`MIQI_RUN_SKILL_EVAL=1`（CI 强制运行）。

## 二、优化改动（提示词注入，3 处代码 + 1 个测试）

| 文件 | 改动 |
|---|---|
| [miqi/runtime/task_runner.py](../../../miqi/runtime/task_runner.py) | 组装 `effective_system_prompt` 时注入技能清单（`SkillsLoader.build_skills_summary(description_max_chars=160)`），补齐主提示词规则 7 引用的 "Local Skills" 列表；含【强制规则】处理请求第一步先 `skill_manage(list)`（纯寒暄除外）、技能优先于等价内置工具 + 典型映射示例（做PPT→pptx-generator 等） |
| [miqi/agent/skills.py](../../../miqi/agent/skills.py) | `build_skills_summary()` 增加 `description_max_chars` 参数（句边界截断，149 技能清单 60KB→43KB，约 11K tokens/回合） |
| [miqi/agent/tools/skill_manage.py](../../../miqi/agent/tools/skill_manage.py) | 工具描述写明 `action='list'` 可发现全部技能、`action='view'` 读取全文 |
| [tests/runtime/test_runtime_task_runner.py](../../../tests/runtime/test_runtime_task_runner.py) | 新增单元测试锁定注入行为（清单在、正文不在、强制规则在） |

## 三、结果对比（同一语料、同一判定口径）

| 指标 | 基线（优化前） | v2（注入清单+技能优先） | v3（v2 + 强制先 list） |
|---|---|---|---|
| 召回率（期望技能被加载） | **14.3%**（2/14） | 78.6%（11/14） | **92.9%**（13/14） |
| 精确率（碰技能时选对） | 66.7%（2/3） | 100%（11/11） | **100%**（13/13） |
| 完全没有技能接触 | 11/14 | 3/14 | **1/14** |
| 加载错误技能 | 1/14 | 0/14 | 0/14 |
| 清单发现率（skill_manage list） | 21.4% | 7.1% | **92.9%**（强制规则生效） |

### 逐用例

| 用例 | 期望技能 | 基线 | v2 | v3（强制先 list） |
|---|---|---|---|---|
| 做PPT | pptx-generator | ❌ create_pptx 直做 | ✅ hit（134 工具/10min） | ✅ **hit**（60 工具/5min） |
| 写周报Word | docx | ❌ create_docx 直做 | ✅ hit | ✅ **hit**（10 工具/32s） |
| 销售数据Excel | xlsx | ❌ create_xlsx 直做 | ✅ hit | ✅ **hit**（72 工具/446s） |
| 生成PDF报告 | pdf | ❌ create_pdf 直做 | ✅ hit | ✅ **hit**（38 工具/133s） |
| 查北京气温 | weather | ❌ web_search 直答 | ✅ hit | ✅ **hit**（8 工具/31s） |
| 每天9点提醒 | cron | ✅ hit（路径错，128 工具） | ✅ hit（80 工具） | ✅ **hit**（24 工具/66s，明显更收敛） |
| 搜MOF论文 | paper-research | ❌ paper_search 直做 | ❌ no_skill | ✅ **hit**（47 工具/135s） |
| 查GitHub提交 | github | ❌ exec gh 直做 | ✅ hit | ✅ **hit**（10 工具/49s） |
| 总结文字要点 | summarize | ❌ 0 工具纯答 | ❌ no_skill | ❌ **no_skill**（0 工具纯答，连 list 都跳过） |
| 整理工作区 | workspace-cleanup | ❌ 自行整理 | ✅ hit | ✅ **hit**（38 工具/157s） |
| 写招聘JD | job-post-builder (KWP) | ❌ 直接写文件 | ❌ no_skill | ✅ **hit**（46 工具/143s，还加载了 docx） |
| 管理本周待办 | task-management (KWP) | ❌ 直接写文件 | ✅ hit | ✅ **hit**（12 工具/37s） |
| 创建新技能 | skill-creator | ⚠️ 自造技能 | ✅ hit | ✅ **hit**（138 工具/588s，最重用例） |
| 记住生日 | memory | ✅ hit | ✅ hit | ✅ **hit**（16 工具/34s） |

v3 唯一未命中：**summarize**——"总结一段文字"零工具即可完成，模型直接作答，连强制规则的 list 第一步都跳过了（判定该请求属"直接可答"，未进入任务流程）。这类"纯模型能力"任务要在提示词层面拉到命中，需要把强制规则的豁免口径再收紧（如"除寒暄外一律先 list"），但代价是寒暄类回合也要付 list 的 token 成本。

## 四、剩余 1 个未命中与后续建议

**summarize（总结文字）**：任务零工具即可完成，模型直接作答并跳过了强制规则的 list 第一步（把该请求归为"可直接答"，没有进入任务流程）。可选收紧方向：
- 把豁免口径从"纯寒暄/聊天除外"改为"除寒暄外一律先 list"——寒暄类回合也要付 list 的 token 成本；
- 或接受该用例永不命中（纯模型能力任务的技能没有加载动机，收益存疑）。

### 遗留问题（与提示词优化无关，另行处理）

- **cron 技能要求 `cron` 工具，但 main agent 的 `available_tools` 里没有**（[agent_registry.py](../../../miqi/runtime/agent_registry.py)）：技能加载成功后仍靠 exec 手写 `cron/jobs.json`（桌面 cron 服务不读该文件，端到端不生效）。建议把 cron 工具加入 main agent 可用列表。
- **技能路径的执行成本高于工具直做**：v3 里 pptx 60 工具/5min、skillcreate 138 工具/10min（基线工具直做 pptx 14 工具/32s）。若追求效率，可考虑"技能指导 + 内置工具执行"的混合策略（如 pptx 技能正文直接指向 `create_pptx`，同 pdf 技能指向 `create_pdf` 的既有模式）。
- **强制 list 的每回合成本**：v3 中 13/14 回合先调 `skill_manage(list)`，list 输出 149 技能约 8-10K tokens/次进上下文；寒暄类回合若也强制，成本会进一步上升。

## 五、结论

三阶段迭代（同一 14 条语料、同一判定口径）：

| 版本 | 改动 | 召回率 | 精确率 |
|---|---|---|---|
| 基线 | 无（技能清单从未注入提示词） | 14.3% | 66.7% |
| v2 | 注入清单 + 技能优先规则 + 典型映射 | 78.6% | 100% |
| **v3（当前）** | **v2 + 强制先 `skill_manage(list)`** | **92.9%** | **100%** |

提示词注入是低成本高杠杆的修复，且"强制先 list"把 KWP 插件技能（job-post-builder）和工具强绑定场景（paper-research）也拉进了命中区。核心机制：技能清单（渐进披露第一层）+ 强制发现动作 + 技能优先规则 + 典型映射一起注入系统提示词，`skill_manage` 工具描述承担发现入口语义。

## 六、方法学注意

- 每个配置 N=1 单次运行（LLM 有随机性；如需更稳结论可跑多轮取均值）；
- `skillsLoaded` 含失败的 `view` 尝试（不存在的技能名也会计入加载动作）；
- 确认卡自动点"确认"，等价于真实用户配合的场景；
- v2 的 14 例分两次运行（12 例主跑 + 2 例子集补跑）；v3 为单次完整运行（37.7 分钟）；
- Electron dev userData 缓存损坏会连环导致启动即崩，清 `%APPDATA%/miqi-desktop-dev/ws-*` 对应目录可恢复；
- 每回合提示词新增约 43KB（≈11K tokens）技能清单 + 强制 list 时额外 ~8-10K tokens 工具输出，是本次优化的主要成本。
