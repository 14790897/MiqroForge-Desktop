---
name: pr-screenshot-mandatory
description: PR「截图」节强制真实图片；2026-09-20 起必须是真实运行界面/窗口的截图（HTML 重绘不算），附 App 界面与真实终端两条采集路径
type: project
---


`pr-template-check.yml` 自 2026-09-14 起强制「截图」节含真实图片（Markdown 图片、图片直链或 GitHub 附件/上传链接），文字占位（如「无需截图」）不通过；失败信息会附 skills 目录的上传指引（https://github.com/14790897/MiqroForge-Desktop/tree/develop/skills）。同一 PR 还放宽了「日志/验证证据」（非空内容即可，不强制代码块）并修掉了判空漏洞（节为空但后接非空节会被误判为已填写）。

**Why:** 用户 2026-09-14 要求「强制校验有没有真实截图，没有截图则提示用 GitHub skills 上传」——所有 PR（含文档/CI 类）都要有真实截图证据。

**⚠️ 2026-09-20 用户明确要求：截图必须是「真实截图」**——把日志/终端内容用 HTML 重绘再截图的出图方式**不算**（我在 #1142/#1143 用过，被要求返工）。要抓真实运行的界面/窗口，且**不得把测试账号等凭据拍进公开 PR**（登录窗口截图先清空表单再拍）。三条已验证的真图路径：

1. **App 真实界面**（最省事，UI 相关改动用这个）：临 E2E spec 里对真实 Electron 窗口 `page.screenshot({ path: 'test-results/xxx.png' })` / 登录窗口 `loginWin.screenshot(...)`；跑 `--project=electron` 后取图（`test-results/` 会被下次任一 project 清空，立刻拷出上传）。
2. **真实终端窗口**（纯 CI/无界面改动时用）：`Start-Process wt.exe -ArgumentList '-w','-1','new-tab','--title',<title>,'cmd','/k',<cmd文件>`，再用 `EnumWindows` 按 class `CASCADIA_HOSTING_WINDOW_CLASS` + 标题前缀找句柄（`FindWindow` 实测找不到），`SetWindowPos` 挪到屏外固定尺寸，`PrintWindow(hwnd, hdc, 2)` 抓像素；完事 `PostMessage WM_CLOSE` 只关自己那个窗口（别 kill wt.exe，会连带关用户自己的窗口）。**坑**：`.cmd` 必须是 CRLF（LF 会被 cmd.exe 解析错，报 "is not recognized as an internal or external command"），PowerShell `Set-Content` 写行数组即是 CRLF。**脚本已留存**（2026-09-20 实测通过）：memory 目录 `terminal-window-capture.ps1`，参数化（-Title/-CmdFile/-Out），直接跑即可。
3. 历史做法（HTML 重绘 + electron capturePage/playwright screenshot）仅可用于本地速览，**不要当 PR 证据图**。

**更省事的路子（UI 有改动时）：** 直接在 E2E spec 里 `page.screenshot({ path: 'test-results/<name>.png' })`，跑完 electron 项目截图就在 `apps/desktop/test-results/`（中文文件名的路径没法直接喂给 bash，用 python 的 os.listdir 匹配后再转 ASCII 名上传）。**⚠️ `test-results/` 是 Playwright 默认 outputDir，每跑一次任一 project 都会清空** —— 2026-09-14 实证：E2E 截图生成后接着跑 smoke，截图全被清掉，只能重跑 E2E。顺序：生成截图 → 立刻拷出（或直接上传）→ 再跑别的套件。
