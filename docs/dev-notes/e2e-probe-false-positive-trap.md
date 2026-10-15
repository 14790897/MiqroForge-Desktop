---
name: e2e-probe-false-positive-trap
description: "E2E 中用 locator('main').getByText(<提示词里的 marker>) 断言会命中用户自己的消息 → 假阳性；探针门禁曾有此坑（issue #1120 / PR #1122）"
type: project
---


E2E 断言 `page.locator('main').getByText(marker).first()` 时，若 `marker` 是**提示词里的字符串**（如 `只回答OK_<ts>`），它同时存在于用户自己的消息气泡与会话标题里——两者都在 `main` 内，`.first()` 先命中它们，于是**模型一次都没回复时断言照样通过**。正确口径是按 `[data-testid="chat-message-assistant"]` 定位（`helpers/electron-setup.ts:276-287` 的 `userMessage()` 注释已记录此坑）。

**Why:** 2026-09-17 用户发现「deepseek-pro 额度用完时 CI 的 AI 连通性探针仍然通过」。本机 Electron E2E 对照实验（provider 指向必 500 的 `scripts/mock_openai_error.py`）证实：修复前探针打印「✅ Full chat pipeline verified」并 1 passed，修复后同一场景 1 failed 并给出 `stage=chat` 诊断。https://github.com/14790897/MiqroForge-Desktop/issues/1120

**How to apply:**
- 写/审 E2E 断言时先问：这个字符串是不是用户自己发的？是 → 必须锚定 assistant 气泡或其他「只有 AI 才会产生」的证据
- `waitForResponseComplete` 只等 `main` 文本稳定，**不校验内容**；回合以「模型服务暂时不可用或过载」收尾时它照样返回
- 剩余同类口径还散落在 8 个 spec 中（PR #1122 只修了探针），排查时用 `grep -rn "locator('main').getByText" apps/desktop/tests/e2e`
- 相关：[real-account-features-need-live-e2e](real-account-features-need-live-e2e.md)、[e2e-exec-slow-spawn-timeout](e2e-exec-slow-spawn-timeout.md)
