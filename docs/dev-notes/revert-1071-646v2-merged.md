---
name: revert-1071-646v2-merged
description: "#1071（646-v2 计划/确认卡）已于 2026-09-22 经 PR #1205 回退并合入 develop；确认卡/PlanCard 类旧记忆只代表回退前状态；相关 issue/PR 的后续处理清单"
type: project
---


2026-09-22：按用户指示回退 #1071（`feat(#646-v2): 计划/确认/危险动作交互重做`，squash 提交 `6bf5bd51`）。PR **#1205** 已合入 develop（squash 提交 **`09f64f14`**，由 14790897 于 13:46 合入）。三个提交：回退功能本体 → 本机 E2E 可跑适配 → CodeRabbit 评审修复。

**Why（用户决策）**：只回退「646-v2 功能本体」，保留 #1071 夹带的无关改进（Anthropic 真流式、trace 写锁预算、中文错误文案、CodeRabbit 安全修复）与后续提交在冲突处的改动（#1146 turnId、#1078 步骤进度、#1188 计划卡 helper、#1199 429 分类）。

**How to apply**：
- 涉及确认卡 v2 / PlanCard / Action Guard / `request_action_confirmation` / `ask_user_plan_confirm` / `task_policy` 的旧记忆（[confirm-card-two-runtime-map](confirm-card-two-runtime-map.md)、[confirm-card-issue-714-fix](confirm-card-issue-714-fix.md) 等）描述的是**回退前**状态；develop 上这些实现已不存在。基础确认卡 `ask_user_confirm_card`（#719/#864 时代）保留、仍是活的路径。
- 回退这类巨型 squash 时逐行 `git blame` 归属冲突行，别整文件回退（#1071 的 squash 里夹带了大量非本功能改动）。
- 相关单据（2026-09-22 均已关闭，理由=载体/前提随回退消失）：**#1078**（计划卡删除后无对应 UI；保留的 StepProgressTracker 因确认卡 steps 只有 `{id,title}` 无 `tools`，仍不会激活）、**#1101** / **#1102**（Action Guard 前提消失）、PR **#1145**（基线被移除、代码无法成立）；#646 维持已关闭。重做危险动作确认或步骤进度时需重新开单。

**仓库合并门（2026-09-22 实测）**：develop 由 ruleset `develop-quality-gate` 管控——要求 **2 个批准评审** + 必过检查 `electron-e2e` / `check-title` + 只能 squash（`required_linear_history`）+ 必须解决全部 review 线程；**仓库关闭了 auto-merge**（`allow_auto_merge: false`）。用户账号 **14790897 是该 ruleset 的 bypass actor（`bypass_mode: always`）**，所以该账号可直接合入、跳过批准要求。
