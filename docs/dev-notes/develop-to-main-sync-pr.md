---
name: develop-to-main-sync-pr
description: "develop↔main 双向同步 PR 的标准格式 + 每周三/周五自动发版与反向同步流水线（develop 版本号带 -dev 后缀）"
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

**自动化流水线（2026-09-14 起，用户选择「建 PR 后立即合并」策略）：**
- `.github/workflows/weekly-release.yml`：每周三、周五 00:00 Asia/Shanghai（cron `0 16 * * 2,4`，即周二/周四 16:00 UTC）自动创建 develop→main 发布 PR 并立即合并（用 `RELEASE_TOKEN`，否则 GITHUB_TOKEN 触发的 push 不会启动 release.yml）；支持 `workflow_dispatch -f dry_run=true` 只建 PR 不合并
- `.github/workflows/sync-main-into-develop.yml`：正式 release published 时自动反向同步（临时分支 + 立即合并）
- **develop 版本号约定：** 反向同步时追加 `chore(version): develop 版本号标记为 X-dev` 提交，develop 版本 = 最新已发布版本 + `-dev`（如 `0.30.0-dev`），手动反向同步也需遵守
- 若自动合并失败（如冲突）工作流会重试 5 次后报错，PR 保留给人处理；加急/临时发布仍走手动流程
- **依赖与约束（2026-09-14 核实）**：自动合并依赖 `RELEASE_TOKEN` 是仓库 owner 账号（两个 ruleset 的 bypass actor，`bypass_mode: always`）；token 换为非 bypass 账号会导致两个 PR 合并失败。反向同步刻意用 merge commit（绕过 develop 的 `required_linear_history`）：squash 会丢失祖先关系，下一轮同步版本号/CHANGELOG 行必然冲突。weekly-release 的空提交检查已排除 `chore(version):` 标记提交（否则每周误建空发布 PR）
- **凭证处理（安全）**：两个工作流 checkout 都 `persist-credentials: false`；`RELEASE_TOKEN` 只在写操作步骤显式注入（sync 推送用 URL 形式 + 显式租约 `--force-with-lease=<ref>:<sha>`，gh 步骤用 env），仓库内脚本执行时环境无凭证
