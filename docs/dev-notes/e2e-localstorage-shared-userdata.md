---
name: e2e-localstorage-shared-userdata
description: E2E 的 --user-data-dir 被 main 的 app.setPath 覆盖，localStorage 在同 checkout 的所有运行间共享
type: project
---

dev 模式（!app.isPackaged）下 main/index.ts 会调用 `app.setPath('userData', %APPDATA%\miqi-desktop-dev\ws-<repoRoot哈希>)`，**覆盖**了 E2E helper 传入的 `--user-data-dir`。因此 localStorage（如 miqi:configReady、miqi:lastSession、#837 的 miqi:privacyConsentVersion）在同一 checkout 的所有 E2E 运行间共享，fresh userData 只隔离了 Chromium 缓存，不隔离 localStorage。

**Why:** #837 隐私协议确认门的 E2E 第二次运行直接跳过门（第一次运行的同意状态残留在共享存储），排查发现是 setPath 覆盖导致。

**How to apply:** 依赖 localStorage 状态的 E2E 要在测试开头显式清除/写入相关键（见 privacy-consent.spec.ts test 1 的清理模式），不能假设 fresh userData = 空 localStorage；跨 spec 的 localStorage 污染也要注意。相关：[e2e-exec-slow-spawn-timeout](e2e-exec-slow-spawn-timeout.md)
