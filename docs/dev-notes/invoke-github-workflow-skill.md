---
name: invoke-github-workflow-skill
description: "用户说\"创建pr\"\"pr模板\"\"创建issue\"\"上传截图/贴图到PR\"等触发词时，先调 github-workflow skill"
type: feedback
---

Always invoke the `github-workflow` skill when the user mentions any of its triggers ("创建PR", "pr模板", "创建issue", "commit规范", "CodeRabbit" 等). **截图上传（"上传截图"/"贴图到PR"/"e2e截图"/"截图评论"）也必须走该技能**——上传到仓库固定 `_gh-imgup` 预发布（release assets API，内容 hash 去重命名），以 `![](url)` 写进 PR 描述的「日志/验证证据」节，不发图片评论（少评论原则）。

**Why:** User corrected me — I knew the skill existed but didn't invoke it, instead writing PR/Issue descriptions manually then later updating them to match the template. Invoking the skill upfront loads the template and CI rules into context, so descriptions are written correctly the first time. For screenshots, the skill defines the `_gh-imgup` 机制（published 预发布，draft 资产对外 404 裂图）与"证据进 PR 描述"约定，不按技能做会贴错地方或裂图。

**How to apply:** When the user says any trigger phrase listed in the skill description (e.g. "创建pr", "pr模板", "创建issue", "上传截图"), call the skill before doing anything else — the skill's instructions tell me what to do next.
