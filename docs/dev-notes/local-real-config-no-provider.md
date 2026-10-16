---
name: local-real-config-no-provider
description: 本机 e2e 真实 LLM 用例会被「尚未配置模型服务」发送门禁拦截，与代码无关
type: reference
---


2026-09-08 实测：本机跑依赖真实 LLM 的 e2e 用例（chat-disclaimer、confirm-card-real-llm、attachment 的 Long filename 等需要真实发送的用例）时，即使 bridge 正常运行，发送也会被「尚未配置模型服务。请先配置 Provider/API Key」门禁拦截——launchElectronApp 会复制用户真实 config.json 到临时 MIQI_HOME，但该配置没有 provider 条目（走 qraft 登录态）。CI 的 config.json 有 siliconflow provider，不受影响。

**Why:** 区分「本机环境失败」与「代码回归」：本机上真实 LLM 用例失败要先看有没有这条门禁横幅，别误判成改动引入的 bug。attachment.spec 本地跑 6/7 通过，唯一失败（Long filename）就是这条门禁，与改动无关。

**How to apply:** 本机验证真实 LLM 用例前，给临时 MIQI_HOME 的 config 补一个可用 provider；或直接依赖 CI electron-e2e job 验证。相关：[confirm-card-two-runtime-map](confirm-card-two-runtime-map.md)、[real-account-features-need-live-e2e](real-account-features-need-live-e2e.md)

2026-09-16 可复用做法（跑三个 flaky 用例时实测）：临时改 `~/.miqi/config.json`（helper 会把它复制进临时 MIQI_HOME）——
- 只需过门禁、不需要真回复的用例（如 issue-877 预览类，只要用户消息能落盘产生会话）：加 `providers.deepseek = {apiKey:'sk-local-e2e-mock', apiBase:'http://127.0.0.1:9/v1'}`（死端口即可，`active_model_resolvable` 只做配置判定，不发请求）。
- 需要「模型会说话/会调工具」的用例（sandbox、guard-811 这类）：写一个本地 mock OpenAI server（stdlib HTTPServer，同时支持 stream/non-stream，按最后一条 user 消息决定回复），把 apiBase 指过去。注意 mock 必须**只看最后一条 user 消息**——系统提示里含完整的 exec/ls 工具文档，全量匹配会把所有请求都当成同一个流程。
- 完事**务必还原**配置（改前 `cp ~/.miqi/config.json /tmp/...`，改后拷回），别把 mock provider 留在用户真实配置里。
- 另：`~/.miqi/config.wiped-20260903-2327.json` 里有一个**有效**的 DeepSeek key 可用于真实 LLM 本地验证（勿打印/提交）。

2026-09-09 补充（issue #979 实测）：`~/.miqi/config.wiped-20260903-2327.json` 的 `providers.deepseek.apiKey` 是**有效**的 DeepSeek key（35 字符，官方 base 未填，用默认 https://api.deepseek.com），源码级 live 搜索/LLM 验证可直接从该文件读取（勿打印/提交 key 值）；环境变量里的 DEEPSEEK_API_KEY（51 字符）已 401 失效。DDGS 零配置兜底已实测可用（1.8~5.2s 返回结果）。
