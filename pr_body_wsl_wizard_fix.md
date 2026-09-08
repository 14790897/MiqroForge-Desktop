## 类型

- [x] 🐛 Bug 修复

## 变更概述

修复 WSL2 一键安装向导的两处实测缺陷：提权命令退出码误报导致安装成功被判失败；docker-desktop 等不可用发行版阻断 Ubuntu 安装步骤。

## 背景/问题

在真实环境实测「卸载 WSL2 → 向导装回」流程（UAC 提权 + 2 次重启）时发现：

1. **提权命令退出码不可读**：`Start-Process -Verb RunAs -Wait -PassThru | Select-Object -ExpandProperty ExitCode` 在 UAC 提权后必抛 `Process must exit before requested information can be determined`（实测 3/3 复现），外层 PowerShell stdout 为空、退出码 1 → `parseInt('')` 得 NaN → 向导三处安装步骤（启用功能/装内核/装 Ubuntu）在**操作实际成功时误报失败**。
2. **docker-desktop 阻断第 4 步**：第 4 步触发条件是 `distros.length === 0`，但发行版列表未过滤。实测注销全部 Linux 发行版后（仅剩 docker-desktop），向导直接报「安装配置完成」，而用户没有任何可用发行版，后续沙箱供给必然失败。
3. 顺带修复：功能状态读取用 DISM `Get-WindowsOptionalFeature`，非提权应用内永远失败 → 「功能已启用但内核未装」的机器被误判为 `not-enabled`，向导第 2 步会无限重跑。

## 变更内容

- `apps/desktop/src/main/ipc/wsl-state.ts`（新增）：纯函数模块，含 WMI 功能状态读取、bash 可用性探测、非 root 用户探测、状态分类等，无 Electron 依赖、可单测。
- `apps/desktop/src/main/ipc/index.ts`：
  - 三处提权命令去掉 `-PassThru | Select-Object -ExpandProperty ExitCode`，改为执行后**重新检测系统状态**判定成败（第 2 步验 WSL 功能状态、第 3 步验 WSL 包/`wsl --status`、第 4 步沿用 postCheck）；UAC 被拒等 spawn 错误走原有 error 分支。
  - `runWslCheckInternal` 功能读取改为 WMI `Win32_OptionalFeature`（非提权可读、即时反映 DISM pending 状态）；发行版列表按 `bash -c "echo ok"` 探测过滤 docker-desktop 类；初始化探测对所有可用发行版取 `some()`。
  - 功能读取失败（WMI 不可用）时不再归类为 `not-enabled`（避免误循环启用功能），归类为 `not-installed` 走内核安装步骤（该步骤能同时修复两种情况）。
- `apps/desktop/src/main/ipc.test.ts`：删除复制逻辑的测试，改为直接测试 wsl-state.ts 真实代码（22 个用例）。

## 日志/验证证据

```
# 向导同款命令（第2步启用功能）——外层拿不到退出码：
Select-Object : Exception getting "ExitCode": "Process must exit before requested information can be determined."
# 但实际功能已启用（WMI 验证）：
Name                              InstallState
Microsoft-Windows-Subsystem-Linux            1    # 2 -> 1 Enabled

# 注销全部 Linux 发行版后（仅剩 docker-desktop）：
$ wsl --list -q
docker-desktop
# 修复前：distros.length===1 -> 第4步跳过，向导误报完成
# 修复后：docker-desktop 被过滤 -> distros=[] -> 正确触发 Ubuntu 安装
```

单测结果：

```
Test Files  1 passed (1)
Tests       22 passed (22)
# src/main 全量: Test Files 10 passed | 2 skipped; Tests 174 passed | 5 skipped
```

类型检查：`npm run typecheck:node` 通过。Prettier 检查通过。

## 测试情况

- 新增/改写 `apps/desktop/src/main/ipc.test.ts` 22 个用例全过（状态分类 8、WMI 解析 4、发行版过滤 3、非 root 用户探测 4、内核后验 4 等）。
- `vitest run src/main` 全量 174 passed / 5 skipped（均为原有跳过）。
- `npm run typecheck:node` 通过；`prettier --check` 通过。
- 未跑真机 E2E（本改动即源自真机实测；WSL 重装流程需要 UAC + 重启，无法在 CI 自动化）。
