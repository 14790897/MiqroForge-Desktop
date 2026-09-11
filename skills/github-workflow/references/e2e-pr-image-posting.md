# E2E 截图自动贴到 PR 描述

> **给 AI 会话的说明**：本文档描述的能力（`postScreenshotToPr`）可以被任何测试或脚本
> 主动调用。阅读「何时调用」与「如何在 spec 中接入」后即可使用，无需询问用户。
> **少评论原则**：AI 手动贴图时把图片写进 PR 描述，不发评论；现有 CI 自动化
> `postScreenshotToPr` 仍是发评论（若需统一为写描述，需改 helper 实现）。

## 功能

e2e 测试跑完后，把截图上传到本仓库的固定预发布（release assets 官方 API），
并以 markdown 图片形式展示到 PR（AI 手动操作为写进 PR 描述，不贴评论）。
评审者打开 PR 即可看到测试证据，无需自己跑测试。

- 通过：贴成功截图（`✅ E2E 通过：<测试名>`）
- 失败：贴 Playwright 自动捕获的 `test-failed-1.png`（`❌ E2E 失败：<测试名>`）

## 核心机制

| 项 | 说明 |
|---|---|
| 存放位置 | 本仓库固定预发布 `_gh-imgup`（复用创建，Releases 列表只有一条） |
| 上传 API | release assets（官方公开 API，token 即可；实测匿名可访问 HTTP 200） |
| 去重 | 文件名 = `原名-<sha256前8位>.<ext>`；相同内容重复上传返回 422 时自动复用已有资产 |
| 实现 | 纯 Node `fetch`（无 shell 依赖），见 [tests/e2e/helpers/pr-image-post.ts](../../apps/desktop/tests/e2e/helpers/pr-image-post.ts) |
| 为何不用 draft release | draft 资产的下载 URL 对外（甚至带 token）都返回 404，PR 里会裂图——必须 published 预发布 |
| 为何不用 user-attachments | 原生贴图端点无公开 API，需驱动真实浏览器且有 TOS 风险（actions-cool/issues-helper 已被封） |

## 何时调用（给 AI 的决策规则）

1. **跑过 e2e 且希望证据出现在 PR 里** → 设置 `MIQI_E2E_POST_IMG=1` 跑测试，其余自动完成
2. **手动上传一张图片** → 调用 `uploadImage` 等价逻辑上传，然后把 `![](url)` 写进 PR 描述（见下方命令），不贴评论
3. **CI 环境** → 默认已开启（`process.env.CI` 为真），无需额外操作
4. **无 PR 上下文**（如 develop 分支裸跑）→ 自动静默跳过，无需处理

## 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `MIQI_E2E_POST_IMG` | CI=1 / 本地=0 | 强制开启（`1`）或关闭（`0`）贴图 |
| `MIQI_IMG_REPO` | `14790897/MiQi` | 目标仓库（fork 测试时覆盖） |
| `GH_TOKEN` / `GITHUB_TOKEN` | 无 | 上传与写描述的鉴权；本地可回退到 `gh auth token` |

## 如何在 spec 中接入（给 AI 的代码模式）

```ts
import { postScreenshotToPr } from './helpers/pr-image-post';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

test.describe('My Acceptance E2E', () => {
  // 失败时自动贴 test-failed-1.png
  test.afterEach(async () => {
    if (test.info().status === 'passed') return;
    const fail = join(test.info().outputDir, 'test-failed-1.png');
    if (existsSync(fail)) {
      await postScreenshotToPr(fail, `❌ E2E 失败：${test.info().title}`);
    }
  });

  test('...', async () => {
    // ...
    await page.screenshot({
      path: `test-results/${test.info().title.replace(/\s+/g, '-')}.png`,
      fullPage: true,
    });
    await postScreenshotToPr(
      `test-results/${test.info().title.replace(/\s+/g, '-')}.png`,
      '✅ E2E 通过：<一句话结果>',
    );
  });
});
```

已接入的参考 spec：

- [no-sandbox-exec.spec.ts](../../apps/desktop/tests/e2e/no-sandbox-exec.spec.ts)
- [mof5-qraft-upload.spec.ts](../../apps/desktop/tests/e2e/mof5-qraft-upload.spec.ts)

## 手动上传一张图（不跑测试）

```bash
# 用 gh 直接调 API（本地需 gh auth login；CI 用 GITHUB_TOKEN）
gh api repos/14790897/MiQi/releases/tags/_gh-imgup --jq '.id' 2>/dev/null ||
  gh api repos/14790897/MiQi/releases -X POST \
    -F tag_name='_gh-imgup' -F name='_gh-imgup' -F prerelease=true \
    -f body='Image assets - do not delete' --jq '.id'
# 再用 release assets 上传接口（uploads.github.com）传文件，
# 取 browser_download_url 后以 `![img](url)` 写进 PR 描述（gh pr edit 更新 body），不发评论。
# 完整 Node 实现见 pr-image-post.ts 的 uploadImage()。
```

## 清理与注意

- 预发布 `_gh-imgup` **不要删除**（历史图片引用会全部裂掉）；body 已写 "do not delete"
- 公开仓库的 release asset 任何人可访问；私有仓库仅成员可见
- 上传失败（无 token / 无 PR / 图片不存在）一律静默跳过，不干扰测试结果
