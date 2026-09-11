# Runtime 引擎

`miqi/runtime/` 是 MiQroForge 的核心运行时引擎，负责管理会话生命周期、将任务映射到执行、驱动 LLM 调用与工具循环。

> **Historical**: 旧版 `AgentLoop` (`miqi/agent/loop.py`) 已在 Phase 48 移除，由 RuntimeSession / TaskRunner / TurnRunner 取代。

## RuntimeSession

`RuntimeSession` (`miqi/runtime/session.py`) 管理一次会话的完整运行时生命周期。

### 核心职责

- 通过 `RuntimeServices` 构造运行时服务图（工具注册表、上下文运行时、工具运行时、TurnRunner 等）
- 通过 `RuntimeClient.ask()` 接收用户消息并返回响应
- 管理会话级锁以序列化同一会话内的请求

### 核心参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `provider` | Provider | LLM 提供商实例 |
| `workspace` | Path | 工作区目录 |
| `model` | str | 使用的模型名称 |
| `temperature` | float | 生成多样性控制 |
| `max_tool_iterations` | int | 最大工具调用轮次 |
| `max_tokens` | int | 单次响应最大 Token |

## TaskRunner

`TaskRunner` (`miqi/runtime/task_runner.py`) 将用户任务映射到 TurnRunner 的执行。

### 核心职责

- 接收 UserMessage，管理任务级状态
- 协调多次 TurnRunner 调用以完成一个任务
- 管理任务上下文与结果收集

## TurnRunner

`TurnRunner` (`miqi/runtime/turn_runner.py`) 拥有 provider.chat + 工具调用循环。从旧版 `AgentLoop._run_agent_loop` 提取而来。

### 核心职责

- 调用 LLM provider 进行流式响应
- 将工具调用路由至 ToolRuntime
- 通过 ContextRuntime 构建和压紧消息
- 返回 TurnResult 给 TaskRunner

### 处理流程

1. **接收消息**：从 Bridge 或 CLI 接收用户输入，通过 RuntimeSession 进入
2. **构建上下文**：调用 `ContextRuntime` 组装 `system_prompt`、历史消息与用户输入
3. **TurnRunner LLM 调用**：向配置的 Provider 发送请求，流式接收响应
4. **工具执行**：解析 Function Calling 响应，通过 `ToolRuntime` 和 `ToolRegistry` 执行
5. **循环迭代**：将工具结果反馈给 LLM，直到任务完成或达到最大轮次

> **Phase 48 退役说明**: 旧版 AgentLoop 在第 5 步之后会自动触发记忆/技能持久化（Nudge）、自动调用 `task_begin`/`task_end` 标记任务边界、自动将工具调用链记录到 TraceStore。这些 AgentLoop-only 自动能力已在 Phase 48 退役，当前 RuntimeSession / TaskRunner / TurnRunner 尚未实现。后续如需要这些能力，必须从旧 AgentLoop 中单独迁移到 RuntimeSession / TaskRunner / TurnRunner。

## ContextRuntime

`ContextRuntime` (`miqi/runtime/context_runtime.py`) 接收调用方传入的 `system_prompt`、历史消息和用户当前输入，并将它们组装成符合 provider 要求的消息列表；同时负责上下文压紧。

它**不**负责读取 `SOUL.md`、技能或记忆，也**不**负责注入 Memory System 指南、Skills System 指南、FTS5 跨会话搜索提示、TraceStore 相似历史任务上下文或 Nudge 提醒。旧版 AgentLoop 的这些注入由 `ContextBuilder`（`miqi/agent/context.py`）在每次 turn 构建 system prompt 时驱动，不是 ContextRuntime 自身的行为。上述旧注入能力已在 Phase 48 随 AgentLoop 退役。

## 子代理支持

当前执行路径的子代理入口为 **SpawnTool → AgentControl → AgentJobRuntime**。`SpawnTool` 触发 `miqi.runtime.agent_control.AgentControl.spawn_agent()`，后者将任务调度给 `miqi.runtime.agent_job_runtime.AgentJobRuntime` 并发执行。legacy `SubagentManager` fallback 已在 Phase 48 禁用。

## 错误处理

- **重试机制**：LLM 调用失败自动重试
- **工具超时**：每个工具有独立超时控制，由 `ToolRuntime` 管理
- **优雅降级**：工具执行失败不中断对话流程
- **异常上报**：通过 Bridge 事件向 UI 报告错误
