---
name: relogin-notification-feature-map
description: 平台登录失效全局告知的实现地图——横幅/顶栏 chip/发送拦截三件套、状态机语义、E2E mock 技术
type: project
---


「平台登录失效需要告知用户」实现地图（2026-09-10，PR #1016，已提交）：

**主进程**（已有，未改）：刷新失败置 `requiresRelogin` + `refreshError`，经 `qraft:statusChanged` 推所有窗口；`REFRESH_TOKEN_INVALID` 停止重试，瞬时失败 30 分钟重试。

**渲染进程三件套**：
- `renderer/components/QraftReloginNotifier.tsx`（挂 App.tsx 主界面分支，紧邻 InstallWarningToaster）：顶部浮动横幅 `qraft-relogin-notify`（常驻不自动消失，X 关闭 `-close`，动作按钮 `-action` 跳设置→Qraft tab）。状态机纯函数 `nextReloginNotifyState`：requiresRelogin 变 true 弹一次（含初始快照即 true——启动即发现 token 作废），用户关闭后不重复弹，requiresRelogin 回 false（重登/登出）自动隐藏并复位。文案 `reloginNotifyCopy` 按 refreshError 区分：REFRESH_TOKEN_INVALID→「已失效，请重新登录」+「去重新登录」；其他→「刷新失败（将自动重试）」+「去查看」。
- TopBar：`topbar-relogin-chip`（警示色 AlertTriangle「登录已失效」，点击去重登）；正常态仍是 `topbar-account-chip`。
- ChatConsole 发送拦截：`loggedIn && requiresRelogin` 分支**先于**网关门禁/无 provider 判定（失效后网关状态仍是旧快照 active），气泡换 `createProviderConfigMessage(…,'login')`（复用 `chat-error-login-btn` 一键登录，登录成功气泡自动移除）；流错误路径 5296/5501 用 `requiresReloginRef` 决定 login vs open-provider-settings。

**E2E**（`tests/e2e/qraft-login.spec.ts`）：`startInvalidRefreshMock`（/oauth2/refresh 返回 Sa-Token 失效响应回显 token）+ `seedExpiredStore`（过期 token + baseUrl 指 mock）→ 重启即触发失效；用例 3 加全局告知断言（横幅/关闭/chip），用例 4 覆盖发送拦截气泡。截图 test-results/qraft-e2e-relogin-notify.png、-send-intercept.png。相关 [qraft-refresh-rotation-and-invalidation](qraft-refresh-rotation-and-invalidation.md) [login-entry-visibility-issue-1000](login-entry-visibility-issue-1000.md)
