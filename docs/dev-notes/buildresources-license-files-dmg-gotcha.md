---
name: buildresources-license-files-dmg-gotcha
description: build/ 下的 license_*.txt 会同时被 NSIS 和 DMG 打包拾取，中文文本会让 macOS 打包失败
type: project
---

electron-builder 的 buildResources（默认 `build/`）目录下名为 `license_<语言>.txt` / `eula_*.txt` 的文件会被**两个目标**按约定自动拾取：

1. **NSIS**：生成按安装语言匹配的安装协议页（拒绝即终止安装）——#837 隐私协议页依赖此机制；
2. **DMG**（dmg-builder 的 `addLicenseToDmg` → dmgbuild licensing）：作为 DMG 挂载协议（SLA）。**中文文本无法用 mac_roman 编码**（`UnicodeEncodeError: 'charmap' codec can't encode character '→'`），直接导致 macos-build 打包失败。

**Why:** #837 加 license 文件后 macos-build CI 失败，根因是 dmg-builder 的约定拾取，与 NSIS 共享同一批文件。

**How to apply:** 中文 license 文件进 build/ 后，必须在 electron-builder.yml 设 `dmg.license: null` 显式禁用 DMG SLA（macOS 用户由应用内首次启动确认门兜底）；`nsis.license`（单语言）会覆盖约定拾取，二者互相独立。相关：[e2e-localstorage-shared-userdata](e2e-localstorage-shared-userdata.md)、[confirm-card-issue-714-fix](confirm-card-issue-714-fix.md)
