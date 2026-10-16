---
name: e2e-ci-needs-miqi-config
description: E2E/CI 跑真实应用前必须存在 ~/.miqi/config.json，否则应用停在首启动向导、chat-input 永不挂载（2026-09-22 云端登录流水线首跑 5/5 失败根因）
type: project
---


应用主界面挂载以 `python.check().config_exists`（即 `~/.miqi/config.json` 是否存在）为准（`App.tsx` 的 `needsSetup` 判定）；不存在时停在「欢迎使用 MiQroForge Desktop … 使用默认配置，进入应用」首启动向导，`[data-testid="chat-input-container"]` 永不出现。

**Why:** 2026-09-22 云端登录 live 流水线（[qraft-test-account-credentials](qraft-test-account-credentials.md)）首次 CI 运行 5/5 全挂：症状只有 `waitForInputReady` 60s 超时 + `App UI may still be loading`，渲染进程无任何报错——根因是该 workflow 没有像 desktop-ci.yml 的 electron-e2e 那样生成 `~/.miqi/config.json`（E2E helper 只把用户 home 的配置复制进 MIQI_HOME，CI runner 的 home 里没有）。

**How to apply:**
- 2026-09-22 起 E2E helper 已自愈：`waitForInputReady` 检测到向导会点「使用默认配置，进入应用」带入主界面，卡住时抛明确错误（不再是泛指超时）——本机新环境无需手动建配置
- CI job 仍建议显式 `mkdir -p ~/.miqi && printf '{}' > ~/.miqi/config.json`（最小空配置，不必带 provider 密钥）保持确定性、不依赖向导 UI
- 本地复现 CI 环境：`USERPROFILE=<空目录>`（Windows）/ `HOME=<空目录>` 模拟无用户配置
- 修复与流水线触发策略见 PR #1189（同仓 PR 触发、无定时）
