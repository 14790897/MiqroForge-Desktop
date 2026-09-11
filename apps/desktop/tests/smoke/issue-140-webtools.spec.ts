import { expect, test } from '@playwright/test';
import { buildMockBridgeScript } from './mocks';

test.describe('Issue #140 Web search settings', () => {
  test('shows ddgs search options and keeps Ollama only for web fetch', async ({ page }) => {
    await page.addInitScript({ content: buildMockBridgeScript() });
    await page.goto('/');
    await page.waitForSelector('#root', { state: 'visible' });

    await page.getByText(/^(System Settings|系统设置)$/).click();
    await page.getByRole('tab').filter({ hasText: /Web/ }).click();

    // 搜索引擎选项收在「自定义搜索引擎」折叠面板里（#844 改版后需先展开；
    // 折叠时内容不在 a11y 树中，role 过滤匹配不到，须按文本点开）
    await page.getByText('自定义搜索引擎（可选）').click();

    const webSearch = page
      .locator('section')
      .filter({ has: page.getByRole('button', { name: 'DuckDuckGo' }) });
    await expect(webSearch).toBeVisible();
    await expect(webSearch.getByRole('button', { name: 'DuckDuckGo', exact: true })).toBeVisible();
    await expect(webSearch.getByRole('button', { name: 'Brave', exact: true })).toBeVisible();
    await expect(webSearch.getByRole('button', { name: 'Tavily', exact: true })).toBeVisible();
    await expect(webSearch).not.toContainText('Ollama');

    const webFetch = page
      .locator('section')
      .filter({ has: page.getByRole('button', { name: 'Ollama' }) });
    await expect(webFetch.getByRole('button', { name: 'Ollama' })).toBeVisible();

    await page.screenshot({
      path: 'test-results/issue-140/settings-webtools.png',
      fullPage: true,
    });
  });
});
