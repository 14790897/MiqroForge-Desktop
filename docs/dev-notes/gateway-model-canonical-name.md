---
name: gateway-model-canonical-name
description: 平台聚合网关的规范模型名是 deepseek-flash（deepseek-v4-flash 只是别名）——CI/终端命令按规范名写
type: reference
---


2026-09-30 实测 `new.sixiangjia.de`（CI 用的聚合网关，`DEEPSEEK_API_BASE` / `SIXIANGJIA_API_KEY` / PR-Agent 的 `OPENAI.API_BASE` 都指向它）：`GET /v1/models` 同时列出 `deepseek-flash` 和 `deepseek-v4-flash`；但发 `deepseek-v4-flash` 的 chat 请求时，响应里的 `model` 字段回的是 `deepseek-flash` —— 规范 id 是 `deepseek-flash`，`v4-flash` 只是别名。用户明确要求 CI 统一用 `deepseek-flash`（PR #1229）。

**How to apply:** 改 CI（`.github/workflows/*`）或终端直连网关的模型名时写 `deepseek-flash`。注意这是**网关层**的名字；应用内部的模型 id 仍是带 provider 前缀的 `deepseek/deepseek-v4-flash`（`miqi/providers/registry.py` 的 `PROVIDER_TEST_MODELS`、`gateway.py` 的 `GATEWAY_MODEL`、前端 `providers.ts` 下拉），那是另一层，别顺手一起改。相关：[invoke-github-workflow-skill](invoke-github-workflow-skill.md)、[pr-screenshot-mandatory](pr-screenshot-mandatory.md)。
