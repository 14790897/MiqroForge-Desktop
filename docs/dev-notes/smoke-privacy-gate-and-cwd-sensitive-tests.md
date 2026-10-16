---
name: smoke-privacy-gate-and-cwd-sensitive-tests
description: smoke 跑前必须重建 out/renderer + mock 需预置隐私同意与 config.onUpdated；exec-timeout 测试对 cwd 敏感（node_modules junction）
type: project
---


**smoke 测试前置条件（2026-09-02 确认）**：
- smoke 项目 serve 的是**预构建产物** `apps/desktop/out/renderer`（python http.server 3458），改渲染层代码后必须先 `npx electron-vite build` 再跑 smoke，否则测的是旧构建
- 3458 有残留旧 http.server 时 `reuseExistingServer: true` 会复用它继续 serve 旧目录——先 netstat 查 3458 并 taskkill（见 [confirm-card-issue-714-fix](confirm-card-issue-714-fix.md) 的同源坑）
- 当前 develop 的 mock（tests/smoke/mocks.ts）已补：#837 隐私门预置 `localStorage.setItem('miqi:privacyConsentVersion','1.0')`、#789 `config.onUpdated` 订阅、qraft pointsBalance——新组件若再依赖新 preload API，需同步补 mock，否则整页渲染错误

**cwd 敏感的 Python 测试**：
- `tests/execution/test_exec_timeout_810.py::test_outer_cancellation_kills_process_tree`：ExecTool 执行前 `_snapshot_workspace(os.getcwd())` 遍历工作目录，worktree 根有 node_modules junction（数万文件）时快照 >2s，wait_for 2s 超时导致子进程没启动就失败。worktree 里跑必挂，从干净 cwd（如 /tmp/clean-cwd）用 `uv run --project <worktree>` 跑可过——是环境问题不是代码问题
- tests/bridge 的 audit 测试用相对路径读 miqi/bridge/loop.py，必须从仓库根跑 pytest

**2026-09-04**：用户决定直接删除 12 个因 #918 欢迎页改版过期的 chat smoke 用例（占位符/气泡/键盘/布局断言挂在新 UI 上），不修只删——issue-109/172 整文件删除，smoke.spec.ts 与 issue-226 只删失败用例。删除后 smoke 全绿（64 passed / 1 skipped）。

**2026-09-16：mock 缺口再次发生且被 CI 开关掩盖（#1095 发现并修复）**
- 资产面板窗口同步（ChatConsole 冷启动 `window.miqi.app.setPanelWindowExtra`）没补进 mock → 挂载即 `Render Error`，**65/75 smoke 用例静默失效**。补 `window.miqi.app`（quit / setPanelWindowExtra 返回 `{ok,applied:0,skipped:true}`）后恢复 74/75。
- 根因是 CI 里 smoke 步骤长期挂着 `continue-on-error: true # flaky in headless, don't block`：9/75 通过也显示绿。**已在本仓库去掉该开关（smoke 现在是 PR 门禁）**——失败模式是确定性崩溃不是抖动，瞬时抖动由 playwright.config.ts 全局 CI `retries=2` 兜底。若哪天 smoke 变门禁后又出现真抖动，回退那一行即可。
- 教训：渲染层每用一个新 preload/主进程 API（尤其 mount 期调用的），必须同步补 mock，否则整页崩；本地 smoke 全绿 ≠ CI 门禁生效，注意 `pull_request_target` 用的是基分支 workflow。
