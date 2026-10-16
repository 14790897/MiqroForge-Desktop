---
name: use-luma-mcp-for-images
description: 读图先用 Read 工具直接读（当前模型支持），Read 失败才回退 mcp__luma-mcp__image_understand
type: feedback
---


读图/截图时**先用 Read 工具直接读图片**；**只有 Read 返回 `[Unsupported Image]`（或读不出内容）时**，才回退到 `mcp__luma-mcp__image_understand`。

**Why:** 2026-09-11 实测——当前模型（Sonnet 5）用 Read 直接读 PNG 成功（能准确识别界面内容与文字）。此前的规则（一律用 luma）基于旧模型/旧环境 Read 返回 `[Unsupported Image]` 的观察，已过时。直接 Read 少一次工具往返、无需把图传给外部服务。

**How to apply:**
1. 需要看图时先 `Read <图片绝对路径>`。
2. 若报 `[Unsupported Image]` / 读不出内容 → 改用 `mcp__luma-mcp__image_understand`（`image_source` 传本地路径/URL/粘贴路径，`prompt` 传原始问题；支持 `task_type=ocr|ui|debug|describe`）。
3. luma 仍适合需要**结构化分析**（OCR 逐字提取、UI 结构、报错日志解析）的场景——即使 Read 能读，若需要这类结构化输出也可用 luma。

**排障提示（2026-08-08）：** 若会话里没有 `mcp__luma-mcp__*` 工具，但 `claude mcp list` 显示 Connected，检查 `~/.claude.json` 的 `mcpServers.luma-mcp` 是否带 `"disabled": true`——该标志会阻止工具加载进会话但不让健康检查失败。已移除该标志。
