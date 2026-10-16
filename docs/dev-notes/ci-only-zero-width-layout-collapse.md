---
name: ci-only-zero-width-layout-collapse
description: "只在 CI（Linux 宽字体）复现的 0 宽布局塌陷——toBeVisible 报 \"resolved but hidden\" 的排查与守卫写法"
type: feedback
---


E2E 断言 `toBeVisible()` 失败但日志显示 `N × locator resolved to <span …>` + `unexpected value "hidden"` 时，通常是**元素被压成 0 宽**（Playwright 把 0 尺寸算作 hidden），不是元素没渲染。

**Why**：flex 行里「主文本 `min-w-0 truncate` + 次要列 `shrink-0`」时，若整行内容超出容器，主文本会被压到 0 宽；本地 Windows 字体窄、CI Linux 字体宽，所以只在 CI 塌陷（StatusBar 积分弹层 w-80 里日期+金额吃满整行，作业 ID 整条不可见，PR #1067 实测）。

**How to apply**：
1. 排查：`gh api .../actions/artifacts/<id>/zip` 下载 Playwright 产物，看 `test-failed-*.png`（肉眼看塌陷）+ `trace.zip` 里的 DOM 快照；本地复现不了优先看 CI 截图。
2. 修复：把争宽的元素分行（主信息独占一行），别让 `shrink-0` 的次要列与可截断的主文本同行。
3. 守卫：E2E 里 `page.evaluate` 把容器压到更窄（如 220px）+ 断言 `boundingBox().width > 40` —— 只断言 visible 不够（被截断到 20px 也算 visible），必须给宽度下限，否则本地永远绿、CI 才红。
