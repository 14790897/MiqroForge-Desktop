---
name: qraft-workflowspec-export
description: >
  Export a WorkflowRun JSON (per workflowspec.schema.json) that organizes the
  scattered outputs of an agent problem-solving session — files, numbers,
  evidence, and conclusions — into a structured, schema-validated run record,
  then confirm the plan with the user and upload it to the MiQroForge cloud
  platform via its dataUpload API (#674). Use when a session that invoked other
  skills has finished and the user wants its products organized, archived, or
  unified (e.g. "整理这次会话的产物", "把结果归档成 JSON", "export the workflow run",
  "上传方案到 MiQroForge", "上传到 MiQroForge 云平台", "把方案上传到平台",
  "upload the workflow"). Also use when an existing WorkflowRun needs to be
  updated with additional artifacts or conclusions. Trigger on post-task
  archiving regardless of which skills produced the outputs. IMPORTANT: the
  MiQroForge platform is an EXTERNAL cloud website, not yourself — "upload to
  MiQroForge" means uploading via the dataUpload API (scripts/upload_run.py),
  never sending a message/attachment to the miqroforge channel.
---

# qraft-workflowspec-export

## ⚠️ 上传红线（必读，先于一切步骤）

**MiQroForge 平台是外部云平台网站，不是你自己。**

- 你（桌面 AI 助手）恰好也叫 MiQroForge，但用户说「上传到 MiQroForge / MiQroForge 云平台」时，
  指的是外部平台网站（测试环境 `https://test.forge.miqroera.com`），**不是**「给你自己发消息」。
- **上传的唯一通道是平台的 dataUpload 接口**：`python <skill_dir>/scripts/upload_run.py <json> --json`（Step 8）。
  只有该脚本返回 `ok:true` 才算「已上传到平台」。
- **严禁用 `message` 工具冒充上传**：`message(channel="miqroforge"/"desktop", media=[...])` 只是把文件作为
  聊天附件发给当前会话（等于发给自己）。文件不会进入平台，平台上看不到任何东西；
  「Message sent to ...」返回 ≠ 上传成功，二者没有任何等价性。
- 平台只接受 **JSON 文件**（`document_kind` = `workflow_definition` / `workflow_run`，≤5MB）。
  Word/PDF 等附件不能直接上传——把方案内容组织成 workflow_definition JSON 后走 dataUpload。
  如需把 Word 文档交给用户，可另外用 `message` 发给用户本地留档；但「上传到平台」这一步必须走 dataUpload。

把一次 agent 问题解决会话的产物整理为 **WorkflowDefinition（上传目标）** 或 **WorkflowRun（归档记录）**：按 `references/workflowspec.schema.json` 定义的结构构建 JSON，完成 schema + 语义校验后，渲染方案视图经用户确认，上传到 MiQroForge 平台（dataUpload 接口）。上传目标默认是 `workflow_definition`（官方 OAuth2 文档 8.4 节），仅当用户明确要求归档运行记录时才导出 `workflow_run`。

产物统一管理 = 让每次会话的产出可追溯（谁调的哪个 skill、产出了什么文件、得出了什么结论、数字是多少）。

> 以下「输入」与「产物归类决策表」两节是 **workflow_run 归档模式**专用；上传 `workflow_definition` 时从 Step 1 直接收集用户对工作流/技能的定义描述（名称、用途、步骤、执行方式），跳过产物归类。

## 输入

调用时，从当前会话上下文收集：

- **文件路径列表**：本次会话产出的文件（相对/绝对路径均可，skill 会解析）
- **结论文字**：agent 得出的判断、分析结果（可多条）
- **被调用的 skill 列表**：本次会话实际使用过的其他 skill（每个 = 一个 node_run）
- **用户原始请求**（prompt）与**解析后的意图**（parsed_intent）——从会话开头提取
- **可选**：workflow_ref 覆盖（默认 `qraft.agent-session/1.0.0`）；backend 覆盖

产物收集方式 = **对话中指定**。不做目录扫描，不猜测哪些文件是产物——agent 刚干完活，自己知道产出了什么。

## 产物归类决策表（核心）

把每条产物按实际意义归入四类。**先判文件，再判数字，再判支撑，最后判结论。**

| 产物形态 | 去向 | 关键规则 |
|---|---|---|
| 文件（本地存在的路径） | `artifacts` | `role` 按语义选：output（最终结果）/ report（报告）/ visualization（图）/ model（模型文件）/ log / intermediate / manifest；本地文件必填 size_bytes + checksum(sha256) + exists=true；远端/URI 引用 exists=false、不填 checksum |
| 数字（带单位或明确数值语义，如"分离因子 45"、"能垒 0.8 eV"） | `metrics` | `value` 填数值（数字字符串转 number）、`unit` 填单位；名称用短横线 ID（如 `separation_factor`）；有上下界填 lower/upper_bound；`evidence_level` 按可信度：validated（实验/权威）/ screening（初步计算）/ descriptive（描述性） |
| 支撑材料（"结果来自 X 文件/第 N 行"、"数据见 table.csv"） | `evidence` | `type` 选 artifact（引文件）或 log_excerpt / observation；`artifact_ref`/`metric_ref` 指向对应 id；`quality` 按来源：direct（直接测量）/ derived（推导）/ proxy（间接指标） |
| 判断/结论（"结构是稳定的"、"该方案可行"） | `claims` | `statement` 写完整断言；`status` 按证据强度：supported（有证据支撑）/ provisional（初步判断）/ unsupported（无证据）；`evidence_refs` 挂支撑证据 id；`limitations` 写局限 |
| 会话整体 | `summary` | title（一句话主题）、human_summary（人可读摘要）、key_metric_refs、supported_claim_refs、limitations、next_actions |

**分类优先级**：一个产物只归一类。若既是数字又有结论性质（"能垒 0.8 eV 表明路径可行"）→ 数字进 metrics、判断进 claims，用 evidence_refs 关联。

**无法归类的产物**：放进 `diagnostics`（severity=info，message 说明"未归类产物：<内容>"）——不丢弃，不硬塞。

## 导出流程

### Step 1: 收集并确认输入

按导出模式收集对应信息，**列出清单给用户确认后再继续**；关键信息缺失时向用户询问，不要猜。

- **上传 workflow_definition（默认）**：收集用户对工作流/技能的定义描述，确认清单为——名称（metadata.name / title）、用途（description）、步骤（graph.nodes 的 id/title/kind/executor）、执行方式（execution.default_mode / entrypoints / backend_policy）。**不收集**文件列表、结论、skill 调用数——那些是 workflow_run 归档记录字段。
- **归档 workflow_run（用户明确要求）**：从会话上下文提取「输入」节所列内容（文件路径相对则先解析为绝对路径），确认清单为——文件 N 个、结论 M 条、skill 列表。

### Step 2: 构建 WorkflowDefinition（上传目标）

按以下骨架构建 JSON（官方 OAuth2 文档 8.4 节最简有效示例；所有 id 用 `^[A-Za-z][A-Za-z0-9._:-]{0,127}$` 格式）：

```json
{
  "spec_version": "1.0.0",
  "document_kind": "workflow_definition",
  "metadata": {
    "id": "com.example.my-skill",
    "name": "my-skill",
    "title": "我的技能",
    "version": "1.0.0",
    "description": "这是一个示例技能"
  },
  "interface": {},
  "activation": { "policy": "explicit" },
  "inputs": [],
  "parameters": [],
  "execution": {
    "default_mode": "full",
    "entrypoints": [{ "id": "run", "mode": "full", "executor": { "type": "shell" } }],
    "backend_policy": { "strategy": "fixed", "backends": [{ "id": "local", "kind": "local" }] }
  },
  "graph": {
    "nodes": [{
      "id": "step1",
      "title": "步骤一",
      "kind": "task",
      "executor": { "type": "shell" },
      "presentation": { "data_view": {}, "action_view": {} }
    }],
    "edges": []
  },
  "completion": [{ "id": "done", "description": "完成", "severity": "success" }]
}
```

**字段填充规则（官方 8.4 必填字段）**：

- **metadata**：必填 id（唯一标识，反向域名风格）、name（机器可读名）、title（人类可读标题，不能为空）、version（语义化版本号）、description（不能为空）；
- **interface**：UI 描述对象，最小为 `{}`；
- **activation**：触发策略，`{"policy": "explicit"}`（显式触发）；
- **inputs / parameters**：可为空数组 `[]`；
- **execution**：default_mode + entrypoints（每个 entrypoint 含 id/mode/executor）+ backend_policy（fixed/local）；
- **graph**：DAG 结构，`nodes` 至少 1 个节点，每个节点必须有 `presentation.data_view` 与 `presentation.action_view`（官方 8.6 错误表明确二者不能为空）；`edges` 描述依赖；
- **completion**：完成状态列表（如 `{"id":"done","description":"完成","severity":"success"}`）。

若用户明确要求归档本次会话运行记录，才导出 `workflow_run`（结构见官方文档 8.5 节；校验与语义规则见下）。

### Step 3: 计算文件校验和

对每个本地文件，用脚本计算 sha256 + size_bytes。**必须真实计算**，不得编造或省略。

### Step 4: Schema 校验（必做）

用 `scripts/validate_run.py` 对构建的 JSON 做完整 schema 校验：

```bash
python <skill_dir>/scripts/validate_run.py <output.json>
```

校验失败 → 按报错修正（字段缺失/枚举值错误/id 格式问题）→ 重新校验，直到 `VALID`。**不允许输出未通过校验的 JSON。**

### Step 4.5: 语义合理性校验（必做，A/B 分级，按 document_kind 分流）

Schema 校验只保证"结构合法"，不保证"内容有意义"。**每次导出必须额外做语义检查**（`--semantic`），按两级处理：

**workflow_definition（上传目标，官方 8.4/8.6）**：

A 级（必拦——禁止生成正式文件）：

0. 任意字段出现 NaN/Infinity 非有限数值
1. `metadata.title` 为空
2. `metadata.description` 为空
3. `metadata.id` 为空
4. `metadata.version` 为空
5. `graph.nodes` 为空（DAG 至少 1 个节点）
6. 任一节点的 `presentation.data_view` 缺失
7. 任一节点的 `presentation.action_view` 缺失
8. `metadata.name` 为空

B 级（警告——允许生成，警告进 diagnostics）：

9. `graph.edges` 为空（节点间无依赖边，线性流程）

**workflow_run（归档记录，官方 8.5）**：沿用原规则——A 级：summary.human_summary 为空 / artifacts+metrics+evidence+claims 同时为空且无 diagnostics / metrics[].value 非 number 或 NaN/Infinity / claims[].statement 为空 / workflow_ref 缺 version 或 request.prompt 为空；B 级：数值量级、request 空对象、零字节文件、node_runs 空、backend kind 不明、checksum 长度不匹配等（详见脚本）。

**报告状态（--report-json）**：`status` 取值 `VALID` / `INVALID`（schema 或 strict 失败）/ `SEMANTIC_BLOCKED`（存在 A 级错误）。仅 `VALID` 允许进入上传流程；命令行模式下 schema 层输出 `SCHEMA VALID`，与 `SEMANTIC BLOCKED` 区分，不得用子串匹配判定成功。

**服务端类型约定（上传前必查）**：平台的 Java 数据模型要求 `metadata.title` / `metadata.description` 为**字符串**。本地 schema 的 `LocalizedText` 允许 `{zh_en}` 对象形式，但服务端会拒绝——**上传前必须把所有 title/description 字段统一转换为中文字符串**（实测 2026-08：21 处本地化对象被服务端 4xx 拒绝，转字符串后通过）。

**NaN / Infinity 处理**：Python `json.dump` 默认会把 NaN 写成非标准 `NaN` 字样（其他解析器可能拒收）。**任何 metric/字段值出现 NaN/Infinity 一律按 A 级拦截**，必须修正为合法数值或 null。

### Step 5: 落盘 + 最终校验（必做）

- 输出文件名：`workflowspec.definition.<YYYYMMDD>.json`（上传 definition；归档 run 时用 `workflowspec.run.<YYYYMMDD>.json`；当天多次导出加序号）
- 写到**当前工作目录**
- **落盘后必须对磁盘上的正式文件再跑一次完整校验**（最终交付物校验，不是构建时校验）：

```bash
python <skill_dir>/scripts/validate_run.py <落盘文件> --schema <权威版或副本> --strict --semantic
```

- 必须输出 `VALID` + `Semantic OK (no A/B findings)`（或仅 B 级警告）才算完成
- **最终校验失败** → 回到修正循环（修正 → 重跑 → 重新落盘 → 重新最终校验），不允许交付未通过最终校验的文件
- 对话中给出：输出文件绝对路径、metadata.id（或归档模式的 run_id）、统计（definition：N 个节点；run：N artifacts / M claims / K metrics）、最终校验结果（VALID + 用时）

## 失败/回退路径

**A 级拦截（必填空/数值不合理/NaN）**：

- **不生成正式文件**。落盘为 `workflowspec.run.<YYYYMMDD>.invalid.json` 草稿（显式标记未通过，正式文件名不占用）
- 返回：完整错误清单（哪个字段、为什么、怎么修）+ 修正指引
- agent 补齐输入 / 修正数值 → 重跑 → 直到 VALID 才落盘正式文件
- 修正完成后提示用户删除 `.invalid.json` 草稿

**B 级警告**：

- 生成正式文件，警告写入 `diagnostics`（severity=warning），对话中明确告知用户哪些值可疑、为什么保留

**其他回退**：

- **文件不存在**（用户给了路径但文件已删除）：artifacts 填 exists=false、uri 保留、不填 size/checksum，加 diagnostics 说明
- **输入信息不足**（不知道调用过哪些 skill / 没有结论文字）：允许最小记录——node_runs 空数组、summary 只填 title+human_summary，但必须告知用户哪些部分因信息不足而留空
- **schema 副本与桌面权威版不同步**：以 skill 内副本为准（自包含原则），如发现桌面版更新，提示用户同步副本

**核心原则：正式文件名只属于通过 schema + 语义校验的产物；`.invalid.json` 只是修正用的工作文件，禁止冒充合格产物。**

## 参考

- `references/workflowspec.schema.json` — 权威 schema（必读字段约束）
- `scripts/validate_run.py` — 校验脚本（Step 4 必用）

## 上传流程（#674 新增：方案确认 → MiQroForge dataUpload）

导出与校验完成（Step 1–5 全部通过）后，继续以下步骤。

### Step 6: 渲染 Markdown 方案视图（会话内）

读取刚导出的 JSON，在对话中输出一张 **Markdown 方案视图**，内容必须包含：

- **metadata**：title / name / id / version / description；
- **execution**：entrypoints 与 backend_policy 摘要；
- **graph**：节点表格（id / title / kind / executor）与 edges 依赖；
- **completion**：完成状态列表；
- **validation_report 摘要**：VALID 状态 + A 级错误数（0）+ B 级警告清单（如有）；
- 归档模式（workflow_run）时改用 summary/artifacts/metrics/claims/证据引用 视图。

只读不改：方案视图是上传前的展示，不要在此步骤修改 JSON。

### Step 7: 会话确认（必做，走 #646 确认卡片）

方案视图输出后，**必须调用 `ask_user_confirm_card` 工具**请求确认，不要只在文本里问：

- 卡片标题：`确认上传方案到 MiQroForge 平台？`
- 卡片正文：方案摘要（title、产物统计 N artifacts / M claims / K metrics）+ 上传目标（测试环境 `test.forge.miqroera.com`）
- choices：`confirm`（确认上传）/ `adjust`（调整方案，返回修改）/ `cancel`（取消）

结果处理：

- `confirmed` → 进入 Step 8；
- `cancelled` 且 choice_id 为 `adjust` → 与用户确认要改什么，修改 JSON 后从 Step 4 重新校验，再走 Step 6–7；
- `cancelled`（超时/取消）→ 停止，不上传，告知用户可随时重来。

### Step 8: 凭据检查 + 上传

自检：上传只能通过 `upload_run.py`（dataUpload）完成——`message` 工具发附件只是聊天附件，不是上传，不得替代本步骤。

```bash
# 0) 依赖自检：两个脚本顶层 import httpx，沙箱内可能未安装
#    （pip 安装是临时的，沙箱销毁后不保留，每次新沙箱都要重装）
python -c "import httpx" 2>/dev/null || pip install httpx

# 1) 检查登录态（只输出 {ok, source, expiresAt}，不含 token —— 防止完整凭据进入
#    工具输出与日志；不要运行不带 --no-token 的 auth.py token）
python <skill_dir>/scripts/auth.py token --json --no-token

# 2) 上传：凭据由 upload_run.py 内部解析（token 文件优先 → env → 自管登录兜底），
#    agent 全程不经手 access_token
python <skill_dir>/scripts/upload_run.py <workflowspec.definition.YYYYMMDD.json> --json
```

- `auth.py` 返回 `NOT_LOGGED_IN` → 提示用户：「请到 MiQroForge 设置 → 平台账号 完成登录（浏览器登录或密码登录），登录后我会自动使用你的登录态」，**不要**自行编造凭据；
- `upload_run.py` 返回 `ok:true` → Step 9；
- 返回 `IP_NOT_WHITELISTED` → 提示「出口 IP 未加白，请联系 MiQroForge 管理员」；
- 返回 `TOKEN_EXPIRED` → 提示用户到设置 → 平台账号 重新登录后重试；
- 返回 `BAD_REQUEST` → 把服务端 message 展示给用户，结合校验报告给修正指引；
- 返回 `SERVER_ERROR` → 平台侧问题（如服务端业务错误/缺表），把响应里的 originalMessage 转给用户并建议联系 MiQroForge 管理员；
- 网络类错误（`NETWORK_UNREACHABLE`）→ 脚本已自动重试，仍失败则提示稍后重试。

### 沙箱环境注意事项（实测 2026-09）

在沙箱（bwrap/WSL）内运行本技能时有两个已知坑：

- **httpx 依赖缺失**：`auth.py` / `upload_run.py` 顶层 `import httpx`，沙箱 Python 环境未预装，直接跑会 `ModuleNotFoundError`。运行前自检：`python -c "import httpx" 2>/dev/null || pip install httpx`。注意 pip 安装是**临时**的——沙箱销毁后不保留，每次新沙箱都要重装一次（自管登录兜底路径还会用到 `cryptography`，同样按需临时安装）。
- **token 自动探测路径不可靠**：自动探测基于 cwd / `MIQI_HOME` / `miqi.paths` 三个候选（沙箱内 `import miqi` 失败即跳过），沙箱内三者都定位不到桌面端实际写入位置，会误报 `NOT_LOGGED_IN`。沙箱内**显式传** `--token-file /home/miqi/workspace/.qraft/token.json`（`auth.py` 与 `upload_run.py` 都要传；或先 `export QRAFT_TOKEN_FILE=/home/miqi/workspace/.qraft/token.json`）。

### Step 9: 结果展示与下一步提示

- 成功：展示脱敏后的上传响应原文（实测 body 为纯文本 `ok`），并提示「上传成功，可在 MiQroForge 平台查看方案」；
- 失败：展示分类后的错误与修复指引（见 Step 8 各分支）；
- 全程脱敏：对话中不得出现完整 access_token / 密码 / 手机号；token 只展示首尾片段。

## 凭据管理约定（#674 功能描述 3）

- **主路径**：读取 MiQroForge Desktop 登录态生成的 token 文件 `<workspace>/.qraft/token.json`（沙箱内 `/home/miqi/workspace/.qraft/token.json`），存在且未临期（`expiresAt - now > 5min`）直接使用——用户在设置 → 平台账号 登录后无需任何额外配置（沙箱内自动探测不可靠，需显式 `--token-file`，见上文「沙箱环境注意事项」）；
- **兜底**：环境变量 `QRAFT_ACCESS_TOKEN`（直接可用）；`QRAFT_PHONE` + `QRAFT_PASSWORD`（走自管 RSA 登录，测试阶段；client_secret 有硬编码默认值，可用 `QRAFT_CLIENT_SECRET` 覆盖，转正式接入前移除默认值）；
- **安全**：SKILL.md 与脚本不硬编码任何真实凭据；token/密码/手机号在界面与日志中一律脱敏；
- 读取策略与安全权衡详见 `docs/frontend/qraft-oauth2-login.md` 第 6 节。

## 参考

- `references/workflowspec.schema.json` — 权威 schema（必读字段约束）
- `scripts/validate_run.py` — 校验脚本（Step 4 必用；`--report-json` 输出结构化 validation_report）
- `scripts/auth.py` — 凭据解析（token 文件优先 → env → 自管登录兜底）
- `scripts/upload_run.py` — dataUpload 上传封装（前置校验 + 重试 + 错误分类）
