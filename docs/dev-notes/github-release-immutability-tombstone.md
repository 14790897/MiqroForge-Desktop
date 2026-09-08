---
name: github-release-immutability-tombstone
description: GitHub release immutability 开启后 v0.23.0 tag 被墓碑封锁、资产上传失败的完整事故链与解法
type: project
---

2026-09-01 事故链：GitHub "Enable release immutability" 功能曾在本仓库开启。

**症状：**
1. 开启时：semantic-release 发布的 release 不可变 → 构建 job 上传资产报 `Cannot upload asset to an immutable release`（构建成功但白跑，两次）
2. 关闭后：被删除过的 release tag 名（v0.23.0）留**永久墓碑** → `git push --tags` 报 `GH013: Repository rule violations ... Cannot create ref due to creations being restricted`，仅该 tag 名被锁，其他 tag（含 v0.24.0、随机名）正常
3. 该设置无公开 API 可查（REST/GraphQL 均无字段），只能 UI 确认

**解法（已验证）：**
- 关闭 Settings → General → Features → "Enable release immutability"
- 被墓碑的版本号**不可恢复**：用 `gh release create` 手动补发下一 patch（如 v0.23.1，tag 指向 main HEAD，notes 从 CHANGELOG 取）
- 之后 semantic-release 从新 tag 起算，正常发布下一个版本（v0.24.0）且资产上传恢复正常

**Why:** 防重打 tag 攻击的设计；墓碑清除只能走 GitHub 支持。

**How to apply:** 发版 CI 报 EMISMATCHGITHUBURL → 查 [repo-renamed-miqroforge-desktop](repo-renamed-miqroforge-desktop.md)；报 immutable release → 先让用户关 immutability，再确认该版本 tag 是否墓碑（测试同名 tag push），墓碑则手动补发 patch 版本跳过。相关流程见 [develop-to-main-sync-pr](develop-to-main-sync-pr.md)。
