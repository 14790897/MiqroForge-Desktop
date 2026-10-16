---
name: e2e-config-patch-clobbered-by-helper
description: launchElectronApp 的 patchConfig 改 channels 会被 helper 整体覆盖——反馈通道 E2E 要用 config.update + 轮询，并串行跑
type: project
---


`tests/e2e/helpers/electron-setup.ts` 的 `launchElectronApp(patchConfig)` 在调用 `patchConfig(config)`
**之后**才执行 `config.channels = { ...config.channels, feishu: {...}, feedback: { enabled: false, ... } }`，
所以 patchConfig 里对 `channels`（尤其 `channels.feedback`）的任何改动都会被静默丢弃。

**Why:** 想给反馈通道 E2E 打开 `channels.feedback.enabled` 时踩到；症状是提交报
`反馈功能未启用 (FEEDBACK_DISABLED)`，而 patchConfig 明明写了 true。

**How to apply:** 需要非默认 channels 的 spec，改在 `launchElectronApp` **之后**走
`window.miqi.config.update({ channels: {...} })`，再轮询 `window.miqi.config.get()` 确认桥读到新值
（Python `load_config` 有 5s TTL 缓存，写完立刻提交会读到旧值）。参照
`tests/e2e/feedback-platform.spec.ts` 的 `enableFeedbackChannel`。

另：同一 spec 文件里多个用例各自启动 Electron 实例时，要
`test.describe.configure({ mode: 'serial' })` —— 并行启动会互相拖垮（Windows 上 userData
缓存争用 → 桥不 ready，用例 600s 超时）。相关：[e2e-localstorage-shared-userdata](e2e-localstorage-shared-userdata.md)
