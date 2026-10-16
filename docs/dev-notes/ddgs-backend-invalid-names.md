---
name: ddgs-backend-invalid-names
description: "ddgs 9.14.4 中 backend=\"html\"/\"lite\" 是无效名会静默回落 auto——web.py/search_orchestrator 存量值是 no-op，PR #1023 因此返工"
type: project
---


ddgs 9.14.4（pyproject pin `ddgs>=9.14.4`，2026-07-24 起）的合法 backend 只有 `bing/brave/duckduckgo/google/grokipedia/mojeek/startpage/yandex/yahoo/wikipedia`；html.duckduckgo.com 端点引擎的键名是 **`duckduckgo`**。传 `backend="html"`/`"lite"` 时库打 warning "backends do not exist or are disabled" 后**静默回落 auto**（全 9 引擎随机 shuffle）。

**Why:** `miqi/agent/tools/web.py` 的 `backend="html,lite"`（#561/08-20 引入）和 PR #1023 新加的 `backend="html"`（含 search_orchestrator 扇出路径）都是 no-op，从引入起就没生效过；PR #1023 的根因对比表（html 3.1s/lite 22.4s/auto 20.0s）实际三行都在跑 auto，差异是 shuffle 抖动。2026-09-14 我实测确认（venv 里打印 ENGINES 键 + `_get_engines('text','html')` 回落 auto）后已发 review 要求返工。
**How to apply:** 涉及 ddgs/联网搜索的改动先实测 `DDGS()._get_engines('text', <name>)` 是否回落；引擎可达性随网络/ISP 变化（本机 2026-09-14 实测 yahoo/yandex 1.2s 可用、duckduckgo 端点 0 条、其余 fast-fail），别硬编码单引擎名。ddgs 的 timeout 是每批（`wait(futures, timeout=...)`）而非整体 wall-clock。相关：[check-competing-pr-before-issue](check-competing-pr-before-issue.md)——#1026（平台网关搜索 Gateway→...→DDGS）是该人群正解，DDGS 仅兜底。
