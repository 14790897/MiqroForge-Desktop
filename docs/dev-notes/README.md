# 开发者笔记（Claude Code 会话记忆导出）

本目录是 **Claude Code 会话自动记忆**的导出——开发过程中由 AI 助手沉淀的实战经验、
踩坑记录与架构地图，按主题分类供团队成员查阅。

> ⚠️ **阅读须知**：这些笔记是**某一时间点的观察**，不是活文档。代码行为、
> 文件行号引用可能已过时，引用前请对照当前代码验证。每篇笔记记录了落笔日期。

## 内容分组

### 工作流（PR / Issue / Git）

- [pr-base-is-develop](pr-base-is-develop.md) — PR 一律指向 develop；冲突用 merge 不用 rebase
- [develop-to-main-sync-pr](develop-to-main-sync-pr.md) — develop→main 同步 PR 的标准格式与 main 被误删事故的教训
- [invoke-github-workflow-skill](invoke-github-workflow-skill.md) — PR/Issue/截图上传等触发词必须先调 github-workflow 技能
- [check-competing-pr-before-issue](check-competing-pr-before-issue.md) — 开工 issue 前先查并发 PR（#810 撞车教训）
- [git-push-proxy-issue](git-push-proxy-issue.md) — HTTP_PROXY/HTTPS_PROXY 会阻塞 git push
- [miqroera-github-mirror-sync](miqroera-github-mirror-sync.md) — origin 双 push URL 镜像同步机制与「推新分支污染 develop」事故
- [github-release-immutability-tombstone](github-release-immutability-tombstone.md) — Release immutability 墓碑坑（tag 名永久封锁）
- [repo-renamed-miqroforge-desktop](repo-renamed-miqroforge-desktop.md) — 仓库更名为 MiqroForge-Desktop
- [miqroforge-desktop-rename](miqroforge-desktop-rename.md) — 命名规范：MiQroForge（大写 Q、大写 F），保留项清单
- [autonomous-action](autonomous-action.md) — 与 AI 协作偏好：少问、自主决策

### E2E 测试

- [macos-ci-local-mock-server](macos-ci-local-mock-server.md) — macOS runner 本地 mock 不可达，需 skip
- [e2e-exec-slow-spawn-timeout](e2e-exec-slow-spawn-timeout.md) — Electron 桥接下 exec spawn 25-30s，超时与同步点策略
- [e2e-localstorage-shared-userdata](e2e-localstorage-shared-userdata.md) — --user-data-dir 被 setPath 覆盖，localStorage 跨运行共享
- [e2e-inject-chat-events](e2e-inject-chat-events.md) — webContents.send('chat:progress') 注入事件测渲染契约
- [local-sandbox-e2e-unavailable](local-sandbox-e2e-unavailable.md) — E2E 沙箱不初始化是 config 问题，patch enabled:true 即可
- [smoke-privacy-gate-and-cwd-sensitive-tests](smoke-privacy-gate-and-cwd-sensitive-tests.md) — smoke 前置条件与 cwd 敏感测试
- [session-scoped-bridge-reads-null](session-scoped-bridge-reads-null.md) — 桥下文件读取必须用全路径且不带 session key
- [use-luma-mcp-for-images](use-luma-mcp-for-images.md) — 读图用 luma-mcp 工具，Read 工具读图返回 Unsupported

### 架构 / 运行时

- [confirm-card-two-runtime-map](confirm-card-two-runtime-map.md) — 确认卡双路径地图；KUN runtime 实际未启用
- [legacy-main-path-only](legacy-main-path-only.md) — 新功能只做 legacy 主路径（KUN runtime 未接入主执行路径）
- [confirm-card-issue-714-fix](confirm-card-issue-714-fix.md) — #714 确认卡排队串行方案要点与踩坑
- [mcp-desktop-wiring](mcp-desktop-wiring.md) — MCP 桌面主路径接线 + anyio cancel-scope 坑
- [pr-613-skill-discovery](pr-613-skill-discovery.md) — 技能发现：一条全局规则胜过触发词机制

### 计费 / 平台集成

- [slurm-mcp-billing-map](slurm-mcp-billing-map.md) — Slurm MCP 作业计费架构地图（#927/#936）
- [qraft-refresh-rotation-and-invalidation](qraft-refresh-rotation-and-invalidation.md) — 平台 refresh_token 轮换升级与客户端处理
- [qraft-netfetch-manual-redirect](qraft-netfetch-manual-redirect.md) — net.fetch manual 重定向 302 被 reject 的修复
- [pr929-review-findings](pr929-review-findings.md) — #929 模型收口 max-effort 审查 15 条 finding（待修复）

### 构建 / 环境

- [worktree-node-modules-junction](worktree-node-modules-junction.md) — worktree 前端测试前 junction node_modules
- [buildresources-license-files-dmg-gotcha](buildresources-license-files-dmg-gotcha.md) — license_*.txt 被 NSIS/DMG 双拾取，中文致 DMG 打包失败
- [wfp-blocks-ipv6-loopback](wfp-blocks-ipv6-loopback.md) — 网络过滤组件拦截回环的两层修复（#895/#898）
- [loguru-stream-sink-encoding](loguru-stream-sink-encoding.md) — loguru stream sink 编码修法
- [dirty-api-key-httpx-crash](dirty-api-key-httpx-crash.md) — 脏 API Key（中文备注）导致 httpx 崩溃的排查
- [heredoc-escaping-pitfall](heredoc-escaping-pitfall.md) — heredoc 转义坑，文件补丁用 Edit/Write 工具

## 说明

- **来源**：Claude Code 会话自动记忆（`~/.claude/projects/<项目>/memory/`），导出时对集群 SSH 凭据、
  API Key 等敏感信息做了脱敏；测试账号（18500000000 / 1q2w3e4R）是公开测试凭据，原样保留。
- **未导出**：含真实密钥的交接笔记、已过期无价值的分支指针记录。
- **维护**：本目录为快照导出。新增经验建议直接贡献到对应主题的正式文档
  （[architecture](../architecture.md)、[contributing](../contributing.md) 等），
  而不是追加到这里。
