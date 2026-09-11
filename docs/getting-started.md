# 快速开始

## 前置依赖

| 依赖 | 版本要求 | 用途 |
|------|----------|------|
| Python | 3.11 或 3.12 | 运行 MiQroForge 后端 Agent 引擎 |
| Node.js | 20+ | 运行 Electron 前端 |
| uv | 最新 | Python 包管理与虚拟环境 |
| Git | 2.x | 版本控制与 MCP 子模块管理 |

## 安装

### 1. 克隆仓库（含子模块）

```bash
git clone --recurse-submodules <repo-url>
cd miqi-desktop
```

### 2. 安装 Python 依赖

```bash
uv sync
```

### 3. 安装前端依赖

```bash
cd apps/desktop
npm install
```

## 开发模式

```bash
# 启动 Electron 开发服务器（支持热重载）
cd apps/desktop
npm run dev
```

应用启动后会自动检测 Python 环境并拉起 Bridge 子进程。首次运行会进入 **设置向导**，引导你：

1. 检测运行环境
2. 配置 WSL2（Windows 用户）
3. 配置 LLM 提供商和 API Key

## 生产构建

### 一键打包

```bash
cd apps/desktop
npm run build:all
```

该命令依次执行：Python 后端打包 → 前端编译 → Electron 安装包打包。

### 分步构建

```bash
# 1. Python 后端打包（生成 dist/miqi-bridge.exe）
cd apps/desktop
npm run build:bridge

# 2. 前端编译
npm run build

# 3. 打包安装包（输出到 ../../dist-new/）
npx electron-builder --win --publish never
```

## 配置文件位置

首次运行后，配置文件自动生成在 `~/.miqi/config.json`。详见 [配置参考](configuration.md)。

## 环境变量

| 变量名 | 说明 |
|--------|------|
| `MIQI_PYTHON_PATH` | 自定义 Python 解释器路径 |
| `MIQI_AGENTS__DEFAULTS__MODEL` | 覆盖默认模型 |
