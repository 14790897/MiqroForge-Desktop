---
name: live-login-e2e-diagnosis
description: "真实登录 live E2E 挂在 #login_phone 时的排查路径——本地真浏览器打开平台登录页验证，bundle grep 不可信"
type: reference
---


CI 的真机登录用例（`mcp-hosted-live` / `qraft-browser-login` / `qraft-login-entry`）失败在 `helpers/electron-setup.ts` 的 `browserLogin` 等待 `#login_phone` 时，排查顺序：

1. **本地用真实浏览器打开 `https://www.miqroforge.com/login` 查 DOM**：`#login_phone` / `#login_password` 是 React 运行时生成的 id，**下载 era-index bundle 后 grep 这些字符串是 0 命中**（2026-09-30 实测误判过一次），别用 bundle grep 下结论。用 playwright chromium 打页面 dump input 元素最直接（本机 `apps/desktop/node_modules/@playwright/test` + `~/AppData/Local/ms-playwright` 已装，脚本要用 `NODE_PATH=<repo>/apps/desktop/node_modules` 跑，且 unset HTTP_PROXY）。
2. 平台前端重部署会让登录页短暂不可用：era-* bundle 名带构建时间戳（如 `era-index-*.js` 后缀 `1790754744023`），重部署当天 CI 里这些 live 用例会抖（同一天 develop 上 3 条同因失败、PR 上只挂 1 条）。
3. 确属抖动 → 重跑失败 job（见 [e2e-real-llm-hard-fail-rerun](e2e-real-llm-hard-fail-rerun.md)），不要改 spec 去迁就。凭据见 [qraft-test-account-credentials](qraft-test-account-credentials.md)，平台侧背景见 [platform-domain-migration](platform-domain-migration.md)。
