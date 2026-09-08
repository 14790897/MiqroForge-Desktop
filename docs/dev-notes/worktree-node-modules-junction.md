---
name: worktree-node-modules-junction
description: worktree 里跑前端测试/构建前，用 junction 把主仓库的 apps/desktop/node_modules 链接进来，避免重新 npm ci
type: project
---

在 `.claude/worktrees/<name>`（git worktree）里跑 npm typecheck/vitest/playwright 前，worktree 没有 node_modules，直接跑会报 `Cannot find module 'vitest'/'electron'/'zod'`。主仓库 `<repo-root>\apps\desktop\node_modules` 是完整的。**Why:** npm ci 在 Windows 上重装耗时长且占空间；junction 零拷贝且 node_modules 是 gitignored，不影响 git 状态。**How to apply:**

```bash
cmd //c "mklink /J <worktree>\apps\desktop\node_modules <repo-root>\apps\desktop\node_modules"
```

注意：junction 共享同一个 node_modules，主仓库和 worktree 并行 npm 操作会互相影响（一般无碍）。后端测试用主仓库 venv：`PYTHONPATH=. "<repo-root>\.venv\Scripts\python.exe" -m pytest tests/...`。

**⚠️ 不要 junction `out/`（构建产物）**：`src/main/index.ts` 用 `join(__dirname, '../../..')` 推 repoRoot，`__dirname` 经 junction 会解析回**主仓库**路径 → 桥接跑主仓库的 `miqi/bridge/server.py` + 主仓库 venv，worktree 的 Python 改动对 E2E 完全不可见（2026-08-17 skill 注入优化踩坑 2 小时）。worktree 里 E2E 前必须本地 `npm run build`（node_modules junction 可以保留，build 会正常输出到 worktree 自己的 apps/desktop/out）。另：dev 模式 userData = `%APPDATA%/miqi-desktop-dev/ws-<repoRoot hash>`，Electron 崩一次后缓存损坏会连环启动即崩，清掉对应 ws-* 目录即可恢复。
