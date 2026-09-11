# 配置参考

MiQroForge Desktop 的全局配置存储在 `~/.miqi/config.json` 中。

## 配置文件位置

| 操作系统 | 路径 |
|----------|------|
| Linux / macOS | `~/.miqi/config.json` |
| Windows | `C:\Users\{username}\.miqi\config.json` |

## 完整配置结构

```json
{
  "approvals": {
    "bypassAll": false,
    "bypassCommandApproval": false,
    "bypassFileWriteApproval": false,
    "bypassToolConfirmation": false,
    "bypassNetworkApproval": false
  },
  "providers": {
    "openai": {
      "apiKey": "sk-...",
      "apiBase": "https://api.openai.com/v1",
      "defaultModel": "gpt-4o"
    }
  },
  "agents": {
    "defaults": {
      "model": "gpt-4o",
      "temperature": 0.1,
      "max_tool_iterations": 100,
      "max_tokens": 16000,
      "memory_window": 100,
      "name": "miqi",
      "workspace": "~/.miqi/workspace"
    },
    "self_improvement": {
      "trace_enabled": true,
      "embedding_model": "intfloat/multilingual-e5-small",
      "trace_inject_top_k": 3,
      "trace_similarity_threshold": 0.65,
      "trace_nudge_interval": 8,
      "lessons_legacy_inject_enabled": false
    },
    "command_approval": {
      "enabled": true,
      "timeout": 60
    }
  },
  "tools": {
    "restrict_to_workspace": false,
    "extra_roots": [],
    "auto_user_dirs": true,
    "web": {
      "search": {
        "provider": "ddgs",
        "apiKey": "",
        "maxResults": 5
      },
      "fetch": {
        "provider": "builtin"
      }
    },
    "exec": {
      "allowed_commands": [],
      "blocked_commands": ["rm -rf /", "format"],
      "timeout": 60,
      "max_timeout": 1800,
      "idle_timeout": 90,
      "heartbeat_interval": 30,
      "kill_grace_seconds": 5
    },
    "mcp_servers": {}
  },
  "channels": {},
  "cron": {
    "job_timeout_seconds": 86400
  }
}
```

## 配置项详解

### providers — LLM 提供商

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `apiKey` | string | 是 | 提供商 API 密钥 |
| `apiBase` | string | 否 | 自定义 API 地址 |
| `defaultModel` | string | 否 | 默认使用模型 |

### agents.defaults — Agent 默认参数

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model` | string | "gpt-4o" | 默认 LLM 模型 |
| `temperature` | float | 0.1 | 生成多样性 (0-2) |
| `max_tool_iterations` | int | 100 | 单次对话最大工具调用轮次 |
| `max_tokens` | int | 16000 | 单次响应最大 Token |
| `memory_window` | int | 100 | 对话记忆窗口大小 |
| `name` | string | "miqi" | Agent 默认名称 |
| `workspace` | string | "~/.miqi/workspace" | 默认工作区路径 |

### agents.self_improvement — 自改进系统

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `trace_enabled` | bool | true | 启用任务追踪 |
| `embedding_model` | string | 多语言E5 | 嵌入模型名称 |
| `trace_inject_top_k` | int | 3 | 注入相似历史任务数 |
| `trace_similarity_threshold` | float | 0.65 | 相似度阈值 |
| `trace_nudge_interval` | int | 8 | Nudge 间隔 (轮) |
| `lessons_legacy_inject_enabled` | bool | false | 启用旧版 Lessons 注入 |

### approvals - approval bypass

These switches skip approval prompts while keeping explicit deny rules and
parameter validation active. When any bypass switch is enabled, MiQroForge Desktop
shows a persistent warning in the top bar.

| Field | Type | Default | Description |
|------|------|--------|-------------|
| `bypassAll` | bool | false | Skip every approval prompt. |
| `bypassCommandApproval` | bool | false | Skip command execution approval prompts. |
| `bypassFileWriteApproval` | bool | false | Skip file mutation approval prompts. |
| `bypassToolConfirmation` | bool | false | Skip generic tool confirmation prompts. |
| `bypassNetworkApproval` | bool | false | Reserved for network approval prompts. |

### tools — 工具配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `restrict_to_workspace` | bool | false | 文件操作限制在工作区 |
| `extra_roots` | array | [] | 文件工具额外允许的根目录（支持 workspace 外目录，WSL 沙箱白名单） |
| `auto_user_dirs` | bool | true | 自动感知用户消息中点名的输出目录（如 `C:\Users\x\Desktop\test_result`），本回合内授权文件工具读写该目录（#821）；仅扫描用户自己的消息，配置/会话文件、用户主目录、盘符根与系统顶层目录始终除外；关闭后仅静态 `extra_roots` 生效 |
| `web.search.provider` | string | "ddgs" | 搜索引擎 (ddgs/brave/hybrid) |
| `web.search.apiKey` | string | "" | Brave Search API Key，仅 brave/hybrid 使用 |
| `web.search.maxResults` | int | 5 | 最大搜索结果数量 |
| `exec.allowed_commands` | array | [] | Shell 命令白名单 |
| `exec.blocked_commands` | array | [] | Shell 命令黑名单 |
| `exec.timeout` | int | 60 | exec 默认执行超时（秒），超时后终止整个进程树 |
| `exec.max_timeout` | int | 1800 | 单次调用 `timeout` 参数的上限（秒，30 分钟），超过上限的请求会被拒绝 |
| `exec.idle_timeout` | int | 90 | 无输出卡死判定阈值（秒），仅作诊断提示，不杀死进程 |
| `exec.heartbeat_interval` | int | 30 | 静默命令的心跳进度事件间隔（秒），保持长任务链路存活 |
| `exec.kill_grace_seconds` | int | 5 | 超时终止时 terminate → kill 的宽限（秒） |

> **#810 长任务说明**：长命令（pip install、LaTeX 编译、PDF 渲染等）超过默认
> 60 秒会被截断。模型可在调用 exec 时显式传 `timeout` 参数（如
> `exec(command="pip install matplotlib numpy", timeout=600)`）申请更长的执行
> 预算，上限为 `max_timeout`（默认 30 分钟）；超过上限的请求会被拒绝而不是
> 静默截断。超时后整个进程树会被终止（不会残留后台进程），超时结果包含
> 结构化元数据（duration_ms / timeout_ms / retryable）和超时前的部分输出，
> 便于模型判断重试或拆分任务。真正超过 30 分钟的任务建议拆分为多个步骤。

### tools.mcp_servers — MCP 服务器

每个 MCP 服务器可配置：

| 字段 | 类型 | 说明 |
|------|------|------|
| `command` | string | 启动命令 |
| `args` | array | 命令参数 |
| `env` | object | 环境变量 |
| `toolTimeout` | int | 工具超时 (秒) |
| `lazy` | bool | 延迟加载 |
| `progressIntervalSeconds` | int | 心跳间隔 (秒) |

### observability — OpenTelemetry 可观测性

默认关闭。启用并安装可选依赖后，运行时事件将导出为 OpenTelemetry traces 和 metrics。

安装依赖: `pip install miqi[otel]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | false | 启用 OTel 导出 |
| `endpoint` | string | null | OTLP gRPC/HTTP 端点 URL |
| `serviceName` | string | "miqi" | 服务名称标签 |
| `consoleExport` | bool | false | 输出到控制台 (开发调试用) |
| `sampleRatio` | float | 1.0 | 采样率 (0-1) |
| `captureContent` | bool | false | 捕获消息文本到 Span 属性 (隐私敏感，默认不捕获) |

## 环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `MIQI_PYTHON_PATH` | 自定义 Python 解释器 | `/usr/bin/python3.12` |
| `MIQI_AGENTS__DEFAULTS__MODEL` | 覆盖默认模型 | `claude-sonnet-4-20250514` |

环境变量使用双下划线 `__` 分隔嵌套键，优先级高于配置文件。

## 配置热更新（Hot Reload）

保存配置后不再一律要求重启应用（#789）。保存时 Bridge 对变更字段分类并广播 `config_updated` 事件，前端按类别提示；分类规则实现在 `miqi/config/hot_reload.py`。

三类语义：

- **A 类 — 热生效**：保存后立即应用到运行中的会话，无需重启。Provider/模型变更对**下一次对话**生效（进行中的 turn 继续使用旧配置）。
- **B 类 — 新会话生效**：保存成功，但需要新建会话/下一次工具调用才生效（工具注册表在会话创建时构建）。
- **C 类 — 必须重启**：进程级配置，运行时无法变更。状态栏显示「需要重启」及原因，可一键重启。

| 配置项 | 类别 | 生效说明 |
|--------|------|----------|
| `providers.*`（apiKey/apiBase/extraHeaders） | A | 下次对话使用新 Provider |
| `agents.defaults.model` | A | 下次对话使用新模型 |
| `agents.defaults.temperature` / `max_tokens` / `max_tool_result_chars` / `context_limit_chars` / `max_tool_iterations` / `name` | A | 下次对话生效 |
| `approvals.*` | A | 审批绕过立即生效 |
| `agents.command_approval.*` / `agents.permanent_approvals` | A | 命令审批规则立即生效 |
| `desktop.*` | A | 纯前端设置，即时生效 |
| `agents.memory.*` / `agents.smart_routing.*` / `agents.self_improvement.*` | B | 新会话生效（记忆/经验钩子在会话创建时构建） |
| `tools.sandbox.enabled` | B | 新会话生效（沙箱运行时在进程启动时构建；启停开关走 `sandbox.setEnabled` 专用通道热切换） |
| `channels.send_progress` / `send_tool_hints` / `send_queue_notifications` | B | 新会话生效（渠道管理器持有会话启动时的配置） |
| `observability.*` | B | 新会话生效（遥测 sink 一次性构建） |
| `tools.web.*`（搜索/抓取） | B | 新会话生效（工具注册时构建） |
| `tools.exec.*` / `tools.papers.*` | B | 新会话生效 |
| `tools.restrict_to_workspace` / `tools.extra_roots` | B | 新会话生效 |
| `agents.defaults.workspace` | B | 新会话生效 |
| `agents.sessions.*` | B | 新会话生效 |
| `channels.*`（渠道接入） | B | 新会话生效（渠道连接进程级） |
| `tools.sandbox.share_net` / `max_sandboxes` / `auto_*` | B | 新会话生效 |
| `heartbeat.*` / `cron.*` | B | 新会话生效 |
| `tools.sandbox.wsl_distro` / `wsl_base_dir` / `sandbox_distro_name` | C | 重启（WSL 发行版在进程启动时检测/导入） |
| `gateway.host` / `gateway.port` | C | 重启（监听地址在启动时绑定） |
| `agents.defaults.runtime` | C | 重启（运行时引擎在会话创建时选择） |
| `agents.sessions.use_sqlite` | C | 重启（存储后端在启动时选择） |
| `tools.mcp_servers` | C | 重启（MCP 服务器在工具注册时连接） |

**回滚语义**：配置保存前先经 Pydantic 校验，非法配置拒绝保存并返回错误，运行中的旧配置不受影响；热应用单项失败时保留旧值（如 Provider 重建失败则继续使用旧 Provider 与旧模型，避免旧 Provider 配新模型的 400），不会出现半生效状态。

**「需要重启」横幅语义**：横幅反映的是**待重启状态**而非单次保存的类别——C 类修改后，后续 A/B 类保存不会清除横幅；只有把 C 类字段改回进程启动时的值（Bridge 与启动快照比对确认已收敛）或真正重启应用后横幅才消失。
