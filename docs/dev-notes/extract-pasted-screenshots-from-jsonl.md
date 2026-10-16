---
name: extract-pasted-screenshots-from-jsonl
description: 用户粘贴到对话里的截图不落盘，可从会话 JSONL 提取 base64 再上传到 _gh-imgup（issue/PR 配图用）
type: reference
---


用户贴在对话里的截图（Ctrl+V）**不会在 Temp 目录留文件**，`find %TEMP% -newermt today *.png` 找不到。
图片以 base64 内联在该会话的 transcript 里，可直接提取：

`C:\Users\<user>\.claude\projects\<项目目录名>\<sessionId>.jsonl`（项目目录名 = 工作目录路径转义，
worktree 会话是 `C--git-program-test-MiQi-Desktop--claude-worktrees-<name>`；sessionId 取该目录下**最新 mtime** 的 jsonl）

逐行 `json.loads`，取 `rec["message"]["content"]` 里 `type=="image"` 的 block，
`block["source"]["data"]` 是 base64，按 `media_type` 后缀写文件即可。

**Why:** 2026-09-18 建 issue #1131 时要在 issue 里配用户的两张报错截图，Temp 和 .claude 目录都没有对应文件，
只有从 JSONL 里能拿到原图。

**How to apply:** 用户粘贴截图并要求「贴到 issue/PR」「上传截图」时，先按上述路径提取再走 `_gh-imgup`
上传（流程见 github-workflow 技能）；提取脚本本身很短，写成一次性 python heredoc 即可（无转义风险，
但按 [heredoc-escaping-pitfall](heredoc-escaping-pitfall.md) 仍不要把含引号/反斜杠的复杂逻辑塞进 bash heredoc）。
