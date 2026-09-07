---
name: use-luma-mcp-for-images
description: 读图优先用 mcp__luma-mcp__image_understand，不要用 Read 工具读图片
type: feedback
---

读取图片时必须使用 `mcp__luma-mcp__image_understand` 工具，不要用 Read 工具直接读图片。

**Why:** 在本环境中 Read 工具读取 PNG 图片返回 `[Unsupported Image]`（实测 session-key-mapping 截图 02-file-in-session-dir.png 和 interface.png 均失败），而 luma-mcp 图像理解工具读取同一张图完全正常，还能按 ocr/ui/debug 等任务类型分析。

**How to apply:** 遇到截图、界面图、报错图、OCR 等需要看图的任务时，直接用 `mcp__luma-mcp__image_understand`，image_source 传本地路径或 URL 或粘贴路径，prompt 直接传用户原始问题。Read 工具仅用于读文本/代码文件。

**排障提示（2026-08-08 修复）：** 如果会话里没有 `mcp__luma-mcp__*` 工具，但 `claude mcp list` 显示 Connected，检查 `~/.claude.json` 的 `mcpServers.luma-mcp` 是否带 `"disabled": true`——该标志会阻止工具加载进会话但不会让健康检查失败。已移除该标志。注意服务本身配置正确时能正常工作（含 `TEMPERATURE=1`、`CUSTOM_MODEL_NAME=<本地模型名>`），改配置后需重启会话生效。
