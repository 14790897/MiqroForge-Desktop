---
name: slurm-mcp-billing-map
description: Slurm MCP 作业计费架构地图——Python 握手桥 + Desktop 扣费发起方、10 分/次、charge_id 去重、扣费历史、capabilities 白名单坑
type: project
---

Slurm MCP 作业计费（issue #927，PR #936）：计费触发点为「slurm MCP 作业运行时每次扣 10 积分」，扣费请求由 **Desktop 主进程发起**。2026-09-04 live E2E（真实凭据 + 真实集群）两次实测通过。

**架构**：
- Python 握手桥 `miqi/agent/billing_resolver.py`（镜像 user_input_resolver 模式）：slurm MCP 工具（服务器名含 "slurm"）执行前，MCPToolWrapper 经会话发射器发 `slurm_job_charge_request`（charge_id/工具名/参数摘要/会话上下文）→ 等 Desktop 决议（25s 超时 fail-closed）→ 放行或「[计费阻止]」；无 Desktop 通道（headless/CLI）阻止。作业提交成功后从输出提取作业 ID（Submitted batch job N / JobId=N / slurm-N 3+ 位）经 `slurm_job_charge_enrich` 回传
- bridge/loop.py：drain 注册 `set_billing_charge_emitter`；`billing.slurmResolve` 方法（带会话归属鉴权 + protocol_specs.BILLING_SLURM_RESOLVE，注意新增方法必须带 spec，否则 phase72 audit 的 legacy 计数会超）
- Desktop：`QraftService.chargeSlurmJob`（10 分、source=slurm-job、memo={jobId,tool,args,session,turn}；charge_id 去重 + 历史文件跨重启不重复扣；token 失效先 refreshNow 重试一次）；历史 `userData/qraft-billing-history.json`（200 条上限，billed/insufficient/error），设置页展示；聊天区提示复用 points 事件流（「本次任务已扣 N 积分 · 可用余额 M」）
- orchestrator 对 `mcp_` 工具注入 `_session_key/_turn_id/_tool_call_id`（MCPToolWrapper pop 掉，不传给 MCP 服务端）

**关键坑（#936 live E2E 暴露，已修）**：桌面主路径 `miqi/runtime/` 的 TurnRunner 经 `CapabilityResolver.resolve` 按 agent 静态白名单（agent_registry 的 available_tools）过滤 registry——用户配置的 MCP 工具（`mcp_<server>_<tool>`）**从未进入模型请求**，DeepSeek 只能幻觉裸名调用（submit_slurm_job）。修复：capabilities.py 放行 `mcp_`/`use_` 前缀（用户 opt-in 能力不受白名单约束），tests/runtime/test_capabilities.py 有穿透用例。注意 KUN runtime（kun_runtime/loop.py）不经过这个白名单，但桌面不用 KUN。

**Live E2E**（`apps/desktop/tests/e2e/billing-live.spec.ts`，opt-in 凭据注入，CI 无凭据跳过）：真实登录 → 注册名提示词提交 hostname 作业（只提交一次，避免模型多提交）→ 轮询到 RUNNING → 扣费提示断言即截图（提示是持久消息行但可能被收尾回放清掉，最终截图拍不到）→ DONE_SLURM。每轮真实消耗 10 积分 + 1 次集群作业。运行环境：slurm MCP 本地服务器（见下）+ junction node_modules + `npm run build`（**不要 junction out/**，否则桥接跑主仓库 Python）。

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

**移除**：#915 的通用计费闸门（orchestrator/tool_host/services 挂载、PointsBillingEvent、miqi/kun_runtime/billing.py + 60+ 测试、hot_reload billing tier）。余额展示/points 事件流/QraftClient.deductPoints 保留复用。

**注意**：改协议方法后要 `uv run python -m miqi.runtime.protocol_snapshot` 重新生成 tests/fixtures/protocol/app_protocol_snapshot.v1.json；Python heredoc 里 `\b` 会被写成退格字符 0x08（用 Edit 工具写正则）。
