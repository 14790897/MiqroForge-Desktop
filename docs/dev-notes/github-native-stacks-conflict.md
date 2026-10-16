---
name: github-native-stacks-conflict
description: "GitHub 原生 stacked PR 与本仓 merge 约定冲突（trunk 前移即失去线性历史、子 PR 被 trunk ruleset 评估）；解散用 POST /stacks/{n}/unstack"
type: project
---


本仓（14790897/MiqroForge-Desktop）不要用 GitHub 原生 stacked pull requests：仓库约定是 merge 同步 develop（见 [pr-base-is-develop](pr-base-is-develop.md)），而 stack 要求「fully linear history between its branches」，trunk（develop）一前移就失去线性历史、整个栈被卡住等 rebase。

**Why:** 2026-09-20 有人用网站 UI 把 #1071（feature/646-v2-ui-workbuddy → develop，77 提交）和 #1145（fix/1101-exec-upload-action-guard → feature 分支）加进 stack #1147（14:00:20 创建，develop 14:01/14:06 前移两个提交）。后果：① 栈失去线性历史，合并框要求 Rebase stack（会重写长分支，与 merge 约定相反）；② stack 里所有 PR 按 trunk 的 ruleset 评估且自底向上原子合并——#1145 被 #1071 拖住、且被 develop 的 required review 卡成 BLOCKED（解散后立刻变 CLEAN、reviewDecision 变空）；③ 官方 FAQ：stack 不支持 rule bypass 和 auto-merge。

**How to apply:** 遇到「stack 出问题」先查 `gh api repos/{o}/{r}/stacks`；要退出直接用 REST 解散，不要点 Rebase stack / 不要 rebase 共享分支：
`gh api -X POST repos/14790897/MiqroForge-Desktop/stacks/1147/unstack`（无 body；204 = 栈内已无 PR，栈解散；200 = 仍有 PR 返回剩余栈）。栈 REST 端点：GET `/stacks`、GET `/stacks/{n}`、POST `/stacks`（body `pull_requests` 自底向上、≥2）、POST `/stacks/{n}/add`、POST `/stacks/{n}/unstack`。规则文档：docs.github.com/en/pull-requests/reference/stacked-pull-requests。
