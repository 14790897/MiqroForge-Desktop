---
name: no-alternatives-section-in-issues
description: 创建 issue 时不写「备选方案」节——用户明确要求省略（2026-09-08）
type: feedback
---


创建 GitHub issue（Feature/Bug）时省略「备选方案」节，只写功能概述/需求背景/功能描述/优先级/参考资料等。

**Why:** 用户 2026-09-08 明确指示「以后不需要提供备选方案」；备选方案是给产品权衡用的，用户已直接拍板需求，写了也是冗余。

**How to apply:** 写 issue body 时跳过备选方案；若模板/CI 不强制，其余节照常。关联 [github-workflow 技能](github-workflow 技能.md)。
