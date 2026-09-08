---
name: dirty-api-key-httpx-crash
description: 用户曾在 API Key 字段粘贴 key 后输入中文备注「用这个」→ httpx 构建 Authorization 头报 UnicodeEncodeError
type: user
---

有用户习惯在 API Key 输入框里粘贴 key 后直接输入中文备注（如 `"sk-xxx  用这个"`）。这类脏 key 存进 config.json 后，httpx 构建 `Authorization: Bearer …` 头时按 ASCII 编码失败，报 `UnicodeEncodeError: 'ascii' codec can't encode`——发生在任何 HTTP 请求发出之前，掩盖真实报错。

**Why:** 用户用中文在字段里做自我备忘，不认为它会被当 key 的一部分。

**How to apply:** 排查该用户"报错失败/UnicodeEncodeError"类问题时，先检查 `~/.miqi/config.json` 的 providers.*.apiKey 是否带中文或空格。`miqi/config/schema.py` 的 `ProviderConfig._clean_api_key` 已在配置加载时自愈（去空白+去非 ASCII）。相关回归 E2E：`apps/desktop/tests/e2e/bridge-chinese-error.spec.ts`（注入脏 key 断言请求仍到达 mock）。stderr 编码层面见 [loguru-stream-sink-encoding](loguru-stream-sink-encoding.md)。
