---
name: develop-to-main-sync-pr
description: "develop→main 同步 PR 的标准格式：标题 chore(release): merge develop into main，body 列出实际提交列表"
type: feedback
---

develop 合并到 main 的 PR 必须使用标准格式，而不是"定期同步"。

**Why:** 之前用了"定期同步"标题被指正，PR #594、#551 等历史 PR 都使用 `chore(release): merge develop into main` 格式。

**How to apply:** 创建 develop→main PR 时：
1. 先用 `git fetch origin develop main` 确保拿到远程最新
2. 用 `git log --oneline origin/main..origin/develop --no-merges` 获取实际提交列表
3. 标题固定为 `chore(release): merge develop into main`
4. Body 按模板列出每个提交的 `- \`hash\` message`，注明提交数量和验证证据
5. 不要用 `git log main...develop`（本地分支可能过期），必须用 `origin/main..origin/develop`

**⚠️ main 分支曾两次因反向同步被误删（2026-08-10 #651、2026-08-25 #813）：** 反向合并 main→develop 时 head 分支直接用 `main`，仓库开启了"合并后自动删除 head 分支"（`delete_branch_on_merge: true`），导致 main 每次合并后都被删。**已确认方案（2026-08-25）：反向同步一律改用临时分支 `chore/sync-main-into-develop` 作为 head，绝不用 main 直接作 head。** 若 main 又被误删，用 `gh api repos/14790897/MiqroForge-Desktop/git/refs -f ref=refs/heads/main -f sha=<合并前head>` 重建（先查 PR 的 `headRefOid`）。

**反向同步（main→develop）正确做法：** 建临时分支 `chore/sync-main-into-develop` 指向 main，用该临时分支作为 head 创建 PR 到 develop，避免 main 被自动删除。
