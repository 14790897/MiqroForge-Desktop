---
name: svg-embed-sanitizer-dompurify-traps
description: "SvgEmbed 的 DOMPurify 消毒边界——ALLOWED_URI_REGEXP 全局语义、use 需 ADD_TAGS、预解析 fail-open 三个坑（PR #843 修复）"
type: project
---


MiQroForge Desktop 的 ```svg 代码块（`apps/desktop/src/renderer/features/chat/components/SvgEmbed.tsx`）外部资源拦截边界的实现约定（2026-09-14 修复 PR #843 CI 后确定）：

1. **不要收紧全局 `ALLOWED_URI_REGEXP`**：DOMPurify 对除 URI_SAFE 名单（id/xmlns/style 等固定 14 个）外**所有属性**的值都跑这个正则。曾设成 `^#[A-Za-z_][\w:.-]*$` 想只放行 fragment 引用 → x/y/fill/viewBox/d 等普通值整批被删，rect 剥成空壳（CI 实证）。
2. **`<use>` 在 svg profile 里被 DOMPurify 内部 `svgDisallowed` 默认禁掉**，仅移出 FORBID_TAGS 无效；要保留本地 fragment 引用的 use 必须 `ADD_TAGS: ['use']`，外链 href 由 hook 拦。
3. **不能用原始字符串的 XML 预解析（DOMParser image/svg+xml）做安全边界**：畸形输入（如属性值内嵌引号 `clip-path="url("https://…")"`）会让解析整体失败 → 原样放行（fail-open）。正确层是 DOMPurify 的 `afterSanitizeAttributes` hook（解析器宽容，总能产出 DOM）。hook 注册需 `typeof window !== 'undefined'` 守卫（node 下 dompurify 导出的是无 addHook 的未启用实例）。
4. 策略：href/xlink:href 只允许 `#fragment`；含 `url(...)` 的属性只允许完整值 `url(#id)`；含反斜杠+括号的值拒绝（防 `u\72l(...)` CSS 转义伪装）。**Why:** CodeRabbit 安全审查 + CI 实证，见 PR #843（2026-09-14 已 squash 合并，develop e6e6b7c4；分支 feature/671-mermaid-visualization）。**How to apply:** 改动这个文件或给其他 SVG/消毒渲染加边界前先读这段；验证用 `apps/desktop/src/renderer/features/chat/components/SvgEmbed.test.ts`（8 用例：外链剥离/本地引用保留/CSS 转义）。相关：[worktree-node-modules-junction](worktree-node-modules-junction.md)。
