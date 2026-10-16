---
name: repo-actions-token-permissions
description: MiQroForge-Desktop 仓库 Actions 权限事实——GITHUB_TOKEN 不能建/批 PR 但可提交 REQUEST_CHANGES；默认权限 read，workflow 必须显式声明 permissions
type: project
---


2026-09-20 实测（`gh api repos/14790897/MiqroForge-Desktop/actions/permissions/workflow`）：`default_workflow_permissions=read`、`can_approve_pull_request_reviews=false`。

- GITHUB_TOKEN **不能**创建或 approve PR——同步 workflow 用 RELEASE_TOKEN（身份 `14790897`）建 PR/合并就是这个原因；但**可以**提交 `REQUEST_CHANGES` review：该设置只限制「创建与批准」，request changes 不在其列（社区实践 cpp-linter 亦证实 bot 默认只能 request changes、不能 approve）。
- 仓库默认 token 权限是 read，每个 workflow 必须自己声明 `permissions:`（仓库现有 workflow 都这么做）。
- **Why:** 写冲突看门狗（PR 冲突自动 changes requested）时验证过：默认 token 就能提 REQUEST_CHANGES，无需 PAT。
- **How to apply:** workflow 里评论 / request changes / 读 PR 用默认 `GITHUB_TOKEN` 即可；建 PR、合并、approve 必须用 PAT。注意 PAT 提的 review 会以 PAT 账号（14790897）身份出现，可能与 PR 作者相同而无法自我 request changes。相关：[git-push-proxy-issue](git-push-proxy-issue.md)
