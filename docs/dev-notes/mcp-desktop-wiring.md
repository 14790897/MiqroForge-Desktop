---
name: mcp-desktop-wiring
description: MCP 桌面主路径接线（RuntimeSession 连接 config MCP 服务器）+ anyio cancel-scope 跨 task 坑 + E2E 测试基础设施
type: project
---

MCP 客户端代码（`miqi/agent/tools/mcp.py`：stdio/HTTP 连接、MCPToolWrapper、lazy 网关）一直存在，但 2026-09-03 前**从未接入桌面聊天主路径**——`connect_mcp_servers` 只被死代码 `GatewayKunRuntime`（无实例化调用）引用。接线方案：

- `RuntimeSession.start()` 调 `_connect_mcp()`：读 `config.tools.mcp_servers`，把各服务器工具注册进会话 ToolRegistry（命名 `mcp_<server>_<tool>`，lazy 时注册 `use_<server>` 网关）。**新建会话即生效**（hot_reload tier C → tier B，无需重启应用）
- 每个服务器连接是一个常驻 asyncio.Task，`connect_mcp_servers(mcp_servers, registry, keep_alive_event)` 返回任务列表；stop() 时 set keep_alive + gather 收尾

**anyio cancel-scope 坑**：MCP SDK/httpx 传输在连接时进入 cancel-scope，anyio 要求在同一 task 内退出。把 AsyncExitStack 交给别的 task 再 `aclose()` 会抛 `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in`（pytest 首跑暴露）。连接/注册/关闭必须全在创建它的 task 内完成。

**E2E 基础设施**（`apps/desktop/tests/e2e/mcp.spec.ts`）：
- `scripts/mock_mcp_server.py`：FastMCP stdio 测试服务器，工具 `e2e_echo` 返回标记 `MCP_ECHO_RESULT_7f3a9c`
- `scripts/mock_mcp.py`：mock LLM 状态机（R1 tool_call `mcp_e2emcp_e2e_echo`；R2 工具结果含标记才输出 `MCP-E2E-PASS`），仿 mock_openai.py 的 SSE 格式
- 两个用例互相独立（重试安全）：UI 添加 uimcp + 持久化断言；新会话工具往返
- **config.json 持久化用 camelCase 键**（`tools.mcpServers`），E2E patchConfig 清理/预置必须写 camelCase——只写 snake_case 清不掉旧键（首次红跑就踩了：用户真实配置的 zeo 服务器残留）
- macOS CI skip（同 confirm-card #710：本地 mock 不可达，见 [macos-ci-local-mock-server](macos-ci-local-mock-server.md)）
- E2E 期间别改 bridge 源码：运行中的 bridge 在 spawn 时 import，编辑到一半的 session.py 会让 thread/start 报 INTERNAL（AttributeError）

相关：[confirm-card-two-runtime-map](confirm-card-two-runtime-map.md)（桌面主路径是 RuntimeSession→TaskRunner，不是 KUN）、[legacy-main-path-only](legacy-main-path-only.md)
