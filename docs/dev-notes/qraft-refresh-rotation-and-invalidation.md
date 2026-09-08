---
name: qraft-refresh-rotation-and-invalidation
description: 平台 2026-09-02 升级后 refresh_token 轮换并作废存量 token，客户端已分类 REFRESH_TOKEN_INVALID 停止重试
type: project
---

MiQroForge 平台（原 Qraft）于 2026-09-02 下午（UTC 14:35–16:20 之间）升级：刷新语义从「不轮换」变为「轮换」——旧 refresh_token 刷新后立即失效，响应携带新值（含 refresh_expires_in≈30 天）。升级同时作废了所有存量 refresh_token，桌面端当时存的 token 全部变无效，导致「自动刷新失败（REFRESH_FAILED），30 分钟后重试」无限刷屏。

服务端无效 token 的响应形态（HTTP 200 + 业务 JSON）：`{"code":500,"msg":"未知错误","data":{"message":"未知错误","originalMessage":"SaOAuth2RefreshTokenException: 无效refresh_token: <token原文>"}}` —— 注意 originalMessage 会**回显 refresh_token 原文**，日志/报错必须脱敏。

修复（2026-09-03 会话实现，PR #917）：client 分类 `REFRESH_TOKEN_INVALID`（永久错误，服务层停止 30 分钟自动重试、引导重登）；响应轮换值落盘；成功响应缺 refresh_token 一律拒绝（轮换语义下旧值已失效）；refreshNow 永久失败撤销待排自动刷新定时器。用户侧旧登录态需重新登录一次。相关：qraft access_token 也是扣费依赖（#915 后设置页登录态下会自动拉积分余额，qraft 相关 E2E 本地 mock 必须按路径区分，否则余额请求会被误计入刷新断言）。
