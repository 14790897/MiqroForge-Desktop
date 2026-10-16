---
name: skill-copies-and-sync
description: e2e/github 技能存在三处副本（仓库 skills/、桌面 app 的 .claude/skills/*.md+zip、用户 ~/.claude/skills/），仓库为准；只改个人副本会丢
type: project
---


同一技能在机器上有三处副本，改一处不等于"技能改了"：

1. **仓库 `skills/<name>/SKILL.md`** —— 权威源（`skills/README.md` 明写「与本地个人技能同步时，以本仓库为准」）
2. **`~/.claude/skills/<name>/SKILL.md`** —— 会话实际加载的那份（只有它生效）
3. **桌面 app 项目级 `<repo>/.claude/skills/<name>.md` + 同名 `.zip`** —— app 侧导入产物，也是 `anthropic-skills:*` 那套的来源；未纳入 git

**Why:** 2026-09-11 用户否定「截图拼帧录屏」后，纠正只写进了个人副本（#2），仓库那份（#1）仍教旧做法；app 副本（#3）同样是旧版。2026-09-22 发现时三份已经互相漂移，且权威源恰是过时的那份 —— 别人 clone / 新会话按 README 从仓库同步，会拿到被否定的做法。已提 PR #1194 把仓库 + 个人两份对齐（顺带补了后续踩的录屏坑）；app 侧副本仍待重新导入。

2026-09-30 更新：仓库与个人副本（`.cc-switch/skills/`，`~/.claude/skills/` 是它的软链）已重新对齐——
本轮按仓库内容覆盖个人副本（顺带补上此前滞后的 `~/.miqi` → `~/.forge` 路径改名），并用 `fc /b` 逐字节校验一致
（PR #1228，e2e 技能加「必须有真实 AI 测试」硬性规则）。**app 级副本（#3）仍是 7 月的旧快照**，需要时重新导入。

**2026-10-13 再次重新对齐**（顺带收尾 [miqro-org-migration-fallout](miqro-org-migration-fallout.md) 的旧 owner 引用）：三份 SKILL.md 又漂了——`e2e-test-workflow` 个人 15182 vs 仓库 21150 字节、`github-workflow` 个人 13199 vs 仓库 20237，且个人缺 `github-workflow/references/`。按仓库整份覆盖后四份文件逐一 `cmp` 字节一致。**同步会立刻生效**：会话里的技能 description 当场就换成了新内容（`github-workflow` 多出「贴图到PR/上传截图」等触发词，`e2e` 多出「real-LLM test requirement」）。
**注意**：仓库那份 `skills/github-workflow/SKILL.md` 当时带着 PR #1284（未合并）的改动，所以个人副本暂时领先 develop —— 若 #1284 被否，个人副本要回退重同步。

**How to apply:** 改技能时先想"这三处要不要一起动"。判断某技能当前生效内容，看会话里该技能 description（= 个人副本）；要与仓库核对时 `diff --strip-trailing-cr` 两边文件（仓库/个人常一 LF 一 CRLF，直接 diff 会把整篇算作改动）。只改个人副本来"记住纠正"是不可靠的，随时会被重新同步覆盖。

**同步的具体做法**（2026-10-13 实操）：`~/.claude/skills/<name>` 是指向 `~/.cc-switch/skills/<name>` 的 **symlink**，真正要写的是 `.cc-switch` 那一侧。个人副本不在版本控制里，**覆盖前先整目录备份**（我是 `cp -r` 到临时目录）。覆盖后逐文件 `cmp -s` 校验字节一致——这比 `diff` 可靠，能同时发现行尾差异（仓库是 LF，个人可能被写成 CRLF）。

相关：[e2e-direct-screen-recording](e2e-direct-screen-recording.md)（录屏方法本身）、[auto-create-pr-after-task](auto-create-pr-after-task.md)。
