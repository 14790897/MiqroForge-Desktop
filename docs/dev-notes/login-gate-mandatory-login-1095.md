---
name: login-gate-mandatory-login-1095
description: "#1095 登录收口（未登录强制退出）的实现地图——门控位置、首帧同步登录态、MIQI_LOGIN_BYPASS 绕过链、E2E 位置"
type: project
---


「未登录不得使用应用」登录门（#1095，PR #1111，2026-09-16）：

**语义**：未登录（qraft `loggedIn === false`）→ `App.tsx` 整屏 `QraftLoginStep`，不进主界面；登录成功由 `qraft:statusChanged` 自动放行（登录页自动卸载，**没有**「进入应用」按钮）；应用内「退出登录」= 未登录 → 立即回到登录页；**已删除**「暂不登录，进入应用」跳过路径。`requiresRelogin`（token 失效但仍有存储态）不算未登录，仍进主界面走横幅/chip/发送拦截（[relogin-notification-feature-map](relogin-notification-feature-map.md)）。

**首帧判定**（关键设计）：`IPC.QRAFT_STATUS_SYNC`（sendSync，同 #1071 协议门的做法）→ preload 顶层 `qraft.initialStatus` → `useQraftStatus` 用它做 `useState` 初始值。异步取会让已登录用户先闪一帧登录页；读取失败（null）时停在 `StartupLoading`（不放行也不闪）。main 侧 handler 在 `qraft/ipc.ts`，首次调用会提前构造 `QraftService`（无害：只读 store + 定时器）。

**退出应用**：登录页底部「退出应用」→ 弹窗「需要登录 MiQroForge 账号后才能使用」（可「返回登录」取消）→ `window.miqi.app.quit()`（#837 同款；macOS 上 window.close 不退应用）。testid：`login-step` / `login-step-login-btn` / `login-step-quit` / `-quit-dialog` / `-quit-cancel` / `-quit-confirm`（`login-step-skip`、`login-step-enter` 已删除）。

**E2E 绕过链（重要）**：`MIQI_LOGIN_BYPASS=1` →（main/index.ts，**仅未打包**）argv `--miqi-login-bypass` → preload `env.loginBypass` → App.tsx `loginBypassed`。helper `launchElectronApp/relaunchElectronApp` **默认设置**该 env（~70 个既有用例要未登录的主界面），传 `noLoginBypass: true` 才走真实门（此时 helper 跳过 UI 就绪等待，由用例自己驱动）。smoke mock 里对应 `env: { isE2E: false, loginBypass: true }`。

**测试**：`tests/e2e/login-gate.spec.ts`（serial 4 条：同意协议→登录门→退出弹窗/进程结束；未登录重启仍停在门；预置 plain 信封 store 已登录放行；应用内登出回门）。`privacy-consent.spec.ts` 只测协议门（登录门被绕过）。启动顺序：协议门 → 登录门 → 加载屏 → 设置向导 → 主界面。

相关 [login-entry-visibility-issue-1000](login-entry-visibility-issue-1000.md)（五处入口仍在，未登录时已不可达）[smoke-privacy-gate-and-cwd-sensitive-tests](smoke-privacy-gate-and-cwd-sensitive-tests.md)（mock 需同步 preload API）
