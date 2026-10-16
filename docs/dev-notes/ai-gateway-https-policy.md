---
name: ai-gateway-https-policy
description: "#922 AI 网关地址现状与 https 强制策略——默认 http 仅测试环境,显式 QRAFT_GATEWAY_BASE 强制 https"
type: project
---


AI 网关(#922,PR #943)地址策略(2026-09-04 定稿,PR 已 squash 合并):

- **平台无 HTTPS 网关域名**:https 裸 IP(118.25.115.164)证书 SAN 为腾讯云 API 网关共享域不覆盖 IP;forge.miqroera.com 公网 NXDOMAIN(#923 实测)。
- **Python 侧策略**(`miqi/providers/gateway.py`):`QRAFT_GATEWAY_BASE` 显式配置强制 https,非 https 回退直连并 loguru 警告;默认 http IP 仅限 test env 实测。生产经 https 域名下发后切换(模块注释已写明)。
- CodeRabbit 曾要求"HTTPS 作默认值"——无此域名存在,回复说明后保留 http 默认 + 显式校验,已被接受。
- **后续**:平台提供带证书真实域名后,经 `QRAFT_GATEWAY_BASE` 下发;#922 范围外的 configVersion 热刷新会下发网关地址与模型清单。
- 相关:[slurm-mcp-billing-map](slurm-mcp-billing-map.md)(同属平台接口),[pr929-review-findings](pr929-review-findings.md)(CodeRabbit 处理风格)。
