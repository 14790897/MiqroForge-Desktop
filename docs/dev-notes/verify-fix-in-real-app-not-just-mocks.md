---
name: verify-fix-in-real-app-not-just-mocks
description: mock smoke 通过 ≠ 修复在真实路径生效——provider/桥相关改动要在真实应用里跑一遍，别用「commit 已覆盖」回话
type: feedback
---


用户指出「问题在这个路径」时，**必须在真实应用里跑到那个路径实测**，不能用「对应 commit 已覆盖/用例已断言」答复。

**Why:** 2026-09-23 修 PR #1182 时，用户对「设置 → 通用 → 默认模型」提了 CHANGES_REQUESTED；我看到 PR 里确有一版 smoke 用例改成先断言「通用」选项卡、且本地通过，就回复「该要求已由 commit b1b51800 落实，只是评审状态没更新」。用户回「修复我要求的」。实测后发现**真实应用里根本没修好**：进入通用页后立即读取下拉，拿到 34 项全量目录（含未配置的 groq/moonshot/zhipu），约 3s 才收敛。原因是 `models.list` 常先于 `providers.list` 返回，此窗口内 `availableProviders` 仍是 null，`filterAvailableModels` 原样放行整份目录。smoke 用例的 mock 里 `providers.list` 立即返回，竞态被完全掩盖——**mock 通过 ≠ 真实路径修好**。

**How to apply:**
1. 涉及 provider/桥/网络时序的 UI 修复，写完 mock 用例后**再跑一次真实应用**（`launchElectronApp` + 真实桥，必要时用真实账号 `browserLogin`），并在「刚进入页面就读取」的时机采样——竞态只在第一时间暴露。
2. 采样要连续多次（`for` 循环连读），单次读取可能恰好落在稳定态。
3. 用户评审否决你的判断时，先假设用户是对的，去实测取证，别急着解释。
4. 反向对照（旧代码复现失败 / 新代码通过）是证明因果最快的办法，见 [stale-pr-branch-spec-mismatch](stale-pr-branch-spec-mismatch.md)。

相关：[local-e2e-before-cloud-verification](local-e2e-before-cloud-verification.md)、[auto-handle-pr-reviews-and-ci](auto-handle-pr-reviews-and-ci.md)
