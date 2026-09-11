# 记忆系统

MiQroForge 的记忆系统由 `miqi/agent/memory/` 实现，提供持久化记忆存储、经验教训管理、技能生命周期和跨会话回忆能力。

## 架构概览

| 组件 | 文件 | 功能 |
|------|------|------|
| `MemoryStore` | `store.py` | 长期记忆存储与检索 |
| `MemorySnapshot` | `snapshot.py` | 记忆快照 |
| `LessonsStore` | `lessons.py` | 经验教训状态管理 |
| `MemoryCurator` | `curator.py` | LLM 驱动的记忆整理 |
| `SkillCurator` | `skill_curator.py` | LLM 驱动的技能生命周期管理 |
| `ExperienceStore` | `experience_store.py` | 经验数据存储 |
| `MemoryProvider` | `provider.py` | 记忆提供者接口 |
| `NLP` | `nlp.py` | 自然语言处理工具 |

## MemoryStore

`MemoryStore` 是记忆系统的核心，负责：

- **持久化存储**：所有记忆条目持久化到磁盘
- **检索**：根据上下文相关性搜索和检索记忆
- **CRUD 操作**：创建、读取、更新、删除记忆
- **自动整理**：定期触发 LLM 驱动的记忆去重和合并

## 经验教训系统

Agent 完成任务后通过追踪系统自动总结经验。经验具有三状态生命周期：

```
active → stale → archived
```

- **active**：活跃经验，可注入 Agent 上下文
- **stale**：陈旧经验，降低引用权重
- **archived**：已归档，不再注入

> **注意**：自 2026-05-15 起，`lessons_legacy_inject_enabled` 默认设为 `false`。经验教训不再自动注入 Agent 提示词，取而代之的是基于 Task Trace 的相似历史上下文机制。

## 技能管理

- **SkillCurator**：LLM 驱动的技能生命周期管理，自动评估和更新技能
- **自动归档**：长期未使用的技能自动标记为 stale
- **SkillManager**：技能加载、作用域管理和注入（详见 [Runtime 引擎](agent.md)）

## 记忆工具

Agent 可通过以下工具操作记忆：

| 工具 | 操作 | 说明 |
|------|------|------|
| `memory_read` | 读取 | 搜索并读取相关记忆 |
| `memory_write` | 写入 | 创建新记忆条目 |
| `memory_append` | 追加 | 向已有记忆追加内容 |
| `session_search` | 搜索 | FTS5 全文搜索历史会话 |

## 与旧系统的区别

> **Historical**: 旧版 MiQi (WorkBuddy) 使用三层架构 (Cloud/User/Workspace)，记忆存储在 `.workbuddy/memory/` 和 `~/.workbuddy/MEMORY.md`。这些路径和架构已不再使用。当前系统统一使用 `MemoryStore` 管理所有记忆层级。

## 配置

```json
{
  "memory": {
    "enabled": true,
    "lessons_legacy_inject_enabled": false
  }
}
```

## 相关文档

- [Runtime 引擎](agent.md) — ContextRuntime 和上下文注入
- [任务追踪](trace.md) — TaskTrace 系统，已替代旧 Lessons 注入
