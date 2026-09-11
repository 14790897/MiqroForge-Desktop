# 法律文本（Legal）

本目录收集与法律合规相关的说明。

## 隐私协议

MiQroForge Desktop 的《隐私协议》**权威文本**位于（入库版本化）：

- 简体中文：`apps/desktop/src/renderer/assets/legal/privacy.zh-CN.txt`
- English：`apps/desktop/src/renderer/assets/legal/privacy.en-US.txt`

同一份文本被三处消费，保证安装器与应用内展示内容一致（避免多份拷贝漂移）：

| 消费方 | 机制 |
| --- | --- |
| NSIS 安装器 | `apps/desktop/scripts/sync-legal.mjs` 在打包前把文本复制为 `apps/desktop/build/license_<语言>.txt`（build/ 已被 .gitignore 忽略）；electron-builder 的 buildResources 约定自动生成按安装语言匹配的协议页，用户拒绝即终止安装 |
| 首次启动确认门 | `apps/desktop/src/renderer/features/setup/PrivacyConsentGate.tsx`（覆盖 portable / zip / MSI 及升级用户；同意状态本地持久化，协议版本更新时重新确认） |
| 设置页查阅入口 | 设置 → 隐私协议（`features/settings/components/PrivacyPage.tsx`） |

渲染层通过 Vite `?raw` 导入直接内联上述文件（`src/renderer/lib/privacy.ts`）。

## 协议版本更新流程

1. 修改 `privacy.zh-CN.txt` 与 `privacy.en-US.txt` 的文本；
2. 递增 `src/renderer/lib/privacy.ts` 中的 `PRIVACY_VERSION`；
3. 已同意旧版本的用户会在下次启动时重新看到确认门（localStorage 中 `miqi:privacyConsentVersion` 与当前版本不一致）。

## 注意事项

- `apps/desktop/build/license_*.txt` 是打包时生成的副本，勿手工编辑，也无需提交；
- electron-builder 打包时会给 build/ 下的副本追加 UTF-8 BOM（原地修改），属正常；
- MSI / portable / zip / DMG 目标无安装协议页：MSI/portable/zip 是 electron-builder 不支持（见 issue #837 调研评论）；DMG 的挂载协议（SLA）因中文无法用 mac_roman 编码，已在 electron-builder.yml 用 `dmg.license: null` 显式禁用。这些分发形式一律由首次启动确认门兜底；
- 免责声明（每条 AI 回答底部常驻）是另一份独立文本，见 issue #836，不在此维护。
