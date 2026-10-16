---
name: feedback-platform-channel-map
description: 应用内反馈双通道地图——平台 POST /oauth2/feedback（登录加写）+ 飞书兜底，标题字段已移除，Bitable 标题由正文首行派生
type: project
---


应用内反馈是**双通道**：飞书 Bitable 始终兜底，登录态下加写平台 `POST {baseUrl}/oauth2/feedback`（issue #1054）。

- 链路：`FeedbackPage` → `feedback:submit`（主进程 ipc/index.ts）→ 先 `bridge.send` 写飞书 →
  成功后 `getQraftService().submitPlatformFeedback()` 加写平台；平台失败**不改判提交成功**，
  只在结果里带 `platform` 字段 → UI 显示「反馈已收到，但平台归属暂未同步」
- **界面文案绝不提飞书**（用户 2026-09-14 明确要求）：占位符/提示/成功页都用「开发团队」
  「平台账号」等中性措辞，飞书只作为实现细节存在于代码注释与后台
- 平台请求体 `feedbackSubmitRequest` **没有 title 字段**（只有 type/content/contact/images），
  所以表单已去掉标题输入框（2026-09-14）；Bitable 的「标题」列由正文首个非空行派生
  （`_derive_title`）；类别 `question` → 平台 `other`
- 截图只进飞书：平台 `images` 要图片 URL，桌面端是 base64 data URL，暂无换取通道
- token 失效会复用登录失效三件套；`refresh_token` 作废 → `requiresRelogin` 横幅

**Why:** 平台侧要把反馈关联到账号（消费组/积分/会话），飞书通道保留作兜底与本地备份。

**How to apply:** 改反馈字段/通道时两处都要动（桌面主进程 + `miqi/runtime/feedback_handlers.py`）；
E2E 用 `config.channels.feedback.skipFeishu` 跳过飞书让平台通道成为被测对象
（见 [e2e-config-patch-clobbered-by-helper](e2e-config-patch-clobbered-by-helper.md)）。
