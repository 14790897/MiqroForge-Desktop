---
name: desktop-mcp-via-miqrogate
description: "桌面端 MCP 改经 MiQroGate 接入的需求（miqro-gate#1）——两边现状、真正卡点、范围边界"
type: project
---


2026-10-12：用户在 MiQroGate 仓库提了需求 [miqro-gate#1](https://github.com/miqro-org/miqro-gate/issues/1)「面向桌面客户端的 MCP 消费者自助接入面」——把桌面端 MCP 从独立共享网关切到 MiQroGate 的 MCP 数据面。**范围用户当场定为「只做接入面最小闭环」**，不含服务托管搬迁、用量计费口径、分布式会话。

**两边现状（2026-10-12 核实，两个仓库源码/文档）**：

- MiQroGate 数据面**已就绪**：`POST /mcpservers/{name}/mcp`（JSON-RPC over HTTP）＋ SSE 双端点 `GET /mcpservers/{name}/sse` + `POST /mcpservers/{name}/message?sessionId=`（#356）；消费者凭据鉴权（`mqk_api_` Key 或消费者 JWT + `mcp:call` 作用域）、服务级/工具级两级 ACL、上游 backend-auth 注入、元数据日志（F01/F11–F18）。代码在 `backend/gateway-app/.../gateway/proxy/McpProxyController.java`、`McpSseSessionRegistry.java`（单节点内存、容量 256、空闲 5 分钟回收）。
- **真正的卡点是「够不着」**：MCP 接入地址只在管理面 `GET /api/v1/admin/mcp-services/{id}/connection`（#685）；消费者凭据由管理员签发 `POST /api/v1/admin/api-consumers`（明文只出现一次）；`/api/v1/me/*` 下**零** MCP 端点。
- 桌面端现状见 [mcp-desktop-wiring](mcp-desktop-wiring.md)：内置 `miqroforge-slurm` 指向 `<平台托管网关地址>/sse`，全客户端共享一个 token（AES-GCM 密文硬编码在 `apps/desktop/src/main/qraft/mcp-gateway-key.ts`），用标准 `mcp.client.sse.sse_client` 传输。
- ADR-0013 曾以「Virtual Key 无消费者身份」**排除** Virtual Key 通道——所以「让用户用已有 Virtual Key 连 MCP」这条路要重开就得动 ACL，需要 MiQroGate 侧拍板凭据形态。

**Why**：桌面端按人鉴权、按人吊销、有审计和用量，替代内置共享密钥；MiQroGate 已有能力不浪费。

**How to apply**：以后再提「桌面端接 MCP / MCP 走网关」，先看 #1 是否已落地；要点在消费者自助面而不是数据面。别默认桌面端能直接用 Virtual Key 连 MCP。相关：[quantum-api-feature-requirement](quantum-api-feature-requirement.md)（量子 API 如果走 MCP 也吃这条链路）。
