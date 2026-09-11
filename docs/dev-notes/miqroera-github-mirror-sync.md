---
name: miqroera-github-mirror-sync
description: origin remote 有双 push URL（内部 git 服务器 + github.com），推到内部服务器后分支会自动同步到 GitHub，可直接用 gh 建 PR
type: project
---

MiQroForge-Desktop 的 origin remote 配了两个 push URL：内部 git 服务器（`http://git.miqroera.com/intership/miqi-desktop`）和 `https://github.com/14790897/MiQroForge-Desktop.git`。推送到其中任意一个后，分支会**自动同步到 GitHub**（实测推完 1 分钟内 `gh api repos/14790897/MiqroForge-Desktop/branches/...` 就能看到，且提交 SHA 一致）。所以 push 后无需分别推两个远端，直接 `gh pr create` 即可。**How to apply:** push 到 origin 任意一个 push URL；建 PR 前可先用 `gh api repos/14790897/MiqroForge-Desktop/commits/<sha>` 验证 GitHub 已同步。push 记得 unset HTTP_PROXY/HTTPS_PROXY（见 [git-push-proxy-issue](git-push-proxy-issue.md)）。

**amend 后强推（2026-08-26 实测）**：`git push --force-with-lease origin` 会报 "stale info" 拒绝——因为 origin 双 pushurl 下 lease 只对第一个 URL 生效，第二个（内部服务器）落后时整体失败；且 force-with-lease 对内部服务器的 lease 永远陈旧。**How to apply:** amend 后改推 `git push --force <单个远端> claude/<branch>`（单 URL 强推，旧提交是自己的工作所以安全），另一侧等镜像同步或单独补齐。

**推新分支污染 develop 事故（2026-09-03 实测）**：首次 push 一个新分支后，内部服务器和 GitHub 两侧的 `develop` 都被同步成了新分支 tip（服务器端镜像机制 bug，push 输出正常只列新分支）。恢复方法：`git push origin <原develop-sha>:develop --force`（内部服务器侧强制指针回退），镜像同步约 1 分钟内自动把 GitHub develop 也修回。**How to apply:** 每次 push 后立即 `git ls-remote origin develop` 核对 SHA 未变；被污染时先恢复 develop 再建 PR（否则 gh pr create 报 "No commits between develop and ..."）。增量 push 到已有分支不会触发该问题（实测安全）。
