/**
 * Issue #1155 — 设置 → 文档 的链接全部 404：前端硬编码的 DOCS_BASE 还停在旧仓库
 * 路径 `…/MiQi/`，而文档站早已随仓库更名迁到 `…/MiqroForge-Desktop/`。
 *
 * 断言用户在设置页实际点到的地址：全部落在当前文档站根地址下（取自仓库根
 * mkdocs.yml——文档站与桌面端链接的唯一事实来源），没有一条指向旧路径。
 */
import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

const repoRoot = resolve(__dirname, '..', '..', '..', '..');
const mkdocsYml = readFileSync(join(repoRoot, 'mkdocs.yml'), 'utf-8');

function mkdocsScalar(key: string): string {
  const match = mkdocsYml.match(new RegExp(`^${key}:[ \\t]*(\\S+)[ \\t]*$`, 'm'));
  if (!match) throw new Error(`mkdocs.yml 缺少顶层配置 ${key}`);
  return match[1];
}

const DOCS_BASE = mkdocsScalar('site_url');
const REPO_URL = mkdocsScalar('repo_url');

async function openDocsTab(page: Page) {
  await page.locator('[data-testid="nav-system-settings"]').click();
  await page.getByRole('tab', { name: '文档' }).click();
  const panel = page
    .locator('div[role="tabpanel"]')
    .filter({ hasText: '点击章节在浏览器中打开对应文档页面' });
  await expect(panel).toBeVisible({ timeout: 15_000 });
  return panel;
}

test('issue #1155: 设置页文档链接指向当前文档站而非旧仓库路径', { timeout: 120_000 }, async () => {
  const { electronApp, page, miqiHome } = await launchElectronApp();

  try {
    const panel = await openDocsTab(page);

    const hrefs: string[] = await panel
      .locator('a')
      .evaluateAll((els) => els.map((el) => (el as HTMLAnchorElement).href));
    expect(hrefs.length).toBeGreaterThan(10);

    // 面板里的链接只应有两类：文档站下的章节链接 + 底部那一条 GitHub 仓库链接。
    // 一律用解析后的 origin / pathname 比对（不对 URL 做子串匹配）——旧仓库路径
    // /MiQi/ 是这条 issue 的现场，pathname 前缀对不上就说明漂移回来了。
    const docsBase = new URL(DOCS_BASE);
    const repoHref = new URL(REPO_URL).href;
    const parsedHrefs = hrefs.map((href) => new URL(href));

    expect(parsedHrefs.filter((url) => url.href === repoHref)).toHaveLength(1);
    const docLinks = parsedHrefs.filter((url) => url.href !== repoHref);
    expect(docLinks.length).toBeGreaterThan(10);
    for (const url of docLinks) {
      expect(url.origin, url.href).toBe(docsBase.origin);
      expect(url.pathname.startsWith(docsBase.pathname), url.href).toBe(true);
    }

    // 「完整文档站点」就是 issue 里点开 404 的那个入口，精确断言。
    await expect(panel.getByRole('link', { name: '完整文档站点' })).toHaveAttribute(
      'href',
      DOCS_BASE
    );

    // 底部 GitHub 仓库链接也别再显示旧仓库名。
    const repoLink = panel.getByRole('link', { name: /GitHub 仓库/ });
    await expect(repoLink).toHaveAttribute('href', REPO_URL);
    await expect(repoLink).toContainText('14790897/MiqroForge-Desktop');

    await page.screenshot({
      path: `test-results/issue-1155-docs-tab.png`,
      fullPage: true,
    });
  } finally {
    await closeElectronApp(electronApp, miqiHome);
  }
});
