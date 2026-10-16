---
name: platform-domain-migration
description: "2026-09-17 生产平台/OAuth 域名已迁移至 www.miqroforge.com（PR #1119，只改生产）；浏览器登录入口的平台侧遗留问题"
type: project
---


平台**生产**域名已在 2026-09-17 迁移到 `https://www.miqroforge.com`（只改生产；测试环境 `test.forge.miqroera.com` 不变）。落地 PR #1119，唯一运行时改动是 `apps/desktop/src/main/qraft/types.ts` 的 `QRAFT_ENV_DEFAULTS.prod.baseUrl`；存量登录态迁移在 `QraftStore.load()`（`migrateStoredBaseUrl`，只改历史默认值，自定义地址保留），评审后补充（CodeRabbit #4032384227）。

**实测事实（2026-09-17）：**
- `www.miqroforge.com` → 47.116.42.189（与**测试环境同一台服务器**），TLS 有效，登录页 bundle 可提取 RSA 公钥
- 真实账号 live 用例在新域名全流程通过（平台登录→授权码→token→userinfo→refresh；QRAFT_BASE_URL 可覆盖，见 `live.integration.test.ts`）
- `miqroforge.com` 裸域不解析；`test.miqroforge.com` 不存在
- 旧域名 `forge.miqroera.com` 公网 NXDOMAIN（生产从未公网上线）
- **坑**：新域名未登录时 `/api/oauth2/authorize` 会 302 到 `test.forge.miqroera.com/login?state=…`（平台前端登录页仍配置在旧测试域名）。App 浏览器登录窗口按 `baseUrl` origin 轮询 `Authorization` cookie（ipc.ts），跨域取不到 → **浏览器登录入口在新域名下无法完成**，需平台把登录页切到新域名自身；API/密码路径不受影响
  - 2026-09-18 实测坐实：平台登录下发的 `Authorization` cookie 是 **host-only**（无 Domain 属性）；把该 cookie 显式带给新域名 authorize → 平台**直接发授权码**（authorize 认会话、不认来源域名）。故平台侧只需把登录页跳转改为自身域名/相对路径 `/login`，App 侧无需改动
  - **2026-09-18 平台已修复并验证**：未登录 authorize 现在 302 到 `https://www.miqroforge.com/login`（同域）；真实账号 live E2E（PR #1119 分支代码）端到端通过——授权窗口直接落在同域登录页，登录后 App `env=prod`、`baseUrl=https://www.miqroforge.com/api`
  - **仍存在的 UI 缺口（未解决）**：`browserLogin({})` 不传 env → `resolveConfig` 取 `stored.env ?? 'test'`，首次登录只会走测试环境；环境选择器与高级设置被隐藏（QraftPage.tsx「待后续需要时恢复」）。且生产环境强制要求「平台注册的 redirect_uri」（service.ts validateConfig），UI 无填写入口 → UI 切到 prod 会以 INVALID_CONFIG 失败（live 测试是显式传 `redirectUri` 才通的）。平台实测接受 `http://localhost:38000/callback`
  - 追踪：issue #1138（平台侧修复，已修复）；PR #1119 / #1122 均已合入
- **2026-09-20 收口（PR #1142）**：默认登录环境由 test 改为 prod（`resolveConfig` 等 4 处），生产缺省 `redirect_uri` 用平台注册值 `http://localhost:38000/callback`；`QraftStore.load()` 把存量测试环境登录态并入生产（含 loopback→注册值）。平台此时已把登录页统一到新域名——**连 test 域名的 authorize 也 302 到 www.miqroforge.com/login**，测试环境实际无法完成浏览器登录（`QRAFT_ENV_DEFAULTS.test` 仍留在代码里但基本失效）。验证方式：真实账号 live（默认路径登录生产）+ 用 `MIQI_QRAFT_STORE` 预置 legacy 态启动（E2E helper 支持预置、只在不设时覆盖）
- **2026-09-21 复核**：默认生产路径真实账号 live 全绿（浏览器登录 E2E、首屏卡片 OAuth、live 集成 2 passed、登录 E2E 全套 13 passed）。云端登录 E2E 原写死测试昵称「MiQi测试」→ 改为「<昵称> 已登录 + 环境 生产」断言；access_token TTL 实测已变 ≈30 天（早期 ≈2 小时），过期的 TTL 断言/页面文案/注释一并修正（PR #1170）
- `.github/workflows/desktop-ci.yml` 的 temperature 修复曾随 #1119 丢失（GitHub 腿拒收 workflow 改动、未核对），2026-09-20 以 PR #1143 单独补回，见 [miqroera-github-mirror-sync](miqroera-github-mirror-sync.md)
- 新域名未代理 AI 网关路径（POST /miqroera-deepseek/v1/messages → 405），网关仍走 `http://118.25.115.164`，见 [ai-gateway-https-policy](ai-gateway-https-policy.md)

**How to apply:**
- 涉及平台地址的排查/改动一律以 `www.miqroforge.com` 为生产、`test.forge.miqroera.com` 为测试
- 改动前先验证新域名的 authorize 跳转落点；浏览器登录链路要真机验证（cookie 按 host 作用域，不能只看 302）
- 全仓替换旧域名时注意 `test.forge.miqroera.com` 是其子串，不能裸替换
