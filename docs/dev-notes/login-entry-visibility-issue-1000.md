---
name: login-entry-visibility-issue-1000
description: "#1000 登录入口显性化的实现地图——共享组件、五处入口、E2E 覆盖与文案约定"
type: project
---


#1000「登录入口过于隐蔽」实现地图（PR #1010，2026-09-09）：

**共享组件（复用原则）**：
- `renderer/features/settings/components/QraftLoginCard.tsx` — `QraftLoginButton`（一键 browserLogin，自管理 busy/取消/错误反馈，testId 由各入口指定）+ `QraftLoginCard`（首屏卡片，已登录自动隐藏）。各入口不要自己再写登录逻辑。
- `renderer/features/setup/QraftLoginStep.tsx` — 协议门之后衔接登录页。⚠️ 2026-09-16 起（[login-gate-mandatory-login-1095](login-gate-mandatory-login-1095.md)）它已升级为**强制登录门**：未登录每次启动都停在这里、无跳过、未登录只能退出应用；五处入口在未登录时已不可达（仅 `requiresRelogin` 场景仍在用）。
- `renderer/lib/qraftErrors.ts` — 错误码指引，QraftPage 与各入口共用。

**入口五处**：首屏欢迎区卡片（chat-hero-login-btn）、顶栏 chip（topbar-login-btn，已登录变账号 chip topbar-account-chip）、发送拦截气泡（chat-error-login-btn，登录成功自动移除气泡）、模型面板（model-quickpanel-login-btn）、通用设置（general-login-btn）；设置页 QraftPage 保留 testid qraft-browser-login-btn（E2E helper 依赖）。

**关键约定**：所有入口文案统一「登录 MiQroForge 账号」；browserLogin 不传 env（resolveConfig 回退 stored.env ?? 'test'，硬编码 'test' 会覆盖存量生产环境）；ChatConsole 发送拦截顺序——网关非 active（已登录）判定先于无 provider 判定；未登录 error 路径用 loggedInRef（handleSend 闭包会捕获旧值）。

**E2E**：`tests/e2e/qraft-login-entry.spec.ts`（3 离线 + 1 live 凭据用例）；privacy-consent.spec.ts 覆盖协议→登录衔接；⚠️ 文件顶层裸 `test.skip(cond)` 会跳过整个文件，单用例跳过必须写在 test body 内。相关 [e2e-inject-chat-events](e2e-inject-chat-events.md) [confirm-card-two-runtime-map](confirm-card-two-runtime-map.md)
