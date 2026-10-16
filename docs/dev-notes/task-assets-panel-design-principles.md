---
name: task-assets-panel-design-principles
description: 任务资产面板的判定/展示原则（用户 2026-09-16 拍板）：agent 显式声明结果文件；交付根顶层产物必须逐张显示，批量中间目录收起
type: feedback
---


任务资产面板（右侧「任务资产」）相关改动的用户拍板原则（2026-09-16，issue #1098 / PR #1099）：

1. **结果文件由 agent 显式声明**（新增 `declare_result_files` 工具，台账 `result: true`），不再靠扩展名白名单猜；显式声明优先于一切启发式（被删除的除外）。
2. **交付根（用户 --out-dir/工作目录）的顶层产物必须逐张显示**——它们是用户最关心的东西（报告、cube、summary.json…），即使有几十个也不折叠。
3. **批量中间目录（如 bvse_sites/ 20 个 per-site 文件）默认收起成一行**「`目录/` · N 个文件」，可展开；判定条件 = 目录下 ≥3 个文件且存在同样持有产物的祖先目录（避免把交付根自身折掉）。
4. 面板不得出现「同一文件重复两条」（台账里同一文件可能有裸名/绝对路径/Windows 8.3 短名多种形态，比较前要 resolve 归一）。

**Why:** 用户实测 BVSE skill 后反馈：红框的报告进不了「结果文件」、过程文件显示不全（33 个只显示 9 个）、bvse_sites 几十个文件淹没面板。

**How to apply:** 再动任务资产面板的判定或展示时按这四条来；参考 [e2e-local-run-bvse-skill](e2e-local-run-bvse-skill.md) 的实跑验证方式。
