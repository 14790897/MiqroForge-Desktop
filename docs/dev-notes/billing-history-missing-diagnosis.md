---
name: billing-history-missing-diagnosis
description: 排查「扣费历史/登录态消失」类反馈的路径——dev userData 按 checkout 隔离（ws-hash=sha256(<checkout>/apps)）、主进程日志里的扣费/登出行为
type: reference
---


用户报「关闭软件后积分扣费历史没有了」时的排查路径（2026-09-14 实测走通）：

1. **扣费历史文件位置**：`<userData>/qraft-billing-history.json`（去重索引 `qraft-billed-job-ids.json` 同目录）。dev 模式 userData 被 [index.ts](index.ts.md) 改道为 `%APPDATA%\miqi-desktop-dev\ws-<hash>`，hash = `sha256(<checkout>/apps)` 前 16 位（`__dirname` 是 `<checkout>/apps/desktop/out/main`，`join(__dirname,'../../..')` = `<checkout>/apps`）。**每个 worktree/checkout 一份**，所以「换个工作区跑 app」看起来就是登录态+历史全丢（本机曾有 31 个 ws-* 目录）。打包版只有 `%APPDATA%\miqi-desktop` 一份。
2. **主进程日志**：`<workspace>/logs/electron-main-YYYY-MM-DD.log`（projectRoot 由 workspace 决定，不一定是 app 二进制的 checkout）。扣费成功/失败/去重命中、`已退出登录`、`自动刷新失败（REFRESH_TOKEN_INVALID）` 都在这里。用「log 里的余额数字差 10」可定位哪次扣费落在哪个 userData。
3. **历史会消失的唯一原因**：登出（2026-09-14 前 `logout()` 会 rmSync 历史+索引；PR #1067 起不再删除，改为读取时按 account.sub 过滤）。
4. 过滤行为：`getBillingHistory()` 只返回当前登录账号的条目（`!e.accountSub || e.accountSub === sub`），未登录返回空——换账号看不到前任账号记录属于预期。
5. E2E 预置/断言：`MIQI_QRAFT_STORE`（登录态）与 `MIQI_QRAFT_BILLING_DIR`（计费文件）两个环境变量改道临时目录，参考 `tests/e2e/qraft-billing-history.spec.ts`。

**How to apply**：遇到「历史/登录态莫名消失」先按上述定位是哪个 userData + 翻日志找登出/扣费行，再怀疑代码；dev 环境先确认是不是换了 checkout。
