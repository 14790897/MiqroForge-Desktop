---
name: skip-ci-suppresses-workflow-runs
description: "提交信息里带 [skip ci] 会让 GitHub 对 release/tag/pull_request/push 事件都不创建 workflow run；本仓库自己写 commit/PR 标题时必须避开这个字面量"
type: project
---


GitHub 的 skip-ci 标记是**提交信息里的子串匹配**，命中后**不创建** workflow run 的事件类型包括：`push`（含 tag push）、`release`（published 等）、`pull_request`、`synchronize`。`pull_request_target` 和 `workflow_dispatch` **不受影响**（前者关联的是基分支提交，后者不是提交驱动）。

**Why（2026-09-15 两次实测）**：
1. v0.29.2 的 release 提交是 `chore(release): 0.29.2 [skip ci]`（semantic-release `@semantic-release/git.message` 固定带这个后缀），导致 `on: release` 的 `sync-main-into-develop.yml` **一次都没跑过**（`actions/runs?event=release` 总数为 0），develop 掉队 5 个提交、版本号停在 0.25.0。
2. 现场复现：本仓库自己提的 PR 提交标题里写了这个标记 → 5 个 `pull_request` 类检查（模板校验/标题语义/lint/format）全部没被创建，只有 `pull_request_target` 的 Desktop CI 跑了；把提交信息改写去掉标记重推后立刻全部正常触发。注意**代码块 / 行内代码里的字面量一样算数**——PR 正文里要引用它，可用 `[skip<!-- --> ci]` 这种 HTML 注释写法（渲染正常、原文不含该子串）；squash 合并时 PR 正文会进提交信息，所以正文也得防。

**How to apply**：在本仓库写 commit message / PR 标题 / PR 正文 / issue 正文时，**不要出现方括号包起来的 skip ci**（要提这件事就写「skip-ci 标记」或上面那种注释写法）。要让 workflow 在发版后触发，别依赖 `release` / tag push 事件，改挂 `workflow_run`（见 sync-main-into-develop.yml，PR #1082）。排查「workflow 没被触发」时，先看相关提交信息里有没有这个标记，再看事件类型。

相关：[release-sync-workflow-never-fires](release-sync-workflow-never-fires.md)、[develop-to-main-sync-pr](develop-to-main-sync-pr.md)
