# MiQroForge Desktop

<p align="center">
  <em>🐈‍⬛🪶 轻量级、可扩展的个人 AI 代理框架，带现代化桌面界面</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12-blue" alt="Python 3.11 | 3.12" />
  <img src="https://img.shields.io/badge/node.js-20+-green" alt="Node.js 20+" />
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Development Status: Alpha" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License" /></a>
</p>

---

## 概述

MiQroForge 是一个个人 AI 代理框架，将强大的 **Python 运行时引擎** 与 **Electron 桌面应用** 相结合。提供类型化应用服务器协议、请求验证、多提供商 LLM 支持、沙箱化命令执行以及插件/技能生态——全部采用本地优先、尊重隐私的架构。

### 核心定位

- 🎯 **个人 AI 代理** — 不只是聊天机器人：持久化记忆、学习技能、文件操作、定时任务
- 🔧 **高度可扩展** — MCP 协议集成外部工具、自定义技能、可插拔 LLM 提供商
- 🖥️ **原生桌面体验** — Electron 系统级集成（WSL2 沙箱、文件系统操作）
- 🔒 **本地优先** — 所有数据本地存储；带版本快照的非破坏性文件编辑
- 📋 **类型化应用协议** — 类型化 AppServer，JSON Schema 目录，方法稳定性追踪，处理器边界验证

### 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 桌面框架 | Electron | 35.2 |
| 前端 UI | React + TypeScript | 19.1 / 5.8 |
| CSS | Tailwind CSS 4 | 4.x |
| 组件库 | Radix UI + Lucide Icons | — |
| 后端引擎 | Python (asyncio) | 3.11+ |
| 数据验证 | Pydantic v2 | 2.12+ |
| CLI 框架 | Typer | 0.20+ |
| 桌面构建 | electron-vite + electron-builder | 3.1 / 26.0 |
| Python 打包 | PyInstaller + Hatchling | 6.20+ |

---

## 主要特性

| 功能 | 描述 |
|---|---|
| **智能聊天** | 自然语言对话，流式响应，工具调用进度实时显示 |
| **多提供商** | OpenAI、Anthropic、Gemini、OpenRouter、DeepSeek 等，带提供商容错 |
| **类型化协议** | 类型化 AppServer，方法规范，JSON Schema 目录，处理器边界验证 |
| **记忆系统** | 长期记忆快照、自改进课程、跨会话回忆 |
| **任务调度** | 基于 Cron 的定时任务，支持时区设置 |
| **技能系统** | 创建、上传、管理代理技能；SkillHub 注册中心集成 |
| **插件生态** | MCP 服务器、插件和市场的确定性目录 |
| **沙箱执行** | 基于 bwrap 的沙箱，LANDLOCK 文件系统规则，流式 I/O，进程生命周期管理 |
| **文件管理** | 工作区 FS 带文件监听、模糊搜索、快照/版本控制、非破坏性编辑 |
| **回放调试** | 回合、时间线和消息的确定性回放用于检查 |
| **会话管理** | 浏览、搜索、归档、导入/导出对话历史 |
| **桌面应用** | 15+ 功能页面，实时流式传输，打字机动画，右键菜单 |

---

## 快速开始

### 前置依赖

- **Python 3.11+** — 运行 MiQroForge 后端
- **Node.js 20+** — 运行 Electron 前端
- **uv** — Python 包管理器（推荐）

> **Windows 用户**可通过 winget 一键安装 uv 和 Node.js（Windows 10 1709+ / Windows 11 自带 winget；若没有，请先从 Microsoft Store 安装「应用安装程序」）：
>
> ```bash
> # 安装 uv
> winget install --id astral-sh.uv -e
>
> # 安装 nvm-windows，并安装 Node.js 22
> winget install --id CoreyButler.NVMforWindows -e
> nvm install 22
> ```
>
> 安装 nvm-windows 后请新开一个终端窗口再执行 `nvm`。

### 安装步骤

```bash
# 1. 克隆仓库
git clone http://git.miqroera.com/intership/miqi-desktop.git
cd miqi-desktop

# 2. 安装 Python 依赖
uv sync

# 3. 安装前端依赖
cd apps/desktop
npm install
```

### 开发模式

```bash
# 启动 Electron 开发服务器（带热重载）
cd apps/desktop
npm run dev
```

### 生产构建

**一键打包**（推荐）：

```bash
cd apps/desktop
npm run build:all    # Python 后端 → 前端编译 → Electron 打包
```

**分步构建**：

```bash
cd apps/desktop

# 1. 构建 Python 后端（生成 dist/miqi-bridge.exe）
npm run build:bridge

# 2. 编译前端
npm run build

# 3. 打包为桌面应用
npx electron-builder --win --publish never
```

打包后的 `miqi-bridge.exe` 是自包含的二进制文件（PyInstaller onefile），内嵌 Python 和全部依赖——目标机器无需安装 Python。支持 `--check` 自检模式：

```bash
miqi-bridge.exe --check
# 输出: {"ok": true, "python_version": "3.12.10", "issues": []}
```

---

## 架构说明

```
┌─────────────────────────────────────────────────────────────┐
│                    MiQroForge Desktop App                   │
├─────────────────────────────────────────────────────────────┤
│  Electron Frontend                                          │
│  ├── React 19 + TypeScript                                 │
│  ├── Tailwind CSS 4 + shadcn/ui                            │
│  └── 15+ 功能页面 (Chat, Agents, Skills, MCPs, ...)        │
├─────────────────────────────────────────────────────────────┤
│  Bridge (IPC 通信)                                          │
│  ├── stdin/stdout JSON-line 协议                            │
│  ├── 状态同步 + 日志转发                                     │
│  └── BridgeRuntimeLoop (持久化 asyncio 事件循环)             │
├─────────────────────────────────────────────────────────────┤
│  AppServer (类型化协议层)                                     │
│  ├── ProtocolRegistry (类型化方法规范)                        │
│  ├── 类型化信封 (Pydantic v2)                                │
│  ├── JSON Schema Draft 2020-12 目录                          │
│  └── 处理器类型化验证                                          │
├─────────────────────────────────────────────────────────────┤
│  MiQroForge Runtime Engine (运行时引擎)                      │
│  ├── RuntimeSession / TaskRunner / TurnRunner               │
│  ├── HistoryRuntime + LedgerRuntime (SQLite 持久化)          │
│  ├── ContextRuntime (压缩、token 预算)                       │
│  ├── ThreadRuntime (fork、rollback、导入/导出)               │
│  └── ReplayRuntime (确定性回放检查)                           │
├─────────────────────────────────────────────────────────────┤
│  Execution & Sandbox (执行与沙箱)                            │
│  ├── ToolOrchestrator (审批 → 沙箱 → 执行)                  │
│  ├── PermissionEngine + ApprovalPolicy + HookRuntime        │
│  ├── bwrap 沙箱 (LANDLOCK、流式 I/O、取消)                   │
│  └── Workbench Process Runtime (command/exec、process/*)    │
├─────────────────────────────────────────────────────────────┤
│  Tools & Integrations (工具与集成)                           │
│  ├── 内置工具 (文件系统、Shell、网络、论文、...)              │
│  ├── MCP Client (外部工具服务器)                             │
│  ├── Plugin Manager + Skill Loader (插件管理 + 技能加载)     │
│  └── Office 文档工具 (docx、pptx、xlsx)                     │
└─────────────────────────────────────────────────────────────┘
```

1. 启动应用后，进入设置向导：
   - **环境检测** — 验证 Python 和依赖（打包环境自动检测 bundled exe；开发环境检查系统 Python）
   - **WSL2 配置** — （仅 Windows）自动检测并安装 WSL2，用于沙箱功能
   - **LLM 提供商** — 配置 API 密钥和默认模型
2. 开始与 AI 代理聊天
### 协议方法族

| 族 | 作用域 | 方法 |
|--------|-------|---------|
| `turn/*` | Turn | start, interrupt, steer |
| `thread/*` | Thread | list, get, rollback, fork, delete, compact/start, inject_items |
| `fs/*` | Filesystem | readFile, writeFile, createDirectory, getMetadata, readDirectory, remove, copy, watch, unwatch |
| `fuzzyFileSearch/*` | Filesystem | sessionStart, sessionUpdate, sessionStop |
| `command/exec` | Process | exec, exec/write, exec/resize, exec/terminate |
| `process/*` | Process | spawn, writeStdin, resizePty, kill, list, get, snapshot |
| `replay.*` | Debug | turns, timeline, messages |
| `config/*` | Session | get, batchWrite |
| `model/*` | Session | list, get |
| `feature/*` | Session | list, set |
| `permission/*` | Session | listProfiles, getProfile |
| `plugin/*` | Session | list, install, uninstall, enable, disable, configure |
| `skills/*` | Session | list, get, create, upload, delete, setExtraRoots |
| `mcp/*` | Session | listServers, getServer, status |
| `agent/*` | Session | list, get, spawn, kill |
| `protocol/*` | Connection | catalog, method_names, schema |

---

## 配置说明

应用配置文件位于 `~/.miqi/config.json`：

```json
{
  "providers": {
    "openai": { "apiKey": "sk-..." },
    "anthropic": { "apiKey": "sk-ant-..." }
  },
  "agents": {
    "defaults": {
      "model": "claude-sonnet-4-6",
      "temperature": 0.1,
      "maxToolIterations": 100
    }
  },
  "tools": {
    "restrictToWorkspace": true
  }
}
```

### 环境变量

| 变量名 | 说明 |
|---|---|
| `MIQI_PYTHON_PATH` | 自定义 Python 解释器路径 |
| `MIQI_AGENTS__DEFAULTS__MODEL` | 覆盖默认模型 |

---

## 架构说明

```
┌─────────────────────────────────────────────────────────────┐
│                    MiQroForge Desktop App                   │
├─────────────────────────────────────────────────────────────┤
│  Electron 前端                                              │
│  ├── React + TypeScript                                    │
│  ├── Tailwind CSS                                          │
│  └── Radix UI 组件库                                       │
├─────────────────────────────────────────────────────────────┤
│  Bridge 通信层 (IPC)                                        │
│  ├── stdout/stderr JSON 协议                                │
│  ├── 状态同步                                              │
│  └── 日志转发                                              │
├─────────────────────────────────────────────────────────────┤
│  MiQroForge Python 运行时                                  │
│  ├── AgentLoop (核心代理引擎)                               │
│  ├── Memory System (记忆系统)                               │
│  ├── Tool Registry (工具注册)                               │
│  ├── Provider Interface (提供商接口)                         │
│  └── Channel Bus (飞书 / 微信 / 钉钉)                       │
├─────────────────────────────────────────────────────────────┤
│  沙箱层 (WSL2 + bwrap)                                     │
│  ├── Per-session 隔离                                      │
│  ├── 文件系统沙箱                                          │
│  └── 安全代码执行                                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 开发指南

### 项目结构

```
miqi-desktop/
├── miqi/                         # Python 后端
│   ├── runtime/                  # 运行时引擎 (AppServer, Session, Turn, Thread, Replay, Agent, MCP, ...)
│   ├── agent/                    # Agent 逻辑、工具、记忆、追踪、上下文压缩、智能路由
│   ├── bridge/                   # Electron 桥接服务 (IPC 协议)
│   ├── bus/                      # 内部消息总线 (异步输入/输出队列)
│   ├── execution/                # 工具编排器、权限、审批、钩子、沙箱策略
│   ├── providers/                # LLM 提供商实现 + 容错
│   ├── protocol/                 # 类型化命令、事件、权限 (运行时-前端协议)
│   ├── channels/                 # 聊天渠道适配器 (飞书、Slack、Discord、Telegram、...)
│   ├── sandbox/                  # bwrap 沙箱管理器
│   ├── skills/                   # 内置技能 (cron、论文研究、飞书报告、...)
│   ├── session/                  # 会话管理 (Manager、SQLite 存储)
│   ├── config/                   # 配置加载器和 schema
│   ├── cli/                      # CLI 命令 (agent、gateway、trace、config)
│   ├── cron/                     # Cron 调度服务
│   ├── context/                  # 上下文片段、线程存储
│   ├── heartbeat/                # 心跳服务
│   ├── plan/                     # 计划追踪器和工具
│   ├── documents/                # Office 文档工具 (docx、pptx、xlsx)
│   ├── observability/            # OpenTelemetry 集成
│   ├── server/                   # 服务器资产和配置
│   ├── templates/                # 模板
│   ├── tui/                      # 终端 UI (基于 Textual)
│   └── utils/                    # 工具函数
├── apps/
│   └── desktop/                  # Electron 前端
│       ├── src/main/             # 主进程 (BridgeManager、IPC 处理器)
│       ├── src/renderer/         # 渲染进程 (React 页面和组件)
│       └── src/preload/          # 预加载脚本 (contextBridge API)
├── tests/                        # 测试套件 (~150+ 测试文件)
│   ├── runtime/                  # 运行时单元和集成测试 (~70+ 文件)
│   ├── bridge/                   # 桥接协议和审计测试 (~20+ 文件)
│   ├── execution/                # 沙箱、权限、编排测试
│   ├── providers/                # LLM 提供商测试
│   ├── protocol/                 # 协议命令/事件/权限测试
│   └── agent/tools/              # 工具级测试
├── docs/                         # 文档 (MkDocs)
├── plan/                         # 实现计划 (不纳入版本交付物)
└── scripts/                      # 构建和工具脚本
```

### 代码规范

- **Python**: 使用 Ruff 进行代码检查 (行宽 100)
- **TypeScript**: 使用 ESLint 进行代码检查
- **提交信息**: 遵循 Conventional Commits 规范

### 测试

#### E2E 端到端测试

```bash
# Electron E2E（完整桌面应用 + bridge + LLM）
cd apps/desktop
npm run build && npx playwright test --config=playwright.config.ts --project=electron
```

| 平台 | E2E 覆盖范围 | 备注 |
|---|---|---|
| **Linux** (Ubuntu CI) | 全部 ✓ | bwrap 沙箱 + 所有 spec |
| **Windows** (WSL CI) | 全部 ✓ | WSL bwrap 沙箱 + 所有 spec（需要 `MIQI_RUN_SANDBOX_E2E=1`） |
| **macOS** (CI) | 仅非沙箱 | bwrap 不可用；沙箱 spec 通过 `--grep-invert` 排除 |

> **macOS 已知限制**：「重启 recall」E2E 测试 (`session-context-recall.spec.ts`) 仅在 `process.env.CI && process.platform === 'darwin'` 时被跳过（本地 macOS 运行仍会执行该测试）。macOS ARM64 CI runner 上应用完全重启后，即使 sidebar 会话标题加载正确且 bridge 报告 `running / initialized`，会话历史（聊天消息）也无法在 `<main>` 中渲染。这很可能是 bridge IPC 时序问题或 APFS/SQLite WAL checkpoint 在冷启动时的竞态问题——需要原生调试。不涉及重启的会话切换 recall 测试仍然在 macOS 上验证 #490 行为。

```bash
# Python 后端测试（~1800+ 测试）
uv run pytest

# 跳过沙箱/子进程测试以快速反馈
uv run pytest -m "not sandbox and not subprocess"

# 前端测试
cd apps/desktop
npm run test
```

---

## 文档

- [快速开始](docs/getting-started.md)
- [系统架构](docs/architecture.md)
- [配置参考](docs/configuration.md)
- [MCP 集成](docs/mcp-integration.md)
- [开发指南](docs/developer-guide.md)
- [内部 Alpha 冒烟测试](docs/internal-alpha-smoke.md)

---

## 许可证

[MIT License](LICENSE)

---

## 贡献

欢迎提交 Issue 和 Pull Request！请参考 [CONTRIBUTING.md](CONTRIBUTING.md) 获取详细信息。
