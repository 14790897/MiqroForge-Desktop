---
name: docs-site-publish-pipeline
description: 文档站发布管道：Actions 按仓库 docs/ 构建（gh-pages 已废弃）；页面必须进 docs/ + mkdocs nav 才会发布
type: project
---


文档站 https://mygithub.sixiangjia.de/MiqroForge-Desktop/ 自 2026-09-03 起由 GitHub Actions 发布：`.github/workflows/deploy-docs.yml`（push 到 main/develop 且 docs/**、mkdocs.yml、CHANGELOG.md 变动时触发；`mkdocs build --strict` + deploy-pages）。仓库的 `gh-pages` 分支是 2026-09-02 之前手动 `mkdocs gh-deploy` 的遗留物，已不再更新，但保留着最后一份完整构建产物，可作历史内容备份。

**Why:** 2026-09-03 发布方式切换后，此前只存在于本地未提交工作区的文档（「产品形态与技术边界 (V1)」页 + features.md 改写）从站点消失；2026-09-14 排查定位，PR #1059 恢复。教训：未提交、未加 nav 的文档即使被手动发布过，也不会出现在线上。

**How to apply:** 遇到「文档站某页 404/打不开」：先确认源文件在 `docs/` 且 `mkdocs.yml` nav 有条目（本地用 `uvx --from mkdocs-material mkdocs build --strict` 复现 CI）；再用 `git ls-tree -r <gh-pages tip>` 与旧产物比对找回历史内容。要上线新页面 = 提交进 docs/ + 加 nav，合入 develop 后自动发布。相关：[dev-notes-export-location](dev-notes-export-location.md)
