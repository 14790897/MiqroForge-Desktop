---
name: miqroera-github-mirror-sync
description: origin 是双 pushurl（miqroera + GitHub）顺序推两份，GitHub 腿可能静默失败；含 .github/workflows 改动时 GitHub 腿需走 SSH（gh token 缺 workflow scope）
type: project
---


MiQi-Desktop 的 origin remote 配了两个 push URL：`http://git.miqroera.com/intership/miqi-desktop` 和 `https://github.com/14790897/MiQi.git`。推送到 miqroera（或 github-14790897 remote）后，分支会**自动同步到 GitHub**（实测推完 1 分钟内 `gh api repos/14790897/MiQi/branches/...` 就能看到，且提交 SHA 一致）。所以 push 后无需分别推两个远端，直接 `gh pr create --repo 14790897/MiQi` 即可。**How to apply:** push 到 origin 或 github-14790897 任意一个；建 PR 前可先用 `gh api repos/14790897/MiQi/commits/<sha>` 验证 GitHub 已同步。push 记得 unset HTTP_PROXY/HTTPS_PROXY（见 [git-push-proxy-issue](git-push-proxy-issue.md)）。

**amend 后强推（2026-08-26 实测）**：`git push --force-with-lease origin` 会报 "stale info" 拒绝——因为 origin 双 pushurl 下 lease 只对第一个 URL（GitHub）生效，第二个（miqroera）落后时整体失败；且 force-with-lease 对 miqroera 的 lease 永远陈旧。**How to apply:** amend 后改推 `git push --force miqroera claude/<branch>`（单 URL 强推，旧提交是自己的工作所以安全），GitHub 侧用 `git push --force github-14790897 <branch>` 补齐（或等镜像同步）。

**推新分支污染 develop 事故（2026-09-03 实测）**：首次 push 一个新分支后，miqroera 和 GitHub 两侧的 `develop` 都被同步成了新分支 tip（服务器端镜像机制 bug，push 输出正常只列新分支）。恢复方法：`git push origin <原develop-sha>:develop --force`（miqroera 侧强制指针回退），镜像同步约 1 分钟内自动把 GitHub develop 也修回。**How to apply:** 每次 push 后立即 `git ls-remote origin develop` 核对 SHA 未变；被污染时先恢复 develop 再建 PR（否则 gh pr create 报 "No commits between develop and ..."）。增量 push 到已有分支不会触发该问题（实测安全）。

**⚠️ 实例（2026-09-17，#1119）**：一条含 `desktop-ci.yml` 改动的提交经 `git push origin` 推了两条腿——miqroera 收到、GitHub 因 workflow scope 被拒；当时没核对，PR head 停在旧提交，squash 合并后该改动**静默丢失**（develop 少了 temperature 修复，两天后才发现）。**How to apply:** 含 workflow 改动的分支一律 SSH 推 GitHub + `ls-remote` 两条腿核对；不含 workflow 改动的分支也要在 push 后核对 GitHub 腿（同样的静默失败路径）。

**⚠️ GitHub 腿静默失败 + workflow scope（2026-09-14 实测，更正上文"自动同步"说法）**：origin 双 pushurl 是 git 顺序推两个 URL，不是服务器镜像；**GitHub 腿失败时 miqroera 腿仍成功**，命令输出里 GitHub 段错误若被 `tail` 截掉就会误以为推成功（表现为"分支在 GitHub 上死活查不到"）。特例：**分支含 `.github/workflows/*` 改动时，gh 的 OAuth token 缺 `workflow` scope，GitHub 腿必被拒**（报 "refusing to allow an OAuth App to create or update workflow ... without `workflow` scope"，miqroera 腿照常成功）。**How to apply:** 改 CI 工作流的分支，GitHub 侧改用 SSH 推：`git push git@github.com:14790897/MiqroForge-Desktop.git HEAD:refs/heads/<branch>`（本机 SSH key 已可用，实测通过；token 加 workflow scope 需 `gh auth refresh -s workflow` 交互授权）。推完 `git ls-remote origin refs/heads/<branch>`（fetch 端=GitHub）核对真的上去了。
