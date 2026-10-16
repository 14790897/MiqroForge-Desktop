---
name: auto-create-pr-after-task
description: 完成任务（代码改动+测试通过）后自动提交、推送并创建 PR，不用等用户要求
type: feedback
---


用户要求（2026-09-10）：完成功能/修复任务后，**自动**走完提交流程并创建 PR——不等待用户单独说「创建 PR」。

**Why:** 用户的工作流以 PR 为交付终点；本次会话实现完「登录失效告知」后停住等指令，用户补发了 create-pr 命令，说明期望是默认自动创建。

**How to apply:**
1. 代码改动完成且验证通过（单测/typecheck/相关 E2E）后，自动执行：commit（conventional commits 规范）→ push → `gh pr create`（非 draft，base 默认 develop，标题/正文严格按 PR 模板）。
2. 全程遵循 github-workflow 技能：PR body 用 `.github/pull_request_template.md` 全部必填节、Feature PR 截图走 `_gh-imgup` 上传嵌入、push 前 unset HTTP(S)_PROXY、新分支首次推送后 `git ls-remote` 核对 develop SHA 未污染、创建前先查该分支是否已有 PR（有则只推送新 commit 并报告已有 URL，不改 draft 状态）。
3. 若有实质未决事项（如关联 issue 号缺失、目标分支不确定），先完成能做的部分，在 PR 里说明，不要卡住不发。
4. 完成后报告 PR URL。相关 [invoke-github-workflow-skill](invoke-github-workflow-skill.md) [pr-base-is-develop](pr-base-is-develop.md) [git-push-proxy-issue](git-push-proxy-issue.md) [miqroera-github-mirror-sync](miqroera-github-mirror-sync.md)
