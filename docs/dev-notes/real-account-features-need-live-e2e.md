---
name: real-account-features-need-live-e2e
description: 涉及真实账号的功能必须补真实账号 live E2E（不只 mock 契约测试）
type: feedback
---


用户要求(2026-09-05):任何涉及真实账号(平台登录、计费、网关凭据等)的功能,除 mock/契约 e2e 外,**必须再跑真实账号 live E2E**。

**Why:** mock e2e 用假凭据只验证链路契约,无法证明"真实拿到 apikey/凭据并成功调用"。#922 曾只有 mock e2e,用户明确质疑后才补 live 实测。

**How to apply:**
- 模式沿用 `tests/e2e/billing-live.spec.ts` 与 `tests/e2e/ai-gateway-live.spec.ts`:opt-in,QRAFT_LIVE/QRAFT_PHONE/QRAFT_PASSWORD 环境变量门控,默认 skip 不入 CI 常规执行;登录态与 token 落临时 MIQI_HOME,测试结束清理
- 测试账号见 [qraft-test-account-credentials](qraft-test-account-credentials.md)(test 环境,勿写代码/CI,只经环境变量注入)
- 跑法:`QRAFT_LIVE=1 QRAFT_PHONE=… QRAFT_PASSWORD=… PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test --config=playwright.config.ts --project=electron <spec>`
- 验证层级:①协议 live 实测(vitest live.*.test.ts)②PR 代码路径真实调用 ③真实账号 E2E(UI 全链路),三层都要过才叫"确定"。
