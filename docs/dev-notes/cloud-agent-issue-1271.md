---
name: cloud-agent-issue-1271
description: "全天候云端 Agent 立项（对标 Grok Bot）issue #1271，前置阻塞是「本地优先/数据不上云」的产品定位决策"
type: project
---


2026-10-10 立项 GitHub issue #1271「[FEATURE] 全天候云端运行的 Agent（对标 Grok Bot）」（P2，enhancement）。

**Why**：用户要求做一个类似 xAI Grok Bot 的功能——关掉桌面应用后 Agent 仍在云端按计划干活，只在需要审批时回来找人。背景是对标产品的调研（Grok Bot 2026-08-11 beta、OpenAI Dots、Meta Muse、Google Spark、Claude Tag）。

**How to apply**：
- 这是一个**产品定位转向**，不是纯技术增量。现有边界文档 `docs/product-form-and-technical-boundaries.md` 明确写「本地优先、数据不上云」并把云端同步列在暂不提供里。issue 里列了 5 个待定的产品决策，**动手写代码前要先过产品决策**。
- 推荐的路线是**别一上来做云电脑**：Grok Bot 走「云电脑+操控屏幕」是因为要够到没接口的系统；本仓已有 MCP 工具层，走「云端 headless agent + 调工具」成本低一个数量级。一期搬 headless gateway CLI 上云 + 只做只读/草案类（抄 OpenAI Dots 的克制）；二期补无人值守审批 + 云端计费 + 审计；三期才碰云电脑/代登录。
- 三个必须解决的断点：审批在没有桌面端通道时 fail-closed（`miqi/agent/user_input_resolver.py:170-175`）；扣积分由 Electron 主进程发起（`qraft/service.ts:944`）；全仓无设备注册/心跳。
- 已发现的具体缺口：**桌面端能增删改查 cron 任务，但调度循环只在 CLI 里实例化，所以用户排的定时任务目前「存了不会跑」**。

设计方案见 `docs/always-on-cloud-agent-design.md`（当时未合入，计划随 issue 提 PR）。

相关：[quantum-api-feature-requirement](quantum-api-feature-requirement.md) 同为「例会/需求提出、尚未落地」的待办需求；[legacy-main-path-only](legacy-main-path-only.md)（设计只对 legacy 主路径做）。
