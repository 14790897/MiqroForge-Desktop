---
name: coderabbit-config-precedence
description: "CodeRabbit 配置来源与优先级（Org UI vs .coderabbit.yaml）+ inheritance 默认关闭的坑；本仓 2026-10-08 起有仓库配置文件（drafts: true）"
type: reference
---


CodeRabbit 评审在本仓（14790897/MiqroForge-Desktop，public、OSS 免费、plan Advanced）的配置来源与优先级。

**优先级（高→低，云版）**：Workspace global overrides(0) > Organization global overrides(1) > **仓库 `.coderabbit.yaml`(2)** > Central YAML(3) > Repository UI(4) > **Organization UI(5)** > Workspace settings(6) > schema 默认值(7)。

**关键坑**：`inheritance` 默认 **false**，仓库文件一旦存在就顶掉所有更低来源；本文件没定义的键会掉回 schema 默认值，而不是留在原 UI 设置上。要保留上层设置必须在文件顶层写 `inheritance: true`（开启后逐层向上合并：对象深合并、数组子项在前父项去重追加、标量子覆盖父）。

**本仓现状（2026-10-08 起）**：
- 之前**完全没有** `.coderabbit.yaml`，配置走 Organization UI（PR 评论 Run configuration 显示 `Configuration used: Organization UI`、`Review profile: CHILL`）。
- PR #1243 新增根目录 `.coderabbit.yaml`：`inheritance: true` + `reviews.auto_review.drafts: true`。`drafts` 是 `reviews.auto_review` 下的合法键（schema 默认 false，"Include draft PRs"）。
- 配置文件读的是**被评审 PR 的 head 分支**上的版本，不是默认分支；所以合入 develop 后对之后从 develop 切的分支生效。

**Why**：draft 评审的动机是与仓内 `pr-agent.yml`（Codium PR-Agent，触发类型无 draft 过滤）行为对齐，WIP 阶段也能拿到 CodeRabbit 反馈。

**How to apply**：改 CodeRabbit 设置前先看有没有仓库文件、它盖住了什么；改完用 PR 评论 `@coderabbitai configuration` 打印带来源注释的完整配置来验证。公开仓库多评审不额外收费，成本不是考量点。

相关：[invoke-github-workflow-skill](invoke-github-workflow-skill.md)、[auto-handle-pr-reviews-and-ci](auto-handle-pr-reviews-and-ci.md)、[repo-renamed-miqroforge-desktop](repo-renamed-miqroforge-desktop.md)
