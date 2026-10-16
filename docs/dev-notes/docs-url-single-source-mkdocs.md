---
name: docs-url-single-source-mkdocs
description: 文档站/仓库地址唯一来源是仓库根 mkdocs.yml，构建期注入渲染层；src/shared 不能引 node:fs（typecheck:web 无 node types）
type: project
---


桌面端「文档站根地址 / 仓库地址」的唯一事实来源 = 仓库根 `mkdocs.yml`（`site_url` / `repo_url`，deploy-docs.yml 用它构建发布站点）。

链路（#1155 / PR #1161，2026-09-21）：`apps/desktop/scripts/mkdocs-site-urls.mjs` 读 YAML 标量 → `electron.vite.config.ts` 与 `vitest.config.ts` 注入 `__DOCS_BASE_URL__` / `__REPO_URL__` → 渲染层只用 `src/renderer/features/settings/docsLinks.ts` 导出的 `DOCS_BASE_URL` / `REPO_URL` / `REPO_LABEL` / `DOCS_TREE`（单测 `tests/docsLinks.test.ts`、E2E `tests/e2e/issue-1155-docs-links.spec.ts` 守着）。

**Why:** 原来根地址硬编码在 SettingsPage，仓库更名 MiQi → MiqroForge-Desktop 后「设置 → 文档」全部链接 404。用户要求单一来源，避免仓库/域名再变时前端漂移。

**How to apply:**
- 文档域名或仓库改名：只改 `mkdocs.yml`，前端自动跟随；不要再在渲染层写字面量 URL。
- 新增构建期注入的坑：`src/shared/**` 同时被 tsconfig.node（`types:["node"]`）和 tsconfig.web（无 node types）收录，所以 `node:fs` 之类只能放 `apps/desktop/scripts/*.mjs`（两份配置都 import 它；绝不能被渲染层 import，否则 vite 会去 resolve node:fs）。`__dirname` 在 electron.vite/vitest 配置里可用（vite 注入 shim）。
- 仍在用旧仓库名的余留（都走 GitHub 重定向、非用户可见 404，未修）：`.github/ISSUE_TEMPLATE/*`、`.claude/skills/e2e-test-workflow.md`、`tests/e2e/helpers/pr-image-post.ts` 的默认 `MIQI_IMG_REPO`。
