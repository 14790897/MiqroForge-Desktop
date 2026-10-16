---
name: legal-documents-feature-map
description: 法律文件体系（律师定稿 5 份，v2.0 中文唯一权威）落点地图、文案不可被格式化器改动的约束、待决的搜索引擎可达性
type: project
---


2026-09-14 上线（issue #1068 / PR #1069，承接 #837）：桌面端协议体系升级为律师定稿的**五份法律文件**（上海兰迪律师事务所 2026-09-11 设计调整，发布/生效日期 2026-09-28）：用户协议 / 隐私政策 / 隐私政策摘要 / 个人信息收集清单 / 个人信息对外提供清单及第三方服务清单。

**落点（改协议时按这张图走）：**

| 位置 | 作用 |
| --- | --- |
| `apps/desktop/src/renderer/assets/legal/*.zh-CN.md` | 权威文本（Markdown，仅中文；律师中文版是唯一权威版本，界面无中英切换） |
| `src/renderer/lib/privacy.ts` | `PRIVACY_VERSION`（现 2.0）、`LEGAL_DOCUMENTS` 目录、`CONSENT_NOTICE_TEXT` 温馨提示（`{{privacy}}`/`{{terms}}` 为链接占位符）、`readConsentVersion`（缓存+权威存储）、`clearConsent` |
| `src/main/privacy-consent.ts` | **同意版本权威存储**（`userData/privacy-consent.json`，#1073）；IPC `privacy:get-consent`（sendSync）/`privacy:set-consent` |
| `src/main/index.ts` | 打包版单实例锁（防双开 profile 争用，#1073） |
| `features/setup/PrivacyConsentGate.tsx` | 首次启动门：温馨提示 + 文内全文弹窗 + 3s 倒计时（「同意」/「不同意，退出」） |
| `features/legal/LegalDocumentsPage.tsx` + `LegalDocContent.tsx` | 设置 → 法律文件：左目录右正文（react-markdown + remark-gfm） |
| `scripts/sync-legal.mjs` | 打包时把用户协议+隐私政策合并、去 Markdown 标记 → `build/license_*.txt`（NSIS 协议页；各安装语言共用中文正文） |

**为什么同意状态有两处存储（#1073）**：打包版双开时第二个实例的 Chromium 存储退化成内存（localStorage 读不到也写不进）→ 用户「每次打开都强制看协议」。现在主进程 userData 文件是权威存储（preload 同步读到，判定不闪现），localStorage 只是快速缓存、丢失会自动回填；打包版另加单实例锁。**改 E2E 清同意时两处都要清**（见 spec 的 `clearStoredConsent`；只清缓存的场景见 #1071 回归用例 `clearCachedConsentOnly`）。

**Why:** 律师 2026-09-11 明确要求弹窗换温馨提示并**保留计时设计**、设置侧边栏「隐私协议」更名「法律文件」并列出五份文件；原 v1.0 文本是开发自拟的，与定稿不一致。版本递增到 2.0 让老用户重新确认。

**How to apply:**

- 协议更新流程：换 `assets/legal/*.zh-CN.md` → 递增 `PRIVACY_VERSION` → 老用户下次启动重新确认（见 `docs/legal/README.md`）。
- **法律文案不能被格式化器或 JSX 折叠改动**：弹窗文案是单字符串常量（JSX 里换行会被折成空格、prettier 会重排）；文档正文入库前要与律师源 docx 逐字比对（去结构标记后字符流必须一致，当时用一次性脚本校验，源 docx 在 WXWork 缓存目录）。
- 源文档已知问题：用户服务协议里有一句重复的「您在接受本协议之前…特别提示：」按原文保留（待律师修订）；源文件含 6 个康熙部首异体字（⾃/⾏/⼊/⻛/⽅/⽌）已按常规字形入库。
- **#1156 核实（2026-09-28）**：文本问题均**继承自律师 docx 原件**（入库逐字保留，非转换引入）：① 品牌拼写「MiQroForge DeskTop」仓库 25 行 / 38 处，律师 docx 同为 38 处（docx 文件名却是正确拼写）；② 邮箱 `forge@miqoera.com` 3 处，DNS 实测 miqoera.com 无 NS/A/MX（域名不存活、必然收不到信），正确域 miqroera.com 有腾讯企业邮 MX（mxbiz1/2.qq.com），miqroforge.com 亦无 MX；③ 日期 2026-09-28（terms+privacy 共 4 行）与发布时点确实不一致（09-15 随 v0.30.0 上线却写 09-28 生效），但**用户 2026-09-28 决定不作问题处理**，已从 #1156 移除；#1156 现只跟踪拼写与邮箱两项，改动待法务确认。
- **待决**：Tavily / Brave / DuckDuckGo 在中国大陆可达性核查，律师建议不可访问则从设置页删除（产品决策，挂在 #1068 待决项）——本次只改了「网页工具」描述为「Web 搜索与网页读取」，搜索引擎选项未动。
- 真实账号无关，不需要 live E2E；覆盖靠 `tests/privacyConsent.test.ts` + `tests/e2e/privacy-consent.spec.ts` + smoke 预置同意版本（该版本号写死在 `tests/smoke/mocks.ts`，改版本时同步）。
