---
name: github-workflow
description: |
  GitHub 协作全流程：Issue 模板（Feature/Bug）、Issue CI 校验规则、
  Conventional Commits 规范、PR 描述模板（必填节 + CI 校验）、PR 标题规范、
  GitHub CLI 操作、CodeRabbit 评审处理、CI Polling。适用于 MiQi 项目。
  Triggers: "提PR", "create PR", "创建PR", "pr template", "PR模板",
  "commit规范", "conventional commit", "CodeRabbit", "coderabbit",
  "PR review", "pr checks", "dismiss review", "开issue", "create issue",
  "创建issue", "bug report", "feature request", "issue模板",
  "issue template", "提交bug", "提交feature", "issue ci", "issue校验",
  "贴图到PR", "上传截图", "PR贴图", "e2e截图", "截图评论".
agent_created: true
---

# GitHub Workflow

## PR 沟通原则：少评论，直接更新 PR 描述

> **默认行为：能更新 PR 描述解决的，绝不多发评论。**

- 测试结果、截图、修复说明、进展等证据 → 直接写进 PR 描述（`gh pr edit <N> --body "$(cat body.md)"`），不逐条发评论
- 图片证据 → 上传后把 `![](url)` 写进 PR 描述的「日志/验证证据」节（见下方 E2E 截图章节）
- 只有 GitHub 机制要求时才评论：CodeRabbit 触发 re-review（`@coderabbitai review`）、回复 reviewer 的具体评论、dismiss
- 同步 PR（develop↔main）同理：进展与证据更新 PR 描述，不发通知性评论

## Issue 模板

### Feature 请求 (feature_request.yml)

标题前缀 `[FEATURE]`，labels: `enhancement`

| 字段 | 必填 | 说明 |
|------|------|------|
| 功能概述 | ✅ | 一句话 |
| 需求背景 | ✅ | 为什么需要？解决什么痛点？ |
| 功能描述 | ✅ | 详细功能行为、交互方式 |
| 备选方案 | ❌ | 替代实现思路 |
| 优先级 | ✅ | P0/P1/P2/P3 下拉选择 |
| 参考资料 | ❌ | 竞品截图、设计稿链接 |

### Bug 报告 (bug_report.yml)

标题前缀 `[BUG]`，labels: `bug`

| 字段 | 必填 | 说明 |
|------|------|------|
| Bug 描述 | ✅ | 一句话 |
| 环境 | ✅ | 运行 diagnose 脚本粘贴输出 |
| 复现步骤 | ✅ | 从零开始的精确步骤 |
| 复现频率 | ✅ | 必现/偶现/条件必现 |
| 期望行为 | ✅ | 正确的应该怎样 |
| 实际行为 | ✅ | 实际发生了什么 |
| 日志 | ✅ | **必须提供，不接受空白** |
| 截图/录屏 | ❌ | 可视化证据 |
| 补充信息 | ❌ | 排查过什么、相关 commit/PR |

诊断脚本：
```bash
# PowerShell
irm https://raw.githubusercontent.com/14790897/MiQi/main/scripts/diagnose.ps1 | iex

# WSL / Git Bash / macOS / Linux
curl -sSL https://raw.githubusercontent.com/14790897/MiQi/main/scripts/diagnose.sh | bash
```

### Issue 模板配置

- `blank_issues_enabled: false` — 必须选择模板，不允许空白 Issue
- 非 Bug/Feature 问题引导到 Discussions 或文档

## Issue CI 校验规则 (issue-validator.yml / action.yml)

CI 自动校验 Issue 模板完整性，**格式不匹配会导致 `incomplete` 标签和评论**。

### 关键约束

| 约束 | 规则 | 易踩坑 |
|------|------|--------|
| Feature 跳过校验 | 标题必须以 `[FEATURE]` 开头 | 用 `feat(xxx): ` 会被当 Bug 校验 |
| Bug 必填 7 项 | Bug描述/环境/复现步骤/复现频率/期望行为/实际行为/日志 | 缺任何一个都失败 |
| 节标题格式 | `## 标题` 或 `### 标题` 均可，CI 用 `#{2,3}` 匹配 | 无限制 |
| Bug描述 ≥10 字 | `minDesc` 默认 10 | |
| 环境 ≥30 字 + ≥3 系统关键词 | OS/Python/CPU/WSL/bwrap 等 | 必须运行 diagnose 脚本粘贴 |
| 复现步骤 ≥10 字 | `minSteps` 默认 10 | |
| 复现频率 ≥1 字 | `minFreq` 默认 1（不限字数） | |
| 期望/实际行为 ≥10 字 | `minDesc` 默认 10 | |
| 日志 ≥20 字 | `minLogs` 默认 20 | 不能为占位文本 |

### 正确格式示例

```markdown
### Bug 描述
简短描述 bug

### 环境
Windows + WSL2, MiQi Desktop develop 分支

### 复现步骤
1. 步骤一
2. 步骤二

### 复现频率
必现

### 期望行为
正确的行为描述

### 实际行为
实际发生的错误行为

### 日志
相关日志输出或代码分析
```

### 创建 Issue 时的注意事项

- `gh issue create` 不会走 YAML 表单 → **必须手动按模板格式写 body**
- Feature issue：标题必须 `[FEATURE]` 开头
- Bug issue：body 必须用 `### ` 节标题，不能用 `## `
- 环境字段应包含系统关键词让 CI 通过（OS、Python、Node、WSL 等）

## Commit Convention

项目使用 [Conventional Commits](https://www.conventionalcommits.org/)，PR 标题必须以语义前缀开头：

```
允许的前缀: feat, fix, perf, refactor, docs, style, test, build, ci, chore, revert
格式: <type>(<scope>): <subject>
常用 scope: agent, sandbox, desktop, bridge, wsl, ci
```

PR title check (CI on `main` branch): 不合规标题会被拒绝。

## PR description template

必须严格使用 `.github/pull_request_template.md` 模板，**所有节必填**，不允许空白节。CI 会校验。

```markdown
## 类型

<!-- 必选，勾一个 -->
- [ ] 🐛 Bug 修复
- [ ] ✨ 新功能
- [ ] 📝 文档
- [ ] ♻️ 重构
- [ ] 🧪 测试
- [ ] 🔧 其他

## 变更概述

<!-- 必填，一句话 -->

## 背景/问题

<!-- 必填。Bug：现象、根因、影响。Feature：需求背景、痛点 -->

## 变更内容

<!-- 必填。列出关键改动文件和原因 -->

## 日志/验证证据

<!-- 必填，不允许空白。Bug 必贴修复前后日志对比。Feature 必贴功能测试截图或终端输出 -->
```
（粘贴日志/截图）
```

## 测试情况

<!-- 必填。跑过哪些测试？结果？ -->
```

### PR CI 校验规则 (pr-template-check.yml)

以下节**空白**会导致 CI 失败：

| 检查项 | 规则 |
|--------|------|
| 类型 | 至少勾选一项 `[x]` |
| 变更概述 | 非空文本 |
| 背景/问题 | 非空文本 |
| 变更内容 | 非空文本 |
| 日志/验证证据 | 代码块内必须有内容（不能是占位文本） |
| 测试情况 | 非空文本 |
| 截图 | Feature 或界面相关 PR **必须**包含图片 |

### PR 标题规范 (pr-title-check.yml)

仅 `main` 分支 PR 校验：
```
feat(sandbox): add auto-install support    ✅
add auto-install support                   ❌ 缺少语义前缀
```

## develop → main 同步 PR

> **硬性要求：只能用远程引用，不能依赖本地分支状态。**

### 标准流程

```bash
# 1. 获取远程最新（必须）
git fetch origin develop main

# 2. 列出待同步提交
git log --oneline origin/main..origin/develop --no-merges

# 3. 创建 PR（标题固定格式）
COMMITS=$(git log --oneline origin/main..origin/develop --no-merges)
COUNT=$(echo "$COMMITS" | wc -l | tr -d ' ')

gh pr create --repo 14790897/MiQi --base main --head develop \
  --title "chore(release): merge develop into main" \
  --body "$(cat <<EOF
## 类型

- [ ] 🔧 其他

## 变更概述

合并 develop 到 main，准备下一次发布。

## 背景/问题

develop 分支包含 $COUNT 个尚未合入 main 的提交，需要定期同步到 main 以发布新版本。

## 变更内容

本次 develop → main 合并包含以下提交：

$(echo "$COMMITS" | while read hash msg; do echo "- \`$hash\` $msg"; done)

## 日志/验证证据

\`\`\`
\$ git log --oneline origin/main..origin/develop --no-merges
$COMMITS
\`\`\`

## 测试情况

- 所有 $COUNT 个提交已在 develop 通过各自 PR 评审并合并
- 自动化测试在各自原 PR 中已通过
- 本次为 release 合并，CI 将由 PR template check + title check 触发
EOF
)"
```

### 常见错误

| 错误做法 | 正确做法 |
|----------|----------|
| 用 `git log main...develop`（本地 main 过期） | 用 `git log origin/main..origin/develop` |
| 标题写"定期同步" → CI title check 失败 ❌ | 标题写 `chore(release): merge develop into main` |
| body 凭记忆写提交列表 | 用 `origin/main..origin/develop` 输出粘贴 |

### CHANGELOG 冲突处理（发版同步 PR 常见）

两侧各有同一版本条目（日期不同）→ 保留与 release tag 一致的日期，删除另一侧。

## main → develop 反向同步（必须用临时分支！）

> **硬性要求：head 分支绝不能用 `main`。**
> 仓库开启 `delete_branch_on_merge: true`（合并后自动删除 head 分支），
> 用 main 作 head 合并后 main 会被自动删除（#651、#813 两次事故，均需重建恢复）。
> **已确认方案（2026-08-25）：head 一律用临时分支 `chore/sync-main-into-develop`。**

```bash
# 1. 获取远程最新
git fetch origin develop main

# 2. 创建临时分支指向 main 并推送
git branch chore/sync-main-into-develop origin/main
git push origin chore/sync-main-into-develop

# 3. 创建 PR：head=临时分支，base=develop
COMMITS=$(git log --oneline origin/develop..origin/main --no-merges)
COUNT=$(echo "$COMMITS" | wc -l | tr -d ' ')

gh pr create --repo 14790897/MiQi --base develop --head chore/sync-main-into-develop \
  --title "chore(release): merge main into develop" \
  --body "$(cat <<EOF
## 类型

- [ ] 🔧 其他

## 变更概述

将 main 上的 release 版本标记同步回 develop，保持版本信息一致。

## 背景/问题

main 分支包含 $COUNT 个 develop 缺失的 release 提交（CHANGELOG 与版本号更新），需要同步回 develop 避免后续发版时版本回退。

## 变更内容

本次 main → develop 合并包含以下提交：

$(echo "$COMMITS" | while read hash msg; do echo "- \`$hash\` $msg"; done)

## 日志/验证证据

\`\`\`
\$ git log --oneline origin/develop..origin/main --no-merges
$COMMITS
\`\`\`

## 测试情况

- 提交均为 semantic-release 自动生成的版本标记（CHANGELOG + package.json + pyproject.toml）
- 无代码逻辑变更，不影响功能
EOF
)"
```

### 若 main 被误删，重建方法

```bash
# 1. 查 PR 的 headRefOid（= 合并前 main 位置）
gh pr view <N> --repo 14790897/MiQi --json headRefOid

# 2. 重建 main 分支
gh api repos/14790897/MiQi/git/refs -f ref=refs/heads/main -f sha=<headRefOid>
```

## GitHub CLI quick reference

```bash
# Create issue
gh issue create --repo 14790897/MiQi --title "[FEATURE] xxx" --body "..." --label enhancement

# Create bug issue
gh issue create --repo 14790897/MiQi --title "[BUG] xxx" --body "..." --label bug

# Create PR (标题必须符合 conventional commits)
gh pr create --repo 14790897/MiQi --base develop --head feat/xxx --title "feat(xxx): description" --body "$(cat body.md)"

# View CI checks
gh pr checks <N>

# Edit PR body
gh pr edit <N> --body "$(cat body.md)"
```

## 处理 CodeRabbit 评审意见

评审以 `CHANGES_REQUESTED` 状态出现，须全部处理才能 merge。

### 查看评审

```bash
# 查看是否有人请求了 changes
gh api repos/{owner}/{repo}/pulls/{N}/reviews --jq '.[] | select(.state=="CHANGES_REQUESTED") | "\(.id) by \(.user.login)"'

# 查看所有 CodeRabbit 评论内容
gh api repos/{owner}/{repo}/pulls/{N}/comments --jq '.[] | select(.user.login | contains("coderabbit")) | "=== id: \(.id) line: \(.line) ===\n\(.body)"'
```

### 修复后提交

```bash
git add -A && git commit -m "fix(scope): address CodeRabbit review comments

1. Brief description of fix 1
2. Brief description of fix 2" && git push
```

### 驳回旧评审 (dismiss)

修复提交后用 dismiss API 把旧的 `CHANGES_REQUESTED` 标为已处理：

```bash
# 逐个 dismiss
gh api repos/{owner}/{repo}/pulls/{N}/reviews/{review_id}/dismissals \
  -X PUT -f message="All comments addressed" -f event="DISMISS"
```

### 常见评审类型与处理

| 级别 | 标签 | 典型意见 | 处理 |
|------|------|----------|------|
| 🔴 Major | Stability/Availability | 缺 timeout、缺缓存、hang 风险 | 必须修 |
| 🟡 Minor | Correctness/Security | 错误日志、凭证泄露、perf | 优先修 |
| 🔵 Trivial | Maintainability | 重复代码、命名、文档 | 建议修 |

### CodeRabbit 常见坑

- 新 commit 会触发 re-review，但旧 comments 不会自动 dismiss（需手动 API）
- `CHANGES_REQUESTED` 阻止 merge，必须 dismiss 或 CodeRabbit 重新 approve
- nitpick 级别注释不阻止 merge，但建议修复
- 修复进展/说明直接写进 PR 描述，不逐条回复评论（少评论原则）
- `@coderabbitai review` PR 评论可手动触发 re-review

## CI Polling

```bash
# One-shot all PRs
for pr in PR_LIST; do
  echo "=== PR $pr ==="
  gh pr checks $pr --repo OWNER/REPO | grep -E "pass|fail|pend"
done

# Continuous polling
while true; do
  clear; echo "=== $(date +%H:%M:%S) ==="
  for pr in PR_LIST; do
    gh pr checks $pr --repo OWNER/REPO | grep -cE "fail" | xargs echo "fail="
  done
  sleep 60
done

# Rerun failed job
gh run rerun <run-id> --repo OWNER/REPO --failed

# Debug CI
gh run view <run-id> --repo OWNER/REPO --log --job=<job-id> | grep "Error:"
```

## E2E 截图自动贴到 PR 描述

> 完整文档见仓库 `docs/e2e-pr-image-posting.md`。此处为 AI 可主动执行的摘要。
> **少评论原则：证据一律写进 PR 描述，不贴图片评论**（CI 自动贴图机制仍为评论，AI 手动操作以本节为准）。

### 何时使用

- 跑过 e2e 且想留证据到 PR → 设 `MIQI_E2E_POST_IMG=1` 跑测试，自动完成
- 手动上传一张图 → 上传后写进 PR 描述（下方命令），不贴评论
- CI 默认开启；无 PR 上下文时静默跳过

### 机制（一句话）

图片上传到本仓库固定预发布 `_gh-imgup`（release assets 官方 API，复用创建 + 内容 hash 去重），再以 `![img](url)` **写进当前 PR 描述的「日志/验证证据」节**。**必须 published 预发布，draft 资产对外 404 会裂图；勿用 user-attachments（无公开 API，有 TOS 风险）。**

### 手动上传一张图并写进 PR 描述

```bash
REPO=14790897/MiQi
# 1) 复用或创建固定预发布
RID=$(gh api "repos/$REPO/releases/tags/_gh-imgup" --jq '.id' 2>/dev/null || echo "")
if [ -z "$RID" ]; then
  RID=$(gh api "repos/$REPO/releases" -X POST \
    -F tag_name='_gh-imgup' -F name='_gh-imgup' -F prerelease=true \
    -f body='Image assets - do not delete' --jq '.id')
fi
# 2) 上传（防碰撞文件名）
HASH=$(sha256sum image.png | cut -c1-8)
UPLOAD_URL=$(gh api "repos/$REPO/releases/$RID" --jq '.upload_url' | sed 's/{?name,label}//')
DL=$(curl -s -X POST "$UPLOAD_URL?name=image-$HASH.png" \
  -H "Authorization: token $(gh auth token)" -H "Content-Type: image/png" \
  --data-binary @image.png | python -c "import json,sys;print(json.load(sys.stdin)['browser_download_url'])")
# 3) 写进 PR 描述（少评论原则，不贴评论）
CUR_BODY=$(gh api "repos/$REPO/pulls/$PR_NUMBER" --jq '.body')
gh api "repos/$REPO/pulls/$PR_NUMBER" -X PATCH -f body="$CUR_BODY

![img]($DL)"
```

### 注意

- 预发布 `_gh-imgup` 勿删（历史图片引用会全裂）
- 上传失败（无 token / 无 PR / 图不存在）静默跳过，不干扰测试

## E2E 截图自动贴 PR 描述

e2e 验收跑完后把截图上传到仓库固定 `_gh-imgup` 预发布（release assets
官方 API），并以 markdown 图片写进当前 PR 描述（少评论原则，不贴评论）。

**何时调用**：用户要求「贴图到 PR / 上传截图 / e2e 截图」，或需要把
测试证据/图片展示到 PR 时（写进描述，不发评论）。

**快速使用**：

```bash
# 跑 e2e 并自动贴图（CI 默认开启，本地需显式设置）
MIQI_E2E_POST_IMG=1 npx playwright test --config=playwright.config.ts \
  --project=electron <spec>.ts

# 手动上传一张图（无 shell 依赖的完整实现见 reference）
# 用 release assets API：复用/创建 _gh-imgup 预发布 → 上传 → 取
# browser_download_url → 以 ![](url) 写进 PR 描述（gh pr edit）
```

- 完整机制、环境变量、代码接入模式、清理注意：见
  [references/e2e-pr-image-posting.md](references/e2e-pr-image-posting.md)
- 关键约定：用 **published 预发布**（draft 资产的下载 URL 对匿名 404 会裂图）；
  固定 tag 复用（Releases 列表只多一条）；文件名内容 hash 去重
- 预发布 `_gh-imgup` **不要删除**（历史图片引用会全部裂掉）
