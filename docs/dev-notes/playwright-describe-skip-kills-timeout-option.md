---
name: playwright-describe-skip-kills-timeout-option
description: "describe 层的 test.skip() 会让单测级 { timeout } 选项失效（info.timeout 仍是项目默认 600s）——长用例必须 test.setTimeout() 兜底"
type: project
---


Playwright（本仓库 electron 项目，`timeout: 600_000` 在 playwright.config.ts:39）：在 `test.describe()` 回调里调用 `test.skip(cond, reason)`（用于 CI/缺依赖环境整组跳过）之后，**该 describe 内 test 的第二个参数 `{ timeout: ... }` 不再生效**——实测 `test.info().timeout` 仍报 600000。长用例（真实 skill 跑 8-10 分钟以上）会报 "Test timeout of 600000ms exceeded"。

**Why:** 2026-09-16 录屏 spec（record-bvse-skill）两次「应用中途退出」实为 10 分钟项目超时把 worker 连同 Electron 一起杀；`[app-stderr] Bridge stopped — request cancelled` + `page.evaluate: Target page... closed` 是超时的表现，不是应用崩溃。

**How to apply:** 需要 >10 分钟的 electron 用例：保留 describe 层 `test.skip`（省掉 CI 里的启动开销）+ 在 test 正文首行加 `test.setTimeout(<ms>)`。排查「应用莫名关闭」先看日志里有没有 "Test timeout of ... exceeded"。相关：[e2e-direct-screen-recording](e2e-direct-screen-recording.md)、[e2e-local-run-bvse-skill](e2e-local-run-bvse-skill.md)
