---
name: release-sync-workflow-never-fires
description: sync-main-into-develop 的 release 事件从未触发过（仓库 release run 总数为 0），导致 develop 版本号/提交长期落后 main
type: project
---


`sync-main-into-develop.yml`（#1052 加的「发版后 main → develop 反向同步」）**从未运行过**：`gh api repos/14790897/MiqroForge-Desktop/actions/runs?event=release --jq .total_count` = 0，该 workflow 自身 run 数也是 0（workflow 状态 active，YAML 合法，default branch = develop 且文件在 develop 上）。

**Why:** v0.29.2（2026-09-15）由 PR #1064 直接合入 main 触发 `Release and Build` 发布，本应由 release published 事件拉起反向同步，但没拉起。结果 develop 缺这 5 个 skills 提交、`package.json` 版本仍停在 0.25.0（main 已是 0.29.2）、CHANGELOG 与 legal 资源等多处内容 main 有 develop 无。develop 与 main 已是双向分叉（155 个文件内容不同、main 独有 276 个提交、develop 独有 26 个未发布提交）；注意其中大量是「同一改动不同 SHA」，判断缺失要用文件内容核验，别只看 `git cherry`/`git rev-list`。

**How to apply:** 排查「develop 缺东西 / 版本号回退」时先查该 workflow 有没有跑，需要补同步就手动 `gh workflow run sync-main-into-develop.yml --ref develop`（会建 PR 并自动 merge 进 develop，属共享状态改动，动手前先问用户）。**根因已于 2026-09-15 定位并修（PR #1082，待合）**：主触发改为 `workflow_run`（Release and Build completed + `branches: [main]`），release 事件保留作兜底——详见 [skip-ci-suppresses-workflow-runs](skip-ci-suppresses-workflow-runs.md)。合并后应能自动跑，但首次真实发版（周三/周五）前不要假设它已生效。相关：[develop-to-main-sync-pr](develop-to-main-sync-pr.md)、[pr-base-is-develop](pr-base-is-develop.md)、[miqroera-github-mirror-sync](miqroera-github-mirror-sync.md)。
