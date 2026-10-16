---
name: slurm-mcp-billing-map
description: Slurm MCP 作业计费架构地图——Python 握手桥 + Desktop 扣费发起方、10 分/次、charge_id 去重、扣费历史、capabilities 白名单坑
type: project
---


Slurm MCP 作业计费（issue #927，PR #936）：计费触发点为「slurm MCP 作业运行时每次扣 10 积分」，扣费请求由 **Desktop 主进程发起**。2026-09-04 live E2E（真实凭据 + 真实集群）两次实测通过。

**架构**：
- Python 握手桥 `miqi/agent/billing_resolver.py`（镜像 user_input_resolver 模式）：slurm MCP 工具（服务器名含 "slurm"）执行前，MCPToolWrapper 经会话发射器发 `slurm_job_charge_request`（charge_id/工具名/参数摘要/会话上下文）→ 等 Desktop 决议（25s 超时 fail-closed）→ 放行或「[计费阻止]」；无 Desktop 通道（headless/CLI）阻止。作业提交成功后从输出提取作业 ID（Submitted batch job N / JobId=N / slurm-N 3+ 位）经 `slurm_job_charge_enrich` 回传
- bridge/loop.py：drain 注册 `set_billing_charge_emitter`；`billing.slurmResolve` 方法（带会话归属鉴权 + protocol_specs.BILLING_SLURM_RESOLVE，注意新增方法必须带 spec，否则 phase72 audit 的 legacy 计数会超）
- Desktop：`QraftService.chargeSlurmJob`（10 分、source=slurm-job、memo={jobId,tool,args,session,turn}；charge_id 去重 + 历史文件跨重启不重复扣；token 失效先 refreshNow 重试一次）；历史 `userData/qraft-billing-history.json`（200 条上限，billed/insufficient/error），设置页展示 + 状态栏积分按钮点击弹层展示（弹层内「在设置中查看全部」跳设置页）；聊天区提示复用 points 事件流（「本次任务已扣 N 积分 · 可用余额 M」）。**2026-09-14（PR #1067）：登出不再删除历史与去重索引**，读取时按 `account.sub` 过滤、未登录返回空——旧行为让平台轮换 refresh_token 后重登的用户丢整份历史（排查路径见 [billing-history-missing-diagnosis](billing-history-missing-diagnosis.md)）
- orchestrator 对 `mcp_` 工具注入 `_session_key/_turn_id/_tool_call_id`（MCPToolWrapper pop 掉，不传给 MCP 服务端）

**关键坑（#936 live E2E 暴露，已修）**：桌面主路径 `miqi/runtime/` 的 TurnRunner 经 `CapabilityResolver.resolve` 按 agent 静态白名单（agent_registry 的 available_tools）过滤 registry——用户配置的 MCP 工具（`mcp_<server>_<tool>`）**从未进入模型请求**，DeepSeek 只能幻觉裸名调用（submit_slurm_job）。修复：capabilities.py 放行 `mcp_`/`use_` 前缀（用户 opt-in 能力不受白名单约束），tests/runtime/test_capabilities.py 有穿透用例。注意 KUN runtime（kun_runtime/loop.py）不经过这个白名单，但桌面不用 KUN。

**Live E2E**（`apps/desktop/tests/e2e/billing-live.spec.ts`，opt-in 凭据注入，CI 无凭据跳过）：2026-09-07 起改为**平台托管网关（<平台托管网关地址>/sse，SSE+insecure_http opt-in）+ mock LLM 状态机驱动**（scripts/mock_slurm_billing.py：提交 sleep 600 长驻作业→轮询到 RUNNING→DONE_SLURM）——真实 LLM 经 AI 网关路由后单步推理数分钟、方差大无法收敛，且模型非被测对象。mock 生效前提：清空 `config.desktop.providerActivation`（否则内置激活强制官方端点）+ 登录后剥离 token 文件 `aiGateway` 块（否则 make_provider 走平台网关真实模型）。实测 50.9s 通过：余额 820→810、totalSpent 180→190。**SSE 工具调用挂起 bug**（#951 修复）：SDK 默认流读 300s、streamable-http httpx 无超时——`_connect_one_server` 已收紧（connect 10s / 读= tool_timeout+30 下限 60s），任何网络停摆 ~2 分钟变异常不再永久卡死。每轮消耗 10 积分 + 1 个集群作业（长驻作业跑完记得 cancel）。

**slurm MCP 部署（方式 A：SSH 密钥）**：
```bash
cd <本地 slurm-skill 目录>/src
MCP_API_KEY="<服务端密钥>" SLURM_HOST="<集群 SSH 地址>" \
SLURM_USER="<SSH 用户名>" \
SLURM_SSH_KEY="<SSH 私钥路径>" \
SSH_KNOWN_HOSTS="<known_hosts 路径>" \
../.venv/Scripts/python slurm_mcp.py --transport streamable-http --host 127.0.0.1 --port 9000
```
SSH host key 需 known_hosts 文件（RejectPolicy）；SLURM_USER 必填；API Key 见服务端 env。端口 9000 被占说明旧进程还活着（先 netstat 查再决定重启）。

**测试账号**：18500000000 / 1q2w3e4R（client_secret 默认 miqi123456；2026-09-04 余额 830，totalSpent 160——每次 live E2E 扣 10，需平台侧重置）。查余额：auth.py（QRAFT_PHONE/QRAFT_PASSWORD 自管登录）取 token → GET /oauth2/points/balance。

**计费触发状态（2026-09-11 修复）**：扣费触发条件原为「仅观测到 `state=RUNNING`」——**快作业**
常在两次轮询间从 PENDING 直接到 COMPLETED、永不被观测到 RUNNING → **漏扣**（实测 Job 12136572
跑完积分不变）。已修为 `_CHARGEABLE_JOB_STATES = {RUNNING, COMPLETED}`（`mcp.py`）——**只有
RUNNING/COMPLETED 扣费**；PENDING/FAILED/TIMEOUT/CANCELLED 不扣（产品确认：未成功完成/未运行不
计费）。PR #1037 含修复 + 单测 + spec 改自然提示词（「使用slurm提交任意一个任务」）并断言扣分。

**移除**：#915 的通用计费闸门（orchestrator/tool_host/services 挂载、PointsBillingEvent、miqi/kun_runtime/billing.py + 60+ 测试、hot_reload billing tier）。余额展示/points 事件流/QraftClient.deductPoints 保留复用。

**注意**：改协议方法后要 `uv run python -m miqi.runtime.protocol_snapshot` 重新生成 tests/fixtures/protocol/app_protocol_snapshot.v1.json；Python heredoc 里 `\b` 会被写成退格字符 0x08（用 Edit 工具写正则）。

**去重与展示的账号口径（PR #1067）**：`charge_id` 由 bridge 每次工具调用 `uuid4().hex` 生成（跨账号不可能重复，按其去重永远安全）；复合作业键 = `sub::server::jobId`，**空 sub（浏览器登录 userinfo 失败的兜底态）不构键**——否则 `::server::jobId` 会在账号间串用。历史展示 `accountSub === undefined`（加字段前的老记录）或等于当前 sub；空 sub 与未登录一律返回空。
