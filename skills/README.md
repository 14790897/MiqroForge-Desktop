# Project Skills

本目录存放 MiQi 项目的共享 Claude Code 技能（SKILL.md 格式）。与
`miqi/skills/`（产品运行时内置技能，打包进应用）不同，这里是**开发协作
用的 AI 技能**，供贡献者/评审者与 AI 会话使用。

## 目录结构

```
skills/
├── github-workflow/          # GitHub 协作全流程（Issue/PR/CodeRabbit/CI）
│   ├── SKILL.md
│   └── references/
│       └── e2e-pr-image-posting.md   # e2e 截图自动贴 PR 评论
└── e2e-test-workflow/        # Electron E2E 测试工作流
    ├── SKILL.md
    └── miqi-e2e/
        └── SKILL.md          # MiQi 平台专属 E2E 模式（WSL/CI/弹窗）
```

## 使用方式

- **本地**：把 `skills/*` 软链或复制到 `~/.claude/skills/`（或 `~/.cc-switch/skills/`），
  AI 会话按各自 `Triggers` 触发词自动加载
- **AI 会话内**：直接说对应触发词（如「提PR」「测e2e」「贴图到PR」）即可命中技能

## 维护约定

- 每个技能 = 一个目录，含 `SKILL.md`（frontmatter 带 `name`/`description`/`Triggers`）
- 长文档/细节放 `references/`，SKILL.md 正文只放摘要 + 链接（渐进披露）
- 与本地个人技能（`~/.claude/skills/`）同步时，以本仓库为准
