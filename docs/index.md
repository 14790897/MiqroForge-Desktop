# MiQroForge Desktop

<p align="center">
  <em>基于 Electron 的轻量级个人 AI 助手桌面应用</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12-blue" alt="Python" />
  <img src="https://img.shields.io/badge/node.js-20+-green" alt="Node.js" />
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Alpha" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT" />
</p>

---

## 概述

MiQroForge Desktop 是一款基于 **Electron** 构建的桌面应用，为 MiQroForge AI 代理提供现代化的图形界面。它将强大的 **Python AI Agent 引擎** 与直观的 **React + TypeScript** 用户界面相结合。

### 核心定位

- 🎯 **个人 AI 助手**：非聊天机器人，而是一个有记忆、有技能、能操作文件的桌面 AI
- 🔧 **高度可扩展**：通过 MCP 协议集成外部工具，支持自定义技能和提供商
- 🖥️ **真实桌面体验**：Electron 原生窗口，系统级集成（WSL2 沙箱、文件系统操作）
- 🔒 **本地优先**：所有数据本地存储，支持文件版本控制的非破坏性编辑

### 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 桌面框架 | Electron | 35.2 |
| 前端 UI | React + TypeScript | 19.1 / 5.8 |
| CSS | Tailwind CSS 4 | 4.x |
| 组件库 | Radix UI + Lucide Icons | - |
| 后端引擎 | Python (asyncio) | 3.11+ |
| CLI 框架 | Typer | 0.20+ |
| 数据模型 | Pydantic v2 | 2.12+ |
| 构建工具 | electron-vite + electron-builder | 3.1 / 26.0 |
| Python 打包 | PyInstaller + Hatchling | 6.20+ |

### 快速链接

- [功能总览](features.md) — 了解 MiQroForge 能做什么
- [快速开始](getting-started.md) — 安装和运行
- [系统架构](architecture.md) — 整体设计
- [配置参考](configuration.md) — 完整配置项说明
- [MCP 集成](mcp-integration.md) — 外部工具集成
- [CLI 参考](backend/bridge.md) — 命令行与 Bridge API
- [开发者笔记](dev-notes/README.md) — 开发实战经验与踩坑记录（AI 会话记忆导出）
