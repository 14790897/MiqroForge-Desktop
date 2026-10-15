---
name: e2e-must-have-real-ai-test
description: 用户要求（2026-09-30）e2e 技能必须规定「真实 AI 测试」为硬性规则；mock-only 覆盖不算交付
type: feedback
---


用户 2026-09-30 要求改 e2e 技能：**要求必须有真实 AI 测试**。已落进
`skills/e2e-test-workflow/SKILL.md` 的「必须有真实 AI 测试（硬性规则）」章节（PR #1228，2026-09-30），
并同步到个人副本；`miqi-e2e/SKILL.md` 的「Bridge API Testing Without LLM」加了交叉引用。

**Why:** 之前技能只教 mock/桥接怎么写，没规定必须有真机用例 → 多次出现「mock 全绿、真实路径仍坏」；
用户此前在真实账号功能上已提过同类要求（[real-account-features-need-live-e2e](real-account-features-need-live-e2e.md)），这次把它扩大成
所有新增/修改功能的通用硬性规则，并要求写进技能（不只是口头共识）。

**How to apply:**
- 写/改 E2E 时至少有一条真实模型往返的用例（`<feature>-real-llm.spec.ts`，`launchElectronApp()`
  不传 patchConfig）；说「修好了」之前真机用例要实际跑过并给结果，不能拿 mock 绿灯代替
  （[verify-fix-in-real-app-not-just-mocks](verify-fix-in-real-app-not-just-mocks.md)）。
- 真机用例带守卫（QRAFT_LIVE / MIQI_RUN_* / win32）时必须在 `apps/desktop/tests/e2e/CI-COVERAGE.md`
  登记，否则 `npm test` 的 `e2eCiCoverage.test.ts` 会红。
- 本机跑真机用例前确认临时 MIQI_HOME 有可用 provider（本机 config 走 qraft 登录态时没有 provider 条目，
  会被「尚未配置模型服务」门禁拦下——那是环境问题不是回归，见 [local-real-config-no-provider](local-real-config-no-provider.md)）。

相关：[skill-copies-and-sync](skill-copies-and-sync.md)（此次三处副本的同步情况）。
