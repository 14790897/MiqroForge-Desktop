---
name: pr-base-is-develop
description: "PRs target the develop branch; sync with develop via merge, not rebase"
type: project
---

PRs must target `develop`, not `main`. Feature branches stay in sync with `develop` by **merging develop into the branch** (never rebasing).

**Why:** `develop` is the integration branch that stays ahead of `main`. Targeting `main` drags in dozens of unrelated commits from the `develop..main` gap, making the PR diff unreadable and triggering unnecessary CI. 2026-08-10 user explicitly asked: 解决冲突优先使用 merge（已写入 github-workflow 技能的"冲突解决"一节）。PR 采用 **squash 合并**，因此特性分支内 merge develop 产生的合并提交会被 squash 掉，不会污染 develop 历史。

**How to apply:** When creating a PR, always use `--base develop`. When the PR branch falls behind develop or hits a merge conflict, resolve it with `git merge origin/develop` (resolve conflicts manually, keep both branches' logic blocks) — do NOT rebase. Merge commit only contains the conflict resolution; test fixes go in separate commits.

**Squash 后开新 PR 的坑（2026-09-01 #901）**：develop 用 squash 合并（旧提交不在 develop 历史里）。若在已合并的本地旧分支上继续提交并推新 PR，merge-base 停在 squash 之前，GitHub 会把**已合并的文件**又列进新 PR 的 files（用户看到"改的文件不对"）。解法：`git fetch origin develop && git rebase --onto origin/develop <已合并分支旧HEAD>` 把新提交单独变基到最新 develop 再 force push（--force-with-lease）。此场景与"冲突解决用 merge"不冲突：冲突仍用 merge，squash 历史分叉用 rebase --onto 整理。
