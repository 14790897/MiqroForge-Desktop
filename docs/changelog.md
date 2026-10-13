# 更新日志

完整版本更新日志请参见 [GitHub 仓库 CHANGELOG.md](https://github.com/miqro-org/MiqroForge-Desktop/blob/develop/CHANGELOG.md)。

## 最新更新 (2026-06)

### 2026-06-23 — Plan 62: Turn API 类型化验证
- **类型化 Turn 请求模型**：5 个 Pydantic v2 模型，支持 camelCase/snake_case 互操作，字段级和模型级验证
- **处理器边界验证**：所有 5 个 turn 处理器在修改运行时状态之前先执行类型化验证
- 验证错误转换为 `AppServerError(code="INVALID_PARAMS")`

### 2026-06-23 — Plan 61: 类型化 App Server 协议
- **类型化信封模型**：`AppServerRequest`、`AppServerResponse`、`AppServerSuccess`、`AppServerError`
- **ProtocolRegistry**：带 `MethodStability`/`MethodScope` 枚举的方法注册表
- **31 个类型化协议方法规范**：`required` 字段与真实处理器参数完全对齐
- **Protocol Catalog**：自描述 `protocol/catalog` 端点 + JSON Schema Draft 2020-12 导出

### 2026-06-22-23 — Plan 60: 可信测试基线
- **跨平台测试基础设施**：pytest 标记 (`subprocess`, `sandbox`, `wsl`, `bwrap`)，GitHub Actions CI
- **测试隔离强化**：可写 pytest basetemp，安全的子进程/沙箱清理
- **路径规范化**：`get_miqi_home()` 规范路径解析器，保留旧路径兼容
- **bwrap/WSL**：Criterion 10 标记为 PENDING — 当前主机 WSL 不可用

### 2026-06-20-21 — Plans 48-59: 执行强化
- **遗留 AgentLoop 移除** (Plan 48)：`AgentLoop` 类退役，`RuntimeModelSettings` 替代
- **声明式权限策略 DSL** (Plan 49) + **细粒度审批策略** (Plan 50) + **生命周期钩子系统** (Plan 51)
- **Agent Graph Store** (Plan 52)：SQLite 持久化 agent 任务和 spawn 边
- **Unified Diff Patch 工具** (Plan 54)
- **提供商容错强化** (Plans 56-57)：OpenAI/Anthropic 指数退避重试
- **OTEL 可观测性** (Plans 58-59)：OpenTelemetry SDK 集成

### 2026-06-14-20 — Plans 31-47: 运行时平台
- **AppServer 运行时** (Plan 35)：类型化应用服务器，客户端/会话隔离，TTL 驱逐
- **Turn API** (Plan 41)：turn/start、turn/interrupt、turn/steer 处理器
- **Replay 调试** (Plan 40)：确定性回放文档、检查器
- **存储线程** (Plan 39)：Ledger 支持的线程导入/导出、rollback/fork
- **插件/技能/MCP 生态** (Plan 37)：市场、技能 CRUD、MCP 状态
- **Workbench 进程** (Plans 43-44)：command/exec、process/* 处理器，环境变量消毒
- **FS Watch & 模糊搜索** (Plan 46)：文件监听、两层评分模糊搜索
- **Initialize 握手** (Plan 45)：客户端能力协商
- **桌面 Alpha 发布** (Plan 47)：内部冒烟测试清单

### 2026-06-10-14 — Plans 9-30: 运行时核心
- 类型化事件系统、RuntimeSession、TurnRunner
- Multi-Agent 运行时、执行引擎 (沙箱、权限、审批)
- History/Ledger 持久化 (SQLite)、前端迁移至 AppServer
- 桌面 15+ 功能页面、TUI 骨架

### 2026-06-08
- 折叠工具调用消息、per-session bwrap 沙箱隔离

## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| v0.1.4.post1 | 2026-06 | 运行时 v2 Phase 0-2: 类型化协议、Turn 验证、测试基线 |
| v0.1.4 | 2026-05 | 任务追踪、SkillHub、WSL2 集成、记忆系统重构 |
| v0.1.3 | 2026-04 | 前端 15 页面完整、MCP 集成、Bridge 协议稳定 |
| v0.1.0 | 2026-03 | 初始 Alpha 版本：聊天、提供商、会话管理 |
