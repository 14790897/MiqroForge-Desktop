# MiQroForge 平台 OAuth2 登录（内置）

> 对应 Issue：[#726](https://github.com/14790897/MiQi/issues/726)（PR #728）
> 实测依据：《Qraft OAuth2 接入实测文档》（2026-08-13 对 `https://test.forge.miqroera.com` 实测）

MiQroForge Desktop 内置 MiQroForge 平台 OAuth2 登录：设置页完成登录后，MiQroForge 持有用户身份的
access_token（安全存储 + 到期自动刷新），供后续以用户身份调用 MiQroForge 业务接口
（如 #674 方案上传的 `dataUpload`）。

## 1. 使用方式

设置 → **MiQroForge 平台**：

| 路径 | 操作 | 适用场景 |
| ---- | ---- | -------- |
| 浏览器登录（推荐） | 点「浏览器登录」→ 应用内打开 MiQroForge 授权页 → 用户自行登录并点击「同意」→ 自动完成授权回到 MiQroForge | 默认首选，无需在 MiQroForge 输入密码 |
| 密码登录 | 填手机号 + 密码（RSA 加密后传输） | 无法弹窗/自动化场景 |

登录后展示昵称、用户名、脱敏手机号、access_token 到期时间与计划自动刷新时间；
「立即刷新」手动续期，「退出登录」清除 cookie 与 token。

**高级设置**（接入配置，按环境预填）：API 基础地址、client_id、client_secret、
redirect_uri。测试阶段 client_secret 有默认值（开箱即用）；生产环境必须填写在
MiQroForge 平台注册的 redirect_uri。

## 2. 代码位置

全部在主进程实现（不依赖 Python bridge）：

| 模块 | 职责 |
| ---- | ---- |
| `apps/desktop/src/main/qraft/client.ts` | OAuth2 协议客户端：平台登录（RSA）、authorize/doConfirm、code 换 token、refresh、userinfo；错误分类与重试 |
| `apps/desktop/src/main/qraft/rsa.ts` | RSA 公钥从登录页 `era-index-*.js` bundle 动态提取（grep `BEGIN PUBLIC KEY`）；PKCS#1 v1.5 加密（与 JSEncrypt 一致）；脱敏工具 |
| `apps/desktop/src/main/qraft/cookie-jar.ts` | 平台登录态 cookie（`Set-Cookie: Authorization=<uuid>`）解析与携带 |
| `apps/desktop/src/main/qraft/store.ts` | 登录态经 Electron safeStorage 加密落盘（userData/qraft-auth.json，0600）；不可用时降级 Base64 并告警 |
| `apps/desktop/src/main/qraft/service.ts` | 登录态编排：登录/登出/自动刷新调度（到期前 15 分钟刷新；瞬时失败 30 分钟重试并引导重登；refresh_token 失效为永久错误，停止自动重试并引导重登）；生产环境强制注册 redirect_uri |
| `apps/desktop/src/main/qraft/ipc.ts` | IPC 处理 + 浏览器登录窗口（独立 partition、导航白名单、回调 code 拦截、登录态 cookie 轮询带回授权） |
| `apps/desktop/src/renderer/features/settings/components/QraftPage.tsx` | 设置页 UI（表单/账号展示/错误指引） |

IPC 通道：`qraft:login` / `qraft:browserLogin` / `qraft:status` / `qraft:refresh` /
`qraft:logout` / `qraft:pointsBalance` + 事件 `qraft:statusChanged`（登录态变化
与积分余额缓存更新时推送，payload 即 `QraftStatus`，含可选 `points` 字段）。

## 3. 登录流程

### 3.1 密码登录（API 路径）

```
① 提取公钥   GET {origin}/login → 解析 era-index-*.js → BEGIN PUBLIC KEY
② 平台登录   POST /api/portal/auth/login（密码 RSA PKCS#1 v1.5 加密 + Base64）
             → Set-Cookie: Authorization=<uuid>（OAuth 依赖 cookie 而非 header）
③ 发起授权   GET /api/oauth2/authorize（redirect_uri 必填、不传 state、
             scope=openid,userinfo,oidc）
④ 确认授权   POST /api/oauth2/doConfirm（授权页 accept=1 按钮修复前必须走此接口）
⑤ 取授权码   再次 GET authorize → 302 Location 中的一次性 code
⑥ 换 token   POST /api/oauth2/token（grant_type=authorization_code）
⑦ 用户信息   GET /api/oauth2/userinfo（Bearer token；实测无 picture 字段）
```

### 3.2 浏览器登录

```
主进程打开独立 partition 窗口加载 authorize URL
  → 未登录：302 到平台登录页，用户自行登录（实测平台 SPA 登录后停留首页，
    主进程轮询 Authorization cookie，出现即主动 loadURL(authorize) 带回）
  → 授权确认页：用户点击「同意」（MiQroForge 修复后可用）
  → 302 → redirect_uri?code=xxx：will-redirect/did-navigate 拦截（仅当
    origin+path 与注册 redirect_uri 完全一致才提取 code），关窗
  → code 换 token + userinfo → 完成登录
```

安全约束：授权窗口 `will-navigate` 白名单（仅 MiQroForge origin），`window.open`
一律拒绝；回调 code 拦截后立即销毁窗口，redirect_uri 不做真实 HTTP 服务。

## 4. 与官方文档的实测差异（实现依据）

| # | 项 | 实测行为 | 实现 |
| - | - | - | - |
| 1 | authorize 的 redirect_uri | 必填 | 恒传；生产环境强制注册值 |
| 2 | 授权确认 | 页面 accept=1 按钮曾无效（已修复），doConfirm 接口有效 | 密码路径走 doConfirm；浏览器路径走页面同意 |
| 3 | state 参数 | 传了报「多次请求的 state 不可重复」 | 恒不传 |
| 4 | access_token 有效期 | expires_in=7199（约 2 小时，非官方 24 小时） | 到期前 15 分钟自动刷新 |
| 5 | refresh_token | 轮换：刷新成功后旧值立即失效（响应携带新值）；平台升级可能作废存量 token | 按响应持久化新值；refresh_token 失效分类为 `REFRESH_TOKEN_INVALID`，停止自动重试并引导重登 |
| 6 | userinfo 响应 | 无 picture 字段 | 界面只展示 nickname/username/sub |
| 7 | IP 白名单 | 未加白出口统一 nginx 403 | 分类提示「出口 IP 未加白，请联系 MiQroForge 管理员」 |
| 8 | 网络抖动 | 随机超时（HTTP 000） | 自动重试 3 次指数退避 |

## 5. 凭据与安全

- **token/cookie**：Electron safeStorage 加密落盘（Windows DPAPI / macOS
  Keychain）；安全存储不可用时降级 Base64 并写 WARN 日志；退出登录即清空。
- **密码**：仅经 IPC 提交主进程，RSA 加密后传输；前端不落存储、不打日志。
- **日志脱敏**：token/code 只记首尾片段（`maskSecret`）；手机号脱敏展示。
- **client_secret**：测试阶段测试/生产环境均提供硬编码默认值（开箱即用），
  可分别经 `QRAFT_TEST_CLIENT_SECRET` / `QRAFT_PROD_CLIENT_SECRET` 环境变量
  覆盖；转正式接入前应移除默认值。生产环境 secret 也可在高级设置填写。
- **授权窗口**：独立 `qraft-login` partition（无持久化），完成后清理，
  不残留平台登录态。

## 6. 给 Skill/Agent 提供 token（收敛通道设计）

> 本节是 [#674](https://github.com/14790897/MiQi/issues/674) 凭据方案收敛目标的
> 设计落地说明，供 Skill 侧 `auth.py` 实现时对齐。

### 已实现：token 文件（方案 A）

主进程在登录成功、自动/手动刷新成功后，将
`{ "accessToken": "…", "expiresAt": <epoch 毫秒>, "baseUrl": "…" }` 写入
**`<workspace>/.qraft/token.json`**（0600 权限，包含 accessToken、expiresAt、
baseUrl 元数据，不含 refresh_token）；退出登录即删除；应用启动恢复登录态时
同步重写。`baseUrl` 记录登录时的平台 API 基础地址（Slurm 作业扣费由
Desktop 主进程用其登录态直接发起，不依赖本文件）；Skill 侧 `auth.py`
计划只读前两个字段（#674 后续落地），多出的 baseUrl 无影响。

- 沙箱可达性：KUN 沙箱将自定义 workspace bind-mount 到
  `/home/miqi/workspace`（bwrap.py），沙箱内 Skill 直接读
  `/home/miqi/workspace/.qraft/token.json`；
- 安全权衡：明文 2 小时 token + 0600 + 登出删除；workspace 本就是 agent
  任意执行代码的信任域，风险可接受。方案 B（bridge 反向调用，不落盘）
  需要改核心协议（当前只有主→Python 请求、Python→主事件，无反向 RPC），
  作为长期目标暂不做。

### auth.py 的读取策略（Skill 侧，待 #674 落地）

1. 读取 token 文件 → 存在且 `expiresAt - now > 5min` → 直接使用；
2. 已过期/不存在 → 若配置了凭据（env）→ 走自管登录兜底；
3. 否则提示用户「请到 设置 → MiQroForge 平台 完成登录」，不阻断流程。

### 实施顺序

1. ~~Desktop 侧：token 文件写入/删除~~（已实现，随 #728 叠放 PR 交付）；
2. Skill 侧（#674 后续）：`auth.py` 优先读 token 文件，自管凭据降级为兜底；
3. 稳定后删除 Skill 自管凭据，auth.py 收敛为纯「取 token + 过期检测」。

## 7. 平台积分计费（Slurm MCP 作业扣费）

> 产品规则（2026-09-03，#927）：**调用 slurm MCP 时，slurm 作业运行
> 每次扣 10 积分**；普通对话与本地任务不扣积分。扣费请求由 **Desktop
> 发起**（memo 携带作业信息形成扣费记录），余额不足时阻止作业提交。

### 扣费接口（OAuth2 第三方接入指南）

| 接口 | 说明 |
| ---- | ---- |
| `POST /oauth2/points/deduct` | body `{amount, source, resourceType?, project?, memo?}`；业务码 `200` 成功 / `40003` 积分不足 / `40101·40102` token 失效；成功返回扣后余额 `PointBalanceVO` |
| `GET /oauth2/points/balance` | 查询余额（availablePoints / heldPoints / totalEarned / totalSpent） |

### 扣费握手（Python 侧：`miqi/agent/billing_resolver.py`）

slurm MCP 工具（服务器名含 "slurm"）实际执行前，`MCPToolWrapper.execute`
发起「扣费请求-决议」握手（镜像 user_input_resolver 模式）：

- **流程**：MCP 工具执行前 → `request_charge` 经会话发射器向 Desktop 发出
  `slurm_job_charge_request`（含 charge_id/工具名/参数摘要/会话上下文）→
  等待 Desktop 决议（25s 超时 fail-closed）→ 放行执行或返回「[计费阻止]」
  消息阻止作业提交；
- **接线**：bridge 的 chat.send drain 按 session_key 注册
  `set_billing_charge_emitter`；Desktop 决议经 bridge 请求
  `billing.slurmResolve` 回传（带会话归属鉴权，镜像 userInput.resolve）；
- **作业 ID 回传**：作业提交成功后从工具输出提取作业 ID（`Submitted batch
  job N` / `JobId=N` / `slurm-N`），经 `slurm_job_charge_enrich` 事件回传
  Desktop 补进扣费历史；
- **无 Desktop 通道**（headless/CLI）时 slurm MCP 调用按 fail-closed 阻止。

### 扣费与历史（Desktop 主进程）

- `QraftService.chargeSlurmJob`：扣 10 分（`source=slurm-job`、memo 携带
  {jobId, tool, args, session, turn}），token 失效先刷新重试一次；去重键
  charge_id（同一作业提交尝试只扣一次，历史文件持久化跨重启不重复扣费）；
- 历史持久化：`userData/qraft-billing-history.json`（上限 200 条，含
  billed/insufficient/error 三种状态），设置页经 `qraft:billingHistory`
  IPC 展示（作业 ID、参数摘要、扣分、扣后余额、时间）；
- 聊天区提示复用 points 事件流（`stream=points`，billed=安静活动行、
  blocked=醒目错误行）；
- 余额缓存：扣费成功后 deduct 响应余额直接更新 `status.points` 并推送
  `qraft:statusChanged`。

### 余额展示（设置页）

设置 → MiQroForge 平台 登录后展示可用积分（含累计获得/支出）与扣费历史；
主进程 `qraft:pointsBalance` IPC 拉取并缓存，余额变化经 `qraft:statusChanged`
事件推送。主进程 `QraftClient` 同时实现 `getPointsBalance` /
`deductPoints`（含业务码分类：`40003 → INSUFFICIENT_POINTS`、
`40101/40102 → SESSION_EXPIRED`）。

## 8. 测试

| 层 | 命令 | 说明 |
| -- | ---- | ---- |
| 单测 | `cd apps/desktop && npx vitest run src/main/qraft` | mock 全流程/错误分类/重试/假时钟自动刷新/safeStorage 往返/脱敏 |
| Smoke | `npx playwright test --config=playwright.config.ts --project=smoke issue-726-qraft.spec.ts` | 设置页 UI（mock bridge） |
| Electron E2E（离线） | `npx playwright test tests/e2e/qraft-login.spec.ts --config=playwright.config.ts --project=electron` | 真实主进程：表单/错误分类/预置登录态与退出清盘；零网络依赖，CI 必跑 |
| Electron E2E（真实环境） | `QRAFT_PHONE=… QRAFT_PASSWORD=… npx playwright test tests/e2e/qraft-browser-login.spec.ts --project=electron` | 打开真实 MiQroForge 页面完成登录全链路；CI 未配凭据自动跳过 |
| live 集成 | `QRAFT_LIVE=1 QRAFT_PHONE=… QRAFT_PASSWORD=… npx vitest run src/main/qraft/live.integration.test.ts` | 平台登录→授权→token→userinfo→refresh 直连测试环境 |

## 9. 已知限制与后续计划

- token 文件通道（第 6 节方案 A）已实现；Skill 侧 `auth.py` 的读取与收敛
  是 #674 的后续步骤；
- 测试阶段 client_secret 为硬编码默认值（测试/生产环境），转正式接入前移除
  （types.ts 已标注）；生产环境仍必须填写注册的 redirect_uri；
- MiQroForge 授权页修复后，密码路径仍保留 doConfirm 流程（对已确认授权用户两者
  等价；对未确认用户 doConfirm 依然可用，多一条兜底路径）。
