---
name: miqro-org-migration-fallout
description: 2026-10-12 仓库迁到 miqro-org 后掉了哪些设置：ruleset bypass actor、Pages 构建源、文档自定义域名的继承关系（含恢复手法与新文档地址）
type: project
---


仓库 2026-10-12 从 `14790897/MiqroForge-Desktop` 迁到 `miqro-org/MiqroForge-Desktop`。GitHub 侧有三样东西没跟着走，都是迁后才发现、已修的：

1. **ruleset 的 bypass actor 被清空**（`develop-quality-gate` 与 `main` 两个 ruleset 的 `bypass_actors` 从「账号 14790897，always」变成 `[]`）。表现是「我怎么不能直接 merge 了」。恢复：`PUT /repos/{owner}/{repo}/rulesets/{id}`，body 用 GET 出来的 `name/target/enforcement/conditions/rules` 原样带上，只把 `bypass_actors` 设为 `[{"actor_id":121866954,"actor_type":"User","bypass_mode":"always"}]`（121866954 = 账号 14790897 的 user id）。**注意** 仓库技能文档 `skills/github-workflow/SKILL.md` 里写明 develop↔main 的自动同步合并依赖 RELEASE_TOKEN 是 bypass 账号，bypass 一没，每周发版和双向同步的自动合并就会失败，迁后要顺手确认这两个 ruleset 的 bypass 还在。

2. **Pages 构建源被重置成 legacy**（源=gh-pages 分支），而 `deploy-docs.yml` 用的是 `actions/deploy-pages@v4`，要求构建源是 GitHub Actions —— 不修的话下次推 `docs/` 部署必失败。这是仓库设置，API：`PUT /repos/{owner}/{repo}/pages -f build_type=workflow`。

3. **文档站自定义域名失效**。文档地址原本是 `https://mygithub.sixiangjia.de/MiqroForge-Desktop/`，靠的是「用户站（`14790897.github.io`）的自定义域名自动继承给该账号下的项目站，形式为 域名/仓库名」这条 GitHub 规则；仓库迁走后不再是该账号的项目，继承断掉，该路径 404。
   **Why:** `mygithub.sixiangjia.de` 是账号 14790897 的个人站域名，用户要求个人站保持不动，所以不能把域名挪到文档仓库。
   **How to apply:** 文档现在挂在文档仓库自己的自定义域名 `https://miqro.sixiangjia.de/` 上（PR #1276 改的 `mkdocs.yml` 的 `site_url`）。项目仓库自己挂自定义域名时站点出在**域名根**、不带仓库名，所以 `site_url` 就是根地址；对应的断言在 `apps/desktop/tests/docsLinks.test.ts`。DNS：`miqro.sixiangjia.de` CNAME → `miqro-org.github.io`，外加验证用 TXT `_github-pages-challenge-miqro-org.miqro.sixiangjia.de`（要长期保留）。组织站仓库 `miqro-org/miqro-org.github.io` 曾短暂创建用于备选方案，已删除。

4. **仓库内的旧 owner 引用**（2026-10-13 发现）。上面三条是 GitHub 设置层面的，这一类是**文件内容**层面的：迁仓库不会改你仓库里的字符串。
   - **最严重的一处已修**（PR #1281）：`package.json` 的 `repository.url` 还写着 `14790897/MiqroForge-Desktop.git`。semantic-release 的 `verifyConditions` 会逐字比对它与 CI 克隆下来的 URL，不一致直接 `EMISMATCHGITHUBURL` 退出——**develop 每次 push 的 `Release and Build` 全红**（release job 18 秒就挂，`build-mac`/`build-win` 因依赖它而未启动），develop 的 `-dev.N` 预发布链断掉。`release.config.cjs` 没覆盖这个值，`package.json` 是唯一来源；`release.yml` 与 `electron-builder.yml` 都没写死 owner。
   - **其余已在 PR #1284 收口**（2026-10-13）：`mkdocs.yml`、spec 的写死字面量、`pr-image-post.ts`、`pr-template-check.yml`、`bug_report.yml`、docs 与 skills 示例。不动的是历史记录（CHANGELOG、`docs/dev-notes/**` 记忆导出、历史 issue 链接、HANDOFF 文档）。
     **关键发现**：**设置页显示的仓库地址不是写死的** —— `mkdocs.yml` 的 `repo_url` → `electron.vite.config.ts` define 成 `__REPO_URL__` → `docsLinks.ts` 的 `REPO_URL`/`REPO_LABEL` → `SettingsPage.tsx` 渲染。所以改 mkdocs.yml 一处，界面就跟着对，**不用动应用代码**。但 `issue-1155-docs-links.spec.ts` 里有一处 `toContainText` 是**写死的字面量**，不跟着单一来源走，迁移后会假失败。
   - **仍未收口**：真正生效的技能副本在本机 —— `~/.claude/skills/github-workflow` 是 symlink → `~/.cc-switch/skills/github-workflow`，那里还有 9 处 `14790897`（不在仓库里，PR 覆盖不到；且 `.cc-switch` 疑为 CC Switch 工具托管，改前先确认）。

   **How to apply:** 迁仓库或改 owner 后，别只查 GitHub 设置——`git grep <旧 owner>` 一遍，重点是 `package.json` 之类**工具会读的元数据**（semantic-release、electron-builder、CI 脚本），这类会直接让流水线红；文档/示例链接靠重定向能苟，但迟早要收。

另外：个人站 `14790897.github.io`（Jekyll + minimal-mistakes）的 Pages 构建自 2026-05-28 起一直失败（主题是 `_themes/minimal-mistakes` git 子模块，Pages 构建拉不到），所以**它没法再发布任何新路径** —— 想在那个域名下加内容的路子都不通。
