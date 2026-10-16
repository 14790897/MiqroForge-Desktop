---
name: verify-workflow-shell-locally
description: 本地复跑 CI workflow 里 shell 逻辑的方法（抽取 run 块 + 下载 jq），以及 Windows 版 jq stdout 加 CRLF 的伪影
type: project
---


改 `.github/workflows/*.yml` 里的 shell 逻辑时，不必推上去等 CI：

1. 本机 **没有 jq**（Git Bash 无），从 `https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-windows-amd64.exe` 下到 `/tmp` 即用（ubuntu-24.04 runner 也是 1.7.1，行为一致）。
2. 抽取真实脚本而不是抄一份：`awk '/^        run: \|$/{f=1;next} f' wf.yml | sed 's/^          //'` 再替换掉 `${{ }}` 模板行、去掉最后的 curl，用假 event JSON（`jq -n` 造）跑用例。抄脚本会漏掉与真实文件的偏差。
3. **Windows 版 jq 的 stdout 会做 LF→CRLF 文本转换**（Linux 不会），比对字符串时会多出 `\r` 导致假失败；用 `printf 'a\nb' | jq -Rrs 'tojson'` 可确认字符串内容本身没被改，比对前 `tr -d '\r'` 归一化即可。
4. 校验要点：非 BMP 字符（emoji）用 `jq` 的 `.[0:n]` 按码点截断不会被切坏；`-R` 不加 `-s` 是逐行读，会吞换行。
5. **别拿命令替换出来的变量做长度/相等判断**：`$(...)` 会丢掉末尾换行，`[ "$BODY" != "$BODY_RAW" ]` 这类比较会把「超出部分是换行」的输入误判成没截断（PR #1088 被 CodeRabbit 抓到的真实 bug）。要判断长度就直接 `jq -e '((.comment.body // "") | length) > 100' "$GITHUB_EVENT_PATH"` 读原始事件 JSON。

**Why:** 2026-09-15 改 wecom-notify.yml 评论截断（2500 字节 → 100 字）时，靠这套本地复跑拿到 8/8 用例证据再提 PR（#1088），比推上去试错快得多；过程中被 jq 的 CRLF 伪影和 `-R` 逐行读坑掉两轮。

**How to apply:** 任何 workflow shell 改动都可照此验证；断言失败先怀疑本机 jq 的 CRLF/逐行读，再怀疑逻辑。相关：[pr-screenshot-mandatory](pr-screenshot-mandatory.md)、[skip-ci-suppresses-workflow-runs](skip-ci-suppresses-workflow-runs.md)
