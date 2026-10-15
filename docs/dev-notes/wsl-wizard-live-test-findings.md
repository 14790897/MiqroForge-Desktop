---
name: wsl-wizard-live-test-findings
description: "WSL2 一键安装向导真机实测发现：ExitCode 怪癖、docker-desktop 阻断、卸载不持久，PR #972 已修"
type: project
---


2026-09-08 真机实测「卸载 WSL2 → 向导装回」（UAC + 2 次重启）核心发现：

- `Start-Process -Verb RunAs -Wait -PassThru | Select-Object ExitCode` 提权后**必抛** "Process must exit before requested information can be determined"（3/3 复现）→ 提权命令永远不能读退出码，只能状态后验。PR #972 已改三处。
- docker-desktop 等不可跑 bash 的发行版会阻断向导第 4 步（`distros.length === 0` 永不成立）→ #972 已按 bash 探测过滤。
- `wsl --uninstall` 弹 60s 倒计时交互提示（ESC/CTRL-C 中止），无控制台时自动中止。
- 本机 WSL 卸载**不持久**：重启后 Windows 自动重新提供包（Store re-provision）+ 发行版注册复活（vhdx 从未清除）。
- VMP 禁用无效（疑似 VBS 内核隔离保护），仅 WSL 功能可禁用；WMI `Win32_OptionalFeature` 非提权可读、即时反映 DISM pending 状态（向导功能读取已改用它）。
- WSL 2.7 支持**非提权** `wsl --import` 到用户目录（恢复无需管理员）。
- 备份 tar：`<用户主目录>\wsl-test-backup\`（Ubuntu + AIShadowSandbox，已实测可导入）。测试日志 test_log.md 同目录。

相关：[wfp-blocks-ipv6-loopback](wfp-blocks-ipv6-loopback.md)（本机 WSL 环境历史坑）
