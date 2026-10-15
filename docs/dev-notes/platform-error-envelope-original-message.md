---
name: platform-error-envelope-original-message
description: MiQroForge 平台业务错误信封——真实异常只在 data.originalMessage，顶层 msg 恒为「未知错误」；积分接口 2026-10-08 服务端 SQL 异常
type: project
---


MiQroForge 平台所有业务错误都是 **HTTP 200 + 业务码**，真实异常只出现在 `data.originalMessage`：

```json
{"code":500,"msg":"未知错误","data":{"message":"未知错误","originalMessage":"<Exception: 明细>","stackTraceLines":[]}}
```

**Why**：顶层 `msg` 对未知异常一律填「未知错误」，只看 `message/msg` 得到的信息量为零。排查平台问题必须以 `originalMessage` 为准；它也可能回显凭据原文（refresh_token 实测会回显，必须脱敏，见 [qraft-refresh-rotation-and-invalidation](qraft-refresh-rotation-and-invalidation.md)）。

**How to apply**：读平台响应/写诊断日志时优先取 `originalMessage`。客户端已有 `serverDetail()`（apps/desktop/src/main/qraft/client.ts，原 `refreshServerDetail` 泛化而来，PR #1233）供余额/扣费/刷新共用。

**2026-10-08 故障（当日已由平台修复，勿再当现存问题）**：`GET /oauth2/points/balance` 曾报 `SQLException: Data truncated for column 'type' at row 1`（code 500）。判据是**账号有没有绑定消费组**，不是有没有积分流水（我最初按「有流水才报错」下结论，被第三账号证伪）：

| 账号 | consumerId / consumerGroupId | 故障时 | 当日 16:52 复测 |
|---|---|---|---|
| sub 28 liuweiqing12 | 有，cg-1b8d69e6b2771b | 报错 500 | ✅ 200，availablePoints 129（与本地 billing-history 末条 balanceAfter 一致） |
| sub 29 hahaha（roleCode tester） | 有，cg-1b8d69e6b2771b | 报错 500 | ✅ 200，四项全 0 |
| sub 23 郑跃强 | userinfo 不返回消费字段 | 正常 200，四项全 0 | — |

两个互不相干的账号报同一个错 → 不是单账号数据异常；对照账号无消费组绑定 → 接口本身可用。判别方法：比 `userinfo` 是否返回 `consumerId`/`consumerGroupId`。修复后同一脚本复测两个账号均恢复。

仍值得记的两个易误判点：
- 报错里的 **`type` 是平台数据库列名，不是请求参数**——该接口没有查询参数也没有请求体，只有 `Authorization` 头；`?consumerId=`、`ConsumerId` 头等变体结果完全相同。
- 只测一个账号容易得出相反结论（换个没绑消费组的账号就「复现不了」）。

排查手法可复用：Electron `safeStorage` + 同 userData 目录解密 `qraft-auth.json` 取 token 直连接口；账号没有可用 token 时走完整 RSA 登录流程现取（`/portal/auth/login` → `doConfirm` → `authorize` 取 code → `/oauth2/token`），凭据见 [qraft-test-account-credentials](qraft-test-account-credentials.md)。注意 refresh_token 失效后平台会把**原文回显在 originalMessage 里**，日志/输出必须脱敏。
