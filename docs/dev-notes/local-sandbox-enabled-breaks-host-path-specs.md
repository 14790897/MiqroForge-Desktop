---
name: local-sandbox-enabled-breaks-host-path-specs
description: 本机 tools.sandbox.enabled=true 会被 E2E 复制进临时 home，工具走 WSL 沙箱而 /mnt/c 只读，写宿主路径的 spec 全挂；对照法判断是否本机环境问题
type: project
---


2026-09-22 排查 write-authorization E2E 本机失败时实测（结论：与当次代码改动无关，纯本机环境）：

**现象**：spec 里 `existsSync(target)` 一直 false，页面快照却显示卡已点、回合已完成。bridge 日志：`Tool write_file done … Error: 沙箱中写入文件失败（path=/mnt/c/…）：Cannot write`；sandbox 日志：`Sandbox start via WSL distro=AIShadowSandbox` + `/bin/bash: … Read-only file system`。

**根因**：本机 `~/.miqi/config.json` 的 `tools.sandbox.enabled: true`（`wslDistro: AIShadowSandbox`）。E2E harness（`launchElectronApp`）会把开发者本机 config 复制进临时 MIQI_HOME，于是工具全走 WSL 沙箱，而沙箱里 `/mnt/c` 只读 → 任何写宿主临时目录的 spec（write-authorization 等）必挂。CI 的 config 没有 `tools.sandbox`（`miqi/bridge/server.py:_ensure_sandbox_manager` 判 `sb_cfg is None → disabled`），所以只在 Windows 本机暴露。
另外 bridge 初始化成功后会**自动回写** `tools.sandbox.enabled=true`（`miqi/bridge/loop.py:_init_sandbox_manager`），所以本机 config 会长期停在 true，不是一次性脏数据。

**How to apply**：改/写需要宿主路径的 E2E spec 时，按仓库既有惯例显式关沙箱——
`config.tools = { ...tools, sandbox: { ...tools?.sandbox, enabled: false } }`（guard-issue-811 / issue-1104 / issue-1131 等都这么写；需要沙箱的 spec 反过来 patch `enabled: true`）。pre-#1071 的老 spec 没有这行，恢复/新增时要补。

**判定「是不是本机环境问题」的对照法**（值得复用）：`git worktree add <tmp> origin/develop --detach` → junction 主仓库 node_modules → `npm run build` → 跑同一 spec。develop 也一样失败即为环境问题，别再去 revert 里找锅。

**失败时抓桥日志**：`closeElectronApp` 的 `keepHome` 参数（`tests/e2e/helpers/electron-setup.ts`）置 true 可保住临时 MIQI_HOME；bridge/sandbox 日志在 `<MIQI_HOME>/workspace/logs/`。

相关：[sandbox-and-workspace-switch-e2e-facts](sandbox-and-workspace-switch-e2e-facts.md)、[local-real-config-no-provider](local-real-config-no-provider.md)、[e2e-localstorage-shared-userdata](e2e-localstorage-shared-userdata.md)、[local-e2e-before-cloud-verification](local-e2e-before-cloud-verification.md)
