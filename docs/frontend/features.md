# 功能页面

MiQroForge Desktop 前端包含 15 个功能页面，通过左侧 Sidebar 导航切换。

## 页面总览

| 页面 | 组件 | 功能描述 |
|------|------|----------|
| Chat | `ChatConsole.tsx` | AI 对话主界面 |
| Sessions | `SessionExplorer.tsx` | 会话历史管理 |
| Providers | `ProvidersPage.tsx` | LLM 提供商配置 |
| Memory | `MemoryPage.tsx` | 记忆与经验管理 |
| Skills | `SkillsPage.tsx` | 本地技能管理 |
| SkillHub | `SkillHubPage.tsx` | 公开技能市场 |
| Settings | `SettingsPage.tsx` | 全局系统设置 |
| Setup | `SetupWizard.tsx` | 首次运行设置向导 |
| MCPs | `MCPsPage.tsx` | MCP 服务管理 |
| Workspace | `WorkspacePage.tsx` | 工作区文件浏览 |
| Cron | `CronPage.tsx` | 定时任务管理 |
| Channels | `ChannelsPage.tsx` | 消息通道配置 |
| Approvals | `ApprovalsPage.tsx` | 命令审批管理 |
| Experience | `ExperiencePage.tsx` | 经验数据面板 |

## 核心页面详解

### ChatConsole — 聊天界面

- **Markdown 渲染**：`react-markdown` + `remark-gfm`（支持表格、代码块、任务列表）
- **代码高亮**：`highlight.js` 集成，支持 180+ 语言
- **`<think>` 块过滤**：自动去除推理模型的思考块（2026-05-25）
- **工具调用进度**：实时显示 Agent 正在调用的工具和参数
- **Typewriter 动画**：流式文本的逐字渲染效果
- **对话中断**：支持随时中止 Agent 执行

### SetupWizard — 设置向导

三阶段引导流程：

```
环境检测 → WSL2 配置 → LLM 提供商 → 完成
```

- **环境检测**：检查 Python 版本和依赖包（打包环境下自动检测 bundled `miqi-bridge.exe`，无需系统安装 Python）
- **WSL2 配置**（Windows 专属）：自动检测安装状态，一键安装
- **LLM 提供商**：配置 API Key 和默认模型
- **非 Windows 自动跳过** WSL2 步骤

### SessionExplorer — 会话管理

- **会话列表**：按时间排序，显示标题和预览
- **归档功能**：悬停显示归档按钮，隐藏不需要的会话
- **归档恢复**：Settings → Archived 标签恢复或永久删除
- **文件追踪**：查看会话中修改的文件，支持 diff/revert/accept
