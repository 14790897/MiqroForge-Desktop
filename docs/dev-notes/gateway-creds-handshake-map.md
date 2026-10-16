---
name: gateway-creds-handshake-map
description: "网关凭据「握手文件」不一致的根因与修复（#1258/PR #1261）：前端看内存 store、后端读 <workspace>/.qraft/token.json，失败全静默；Windows junction 会让 syncTokenFile 拒写"
type: project
---


`Unsupported model: deepseek/deepseek-v4-flash (INVALID_PARAMS)`（真实用户两次遇到、平台侧误判为「平台不稳定，重新登录就好」）的根因与修复。2026-10-09 定位，issue #1258 / PR #1261。

**根因**：两套「网关是否可用」的判定从不互相校验 ——
- 渲染进程 `useQraftStatus` 的 `gatewayActive = 登录态 store 里 aiGateway.status === 'active'`（内存）；
- 后端保存门控 `_model_provider_resolvable` → `_qraft_gateway_routable` → `read_gateway_creds(<workspace>/.qraft/token.json)`（磁盘）。

**Why**：握手文件的每次失败都是静默的 —— `tokenFilePath()` 解析失败直接 return（无日志）、`syncTokenFile` 写失败只有 WARN、Python `read_gateway_creds` 遇 `OSError/ValueError` 静默返回 None。于是文件没到位时用户只拿到一句误导且不可重试的 `Unsupported model`；前端自动就绪重试 4 次后静默放弃。

**How to apply**：以后排查同类「前端认为可用、后端说不可用」的报错，先比内存视图（`store.current` / `status()`）与磁盘握手文件，别急着怀疑平台。已排除平台侧：生产域名下多个账号 userinfo 都正常下发 `encryptedApiKey` + `aiGatewayStatus=active`（平铺在顶层）。

**修复后的契约（#1258）**：`GATEWAY_CREDS_UNAVAILABLE`（凭据暂缺，`recoverable=True`，前端 `qraft:syncToken` 重新同步握手文件后重试一次）/ `GATEWAY_ORIGIN_INVALID`（`QRAFT_GATEWAY_BASE` 非 https，重试无用）/ 其余才是 `Unsupported model`。三处保存门控（`config.update`、`config/batchWrite`、`providers.update`）共用 `provider_handlers.unsupported_model_error`。

**非显然的事实**：
- 写「网关是唯一可用路径」类断言时，spec 必须用 `noUserConfig: true` 启动：**CI 会把 `DEEPSEEK_API_KEY` 写进 app 的 config.json**，带着本地 provider 凭据时保存会由「模型自己的 provider 有凭据」合法放行，断言就测不到网关那条分支（首轮 CI 在 ubuntu + macOS 两个 job 各挂三条，正是这个原因）。零凭据也才是真实登录用户的处境。
- 用「删掉握手文件」做前置时要**先等登录后的自动就绪落盘完成**（轮询 config.json 的 `agents.defaults.model`）：全新安装下自动就绪会自己写一次网关模型，而它新增的「重同步 + 重试」会把被删的文件补回来，随后用例的保存就成功了 —— 被测对象被悄悄换掉。成功一次后 `attemptedRef` 不再复位，本次运行不会重跑。
- Windows 上 **目录 junction 在 `fs.lstatSync` 里 `isSymbolicLink()===true`、`isDirectory()===false`**（实测）；POSIX 上 `symlinkSync(..., 'junction')` 退化为普通目录 symlink，同样命中同一条守卫 —— 所以 `.qraft` 被链接占用这条用例跨平台都成立。
- 前后端工作区解析**没有**路径分歧：junction/8.3 短名/`..` 在 Windows 文件系统层会收敛到同一文件（已用真实 TS+Python 差分实测）。
- 复现/回归 spec：`apps/desktop/tests/e2e/gateway-creds-handshake.spec.ts`（mock 登录态，含真实界面路径，跑在 CI）、`gateway-creds-live.spec.ts`（真实账号 live，登记在 `tests/e2e/CI-COVERAGE.md`）。
- 该 spec 里 `config.update` 偶发 `Request config.update timed out`（桥超时类抖动），已做「只对超时重试」的窄兜底。
- issue 号容易撞车：动手前先 `gh issue list` / `gh pr list` 确认 #号归属（本次差点把 #1253 写成自己的号，实为「登录失效自动退出」那条 FEATURE）。

相关：[verify-fix-in-real-app-not-just-mocks](verify-fix-in-real-app-not-just-mocks.md)、[real-account-features-need-live-e2e](real-account-features-need-live-e2e.md)、[qraft-test-account-credentials](qraft-test-account-credentials.md)、[login-gate-mandatory-login-1095](login-gate-mandatory-login-1095.md)
