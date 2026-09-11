---
name: autonomous-action
description: 与 AI 协作的偏好：少问问题、自己做决定并直接执行，不要频繁抛选项征求确认
type: feedback
---

用户明确要求"少问我，自己做决定"。遇到可自行判断的情况，直接选择技术方案并执行，给出结果，而不是列出选项让用户挑。

**Why:** 在查 IP/代理问题时，用户对反复询问和确认感到不耐烦。

**How to apply:** 任务执行时自主决策并实施；只在真正阻塞（无法从代码/环境推断、高风险不可逆操作）时才询问。与 [invoke-github-workflow-skill](invoke-github-workflow-skill.md) 这类需要明确触发词的情况不同，常规技术任务默认自主完成。
