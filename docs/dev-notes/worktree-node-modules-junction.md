---
name: worktree-node-modules-junction
description: MiQi worktree 里跑测试/构建前要 junction 主仓库的 node_modules 和 .venv，并装 Playwright 浏览器
type: project
---


在 `.claude/worktrees/<name>`（MiQi-Desktop 的 git worktree）里跑 npm typecheck/vitest/playwright 前，worktree 没有 node_modules，直接跑会报 `Cannot find module 'vitest'/'electron'/'zod'`。主仓库 `C:\git-program\test\MiQi-Desktop\apps\desktop\node_modules` 是完整的。**Why:** npm ci 在 Windows 上重装耗时长且占空间；junction 零拷贝且 node_modules 是 gitignored，不影响 git 状态。**How to apply:**

```bash
cmd //c "mklink /J <worktree>\apps\desktop\node_modules C:\git-program\test\MiQi-Desktop\apps\desktop\node_modules"
```

注意：junction 共享同一个 node_modules，主仓库和 worktree 并行 npm 操作会互相影响（一般无碍）。后端测试用主仓库 venv：`PYTHONPATH=. "C:\git-program\test\MiQi-Desktop\.venv\Scripts\python.exe" -m pytest tests/...`。

**共享 node_modules 可能缺包**（2026-09-14 实证）：主仓库 node_modules 曾缺 `jsdom`（package.json 声明了 ^25 但没装），vitest 报 `Cannot find package 'jsdom'`。修法：在 apps/desktop 下 `npm install jsdom@25 --no-save --no-audit --no-fund`（装进共享 store，不动 package.json/lock；会顺带补齐几百个缺失包）。

**⚠️ 不要 junction `out/`（构建产物）**：`src/main/index.ts` 用 `join(__dirname, '../../..')` 推 repoRoot，`__dirname` 经 junction 会解析回**主仓库**路径 → 桥接跑主仓库的 `miqi/bridge/server.py` + 主仓库 venv，worktree 的 Python 改动对 E2E 完全不可见（2026-08-17 skill 注入优化踩坑 2 小时）。worktree 里 E2E 前必须本地 `npm run build`（node_modules junction 可以保留，build 会正常输出到 worktree 自己的 apps/desktop/out）。另：dev 模式 userData = `%APPDATA%/miqi-desktop-dev/ws-<repoRoot hash>`，Electron 崩一次后缓存损坏会连环启动即崩，清掉对应 ws-* 目录即可恢复。

**E2E 还要补两样（2026-09-14 实证，worktree 首跑 electron E2E 用到）**：

1. **`.venv` junction**：worktree 根有 pyproject.toml/uv.lock 时桥接走 `uv run python` 源码模式，worktree 没有 .venv → junction 主仓库的：
   ```bash
   cmd //c "mklink /J <worktree>\.venv C:\git-program\test\MiQi-Desktop\.venv"
   ```
2. **Playwright 浏览器**：共享 node_modules 的 playwright 版本要求的浏览器可能没装全，headless shell 缺失时 smoke 整套 74 个用例全挂在 `browserType.launch: Executable doesn't exist ... chromium_headless_shell-1234`（不是代码回归，别误判）：
   ```bash
   cd apps/desktop && npx playwright install chromium-headless-shell
   ```

另：`npm run test:electron` 里管道接 `| tail` 会把退出码吃掉（`${PIPESTATUS[0]}` 或读 `test-reports/results.json` 判成败）。
