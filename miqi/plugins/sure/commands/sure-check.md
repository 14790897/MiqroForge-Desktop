---
description: 用本地 SURE 核查指定项目(必须传项目绝对路径)
argument-hint: <项目绝对路径>
---

# sure-check

对「用户参数」中给出的项目**绝对路径**执行一次 SURE 核查:

1. 调用 `mcp_sure_sure_check`,`project` 必须显式传该绝对路径——省略时 SURE 会核查它自己的启动目录,而不是用户项目(运行时的参数级守卫也会拒绝缺路径/非绝对路径的调用)。
2. **如实转述**:`NOT CHECKED` / `skipped` / `unknown` 不等于通过;`exit_code 1 / outcome not_green` 不是工具故障(SURE 的语义是「查了,但证据不足以判干净」);长报告先贴结论区,不改写措辞。
3. 有发现时用大白话解释每条发现与后果;用户同意后按 `sure-fix` 流程进入修复(修复契约 → 修复 → `mcp_sure_sure_recheck` 通过才算修好)。

完整使用纪律见内置技能 `sure-check` / `sure-fix`。
