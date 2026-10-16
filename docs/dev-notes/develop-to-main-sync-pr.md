---
name: develop-to-main-sync-pr
description: "develop↔main 双向同步 PR 的标准格式 + 每周三/周五定时发版（保留）+ develop 每次推送发 -dev.N 预发布（不写文件、不打包）"
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

**⚠️ main 分支曾两次因反向同步被误删（2026-08-10 #651、2026-08-25 #813）：** 反向合并 main→develop 时 head 分支直接用 `main`，仓库开启了"合并后自动删除 head 分支"（`delete_branch_on_merge: true`），导致 main 每次合并后都被删。**已确认方案（2026-08-25）：反向同步一律改用临时分支 `chore/sync-main-into-develop` 作为 head，绝不用 main 直接作 head。** 若 main 又被误删，用 `gh api repos/14790897/MiQi/git/refs -f ref=refs/heads/main -f sha=<合并前head>` 重建（先查 PR 的 `headRefOid`）。

**反向同步（main→develop）正确做法：** 建临时分支 `chore/sync-main-into-develop` 指向 main，用该临时分支作为 head 创建 PR 到 develop，避免 main 被自动删除。

**自动化流水线（2026-09-14 起，用户选择「建 PR 后立即合并」策略）：**
- `.github/workflows/weekly-release.yml`：每周三、周五 00:00 Asia/Shanghai（cron `0 16 * * 2,4`，即周二/周四 16:00 UTC）自动创建 develop→main 发布 PR 并立即合并（用 `RELEASE_TOKEN`，否则 GITHUB_TOKEN 触发的 push 不会启动 release.yml）；支持 `workflow_dispatch -f dry_run=true` 只建 PR 不合并
- `.github/workflows/sync-main-into-develop.yml`：正式 release published 时自动反向同步（临时分支 + 立即合并）
- **develop 版本号约定：** 反向同步时追加 `chore(version): develop 版本号标记为 X-dev` 提交，develop 版本 = 最新已发布版本 + `-dev`（如 `0.30.0-dev`），手动反向同步也需遵守
- **develop 推送触发预发布（2026-10-03 起，用户要求「semantic release 每次推送直接触发，定时保留」）**：`release.yml` 触发条件加 `push: branches: [develop]`，每次推 develop 都跑 semantic-release，提交符合发布规则（`feat`/`fix`/`perf`/破坏性变更）才发 `vX.Y.Z-dev.N` 的 GitHub Prerelease，纯 `chore`/`docs`/`test` 不创建 release（本地 dry-run 实测：0.36.1 → 0.37.0-dev.1）。分支差异写在根目录 `release.config.cjs`（从 package.json 的 `release` 字段迁出，cosmiconfig 认 `release.config.cjs`）：`GITHUB_REF_NAME=develop` 时**跳过 changelog/exec/git 三个会改仓库文件的插件**——develop 的版本号由反向同步统一标记成 `<已发布版本>-dev`，两边都写会互相覆盖、每周发布 PR 必然在 CHANGELOG 上冲突。打包门控：build-win/build-mac 用 `needs.release.outputs.prerelease != 'true'` 跳过预发布。**检测新版必须只认本次运行的 tag**（`git tag --points-at HEAD` → 再按该 tag 查 `isPrerelease`，查不到按 `vX.Y.Z` vs `vX.Y.Z-dev.N` 兜底）：concurrency 按分支分组，develop 与 main 可同时在跑，用「仓库最新 release」会拿到另一分支的版本，导致正式版被打包跳过（PR #1232 评审发现）。定时发版（每周三/五）**保留不变**
- 若自动合并失败（如冲突）工作流会重试 5 次后报错，PR 保留给人处理；加急/临时发布仍走上面的手动流程
- **依赖与约束（2026-09-14 核实）**：自动合并依赖 `RELEASE_TOKEN` 是仓库 owner 账号（`develop-quality-gate`/`main` 两个 ruleset 的 bypass actor，`bypass_mode: always`；legacy branch-protection API 查不到这些 ruleset，要用 `gh api repos/.../rulesets`）；token 换为非 bypass 账号会导致两个 PR 合并失败。反向同步刻意用 merge commit（绕过 develop 的 `required_linear_history`）：squash 会丢失祖先关系，下一轮同步版本号/CHANGELOG 行必然冲突。weekly-release 的空提交检查已排除 `chore(version):` 标记提交（否则每周误建空发布 PR）
- **凭证处理（安全，2026-09-14）**：两个工作流 checkout 都 `persist-credentials: false`（仓库 public，fetch 匿名）；`RELEASE_TOKEN` 只在写操作步骤显式注入（sync 推送用 `https://x-access-token:…` URL + 显式租约 `--force-with-lease=<ref>:<sha>`，gh 步骤用 env），避免仓库内脚本执行时能读到凭证。release.yml 的 release job 仍是旧模式（token 持久化 + 执行 npx semantic-release），未改、属既有问题
- **同步 PR 的截图节：保持现状（用户 2026-09-14 决定）**——自动化生成的同步 PR 没有界面可截，截图节仍写「无界面变更（…）」，新版 pr-template-check（要求真实图片）会让 `validate` 变红，但自动合并不依赖它、不阻塞。**不要**为此给同步 PR 塞说明图，也**不要**去改 pr-template-check 加豁免；用户明确选择「不管它」
