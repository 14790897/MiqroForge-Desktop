---
name: macos-ci-local-mock-server
description: macOS GitHub runner 上 E2E 无法使用本地 mock 服务器（undici fetch 连不上 127.0.0.1 监听、spawn 的 python stdout 管道不投递），需跳过或改用其他验证方式
type: project
---

MiQroForge Desktop 项目 macos-e2e（GitHub runner）上，E2E spec 里 spawn 的本地 mock 服务器（scripts/mock_openai.py）不可用，两次实测（PR #711）：

1. **undici fetch 探测失败**：mock 服务器已成功绑定并打印启动行，但 Node `fetch('http://127.0.0.1:8899/v1/models')` 持续 30s 报 `TypeError: fetch failed`（无具体 cause）。
2. **stdout 管道不投递**：改成随机端口 + stdout 就绪行判定后，spawn 的 python 进程零 stdout/零 stderr、不退出（30s 后被 kill），启动行永远收不到。

处理方式（与 #710 的 macOS 裁剪策略一致）：spec 内 `test.skip(process.platform === 'darwin' && !!process.env.CI, ...)`，由 Linux electron-e2e job 全量覆盖。**Why:** macOS runner 网络栈/管道行为与本地不同，盲修不可验证。**How to apply:** 新 E2E 涉及本地 HTTP mock 时，默认在 macOS CI 跳过，注释引用 #711 的两次实测；另外 mock 服务器要支持 SSE（OpenAI SDK stream:true 对纯 JSON 响应解析出 0 chunk，回合空回复——见 confirm-card-sse-mock 相关记录）。
