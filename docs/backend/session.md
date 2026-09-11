# 会话管理

MiQroForge 的会话管理分为两层：底层的 `SessionManager`（`miqi/session/manager.py`）负责磁盘持久化，上层的 `RuntimeSession`（`miqi/runtime/session.py`）负责运行时服务图构造和执行。

## RuntimeSession

`RuntimeSession` (`miqi/runtime/session.py`) 管理一次会话的完整运行时生命周期。

### 核心职责

- 通过 `RuntimeServices` 构造运行时服务图（工具注册表、上下文运行时、工具运行时、TurnRunner 等）
- 通过 `RuntimeClient.ask()` 接收用户消息并返回响应
- 管理会话级锁以序列化同一会话内的请求
- 维护提交队列 (`submission_queue`) 和事件队列 (`event_queue`)

### 核心参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `provider` | Provider | LLM 提供商实例 |
| `workspace` | Path | 工作区目录 |
| `model` | str | 使用的模型名称 |
| `temperature` | float | 生成多样性控制 |
| `max_tool_iterations` | int | 最大工具调用轮次 |
| `max_tokens` | int | 单次响应最大 Token |

## ClientSessionRegistry (AppServer)

`ClientSessionRegistry` (`miqi/runtime/app_server.py`) 管理 AppServer 层的客户端/会话隔离：

- **客户端隔离**：按 `client_id` 区分不同前端客户端
- **会话隔离**：每个客户端可有多个会话
- **TTL 驱逐**：空闲会话自动清理

## SessionManager

`SessionManager` (`miqi/session/manager.py`) 负责会话的磁盘持久化管理。

### 存储结构

会话数据支持两种后端：
- **JSONL 文件存储**：`SessionManager` — 传统磁盘存储
- **SQLite 存储**：`miqi/session/sqlite_store.py` — SQLite 后端

```
sessions/
├── desktop:1747123456789/       # 按 channel:timestamp 命名
│   ├── conversation.jsonl       # 对话历史 (JSON Lines)
│   ├── tracked_files.json       # 修改文件追踪
│   └── .archived                # 归档标记 (空文件)
├── desktop:1747123999999/
│   └── ...
└── feishu:oc_xxx/              # 飞书等外部通道会话
    └── ...
```

### 会话生命周期

1. **创建**：首次发送消息时自动创建会话
2. **活跃**：持续对话，追加 `conversation.jsonl`
3. **归档**：通过 `.archived` 标记隐藏，零开销
4. **恢复**：删除 `.archived` 即可恢复
5. **删除**：永久删除会话目录

### 会话隔离

- **工作目录隔离**：每个会话的文件操作限定在自身目录
- **上下文独立**：不同会话不共享对话历史
- **工具作用域**：文件工具默认限制在当前会话目录

### 文件追踪

`tracked_files.json` 记录会话中 Agent 修改的文件列表：

```json
{
  "files": [
    {
      "path": "/abs/path/to/file.py",
      "snapshot": ".miqi_snapshots/hash123.snap",
      "modified_at": 1747123456.789
    }
  ]
}
```

前端通过 `sessions:get_tracked_files` IPC 获取列表，提供 diff/revert/accept 操作。

## 会话搜索

通过 FTS5 全文索引实现跨会话搜索：

- SQLite FTS5 引擎
- 实时增量索引
- 支持中文分词
- `session_search` 工具在 Agent 上下文中可用

## 会话压缩

长时间对话可通过以下方式压缩：

- **窗口截断**：`memory_window` 参数控制保留的最近 N 轮对话
- **摘要生成**：LLM 生成对话摘要替代完整历史
- **上下文压缩**：`ContextCompressor` 5 阶段压缩算法

## CLI 命令

```bash
miqi session list              # 列出所有会话
miqi session show <id>         # 查看会话详情
miqi session delete <id>       # 删除会话
miqi session archive <id>      # 归档会话
miqi session search <query>    # 搜索会话内容
```

## 相关文档

- [Runtime 引擎](agent.md) — RuntimeSession / TaskRunner / TurnRunner
- [Bridge 通信](bridge.md) — Bridge Server 会话处理
