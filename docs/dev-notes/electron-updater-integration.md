---
name: electron-updater-integration
description: "桌面端自动更新接入要点（#1124）：CJS getter 导出坑、latest.yml/dev.yml channel 规则、本地假 feed 验证法、产物命名一致性"
type: project
---


2026-09-17 接入 electron-updater（PR #1127，配套安装模式改动 PR #1125）。踩过的坑与验证方法：

1. **CJS 互操作**：electron-updater 的 `out/main.js` 用 `Object.defineProperty(exports, name, {get})` 导出，在 electron-vite 打包出的 CJS 主进程里 `await import('electron-updater')` 的**命名导出是 undefined**（`autoUpdater` 拿不到，报 `Cannot set properties of undefined`）。必须从 `mod.default?.autoUpdater` 兜底。参考 trampoline 里同源的注释。
2. **channel 与元数据文件**：`--publish never` 也会写更新元数据（只要配了 `publish`）；文件名按版本通道——版本是预发布（`0.31.0-dev`）→ `dev.yml`，正式版 → `latest.yml`。客户端也按自身版本取：预发布版本 `allowPrerelease=true` 读 dev.yml。CI 只发正式版，线上走 latest.yml。
3. **产物命名必须与 GitHub 资产名一致**：见 [github-asset-name-sanitization](github-asset-name-sanitization.md)（`nsis.artifactName: MiQroForge.Desktop.Setup.${version}.${ext}`）。
4. **本地验证更新链路**（不用发版）：打包安装后设 `MIQI_UPDATE_FEED_URL=http://127.0.0.1:8899`（仅打包版生效），起一个静态服务器提供对应 channel 的 yml（version 高于当前、sha512 用 `openssl dgst -sha512 -binary | openssl base64 -A` 算）+ 一个安装包副本，应用启动 30s 后自动检查→下载→pending。差分下载会给旧版本 blockmap 404 → 自动回落全量（正常）。
   - 假 feed 脚本注意：目录比较要用 `path.resolve` 两侧，否则 `C:/` 与 `C:\` 混用导致 startsWith 失败、全部 404。
5. **测试后必须强杀进程**：`autoInstallOnAppQuit = true`，正常退出会触发安装 pending 的包（下载的是自己那份安装包，会真的重装一遍）。
6. Windows-only：macOS 包未签名，`app.isPackaged && process.platform === 'win32'` 之外一律 unsupported。
