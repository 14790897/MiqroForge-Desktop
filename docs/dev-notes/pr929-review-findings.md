---
name: pr929-review-findings
description: "PR #929（模型选择收口）max-effort 审查结论：15 条 finding 待修复，集中在 merged-model 门控与 resolvable≠usable"
type: project
---

PR #929（feat(desktop): 模型选择收口 #835，2026-09-03 已合并 develop）经 max-effort 审查（10 角度 + 14 验证 + 缺口扫描）共 15 条 finding，全部 CONFIRMED/PLAUSIBLE，未修复。核心问题类：

1. **安全**：内置企业共享密钥在聊天路径（factory.get_api_base）仍会发往遗留自定义 api_base，test 路径的钉端点守卫没覆盖聊天；收口后无任何应用内入口能清除遗留 base。
2. **merged-model 门控**（config_handlers.py:229）：按深合并后的当前模型校验，遗留 custom/*、裸名、本地 vllm/ollama 模型的用户所有 config.update 被拒且前端静默吞错；空字符串绕过门控；agents:null 抛 AttributeError 变 INTERNAL。
3. **可解析≠可用**：providers.update 可把注册表内但无凭据 provider 的模型写为默认 → _match_provider 兜底错发到错误 API（#602 类）；bedrock/* 无条件放行但无 spec。
4. **前端**：激活后保存空模型报 "No fields to update"；ModelSelect 清除 effect 清掉裸模型名（含激活自己写入的 fallback）；取消激活后状态陈旧+默认模型指向无密钥 provider；登录门控仅 UI 层。
5. **绕过**：config/batchWrite 与 setup.writeInitialConfig 完全没有模型门控。
6. **测试**：issue-137 smoke 测试被 PR 弄坏（旧 provider/model-name 占位符已删+登录门控），PR 只更新了 issue-185。

E2E 实证：登录后下拉显示「请选择模型…」占位符（裸名被清）已截图确认。修复时优先 1/2/3。相关：[confirm-card-issue-714-fix](confirm-card-issue-714-fix.md)（截图惯例）、[e2e-inject-chat-events](e2e-inject-chat-events.md)。
