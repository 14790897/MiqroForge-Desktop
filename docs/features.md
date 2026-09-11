# 功能总览

MiQroForge 是一个**本地优先的个人 AI 助手桌面应用**，将强大的 AI Agent 引擎与现代化的 Electron 桌面界面相结合。以下按功能域分类介绍 MiQroForge 能做什么。

---

## 🤖 AI 智能对话

| 能力 | 说明 |
|---|---|
| **自然语言对话** | 与 AI 进行多轮对话，支持流式响应、打字机动画效果 |
| **工具调用可视化** | 实时显示 Agent 正在调用的工具及其参数、执行结果 |
| **中断与恢复** | 随时中止 Agent 执行，不影响已保存的状态 |
| **Markdown 渲染** | 对话内容支持完整的 Markdown 渲染（表格、代码块、任务列表） |
| **代码高亮** | 180+ 编程语言的语法高亮（通过 highlight.js） |
| **Diff 视图** | 文件编辑操作以 diff 对比形式展示变更 |
| **文件附件** | 支持上传图片、文档等文件，Agent 可解析并理解内容 |

---

## 📄 办公文档处理

通过自然语言指令即可完成 Office 文档的读写操作，无需手动打开软件。

### Word 文档 (.docx)

- **读取**：提取 Word 文档中的文本内容、表格数据
- **创建**：支持结构化内容（标题、段落、表格、图片）和 Markdown 风格文本输入
- **编辑**：全文文本替换（段落 + 表格）、追加段落
- **中文排版**：内置 `chinese_document` 预设（标题黑体三号加粗居中，正文宋体小四 1.5 倍行距）
- **自然语言格式**：直接说"正文宋体小四，标题黑体加粗三号字居中"即可生效

### PowerPoint 演示文稿 (.pptx)

- **读取**：提取幻灯片文本、演讲者备注、表格数据
- **创建**：多张幻灯片，每张支持标题、副标题、正文、项目符号、图片
- **图片定位**：可精确控制幻灯片中图片的位置和尺寸

### Excel 电子表格 (.xlsx)

- **读取**：按工作表名称读取数据
- **创建**：多工作表工作簿，支持公式（以 `=` 开头自动识别）、内嵌图表（条形图、折线图、饼图）
- **追加**：向已有工作表追加数据行

### PDF 文档 (.pdf)

- **读取**：三层提取——pypdfium2 快速提取 → pypdf 备用 → Tesseract OCR 扫描件识别（支持中英文混排）
- **创建**：支持标题、各级标题、正文段落、表格（含表头）、列表、分页符
- **中文字体**：自动发现系统中的 CJK 字体（SimSun/SimHei/NotoSansSC 等），覆盖 Linux/WSL/Windows 路径
- **样式预设**：`chinese_document`、`chinese_essay`、`report`
- **页面规格**：支持 A4/Letter/A3，边距按中文公文规范

### 统一文档解析

`document_parser.py` 提供跨 23 种文件格式的统一解析服务，同时用于文件预览和 LLM 上下文注入：

- **Office 文档**：PDF / DOCX / DOC / ODT / PPTX / PPT / ODP / XLSX / XLS / ODS
- **标记语言**：Markdown（提取 Mermaid 图、表格、代码块、标题大纲）/ HTML（lxml，stdlib 回退）
- **数据文件**：CSV / JSON / XML / YAML / ENV / LOG / SQL / INI / TOML / HTACCESS
- **脚本文本**：SH / BASH / TXT / RTF

---

## 📂 文件管理

- **工作区文件浏览器**：树形文件结构，创建、重命名、删除操作
- **文本文件编辑器**：内置编辑器，支持未保存更改检测
- **Markdown 预览**：Markdown 文件即时渲染预览
- **图片查看**：二进制图片文件直接查看
- **模糊搜索**：快速定位工作区内的文件
- **快照/版本控制**：文件写入前自动创建快照，支持回滚、对比差异、接受/撤回变更
- **文件监听**：实时跟踪工作区文件变化

---

## 🧠 记忆系统

- **长期记忆**：Agent 自动记录重要信息（项目事实、用户偏好、发现的问题等），跨会话持久化
- **用户画像**：维护 `USER.md`，记录用户偏好、技能、需求
- **项目知识**：维护 `MEMORY.md`，累积项目相关的技术决策和环境信息
- **经验学习**：自改进课程（lessons），Agent 从每次交互中积累经验
- **会话搜索**：FTS5 全文搜索 + 语义搜索，回溯历史对话

---

## ⏰ 定时任务

- **Cron 调度**：支持标准 5 字段 cron 表达式和时区感知的 ISO 时间戳
- **任务管理**：创建、查看、删除定时任务
- **前端面板**：CronPage 可视化查看和管理所有定时任务

---

## 🔌 多渠道消息集成

MiQroForge 可作为多渠道消息中枢，统一接入以下平台，让 AI 助手在不同平台间无缝响应：

| 渠道 | 协议/方式 |
|---|---|
| **飞书 / Lark** | Webhook + 消息卡片 |
| **钉钉** | Webhook |
| **Slack** | Webhook |
| **Discord** | Bot API |
| **Telegram** | Bot API |
| **QQ** | 协议适配 |
| **Email** | IMAP 轮询 + SMTP 回复 |
| **MoChat** | API 集成 |

所有渠道通过统一的 `BaseChannel` 抽象接口接入，支持入站消息监听和出站回复，并可通过访问控制列表（ACL）限制消息来源。

---

## 🧩 技能系统 (Skills)

技能是 Agent 能力的模块化扩展，每个技能是一个包含 `SKILL.md` 的目录，遵循 OpenClaw 兼容格式。

### 内置技能

| 技能 | 用途 |
|---|---|
| `docx` | OOXML 原生操作，注释支持，接受修订 |
| `pdf` | PDF 表单填写、注释、边界框检查 |
| `xlsx` | Excel 公式重算、数据恢复 |
| `pptx-generator` | PPT 生成、编辑、设计系统 |
| `github` | 通过 `gh` CLI 与 GitHub 交互 |
| `cron` | 定时提醒和重复任务 |
| `memory` | RAM-first 记忆系统，短期 + 长期回顾 |
| `paper-research` | 学术论文搜索、下载、翻译、总结 |
| `feishu-report` | 飞书内容推送（文本/卡片/文档/日历/任务） |
| `summarize` | 总结 URL、文件和 YouTube 视频 |
| `tmux` | 远程操控 tmux 会话 |
| `weather` | 通过 wttr.in 和 Open-Meteo 获取天气 |
| `workspace-cleanup` | 整理工作区目录 |
| `skill-creator` | 创建新技能的向导 |

### SkillHub 在线市场

- 从社区注册中心 `skills.sixiangjia.de` 浏览和搜索公开技能
- 一键安装到本地
- 前端页面：`SkillHubPage.tsx`

---

## 🔌 插件系统 (Plugins)

- **插件格式**：`plugin.json` 清单，可包含 MCP 服务器、技能、斜杠命令、生命周期钩子
- **安装方式**：从 GitHub/GitLab/Bitbucket HTTPS URL 克隆安装
- **管理页面**：PluginMarket，支持启用/禁用/卸载、查看版本和状态
- **钩子系统**：支持 `pre_tool` / `post_tool` / `on_error` 生命周期注入

---

## 🌐 网络搜索

- **web_search**：通过 Brave / SearXNG / Hybrid 搜索引擎检索互联网
- **web_fetch**：抓取网页内容，使用 readability-lxml 提取正文，内置 SSRF 防护（阻止私有 IP 网段和元数据服务）

---

## 🎓 学术论文

- **paper_search**：通过 arXiv API 搜索学术论文
- **paper_get**：获取论文元数据（标题、作者、摘要、分类等）
- **paper_download**：通过 Sci-Hub 下载 PDF，自动检测付费墙

---

## 🛡️ 安全与沙箱

### 执行管道

所有工具执行经过四阶段安全管道：
```
审批 (ApprovalPolicy) → 沙箱选择 (SandboxPolicyEngine) → 执行 (ToolRegistry) → 重试 (指数退避)
```

### WSL2 + bwrap 沙箱

- **Windows 平台**：通过 WSL2 隔离执行环境，bwrap 提供 per-session 容器化
- **LANDLOCK 规则**：文件系统级网络策略控制
- **文件隔离**：独立 tmpfs + bind mount，进程/PID/UTS/User 命名空间隔离
- **流式 I/O**：支持 stdin/stdout 流式交互，PTY 大小动态调整
- **自动清理**：会话结束自动销毁沙箱，异常退出后重启自动清理

### 权限系统

- **审批策略**：`AUTO`（自动批准安全工具）/ `ASK`（每次确认）/ `DENY`（拒绝）
- **危险命令检测**：39 种危险命令模式自动触发审批
- **工作区隔离**：文件操作默认限制在工作区目录内
- **快照保护**：写入前自动创建原始内容快照，支持回滚
- **默认拒绝**：deny-by-default 权限策略

---

## 🔧 开发者工具

| 工具 | 说明 |
|---|---|
| **Shell 执行** | 在沙箱内执行 Shell 命令，支持流式 I/O |
| **代码补丁** | Unified Diff 多文件多 hunk 补丁应用，集成版本快照 |
| **进程管理** | 进程生成、终止、列出、快照 |
| **Git 集成** | 通过 `github` skill 调用 `gh` CLI（PR、Issue、Review 等） |
| **SLURM** | 高性能计算集群作业管理 |

---

## 🤖 多模型提供商

支持多家 LLM 提供商，带故障转移和负载均衡：

- **Anthropic**（Claude 系列）
- **OpenAI**（GPT 系列）
- **Google Gemini**
- **OpenRouter**（聚合平台）
- **DeepSeek**
- 可扩展的 Provider 接口，支持添加自定义提供商

通过 `ProvidersPage` 前端页面可视化管理 API Key 和模型配置。

---

## 📊 计划与任务追踪

- **PlanTracker**：步骤状态管理（pending → in_progress → completed → skipped），前端实时更新
- **任务追踪**：`task_begin` / `task_end` 覆盖自动生成的追踪名称，设置目标和父哈希实现跨会话血缘追踪

---

## 💬 会话管理

- **会话列表**：按时间排序，显示标题和预览
- **归档与恢复**：支持归档不需要的会话，在 Settings → Archived 恢复或永久删除
- **搜索**：FTS5 全文搜索历史对话
- **导入/导出**：会话数据完整导入导出
- **懒加载**：每页 20 个会话，无限滚动
- **批量操作**：全选删除、全选归档

---

## 🔗 MCP 协议集成

通过 Model Context Protocol 接入外部工具服务器：

- 动态加载 MCP 工具，自动注册到工具注册表
- 进度心跳：长时任务每 15 秒报告进度
- 超时控制：每个 MCP 工具可独立设置超时
- 连接复用：MCP 进程保持存活，避免重复启动
- 前端管理：MCPsPage 可视化管理 MCP 服务器

---

## 🖥️ 设置向导

首次启动自动进入三阶段设置引导：

```
环境检测 → WSL2 配置（仅 Windows） → LLM 提供商配置 → 开始使用
```

- 自动检测 Python 版本和依赖（打包版本直接使用内置 `miqi-bridge.exe`）
- Windows 下自动检测并引导安装 WSL2 沙箱发行版
- 非 Windows 系统自动跳过 WSL2 步骤

---

## 📱 功能页面一览

| 页面 | 导航 ID | 功能 |
|---|---|---|
| **聊天** | `chat` | AI 对话主界面 |
| **工作区** | `workspace` | 文件浏览、编辑、预览、版本管理 |
| **会话** | `sessions` | 会话历史浏览、搜索、归档、导入导出 |
| **计划** | `plan` | 步骤状态追踪 |
| **智能体** | `agents` | Agent 管理和状态监控 |
| **MCP** | `mcps` | MCP 服务器管理 |
| **定时任务** | `cron` | Cron 任务管理 |
| **记忆** | `memory` | 记忆和经验数据管理 |
| **经验** | `experience` | 经验数据面板 |
| **技能** | `skills` | 本地技能管理 + SkillHub 市场 |
| **插件** | `plugins` | 插件市场管理 |
| **WSL** | `wsl` | WSL2 沙箱状态 |
| **权限** | `permissions` | 权限策略管理 |
| **审批** | `approvals` | 执行审批队列管理 |
| **设置** | `settings` | 全局系统设置（含 15+ 子标签页） |
