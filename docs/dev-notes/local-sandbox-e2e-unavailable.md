---
name: local-sandbox-e2e-unavailable
description: E2E 沙箱不初始化的原因是 dev config sandbox.enabled=false，不是机器问题——spec 里 patch enabled:true 后秒级就绪
type: project
---

2026-09-03 排查更正（用户纠正过）：本机沙箱完全可用，生产 app 一直在用（WSL distro `AIShadowSandbox`）。E2E 里 `waitForSandboxReady` 永不 true 的根因是 **dev config（~/.miqi/config.json）里 `tools.sandbox.enabled = false`**——E2E 把这份 config 拷到临时 MIQI_HOME，桥的 `_ensure_sandbox_manager()` 直接置 "disabled"，`_init_sandbox_manager()` 早退、一条初始化日志都不打。打包版用另一份配置（沙箱开着，用户截图里的中文隔离报错只有 WSL 沙箱路径会抛）。

**How to apply:** E2E spec 需要沙箱时在 patchConfig 里 `config.tools.sandbox.enabled = true`——distro 已存在，`initialize()` 只查 bwrap 可用性，**秒级就绪**（实测 "Sandbox ready after 0s"）。不 patch 就永远 disabled，别浪费时间等。相关：[e2e-inject-chat-events](e2e-inject-chat-events.md)

另：native（禁沙箱）路径下 read_file/write_file 的 PermissionError 全部被工具内 try/except 捕获转为结果字符串（filesystem.py:1283 "Error: 权限被拒绝"），不产生 ToolErrorEvent——只有 WSL 沙箱路径（_resolve_sandbox_path 在 try 外抛）才走 ToolErrorEvent 渲染链。
