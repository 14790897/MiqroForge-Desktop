---
name: qraft-netfetch-manual-redirect
description: Electron net.fetch 对 redirect:manual 的 302 直接 reject（"Redirect was cancelled"），授权码流程须回退 undici fetch
type: project
---

Electron 主进程 `net.fetch(url, {redirect:'manual'})` 在目标响应为 302 时**直接 reject**（错误信息 "Redirect was cancelled"，Chromium 行为），而不是返回 302 响应 —— QraftClient 的 OAuth2 授权码流程（authorize → 读 Location）因此挂掉，密码登录路径不可用（浏览器登录走 webContents 不受影响）。此前 #726 的 E2E 是零网络设计，从未覆盖这条真实链路。

**修复（PR #925，2026-09-03）**：`apps/desktop/src/main/qraft/ipc.ts` 的 `netFetchWithManualFallback` —— 仅当 `redirect:'manual'` 且报错含 "Redirect was cancelled" 时回退 Node 内置 fetch（undici，manual 语义正确返回 302），其余请求仍走 net.fetch（系统代理支持）。登录 cookie 由 QraftClient 显式经 Cookie 头携带，回退不影响凭据。

**相关**：#916（2026-09-03）已把设置页 "Qraft 平台" 标签更名 "MiQroForge 平台"，E2E 选择器要 `filter({ hasText: /MiQroForge/ })`；真实扣费链路 E2E 见 `billing-live.spec.ts`（opt-in，凭据环境变量注入）。
