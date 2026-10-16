---
name: stale-pr-branch-spec-mismatch
description: CI 失败先查 PR 分支是否落后 develop——workflow 来自基分支但代码来自 PR head，develop 上已修的 spec 不会生效
type: feedback
---


修某个 PR 的 CI 失败时，**先 `git rev-list --left-right --count origin/develop...origin/<branch>` 看落后多少**，别急着当 flake 重跑。

**Why:** Desktop CI 是 `pull_request_target`：workflow 文件来自基分支（develop），但 checkout 的是 PR head SHA。于是会出现「develop 上早就修好的测试，在 PR 分支上仍按旧版跑并确定性失败」。2026-09-23 PR #1182 实锤：`qraft-browser-login.spec.ts` 写死账号昵称 `MiQi测试`（平台 09-20 并入生产后昵称变 `liuweiqing12`，develop 的 #1170 已改成不写死昵称的正则），PR 分支落后 22 个提交 → 该用例 3 次尝试全败、阻塞 PR。合并 develop 后一次通过。

**How to apply:**
1. 分类失败：`gh pr checks <n>` + `gh run view --job <id> --log-failed`；「真实环境/平台/LLM 相关」与「断言写死的旧文案」要分开看。
2. 对每条失败，`git show <PR head>:<spec 路径>` 与 `git show origin/develop:<spec 路径>` 对拉——若 develop 版本已不一样，基本就是落后所致，合并 develop 即可（本仓惯例用 merge 不用 rebase，见 [pr-base-is-develop](pr-base-is-develop.md)）。
3. 确定性失败 vs flake 的判定：拿旧 spec 在本机做**反向对照**跑一次（真实账号用例可行，见 [qraft-test-account-credentials](qraft-test-account-credentials.md)），能复现即确定性。
4. 纯 LLM 超时类（`waitForResponseComplete` 120s 全重试失败）才是 [e2e-real-llm-hard-fail-rerun](e2e-real-llm-hard-fail-rerun.md) 的重跑场景，别混为一谈。
5. 合并后本地至少跑 smoke 全量（`npm run build` 后 `npx playwright test --project=smoke`）再 push，遵循 [local-e2e-before-cloud-verification](local-e2e-before-cloud-verification.md)。

相关：[real-account-features-need-live-e2e](real-account-features-need-live-e2e.md)、[skip-ci-suppresses-workflow-runs](skip-ci-suppresses-workflow-runs.md)
