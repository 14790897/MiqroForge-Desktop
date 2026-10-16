---
name: e2e-window-offscreen-default
description: E2E 应用窗口默认停在屏幕外（MIQI_E2E_OFFSCREEN），以及为什么不能用真·最小化（fullPage 截图超时）
type: project
---


E2E 启动的 Electron 应用默认「停到所有显示器之外 + showInactive」，不出现在桌面上、不抢焦点（PR #1136，2026-09-18）。

**Why:** 用户本机跑 E2E 时窗口弹出遮挡操作并抢走焦点。原始诉求是「默认最小化」，但实测真·最小化会让 Chromium 停止为窗口出帧 → `page.screenshot({ fullPage: true })` 等不到新帧、30s 超时（仓库有 25 个 spec 在用 fullPage：privacy-consent / login-gate / billing-live 等）。关掉 `CalculateNativeWinOcclusion` 也救不回来。停屏幕外的窗口照常合成，rAF/截图语义与普通可见窗口一致（实测 rAF 150 帧/2s、视口与 fullPage 截图均正常、系统前台窗口不变）。

**How to apply:**
- 主进程：`src/main/index.ts` 的 `shouldStartOffscreen()`（仅未打包生效，env `MIQI_E2E_OFFSCREEN=1`）；窗口 `show:false` + `skipTaskbar:true`，`ready-to-show` 后挪到 `x-30000` 再 `showInactive()`；该模式不建 splash（alwaysOnTop 会浮在别人桌面上）。
- 测试侧：`tests/e2e/helpers/electron-setup.ts` 的 `applyWindowVisibilityEnv()`，`launchElectronApp`/`relaunchElectronApp` 都套用。本机默认开、CI 默认关（CI 无人看桌面，保持原行为）。
- 需要真实窗口出现在屏幕上时：spec 传 `launchElectronApp(cb, { showWindow: true })`，或跑用例前 `MIQI_E2E_SHOW_WINDOW=1`。当前只有 `record-bvse-skill.spec.ts`（desktopCapturer 录屏）需要。
- 裸 `electron.launch()` 绕过 helper 时记得手动调 `applyWindowVisibilityEnv(env)`（见 full-electron.spec.ts 第二个实例）。
- 排查「窗口是否真没抢焦点」：用 PowerShell 取 `GetForegroundWindow()` 标题前后对比；注意用户自己随时会切窗口，断言应写「前台不是 MiQroForge」而不是「前台没变」。

相关：[e2e-direct-screen-recording](e2e-direct-screen-recording.md)、[pr-screenshot-mandatory](pr-screenshot-mandatory.md)
