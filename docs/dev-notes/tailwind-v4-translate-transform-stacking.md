---
name: tailwind-v4-translate-transform-stacking
description: Tailwind v4 的 translate-x 工具类落到独立 translate 属性，与 keyframes 的 transform 叠加——浮动居中元素做 transform 动画时会双重位移
type: project
---


Tailwind CSS v4（本仓库 4.3.3）的 `translate-x-*` / `translate-y-*` 工具类不再写进 `transform`，而是落到 CSS 独立属性 `translate`（编译产物实测）：

```css
.-translate-x-1\/2 {
  --tw-translate-x: calc(calc(1 / 2 * 100%) * -1);
  translate: var(--tw-translate-x) var(--tw-translate-y);
}
```

`translate` 与 `transform` 按规范（CSS Transforms L2）是**叠加**关系——先 translate 再 transform——不是互相覆盖。所以 `fixed left-1/2 -translate-x-1/2` 的元素，若动画 keyframes 里又写了 `transform: translate(-50%, …)`，水平位移被算两次，元素会额外左移自身宽度的一半。

**Why:** 2026-09-14 平台登录失效横幅就因此偏左 254px（正好是横幅宽度 508px 的一半），用户直接报「这里没有居中」；「审批绕过」横幅是同一机制（它把居中完全交给 keyframes，当前正常但同样脆弱）。修复见 PR #1065。

**How to apply:** 做浮动居中元素的进场/退场动画时，水平居中只保留**一个**来源——优先 `left-1/2` + `-translate-x-1/2` 工具类，keyframes 里只写 `translateY` / `scale`。同一元素上 `translate-x-*` 与 `translate-y-*` 工具类之间叠加是安全的（合并进同一个 `translate` 属性）。排查「居中元素偏左/偏右半个身位」先查这个叠加。相关 [relogin-notification-feature-map](relogin-notification-feature-map.md)
