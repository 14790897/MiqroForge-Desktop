# 开发指南

## 开发环境搭建

### 必备工具

- Python 3.11+ 和 uv
- Node.js 20+ 和 npm
- Git (含子模块支持)
- WSL2 (Windows 用户必需，Sandbox 依赖)
- bubblewrap (安装在 WSL 发行版内)

### WSL Sandbox 环境准备

项目在 Windows 上通过 WSL2 运行 bwrap 沙箱，需要先准备沙箱发行版：

```powershell
# 1. 导出已有的 WSL 发行版（如 Ubuntu）为镜像
wsl.exe --export Ubuntu C:\TempSandbox\ubuntu-full.tar.gz

# 2. 导入为独立沙箱发行版
wsl.exe --import AIShadowSandbox C:\TempSandbox\ActiveInstance C:\TempSandbox\ubuntu-full.tar.gz --version 2

# 3. 在沙箱发行版中安装 bubblewrap
wsl.exe -d AIShadowSandbox -- bash -c "apt-get update && apt-get install -y bubblewrap"

# 4. 确认安装
wsl.exe -d AIShadowSandbox -- bash -c "bwrap --version"
```

### 克隆并初始化

```bash
git clone --recurse-submodules <repo-url>
cd miqi-desktop
uv sync
cd apps/desktop && npm install
```

### 启动开发模式

```bash
cd apps/desktop
npm run dev
```

开发模式下：
- **Renderer (React UI)**：Vite HMR 热更新，修改即时生效
- **Main Process (Electron)**：修改后需重启应用
- **Bridge Server (Python)**：修改后需重启应用

## 架构概览

```
┌────────────────────────────────────────────────┐
│  Electron App                                   │
│  ┌──────────┐  IPC   ┌──────────────────────┐  │
│  │ Renderer │◄──────►│ Main Process          │  │
│  │ (React)  │        │  ┌──────────────────┐ │  │
│  │          │        │  │ BridgeManager     │ │  │
│  └──────────┘        │  │ (Python 子进程)    │ │  │
│                       │  │  stdin/stdout     │ │  │
│                       │  │  JSON-line 协议   │ │  │
│                       │  └──────────────────┘ │  │
│                       └──────────────────────┘  │
└────────────────────────────────────────────────┘
         │
         │ wsl.exe -d AIShadowSandbox
         ▼
┌────────────────────────────────────────────────┐
│  WSL2 AIShadowSandbox                           │
│  ┌──────────────────────────────────────────┐  │
│  │  bwrap sandbox (per-session)              │  │
│  │  - 文件系统隔离 (tmpfs + bind mounts)      │  │
│  │  - 网络共享 (share_net=True)              │  │
│  │  - PID/UTS/User 命名空间隔离              │  │
│  └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────┘
```

前后端通过 **JSON-line stdin/stdout** 协议通信，每行一个完整的 JSON 消息，格式为 `["CMD", {...payload}]`。

## 开发工作流

### Python 后端开发

```
miqi/
├── agent/          Agent 引擎
│   ├── loop.py         AgentLoop — LLM 调用循环
│   ├── context.py      ContextBuilder — 上下文注入
│   └── tools/          工具实现
│       ├── shell.py        ExecTool — Shell 执行（集成 Sandbox）
│       ├── filesystem.py   文件操作（支持 WSL 路径映射）
│       └── web.py          网络搜索与抓取
├── bridge/
│   ├── server.py      Bridge Server — IPC handler + SandboxManager
│   └── protocol.py     JSON-line 协议解析
├── config/
│   ├── schema.py      Pydantic 配置模型（含 SandboxConfig）
│   └── loader.py      配置加载（~/.miqi/config.json）
├── providers/         LLM 提供商适配
├── sandbox/
│   ├── bwrap.py       BwrapSandbox — per-session bwrap 封装
│   └── manager.py     SandboxManager — 沙箱生命周期管理
└── memory/            三层记忆系统
```

关键模块说明：

| 模块 | 职责 |
|------|------|
| `AgentLoop` | 管理 LLM 调用循环，处理工具调用结果，注入上下文 |
| `BridgeServer` | 57 个 IPC handler，管理 Bridge 状态、SandboxManager、配置热更新 |
| `BwrapSandbox` | 封装 bwrap 命令构建与执行，自动检测 Windows+WSL 环境 |
| `SandboxManager` | 管理 per-session 沙箱生命周期，状态落盘到 `~/.miqi/sandbox_state.json` |
| `SandboxConfig` | 沙箱配置：`enabled`、`share_net`、`wsl_distro`、`sandbox_distro_name`、`max_sandboxes` 等 |

### 前端开发

```
apps/desktop/src/
├── main/             Electron 主进程
│   ├── bridge.ts         BridgeManager — Python 子进程管理
│   └── ipc/              IPC handler 注册
│       └── index.ts          PYTHON_CHECK 等环境检查
├── preload/           contextBridge 安全 API
│   └── index.ts           暴露 window.miqi.* 命名空间
├── renderer/          React 19 UI（HMR 热更新）
│   ├── components/        通用组件
│   ├── pages/             15 个功能页面
│   └── hooks/             自定义 Hooks
└── shared/            前后端共享类型
    └── ipc.ts             IPC 通道 + Zod Schema 定义
```

修改规则：

| 修改范围 | 生效方式 |
|----------|----------|
| `renderer/` | HMR 热更新，无需重启 |
| `main/` | 需重启应用 |
| `preload/` | 需重启应用 |
| `shared/` | 前后端同步，需重启 |

### 添加新的 IPC 通道

1. **shared/ipc.ts** — 定义 IPC 常量 + Zod Schema（入参和返回值类型）
2. **main/ipc/** — 实现 Main 进程 handler（调用 BridgeManager）
3. **bridge/server.py** — 实现 Bridge handler（业务逻辑）
4. **preload/index.ts** — 通过 `contextBridge` 暴露 API
5. **renderer/** — 调用 `window.miqi.*` API

### 沙箱配置

`~/.miqi/config.json` 中 `tools.sandbox` 段：

```json
{
  "tools": {
    "sandbox": {
      "enabled": true,
      "share_net": true,
      "allow_system_installs": false,
      "max_sandboxes": 10,
      "auto_cleanup": true,
      "wsl_distro": "AIShadowSandbox",
      "sandbox_distro_name": "AIShadowSandbox",
      "wsl_base_dir": "/tmp/miqi-sandboxes"
    }
  }
}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | true | 启用沙箱隔离 |
| `share_net` | bool | true | 共享宿主机网络（默认开启，沙箱内可直接联网、`pip install` 等；设为 false 则隔离网络命名空间 `--unshare-net`，沙箱内无外网） |
| `allow_system_installs` | bool | false | 将系统包安装命令（`sudo apt-get install ...` 等）路由到 WSL 发行版以 root 执行。沙箱内无 root 且 `/usr` 等系统目录只读，apt 永远无法在沙箱内安装；开启后安装命令会改为在 WSL 发行版中执行，**安装一次、跨会话持久**，并立即通过沙箱对发行版系统目录的 ro-bind 在沙箱内可见（如 `xelatex`）。默认关闭：在 WSL 发行版中执行 root 命令属于主动让渡的权限，需用户显式开启。仅 Windows + WSL 生效（#759） |
| `max_sandboxes` | int | 10 | 最大并发沙箱数 |
| `auto_cleanup` | bool | true | 会话结束时自动清理沙箱 |
| `wsl_distro` | str | "AIShadowSandbox" | WSL 发行版名称 |
| `sandbox_distro_name` | str | "AIShadowSandbox" | 专用沙箱发行版（优先级高于 wsl_distro） |
| `wsl_base_dir` | str | "/tmp/miqi-sandboxes" | WSL 内沙箱根目录 |

> **重型工具链（LaTeX 等）安装**：需要 xelatex/编译器这类系统级工具时，
> 在 `allow_system_installs: true` 下直接让 agent 执行
> `sudo apt-get install -y texlive-xetex` 即可——命令会被路由到 WSL 发行版
> 以 root 执行（`wsl.exe -d <distro> -u root`），一次安装，跨会话可用。
>
> 路由只接受单一安装命令（含 `-y`/`--non-interactive` 等安全选项，及
> `pkg:arch`、`pkg=ver` 形式的包名）；`dist-upgrade`/
> `full-upgrade`、本地包文件安装（任何含 `/` 的包名——多段相对路径
> `pkgs/x.deb` 同样拒绝，沙箱内的 agent 不能以 root 执行其自产文件；
> `.deb`/`.rpm`/`.apk` 等包文件后缀也拒绝）、绝对路径、自定义选项
> （`-o`/`--config` 等）与复合命令会被安全护栏拦截。沙箱被显式禁用
> （`enabled: false`）时不参与路由；沙箱策略禁止网络访问（BLOCK_ALL）
> 时安装也会被拦截（安装必须联网下载包）。长安装（如 texlive）期间
> 会周期性发送进度事件，避免回合被空闲超时误判为失败。

## 代码规范

### Python

```bash
# 代码检查
uv run ruff check .

# 代码格式化（行长度限制: 100）
uv run ruff format .

# 类型检查
uv run mypy miqi/
```

### TypeScript

```bash
# ESLint 检查
cd apps/desktop && npm run lint

# 格式化
cd apps/desktop && npm run format
```

### 提交规范

遵循 Conventional Commits：

```
feat: 添加 SkillHub 页面
fix: 修复快照回滚时的文件创建问题
refactor: 重构 SessionManager 存储层
docs: 更新 README 安装说明
test: 添加 TaskTrace 数据模型测试
```

## 构建与打包

### Python Bridge 可执行文件

使用 PyInstaller 打包为自包含 `miqi-bridge.exe`（内嵌 Python + 全部依赖）：

```bash
uv run pyinstaller miqi.spec
```

产物在 `dist/` 目录：
- `dist/miqi-bridge.exe` — 自包含可执行文件
- `dist/miqi-bridge/` — 目录形式的分发包

关键规格 (`miqi.spec`)：
- `console=False` — 无控制台窗口，但仍可通过 stdout 管道通信
- 打包全部 Python 依赖（loguru、pydantic、httpx 等）
- **不支持 `-c` 参数**：传参会变为 `sys.argv` 而非 Python 解释器选项

### 环境检查

Electron 启动时通过 `PYTHON_CHECK` IPC 检测 Python 环境：

1. 优先检测 `dist/miqi-bridge.exe`（bundled 可执行文件）
2. 不存在则检查系统 Python（`python -c "import miqi"`）

### 生产构建

三个 npm 命令的区别：

```bash
cd apps/desktop

npm run build        # 只构建前端（electron-vite），Bridge 仍用 uv run python 启动源码
npm run build:bridge # 用 PyInstaller 打包 Python 后端 → miqi-bridge.exe
npm run build:all    # 全量构建：Python 后端 + 前端 + electron-builder 打包
```

| 命令 | 前端 | Python | 适用场景 |
|------|------|--------|----------|
| `build` | ✅ 构建 | ❌ 源码 | 日常开发、改前端 |
| `build:bridge` | ❌ | ✅ 打包 exe | 只改 Python、生产打包 |
| `build:all` | ✅ 构建 | ✅ 打包 | 发布安装包 |

### 生产构建

```bash
cd apps/desktop
npm run build        # Vite 构建渲染进程
npm run package      # electron-builder 打包安装程序
```

## 测试

### Python 测试

```bash
# 运行所有测试
uv run pytest

# 运行特定模块
uv run pytest tests/test_trace.py

# 带覆盖率
uv run pytest --cov=miqi --cov-report=html

# 沙箱网络测试
uv run python tests/test_sandbox_network.py
```

### 前端测试

```bash
cd apps/desktop

# 运行测试
npm run test

# 监视模式
npm run test:watch
```

## 调试

### Python 调试

开发模式下，Bridge Server 的日志输出到 Electron DevTools Console：

```python
from loguru import logger
logger.info("Debug message")  # → DevTools Console
```

日志格式：`[miqi-bridge] <level> <message>`

通过 `--no-logs` 参数可将日志级别降为 WARNING。

### 前端调试

- 打开 Electron DevTools：`Ctrl+Shift+I`
- React DevTools 可用
- Network 面板可查看 IPC 通信

### Sandbox 调试

#### 查看沙箱状态

```bash
cat ~/.miqi/sandbox_state.json
```

状态文件记录所有活跃沙箱的 session_key、路径、创建时间。Bridge 重启时自动清理遗留沙箱。

#### 手动进入 bwrap 沙箱

```bash
# 进入 WSL 沙箱发行版
wsl.exe -d AIShadowSandbox

# 查找沙箱目录
ls /tmp/miqi-sandboxes/

# 进入沙箱（替换 YOUR_SESSION_KEY 为实际会话 ID）
bwrap \
  --unshare-pid --unshare-ipc --unshare-uts \
  --hostname miqi-sandbox \
  --unshare-user-try --uid 1000 --gid 1000 \
  --proc /proc --dev /dev \
  --ro-bind-try /usr /usr \
  --ro-bind-try /bin /bin \
  --ro-bind-try /lib /lib \
  --ro-bind-try /lib64 /lib64 \
  --bind /tmp/miqi-sandboxes/YOUR_SESSION_KEY/etc /etc \
  --bind /tmp/miqi-sandboxes/YOUR_SESSION_KEY/home/miqi /home/miqi \
  --bind /mnt/c/Users/Intership003/.miqi/workspace /home/miqi/workspace \
  --tmpfs /tmp \
  --die-with-parent --new-session \
  --setenv HOME /home/miqi \
  --setenv PATH /usr/local/bin:/usr/bin:/bin \
  bash -i
```

> **注意**：`/etc` 使用沙箱本地的完整拷贝（`sandbox_base/etc`），而非 `--ro-bind-try /etc /etc`。后者在 WSL 中会静默失败。

#### 常用沙箱内调试命令

```bash
# 检查 DNS
cat /etc/resolv.conf
cat /etc/nsswitch.conf
getent hosts www.baidu.com

# 检查网络
curl -I https://www.baidu.com
ping -c 1 8.8.8.8

# 检查挂载
mount | grep bwrap
ls /home/miqi/workspace/
```

#### 沙箱故障排查

| 问题 | 可能原因 | 解决 |
|------|----------|------|
| `bwrap: Can't create file at /etc/...` | `/etc` 挂载失败 | 检查 sandbox_etc 目录是否存在 |
| 网络不通 (DNS 失败) | nsswitch.conf 缺失或 resolv.conf 错误 | 检查 `/etc/nsswitch.conf` 有 `hosts: files dns` 行 |
| `WinError 206` | bwrap 参数超 Windows 命令行长度限制 | 已自动使用脚本方式执行，如仍出现需进一步拆分参数 |
| 沙箱不释放 | Bridge 异常退出 | 重启 Bridge 自动清理，或手动 `rm -rf /tmp/miqi-sandboxes/*` |

### 路径映射

Windows 路径在 WSL sandbox 中自动映射：

| Windows 路径 | WSL 内路径 |
|--------------|-----------|
| `C:\Users\foo\.miqi\workspace` | `/mnt/c/Users/foo/.miqi/workspace` |
| `D:\projects\app` | `/mnt/d/projects/app` |

`filesystem.py` 中的 `_resolve_sandbox_path()` 和 `_resolve_sandbox_cwd()` 处理路径自动转换。

## 目录约定

| 目录 | 约定 |
|------|------|
| `miqi/` | 纯 Python，不包含 Node.js 依赖 |
| `apps/desktop/` | 纯 Node.js/TypeScript，通过 Bridge 的 JSON-line 协议通信 |
| `tests/` | 镜像 `miqi/` 结构，`test_module.py` 对应 `module.py` |
| `docs/` | MkDocs Material 格式，每个页面一个 `.md` 文件 |
| `dist/` | PyInstaller 打包产物（miqi-bridge.exe 等） |
| `dist-new/` | electron-builder 打包产物 |
| `build/` | PyInstaller 构建中间文件 |
| `scripts/` | Shell 辅助脚本 |
