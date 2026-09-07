---
name: check-competing-pr-before-issue
description: "开工 issue 前先查是否已有 PR 在处理——#810 曾撞车 PR #845，白做一遍实现"
type: feedback
---

完成 issue 前，先 `gh pr list --repo 14790897/MiqroForge-Desktop --state all --search "<issue号> in:title"` 和 `gh search prs --repo 14790897/MiqroForge-Desktop "<issue号>"` 检查是否已有并发 PR。2026-08-26 完成 #810 时，sijie-Z 的 PR #845 已早 4 分钟提交（功能更全：修 SandboxPolicyEngine 硬编码 30s 根因 + 心跳 + 进程树终止），我的 #846 白做并已关闭让位。

**Why:** 团队里 sijie-Z（主力开发者）和其他人可能同时认领同一 issue；issue 列表状态是 OPEN 不代表无人动工。

**How to apply:** 任何「完成 issue X」任务的第一条工具调用之前，先跑一次 PR 搜索；若已有 open PR 覆盖该 issue，先向用户报告冲突而不是继续实现。相关：[pr-base-is-develop](pr-base-is-develop.md)、[miqroera-github-mirror-sync](miqroera-github-mirror-sync.md)
