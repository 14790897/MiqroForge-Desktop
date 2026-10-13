---
name: sure-fix
description: "Fix findings from a SURE project check via the bounded repair contract (MCP tools, e.g. mcp_sure_sure_get_repair / mcp_sure_sure_recheck): fetch the contract, make the repair, then re-check until the contract's checks pass. Use after sure-check reports findings, or when the user asks to 修复 SURE 发现的问题 / 按修复契约修一下。A fix is only verified by re-check — your own completion message is not evidence."
metadata: {"miqi":{"emoji":"🛠️"}}
---

# SURE repair loop

修复契约是 SURE 给出的**有边界的修复说明**:问题是什么、修复必须保留什么、验收标准是什么。修复以契约为界。

## 步骤

1. 确认工具可用(`mcp_sure_sure_get_repair`、`mcp_sure_sure_recheck`);没有就说清无法走修复流程,不要假装。
2. **取契约**:调用 `mcp_sure_sure_get_repair`,`project` = 项目**绝对路径**(必须显式传;省略时 SURE 用的是它自己的启动目录)。
3. **按契约修复**:只修契约点名的问题;契约里「必须保留」的部分不许破坏;不做契约之外的顺带重构。
4. **修复后必须复核**:调用 `mcp_sure_sure_recheck`(同项目路径)。只有当契约点名的检查项在 recheck 里全部通过,相关发现才算关闭。
5. **如实汇报**:recheck 未通过就直说;残留发现(residual)与未检查项原样保留,不美化、不隐藏。

## 硬规则

- **你自己的完成语不算证据**——只有 recheck 通过才算修好。
- recheck 报告里的 `NOT CHECKED` / `not_green` 语义与 `sure-check` 相同:如实转述。
