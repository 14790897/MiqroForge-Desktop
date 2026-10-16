---
name: miqro-gate-repo-map
description: miqro-gate（MiQroGate 网关仓库）协作事实——分支、权限、标签、本地克隆位置、模板已对齐桌面端
type: reference
---


第二个我们直接动手的仓库：`miqro-org/miqro-gate`（公开，MiQroEra 的凭证虚拟化/用量治理网关，Java 21 + Spring Boot 3 + Vue 3 + PostgreSQL）。区别于本会话主仓库 MiqroForge-Desktop。

- **分支**：默认 `main`，工作在 `develop`，**PR base 用 develop**（与桌面端一致）。main/develop 常在同一 commit（develop→main 同步 PR 是他们的发版节奏）。
- **权限**：gh 账号有 admin/push，可以直接建分支推 PR。**push 前照例 unset HTTP_PROXY/HTTPS_PROXY**（见 [git-push-proxy-issue](git-push-proxy-issue.md)）。
- **本地克隆**：`<本地代码根目录>/miqro-gate`（2026-10-12 建，用于模板迁移）。
- **协作面现状**：Discussions **关闭**；issue/PR 模板已于 2026-10-12 对齐桌面端（PR #2，**已合并**）——YAML 表单 + 仓根 `action.yml`（issue 校验 + AI 日志复核）+ `.github/workflows/issue-validator.yml`。标签新增 `Triage Needed` / `incomplete` / `ai:logs-suspicious`。issue #1 也已改写成新模板（`[FEATURE]` 前缀）。
- **Postgres 镜像扫描（Trivy）红了先看第二个 target**：`ci.yml` 的 `Trivy scan of the pinned Postgres image` 输出里有**两个 target**——`(alpine 3.22.2)` 和 `usr/local/bin/gosu (gobinary)`。第一个常见 `Total: 0`（全被 `.trivyignore` 豁免），**红的原因通常在第二个**（gosu 内置 Go stdlib 的 CVE）。2026-10-12 实测踩过：我只看了第一个 target 的 `Total: 0`，误判成「0 发现却 exit 1」，怀疑是 `--show-suppressed` 的交互问题——实际是 gobinary 有 3 条未豁免。**教训：读 Trivy 输出要 grep 所有 `Total:` 行，别看渲染出来的最后一张表**（`--show-suppressed` 会把被豁免的表也打出来，最后一张往往是被豁免的）。
- **Postgres 镜像不再钉 digest（2026-10-12，PR #4）**：用户决定改为浮动 `postgres:17-alpine`（明确**不要 `latest`**——PG 不允许新主版本打开旧数据目录，会让客户 pgdata 起不来）。随之删掉 `postgres-image-update.yml`（digest 监视）和 ci.yml 的 `Require digest-pinned images` 策略步骤。**副作用：`compose.prod.yaml` 里的 redpanda 也不再被 CI 强制钉 digest**。16 处引用统一（含 10 个 Testcontainers 文件——「测试跑的镜像=部署的镜像」是有意维护的不变量）。
- **两个豁免文件分叉（截至 2026-10-12 未收口）**：`deploy/security/.trivyignore`（文本格式，44 条）是 ci.yml **实际挂载**的；`.trivyignore.yaml`（2026-09-07 迁移的新格式，30 条，支持 purl 与 `expired_at`）**从未接线到 CI**，且它是按更老的 digest 写的。`docs/security.md` 曾宣称迁移已完成 —— 文档与代码不一致。切到 YAML 前注意里面 7 条 util-linux 临时豁免带 `expired_at: 2026-10-07`，**已硬到期**。
- **前端依赖审计**：Security gate 里原本有两个 npm audit 步骤（`frontend` / `miqro-context`）。frontend 那个已于 2026-10-12 下线（PR #3）——`braces` 上游**没有补丁版本**（公告：影响 `<= 3.0.3`、patched: None，npm 上最新就是 3.0.3），经 `@vue/eslint-config-typescript → fast-glob → micromatch` 引入，换版本躲不掉（该配置最新版仍依赖 fast-glob）。**别再试图用依赖升级修它**，npm 报的 "fix available" 在 `range = *` 时是误报。miqro-context 的 audit 保留（0 漏洞）。另：本仓库前端用 `vite@8`（rolldown 打包器），锁文件里 rollup/esbuild 条目是残留、可安全清理。
- **坑**：① `issues` 事件的工作流取自**默认分支**，改 .github/workflows 后必须等 develop→main 同步才生效，合并前无法端到端验证；② 表单只有合入默认分支后才会在 `issues/new` 渲染，**合并前截不到真实界面截图**；③ AI 日志复核需要 `DEEPSEEK_API_KEY` / `DEEPSEEK_API_BASE` secret，缺省自动跳过；④ 仓库 .gitattributes 有空白门禁（#895），提 PR 前跑 `git diff --cached --check`。
- 他们的文档体系以 `docs/*.md` + ADR 为事实来源；`docs/feature-backlog.md` 是功能登记总表（F 编号）。查某能力是否已实现，先看 backlog 的 F 项状态再翻代码。

**Why**：跨仓库工作时容易把桌面端的规矩（PR base develop、截图强制、issue 前缀 `[FEATURE]`）不假思索套过来；这里记下哪些相同、哪些不同。

**How to apply**：在这个仓库提 PR/issue 时按上面走；需要它的能力时先读 `docs/feature-backlog.md` 与 `docs/api-contract.md`。相关需求见 [desktop-mcp-via-miqrogate](desktop-mcp-via-miqrogate.md)。
