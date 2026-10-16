---
name: gateway-model-auto-readiness
description: "登录后默认模型自动就绪（#1172/PR #1173）：判空逻辑为何失效、active_model_resolvable 判据、smoke mock 默认登录态带网关的坑"
type: project
---


「平台登录后默认模型自动就绪」的实现与坑（2026-09-21，#1172 / PR #1173）：

**用户诉求**：平台登录后仍需手动去「设置 → 模型」选模型 —— 要自动。

**根因（关键坑）**：`GatewayModelAutoSync` 旧判定只在 `agents.defaults.model` 为**空串**时自动写网关模型，但 `config.get`（`miqi/runtime/config_handlers.py` 的 `config.model_dump(by_alias=True)`，无 exclude_defaults）会把 Python schema 默认值 `anthropic/claude-opus-4-5`（`miqi/config/schema.py`）序列化出来 —— **永远非空**，判空条件在生产中从不成立。全新安装的 config.json 由 main 进程 `config:write_initial` 写，只含 workspace，不含 model；Python `load_config` 缺文件只在内存返回 `Config()` 不落盘。**凡是"读 config.get 后判空/判默认"的逻辑都要先想到这层默认值兜底。**

**修复语义（用户拍板）**：登录 + `gatewayActive` 时，当前默认模型**不可解析**才替换为 `deepseek/deepseek-v4-flash`（`GATEWAY_MODEL_ID`）；可用模型（内置激活 / 已配置网关兜底）不动。可用性判据 = `providers.list` 的 `active_model_resolvable`（`miqi/runtime/provider_handlers.py _model_provider_resolvable`，含网关路由，与运行时 `factory.make_provider` 同一套判定）。undefined（旧版 bridge）时仅空值兜底。比较并设置 `expectModel=<当前值>` 保留用户间隙内的手动选择。

**测试坑**：smoke `mocks.ts` 默认 `qraftLoggedInStatus` 带 `aiGateway: {status:'active'}` + `qraft.login()` 调用 → 触发自动写入，会让「与网关无关」的用例（issue-137 断言 configUpdates 长度、issue-929 断言页头遗留模型）失败。解法：新增 `loggedInWithoutGateway()` 登录态（未下发网关）供无关用例使用。同类改动务必全量跑 smoke 排查。

**验证矩阵**：单测 12 项、smoke 全量、e2e `ai-gateway.spec.ts`（落盘+页头）、live `ai-gateway-live.spec.ts`（真实账号从 opus 遗留值出发 → 自动就绪 → 网关真实回复）。截图上传走 `_gh-imgup`。

**CI 差异坑（PR #1173 首次 CI 暴露）**：CI 的 e2e workflow 有 `Generate config.json` 步骤，往 `~/.miqi/config.json` 注入 `providers.siliconflow`（**is_gateway=true**，带 secrets 里的真实 key）——`_model_provider_resolvable` 的「已配置网关兜底」分支会把 `anthropic/claude-opus-4-5` 这类模型判为**可解析**（本机 providers 为空时才是 false）。所以依赖「模型不可解析」的 E2E 用例（自动就绪、发送拦截）在 CI 上会翻转结果。夹具要用 `custom/*`（判定硬编码 false，任何 provider 配置下都不可解析）而不是 anthropic 默认值。排查手法：Python 内联复现 `_model_provider_resolvable(config, model)` 两种配置的返回值。

相关：[real-account-features-need-live-e2e](real-account-features-need-live-e2e.md) [qraft-test-account-credentials](qraft-test-account-credentials.md) [platform-domain-migration](platform-domain-migration.md)（测试账号已在生产域名登录）
