---
name: plain-language-communication
description: 2026-09-29 起：对用户说话、以及 PR/issue 评论都要用平实中文，先说结论再讲原因，不堆术语/行号
type: feedback
---


写给用户看的所有内容——聊天回复、PR/issue 评论、说明文字——一律"说人话"：先一句话给结论，再讲为什么；用日常词，不堆文件路径、行号、内部术语。技术上必须精确的地方（如评审定位到某行）可以保留引用，但正文叙述仍要先讲人话。

**Why:** 2026-09-29，我在 PR #1218 上贴了一条术语密集的复核评论（大量 file:line、"潜伏路径""orphan 转发"等），用户先要求「要简单点，说人话」，随后明确要求「记住以后说人话」——是长期偏好，不只针对那一条。

**How to apply:** 每次输出面向用户或写在 GitHub 上的文字时都按这个标准自检；内部推理、代码注释不受影响。相关：[auto-handle-pr-reviews-and-ci](auto-handle-pr-reviews-and-ci.md)。
