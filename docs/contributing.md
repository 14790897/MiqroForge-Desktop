# 贡献指南

## 欢迎贡献

MiQroForge Desktop 是一个开源项目，采用 MIT 许可证。我们欢迎各种形式的贡献。

## 贡献方式

### 报告 Bug

1. 在 GitHub Issues 中搜索是否已有相同问题
2. 使用 Bug Report 模板提交 Issue
3. 尽可能提供：
   - 操作系统和版本
   - MiQroForge Desktop 版本
   - 复现步骤
   - 预期行为 vs 实际行为
   - 相关日志或截图

### 提交代码

1. **Fork 仓库**
2. **创建特性分支**：`git checkout -b feat/your-feature`
3. **编写代码**：遵循项目代码规范
4. **添加测试**：确保新功能有测试覆盖
5. **运行测试**：`uv run pytest && cd apps/desktop && npm run test`
6. **提交 PR**：使用 PR 模板，描述变更内容

### 改进文档

文档使用 MkDocs Material 构建，位于 `docs/` 目录：

```bash
# 本地预览文档
uv run mkdocs serve

# 构建静态站点
uv run mkdocs build

# 部署到 GitHub Pages
uv run mkdocs gh-deploy
```

## 代码审查清单

提交 PR 前请确认：

- [ ] 代码通过 Ruff / ESLint 检查
- [ ] 所有测试通过
- [ ] 新功能有适当的测试覆盖
- [ ] 提交信息遵循 Conventional Commits
- [ ] 更新了相关文档
- [ ] 没有引入新的 lint 警告
- [ ] 如果是 UI 变更，已在 Windows 上测试

## 技术栈要求

| 技术 | 版本要求 |
|------|----------|
| Python | 3.11 或 3.12 |
| Node.js | 20+ |
| TypeScript | 5.8+ |

## PR 流程

1. 创建 Draft PR 进行早期讨论
2. CI 通过后标记为 Ready for Review
3. 维护者审查并提供反馈
4. 修改完成后合并到 `main` 分支

## 沟通渠道

- GitHub Issues：Bug 报告和功能请求
- GitHub Discussions：技术讨论和 Q&A

## 许可证

贡献的代码自动采用项目的 MIT 许可证。
