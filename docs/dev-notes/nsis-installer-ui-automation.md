---
name: nsis-installer-ui-automation
description: 无人值守驱动 NSIS 安装器（点同意/下一步）与截被遮挡窗口的方法；AppActivate/SetForegroundWindow 会被前台锁拒绝
type: project
---


本机验证桌面安装器时（2026-09-17，PR #1125）摸索出的可靠方法：

- **点按钮**：给安装器对话框 `PostMessage($hwndParent, WM_COMMAND=0x0111, wParam=控件ID, 0)`。NSIS 的"我同意/安装/完成/下一步"都是 `id=1`，取消 `id=2`，上一步 `id=3`。用 **PostMessage 而非 SendMessage**——跨进程 SendMessage 会卡死（实测 BM_CLICK / SendMessage 挂住 120s+）。
- **截图**：`PrintWindow(hwnd, hdc, 2)` 能截取**被其他窗口遮挡**的安装器窗口（窗口级、干净）；全屏 CopyFromScreen 只会拍到前台窗口。
- **不要试图抢前台**：`SetForegroundWindow`、`SwitchToThisWindow`、`WScript.Shell.AppActivate`（返回 True）都会被 Windows 前台锁拒绝，实测无效。
- **页面序列**（oneClick:false 的 assisted 安装器）：协议页（我同意）→ 安装选项页（所有用户/仅为我）→ 安装进度 → 完成页（自带"运行应用"勾选）。
- **UIA 探页面**：`AutomationElement` 按 pid 找到窗口，dump descendants 的 Name/AutomationId 判断当前页；NSIS 控件多为 Pane 类型，SelectionItemPattern 取不到选中态，选中态直接看截图。

**Why**：E2E/验证安装器行为时没有 Playwright 可用（Electron 之外），这套组合是实测唯一能稳定跑通的方式。

**How to apply**：写 PowerShell 脚本驱动安装器验证（安装/升级/卸载路径、模式页默认项）时直接复用；注意脚本里 `"f$i: ..."` 要写成 `"f${i}: ..."`，PowerShell 会把 `$i:` 解析成盘符。相关：[e2e-direct-screen-recording](e2e-direct-screen-recording.md)、[pr-screenshot-mandatory](pr-screenshot-mandatory.md)。
