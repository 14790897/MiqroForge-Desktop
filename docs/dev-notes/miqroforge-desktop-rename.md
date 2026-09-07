---
name: miqroforge-desktop-rename
description: 规范拼写为 MiQroForge（大写 Q、大写 F）— 书面/界面/文档一律用新名，平台名同步更名
type: project
---

2026-08-20 起，产品更名为 MiqroForge Desktop（#780，PR #782 落地）。2026-08-21 issue #786 确认唯一规范拼写为 **MiQroForge**（大写 Q、大写 F），外部平台名（原 Qraft / microforge）同步更名，已写入 CONTRIBUTING.md Naming Convention。

**Why:** 旧名 "MiQi Desktop"/"Qraft" 与新品牌不一致，继续混用会造成内外沟通误差。issue #786 标题与需求明确大写 Q、大写 F。

**How to apply:**
- 书面/文档/issue/PR 描述一律写 "MiQroForge"（注意大写 Q、大写 F）。2026-09-03 完成全仓大小写对齐：245 个跟踪文件 + git mv docs/brand/MiqroForge元素组成.txt（小写 q 残留已清零），与平台标签更名合并提交为 PR #916（分支 chore/brand-miqroforge-alignment，CI 17 项全绿）
- 对齐时保留：CHANGELOG 历史条目 2 处、仓库 URL github.com/14790897/MiqroForge-Desktop（package.json + 隐私政策 ×2）、消息通道标识 channel="miqroforge"、组件名 MiQroForgeLogo（本已规范）；全大写横幅 MIQROFORGE DIAGNOSTIC 已改为 MiQroForge
- 2026-09-03 同日完成「平台」标签对齐：界面文案（设置页 tab、平台页表单/错误指引、授权窗口标题）、主进程错误消息、upload_run.py/auth.py 提示、隐私政策、SKILL.md、docs、E2E/smoke 断言（含 tab 选择器 /Qraft/→/MiQroForge/）全部改为 MiQroForge。保留：Qraft* 类型/类名、QRAFT_* 环境变量、qraft 模块/文件名、《Qraft OAuth2 接入实测文档》标题、qraft: 日志前缀、CHANGELOG/CONTRIBUTING、mof5/no-sandbox 测试的模块指称
- 平台名用 "MiQroForge"，不再写 Qraft / microforge；域名 forge.miqroera.com、OAuth client_id、qraft 模块/文件名、qraft.agent-session workflow_ref、QRAFT_* 环境变量属保留项
- 旧名 "MiQi Desktop" 仅允许出现在内部标识符（npm 包名 `miqi-desktop`、appId `com.miqi.desktop`、bridge 产物 `miqi-bridge`、Python 包 `miqi`）与历史变更记录中
- #786 落地 PR #787（2026-08-21）：6 个提示语/mock/测试文件 + skill SKILL.md + CONTRIBUTING.md + CHANGELOG
- WSL distro 名 "MiQi Sandbox"、代码符号（MiQiLogo/MiQiToolHost/MiQiTui 等）、仓库 URL 也属保留项，改动会破坏兼容/测试；ASCII 架构图换名后需同步调整边框宽度
