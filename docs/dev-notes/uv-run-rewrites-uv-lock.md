---
name: uv-run-rewrites-uv-lock
description: 本仓库跑 uv run/uv sync 会重写 uv.lock（版本号漂移），提交前要 checkout 还原
type: project
---


在本仓库（worktree 同样）跑 `uv run pytest ...`、`uv sync` 等会把 `uv.lock` 里的 `miqi` 版本号从 `0.29.1` 刷成 `0.30.0.dev0`（跟随 pyproject），出现在 `git status` 里。

**Why:** 这是工具链副产物，与业务改动无关；混进 PR diff 会扩大改动面、干扰 review。

**How to apply:** 提交前 `git checkout -- uv.lock`；若确实要提交锁文件更新，单独一个 `chore` 提交说明原因。
