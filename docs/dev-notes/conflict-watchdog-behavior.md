---
name: conflict-watchdog-behavior
description: 冲突看门狗（conflict-watchdog.yml）的既定行为与用户定下的边界：冲突时标记 changes requested + 只取消 14790897 本人的待评审请求，不碰任何已提交评审
type: project
---


`.github/workflows/conflict-watchdog.yml`（#1148 已合入 develop，PR #1154 增补第二条行为）：

- 冲突且非 draft → 以 github-actions[bot] 提交 REQUEST_CHANGES（正文含用 merge 解冲突的指引）；冲突解决（MERGEABLE）后自动 dismiss 自己的标记；draft 全程跳过
- 冲突期间**只取消「请求 14790897 评审」的待评审请求**：`DELETE /pulls/{n}/requested_reviewers`，账号由 env `CANCEL_REVIEW_REQUEST_FOR` 控制
- 触发：pull_request（单 PR）/ push 到 develop、main（REST 分页全量扫）/ 每日 schedule / workflow_dispatch；并发 group 按 `github.ref` 拆分，单 PR run 不会取消全量 run

**Why:** 用户 2026-09-20 先要求「冲突时把其他人的 review 取消」，看过影响面（会 dismiss #1116 的 6 条 CodeRabbit 标记、#719 的 7 条含用户本人的 2 条）后更正为「只取消我的 request review」，并确认语义是「取消请求我评审的请求」，不是「撤销我提的 changes requested」。即：任何已提交的评审（他人或本人的 CR/approve/comment）都不允许动。

**How to apply:** 扩展该看门狗时保持这条边界——不自动 dismiss/approve 任何已提交评审；查询类失败一律 `::warning::` 降级不中断冲突标记（评审请求取消是附加动作，读取失败也只置空继续）。相关 [repo-actions-token-permissions](repo-actions-token-permissions.md)、[pr-base-is-develop](pr-base-is-develop.md)
