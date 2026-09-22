---
name: local-e2e-before-cloud-verification
description: 为提速：先在本地把 E2E 跑通，最后再推云端让 CI 做最终验证；不要靠反复推云端来迭代
type: feedback
---

**用户要求（2026-09-22）：为了加快速度，应该本地 E2E 跑通后，最后再云端验证。**

**Why:** 云端 CI（`electron-e2e` 等）排队 + 全量执行耗时长，靠「推一版 → 等 CI → 看结果 → 再推」来迭代非常慢；本地跑 E2E 反馈快得多，云端只做最终确认。

**How to apply:**

1. 完成任务后（含评审修复），先在本地把受影响的 E2E 跑到通过——本地跑法/已知坑见
   [local-sandbox-e2e-unavailable](local-sandbox-e2e-unavailable.md)、
   [smoke-privacy-gate-and-cwd-sensitive-tests](smoke-privacy-gate-and-cwd-sensitive-tests.md)、
   [worktree-node-modules-junction](worktree-node-modules-junction.md)、
   [e2e-exec-slow-spawn-timeout](e2e-exec-slow-spawn-timeout.md)、
   [e2e-localstorage-shared-userdata](e2e-localstorage-shared-userdata.md)。
2. 本地通过后再 commit → push → 建 PR（base `develop`，流程见 [pr-base-is-develop](pr-base-is-develop.md)、[invoke-github-workflow-skill](invoke-github-workflow-skill.md)），让云端 CI 跑最终验证。
3. 不要用「推云端看结果」当调试手段；push 前本地必须已绿。云端仍偶发 flaky（依赖真实 LLM 的用例可能重试全败），那是重跑失败 job 的问题，不是本地验证的替代。
