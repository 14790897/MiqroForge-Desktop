# 数据流

## 用户消息 → AI 响应的完整链路

```mermaid
sequenceDiagram
    actor User as 👤 用户
    participant ChatConsole as ChatConsole<br/>(React Renderer)
    participant IPC as IPC Handler<br/>(Main Process)
    participant Bridge as BridgeManager<br/>(Python 子进程)
    participant AppServer as AppServer
    participant Runtime as RuntimeSession<br/>/TaskRunner
    participant TurnRunner as TurnRunner
    participant ContextRuntime as ContextRuntime
    participant LLM as LLM Provider
    participant Orchestrator as ToolOrchestrator<br/>(审批→沙箱→执行)
    participant Sandbox as bwrap Sandbox

    User->>ChatConsole: 输入消息
    ChatConsole->>IPC: ipcRenderer.invoke("chat:send")
    Note over IPC: Zod 参数验证
    IPC->>Bridge: bridge:chat-send
    Note over Bridge: JSON-line 写入 stdin

    Bridge->>AppServer: dispatch("turn/start", params)
    Note over AppServer: 类型化方法分发 + Middleware 链
    AppServer->>Runtime: 分发到 RuntimeSession

    Runtime->>ContextRuntime: build_messages()
    Note over ContextRuntime: 组装 system_prompt + 历史 + 用户输入
    ContextRuntime-->>Runtime: 消息列表

    Runtime->>TurnRunner: 启动 turn

    loop Turn Loop (TurnRunner)
        TurnRunner->>LLM: Chat Completion (stream)
        LLM-->>Bridge: 流式文本增量
        Bridge-->>IPC: 类型化事件
        IPC-->>ChatConsole: 实时渲染 Markdown

        alt 需要工具调用
            LLM-->>TurnRunner: function_call
            TurnRunner->>Orchestrator: execute(tool_name, params)

            rect rgb(240, 248, 255)
                Note over Orchestrator: 阶段 1: 审批
                Orchestrator->>Orchestrator: ApprovalPolicy.check()
                Note over Orchestrator: 阶段 2: 沙箱选择
                Orchestrator->>Orchestrator: SandboxPolicyEngine.select()
                Note over Orchestrator: 阶段 3: 执行
                Orchestrator->>Sandbox: 在 bwrap 沙箱中执行
                Sandbox-->>Orchestrator: 结果
                Note over Orchestrator: 阶段 4: 重试 (按需)
                Orchestrator->>Orchestrator: 指数退避重试
            end

            Orchestrator-->>TurnRunner: ToolResult
            Note over Bridge: tool_progress 事件推送
        end
    end

    TurnRunner-->>Runtime: TurnResult
    Runtime-->>AppServer: 响应结果
    AppServer-->>Bridge: 类型化响应
    Bridge-->>ChatConsole: 完整响应
    ChatConsole-->>User: 渲染结果
```

## 协议层

### 消息格式

MiQroForge 使用 **JSON-line 协议** 通过 stdin/stdout 通信，每条消息为一行完整 JSON：

```
Request (前端 → 后端):
  {"jsonrpc": "2.0", "id": "uuid-001", "method": "turn/start", "params": {...}}

Success Response (后端 → 前端):
  {"jsonrpc": "2.0", "id": "uuid-001", "result": {...}}

Error Response (后端 → 前端):
  {"jsonrpc": "2.0", "id": "uuid-001", "error": {"code": "INVALID_PARAMS", "message": "..."}}

Event (后端 → 前端, 流式推送):
  {"jsonrpc": "2.0", "method": "turn/progress", "params": {...}}
```

### 事件类型

AppServer 通过 `miqi/protocol/events.py` 定义多种事件类型：

| 事件 | 方向 | 说明 |
|------|------|------|
| `TurnStartedEvent` | Backend → Frontend | Turn 开始执行 |
| `AgentMessageDeltaEvent` | Backend → Frontend | LLM 流式文本增量 |
| `ToolCallBeginEvent` | Backend → Frontend | 工具调用开始 |
| `ToolCallEndEvent` | Backend → Frontend | 工具调用完成 |
| `ApprovalRequestedEvent` | Backend → Frontend | 命令审批请求 |
| `SubAgentSpawnedEvent` | Backend → Frontend | 子 Agent 启动 |
| `PlanUpdateEvent` | Backend → Frontend | 计划更新通知 |
| `ErrorEvent` | Backend → Frontend | 异常错误 |
| `TurnCompletedEvent` | Backend → Frontend | Turn 完成 |
| `TurnInterruptedEvent` | Backend → Frontend | Turn 被中断 |
| `FsChangedEvent` | Backend → Frontend | 文件系统变更通知 |
| `FuzzyFileSearchUpdatedEvent` | Backend → Frontend | 模糊搜索更新 |
| ... | | 等 |

## 连接握手

客户端（Electron）启动 Python 子进程后，通过 `initialize` 进行能力协商：

```
Client → Server:  {"method": "initialize", "params": {"clientInfo": {...}, "capabilities": {...}}}
Server → Client:  {"method": "initialized", "params": {"serverInfo": {...}, "capabilities": {...}}}
```

## 并发处理

- **多会话并行**：`ClientSessionRegistry` 按 `(client_id, session_id)` 隔离，TTL 驱逐
- **工具并行**：`ToolOrchestrator` 支持批量工具并发执行
- **多 Agent 并发**：`AgentControl` + `AgentJobRuntime` 管理并发 agent 任务
- **请求序列化**：同一会话内请求通过 `RuntimeSession` 锁序列化
