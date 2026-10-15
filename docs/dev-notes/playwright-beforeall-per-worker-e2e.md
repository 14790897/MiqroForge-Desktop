---
name: playwright-beforeall-per-worker-e2e
description: Electron E2E 的 beforeAll 每 worker 一次，worker 重启会重新 launchElectronApp（全新 MIQI_HOME），依赖上一条用例会话的 spec 会挂
type: project
---


Electron E2E 里 `test.beforeAll` **每个 worker 跑一次**，不是每个测试文件一次。
worker 中途重启（前一条用例失败，或进程退出/OOM）时，剩余用例被重新排到新
worker，beforeAll 重跑 `launchElectronApp` —— 新实例的 `MIQI_HOME` 是全新的，
前一条用例建的会话一条都不在。

**症状**：spec 里第 2 条用例断言「有会话但搜不到」的空态文案，实际拿到的是
`sessions.length === 0` 的另一个分支（侧栏「暂无任务」），断言找不到元素；
而重复三次尝试都在同一行失败（不是 flaky，是确定性走错分支）。

**Why**：spec 默认「上一条用例已经把会话建好了」。CI 上 PR #1151 就是这样挂的
（`sidebar-search-back-entry.spec.ts` 第 157 行），本地复现时看到每条用例各拿
一个 MIQI_HOME、文件被加载两次，才定位到是 worker 重启而非并发。

**How to apply**：多用例 spec 里，需要会话/文件的用例自己幂等补齐
（`seedSessions` 改成「按目标条数补齐，已有直接复用」），不要依赖前面用例的
残留状态。别在 `test.beforeAll` 里建数据然后假设每条用例都能看到。

相关：[e2e-localstorage-shared-userdata](e2e-localstorage-shared-userdata.md) 讲的是跨 run 共享 profile 的另一面；
这条是同一份 app 实例在 run 内被重建。
