---
name: github-asset-name-sanitization
description: GitHub Release 上传资产把文件名空格替换成点；electron-updater 的 latest.yml path 必须与资产名一致，否则 404
type: project
---


GitHub Release 通过 API 上传（softprops/action-gh-release 等）会把文件名里的空格自动替换成 `.`：
本地构建产物 `MiQroForge Desktop Setup 0.31.0.exe` → GitHub 资产名 `MiQroForge.Desktop.Setup.0.31.0.exe`（mac 同理 `MiQroForge.Desktop-0.31.0.dmg`）。

**Why（为何重要）**：接入 electron-updater 时，latest.yml 里记录的 `path` 是本地构建的原名（带空格），而 GitHub 上的资产名是点号版 → electron-updater 按 path 拼下载 URL 会 404。

**How to apply**：给受影响的 target 显式设置 `artifactName`（如 `nsis.artifactName: "MiQroForge.Desktop.Setup.${version}.${ext}"`，与现有公开资产名完全一致），让本地构建名、资产名、latest.yml path 三者统一。改发版产物命名前先核对 docs/发布说明里引用的下载文件名。相关：[pr-screenshot-mandatory](pr-screenshot-mandatory.md)、#1124。
