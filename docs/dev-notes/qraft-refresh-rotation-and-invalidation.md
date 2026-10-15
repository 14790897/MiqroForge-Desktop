---
name: qraft-refresh-rotation-and-invalidation
description: Qraft/MiQroForge 平台 refresh_token 真实语义（2026-09-15 实测更正）：不轮换、可复用、30 天 TTL，但平台会成批作废——「登录每天失效」根因
type: project
---


**2026-09-15 live 实测（test 环境，测试账号）更正了 09-02 的「轮换」结论**：

平台当前 refresh_token 语义：
- **不轮换**：刷新成功后响应里的 refresh_token 与旧值**相同**（可重复刷新任意次）
- TTL：`refresh_expires_in=2591999`（≈30 天，= Sa-Token 默认 timeout 2592000−1，与登录会话超时同源）
- **2026-09-21 补充**：access_token 寿命已被平台改为 ≈30 天（`expires_in=2591999`，早期 7199≈2 小时）——桌面端刷新间隔随之变长（setTimeout 上限 ~24.8 天先醒一次再重排）；仓库内过期的 TTL 断言/文案已修（PR #1170）
- 可重复使用；**新登录（API 路径 / 浏览器 UA「网站式登录」）不作废旧链**（多条链共存）
- **会话登出/彻底销毁（authorize 接口验证会话已死）也不作废 refresh_token**
- 并发授权无数量上限（连续 9 个全部可用）；userinfo / points balance / deduct 等业务调用无副作用
- 单个 token 完全空闲 1~6 小时**不失效**（6 个 idle token 实测）

**真正的失效模式 = 平台侧「成批作废」**：2026-09-15 实测到全局清理事件，**同一时刻**所有 token（含从未使用过的）一起被判无效（SaOAuth2RefreshTokenException）：
- 24h 组（08:54 创建）：14:46:04Z 还 OK，14:51:04Z 全死
- 用户的 liuweiqing12：14:29:07Z 刷新 OK → 16:14:06Z 刷新失败
- 6h 组（02:43 创建）：08:43:26Z 仍 OK（08:48 后无采样）；用户 token 09:00:46Z 已失效
- 推断存在 ~6 小时间隔的清理窗口（08:48 / 14:48 UTC），**待证**（探针脚本 qraft-idle24-probe.mjs 覆盖后续窗口）
- 另观察到偶发「出生即死」：高强度登录风暴中第 7 个 token 创建即 INVALID 且永久无效（未复现）

**后果**：桌面端每 105 分钟刷新（access 2h，提前 15 分钟），健康期可连续刷新 10.5h+（09-10 实测 7 连刷）。但一旦撞上平台清理（尤其应用关闭/隔夜期间），下次刷新必然 REFRESH_TOKEN_INVALID → 客户端按 #917 设计停止重试并引导重登 → 用户感受「登录每天都会失效」。**不是桌面端 bug；#917 的「轮换」前提已过时**（客户端「响应缺 refresh_token 即拒绝」「INVALID 不重试」两条硬化基于旧语义）。

**排查方法论（可复用）**：
- 重放 store 里存的 token 验证服务端真实态度：qraft-auth.json 是 Electron safeStorage 加密 = Windows Local State `os_crypt.encrypted_key`（DPAPI 解 → AES-256-GCM，载荷 v10 前缀 + 12B nonce + ciphertext + 16B tag）
- 探针脚本在 `%TEMP%\qraft-*.mjs`（refresh 语义/UA 登录/会话登出/并发数量/业务副作用/idle 阶梯/24h 探针），凭据只走 QRAFT_PHONE/QRAFT_PASSWORD 环境变量，日志全程脱敏
- 平台错误形态：HTTP 200 + `{"code":500,...,"originalMessage":"SaOAuth2RefreshTokenException: 无效refresh_token: <token原文>"}`（会回显 token，必须脱敏）

相关：[relogin-notification-feature-map](relogin-notification-feature-map.md)（失效告知三件套）、[real-account-features-need-live-e2e](real-account-features-need-live-e2e.md)、[qraft-test-account-credentials](qraft-test-account-credentials.md)
