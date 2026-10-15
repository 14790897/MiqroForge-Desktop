---
name: macos-packaging-release-only
description: "macOS 打包只在发版跑（PR #1077 从 PR CI 移除 macos-build）；pull_request_target 用基分支 workflow 定义，改动下个 PR 才生效"
type: project
---


Desktop CI（`desktop-ci.yml`）的 `macos-build` job 已删除（2026-09-15，PR #1077）——
它每个 PR 都起 macos-latest 做 bridge+Electron 打包，但产物 `miqi-desktop-macos`
没有任何下游消费方，而发版侧的 macOS 打包由 `release.yml` 的 `build-mac` 负责
（`push main` + `new_release == 'true'` 门控，构建后传 Release 资产）。

**Why:** 用户 2026-09-15 明确要求「macos-build 应该只在 release 的时候跑」；
macOS runner 约 7 分钟/次 × 每个 PR × 每次 push，纯浪费。代价：只在 mac 上暴露的
打包问题（如 buildresources license 的 DMG 编码坑）今后到发版才暴露。

**How to apply:** 排查「为什么某个 CI 没跑/跑了」先看 `desktop-ci.yml` 的 jobs 与
`gh api repos/.../rulesets` 的必需检查（develop 只要求 `electron-e2e` + `check-title`）。
注意 **`pull_request_target` 使用基分支上的 workflow 定义**，所以改 workflow 的 PR
自身仍会跑旧 job，要合入后新开的 PR 才生效。`macos-e2e`（约 20 分钟/PR）仍在跑，未收敛。
