---
name: heredoc-escaping-pitfall
description: 用 heredoc 喂 Python 脚本做文件补丁会踩反斜杠转义坑，改用 Edit/Write 工具
type: feedback
---

在本机（Windows + Git Bash）用 `python - <<'EOF' ... EOF` 传脚本做字符串替换/文件补丁，多次踩坑：

1. **两层解析**：bash heredoc（`<<'EOF'` 原样、`<<EOF` 展开 `$`/`\`）→ Python 字符串（`\U`/`\b`/`\n` 是转义序列）——Windows 路径 `C:\Users\...` 在非 raw 字符串里触发 "truncated \UXXXXXXXX escape" SyntaxError
2. **静默失效**：replace 目标串反斜杠个数与文件实际内容对不上时，replace 静默不改动，看起来「已打补丁」其实没生效
3. **教训**：所有文件补丁/替换一律用 Edit/Write 工具（所见即所得、有匹配失败报错），heredoc 只用于无路径转义的纯执行型命令

**Why:** heredoc 的转义层让路径类字符串极易出错且失败静默，浪费多轮调试。
**How to apply:** 需要改文件时直接 Edit/Write；heredoc 脚本里若必须出现 Windows 路径，用 raw 字符串并验证 replace 的 assert。
