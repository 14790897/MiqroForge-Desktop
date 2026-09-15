# 法律文本（Legal）

本目录收集与法律合规相关的说明。

## 法律文件（2026-09-28 版，律师定稿）

MiQroForge Desktop 的法律文件**权威文本**位于（入库版本化，仅中文，
中文版为唯一权威版本）：

- 用户协议：`apps/desktop/src/renderer/assets/legal/terms.zh-CN.md`
- 隐私政策：`apps/desktop/src/renderer/assets/legal/privacy.zh-CN.md`
- 隐私政策摘要：`apps/desktop/src/renderer/assets/legal/privacy-summary.zh-CN.md`
- 个人信息收集清单：`apps/desktop/src/renderer/assets/legal/data-collection.zh-CN.md`
- 个人信息对外提供清单、第三方服务清单：`apps/desktop/src/renderer/assets/legal/data-sharing.zh-CN.md`

文本来源为上海兰迪律师事务所 2026-09-11 定稿（生效日期 2026-09-28），
以 Markdown 入库（标题/列表/表格保留原结构），转换时只重建结构、不改动文字。

### 与律师源文档的差异清单（审计用）

入库文本与律师 docx 逐字一致，仅以下两类别例外——改动与保留都记录在此，
供律师侧复核：

**一、已修正的源文档笔误**（明显错别字/漏字/产品名，不涉及条款含义）

| 文件 | 原文 | 修正为 |
| --- | --- | --- |
| data-sharing | 以向您提供相关，该等第三方SDK信息如下 | 以向您提供相关**服务**，该等第三方SDK信息如下 |
| privacy | MiQroForgDeskTop | MiQroForge DeskTop |
| privacy | 您可以本隐私政策载明的方式联系我们 | 您可以**通过**本隐私政策载明的方式联系我们 |
| terms | 本软件**务**可能使用或包含了由第三方提供的软件或服务 | 本软件可能使用或包含了由第三方提供的软件或服务 |
| terms | …仍未登录、使用账号的**.**（半角句点） | …仍未登录、使用账号的**。** |
| terms | …等方式**发送**向您发送关于…（重复「发送」） | …等方式向您发送关于… |

**二、保留未改、待律师确认的缺陷**（涉及取舍/语义，仓库不擅自改法务文本）

| 文件 | 位置 | 问题 |
| --- | --- | --- |
| terms | 「您在接受本协议之前，请仔细阅读并充分理解以下特别提示：」 | 该句在源文档中重复出现两次，需律师确认保留哪一处 |
| terms | 「您承诺在使用本软件您提交的输入内容：」 | 缺少连接词（「使用时」？「您在使用本软件时提交的输入内容」？），语义有待律师确认 |


同一份文本被多路消费，保证安装器与应用内展示内容一致（避免多份拷贝漂移）：

| 消费方 | 机制 |
| --- | --- |
| NSIS 安装器 | `apps/desktop/scripts/sync-legal.mjs` 在打包前把《用户协议》+《隐私政策》合并、去掉 Markdown 标记后写为 `apps/desktop/build/license_<语言>.txt`（build/ 已被 .gitignore 忽略）；electron-builder 的 buildResources 约定自动生成按安装语言匹配的协议页，用户拒绝即终止安装。法律文本仅中文，各安装语言共用中文正文 |
| 首次启动确认门 | `apps/desktop/src/renderer/features/setup/PrivacyConsentGate.tsx` 展示律师定稿的《温馨提示》，《隐私政策》《用户协议》在文内可点开全文；「同意」在倒计时结束后启用（覆盖 portable / zip / MSI 及升级用户；同意状态本地持久化，协议版本更新时重新确认） |
| 设置页查阅入口 | 设置 → 法律文件（`features/legal/LegalDocumentsPage.tsx`），左侧目录五份文件、右侧正文 |

渲染层通过 Vite `?raw` 导入直接内联上述文件（`src/renderer/lib/privacy.ts`），
经 `features/legal/LegalDocContent.tsx` 渲染。

## 协议版本更新流程

1. 替换 `assets/legal/*.zh-CN.md` 的文本；
2. 递增 `src/renderer/lib/privacy.ts` 中的 `PRIVACY_VERSION`；
3. 已同意旧版本的用户会在下次启动时重新看到确认门（localStorage 中 `miqi:privacyConsentVersion` 与当前版本不一致）。

## 注意事项

- `apps/desktop/build/license_*.txt` 是打包时生成的副本，勿手工编辑，也无需提交；
- electron-builder 打包时会给 build/ 下的副本追加 UTF-8 BOM（原地修改），属正常；
- MSI / portable / zip / DMG 目标无安装协议页：MSI/portable/zip 是 electron-builder 不支持（见 issue #837 调研评论）；DMG 的挂载协议（SLA）因中文无法用 mac_roman 编码，已在 electron-builder.yml 用 `dmg.license: null` 显式禁用。这些分发形式一律由首次启动确认门兜底；
- 免责声明（每条 AI 回答底部常驻）是另一份独立文本，见 issue #836，不在此维护；
- 待决：Tavily / Brave / DuckDuckGo 在中国大陆的可达性核查（律师建议不可访问则删除），见 issue #1068 待决项。
