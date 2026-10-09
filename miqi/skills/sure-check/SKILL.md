---
name: sure-check
description: "Verify an AI-built project with the local SURE checker (MCP tools, e.g. mcp_sure_sure_check). Runs a read-only project check and reports what was checked, what was NOT checked, and what was found. Use when the user asks whether a project actually works, is finished, or can be handed off (能否交付、做完了吗、帮我核查项目、AI 说做完了是不是真的), or before claiming a software project is done."
metadata: {"miqi":{"emoji":"🔎"}}
---

# SURE project check

SURE 是本地核查引擎:对项目做**只读**检查,输出「已核查 / 未检查 / 发现的问题」,并可生成修复契约。**结论一律以 SURE 的报告为准,不要自己下「没问题」的结论。**

## 步骤

1. 确认工具可用:应存在 `mcp_sure_sure_check`(MCP server `sure`)。**没有就直说「SURE 未安装/未接入,无法核查」,不要假装跑过**(SURE 为 per-user 安装,如 `%LOCALAPPDATA%\SURE\bin\sure.exe`)。
2. 确定项目**绝对路径**:优先用户点名的项目;没点名就用当前会话工作区。
3. 调用 `mcp_sure_sure_check`,`project` 必须传上述**绝对路径**——省略 `project` 时 SURE 会核查它自己的启动目录(不是用户项目)。
4. 报告里出现「未检查(NOT CHECKED)」的阶段,要明确指出这些阶段**没跑过**,不能说成通过。

## 如实转述(硬规则)

- 状态原样保留:`NOT CHECKED` / `skipped` / `unknown` **不等于**通过;不得把「SURE 没检查到」说成「项目没问题」。
- `exit_code 1 / outcome not_green` **不是工具故障**:SURE 的语义是「查了,但证据不足以判干净」——照实告诉用户。
- 报告很长时,先贴**结论区**(开头到 findings / "No open findings"),细节按需补充;不要改写结论措辞。
- capability tier 按报告原文说明(0 = 只看项目现状,看不到 AI 的工作过程)。

## 有发现时

用大白话解释每条发现与后果;征得用户同意后进入 `sure-fix`(修复契约 → 修复 → recheck)。
