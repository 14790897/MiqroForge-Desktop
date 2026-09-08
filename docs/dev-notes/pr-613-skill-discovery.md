---
name: pr-613-skill-discovery
description: "本地 Skill 前置索引的最终状态——全局提示词规则（#644）取代触发词（#643）与运行时注入（#642），一条规则即可让 AI 主动发现技能"
type: project
---

PR #625（本地 Skill 前置索引）的最终状态：
- **#642**（已合并后回退）：技能清单注入 + 意图匹配被 #644 取代，已 revert（#645），但 sandbox 读工具修复（根 workspace + session 隔离）保留
- **#643**（已关闭）：触发词机制被 #644 取代，无价值保留
- **#644**（最终方案）：全局提示词规则——`_build_main_prompt` 第 8 条静态规则（先查 Local Skills 清单、skill_manage 加载、未经核查不得否认）。**E2E 验证：一条规则即可让 AI 主动发现技能**（weather：skill_manage → curl wttr.in；workspace-cleanup：artifacts/ 规范），无需任何命中机制
- **#645**（revert）：回退 #642 技能注入，保留 sandbox 修复

**Why:** 用户发现最简单方案——全局记忆告诉 AI"先找技能"，比触发词/命中预载更有效。触发词（#643）和运行时注入（#642 技能部分）都被淘汰。

**How to apply:** 技能发现功能以 #644 为准。sandbox 修复（读工具根 workspace + session 隔离）在 #645 中保留。触发词设计（Triggers frontmatter、两阶段匹配）已废弃，不再使用。
