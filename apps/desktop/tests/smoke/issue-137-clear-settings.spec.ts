import { expect, test } from '@playwright/test';
import { buildMockBridgeScript } from './mocks';

async function injectMockAndGoto(page: import('@playwright/test').Page) {
  await page.addInitScript({
    content: buildMockBridgeScript({
      config: {
        agents: {
          defaults: {
            name: 'miqi',
            workspace: 'C:/old-workspace',
            model: 'openai/gpt-4.1',
            temperature: 0.2,
            maxTokens: 4096,
          },
        },
        tools: {
          web: {
            search: {
              provider: 'auto',
              tavilyApiKey: 'tvly-old',
              braveApiKey: 'BSA-old',
            },
            fetch: {
              provider: 'hybrid',
              ollamaApiBase: 'https://old-fetch.example',
              ollamaApiKey: 'fetch-old',
            },
          },
          papers: {
            provider: 'semantic_scholar',
            semanticScholarApiKey: 's2-old',
          },
        },
      },
    }),
  });
  await page.goto('/');
  await page.waitForSelector('#root', { state: 'visible' });
}

test('issue #137: clearing workspace sends explicit empty values', async ({ page }) => {
  await injectMockAndGoto(page);
  // #929 收口后默认模型改为预设下拉，未登录时被门控隐藏 —— 登录后
  // 才能断言下拉；清空工作目录时模型保持原值（不再有空模型写入路径）。
  await page.evaluate(() => (window as any).miqi.qraft.login('18500000000', 'test-password'));

  await page.getByText(/^(System Settings|系统设置)$/).click();

  const workspaceInput = page.getByPlaceholder('~/.miqi/workspace');
  const modelSelect = page.locator('select').first();
  await expect(workspaceInput).toHaveValue('C:/old-workspace');
  await expect(modelSelect).toBeVisible();

  await workspaceInput.fill('');
  const generalPanel = workspaceInput.locator('xpath=ancestor::div[contains(@class, "p-6")][1]');
  await generalPanel.locator('button').nth(1).click();

  const updates = await page.evaluate(() => window.__miqiMock.getConfigUpdates());
  expect(updates).toHaveLength(1);
  const defaults = (updates[0] as any).agents.defaults;
  expect(defaults.workspace).toBe('');
  expect(defaults.name).toBe('miqi');
  expect(defaults.temperature).toBe(0.2);
  expect(defaults.maxTokens).toBe(4096);
  // 模型未改动时保留原值，不会被清成空字符串（#929 收口：空模型被后端拒绝）
  expect(defaults.model).toBe('openai/gpt-4.1');
});

test('issue #137: clearing web tool keys sends explicit empty values', async ({ page }) => {
  await injectMockAndGoto(page);

  await page.getByText(/^(System Settings|系统设置)$/).click();
  await page.getByRole('tab').filter({ hasText: 'Web' }).click();

  // 搜索 key 收在「自定义搜索引擎」折叠面板里（#844 改版后）
  const details = page.locator('details').filter({ has: page.getByText('自定义搜索引擎') });
  await details.locator('summary').click();

  const tavilyKeyInput = page.getByPlaceholder('tvly-...');
  const braveKeyInput = page.getByPlaceholder('BSA...');
  const fetchBaseInput = page.locator('input[value="https://old-fetch.example"]');
  const fetchKeyInput = page.locator('input[value="fetch-old"]');
  const s2KeyInput = page.locator('input[value="s2-old"]');

  await expect(tavilyKeyInput).toHaveValue('tvly-old');
  await expect(braveKeyInput).toHaveValue('BSA-old');
  await expect(fetchBaseInput).toHaveValue('https://old-fetch.example');
  await expect(fetchKeyInput).toHaveValue('fetch-old');
  await expect(s2KeyInput).toHaveValue('s2-old');

  await tavilyKeyInput.fill('');
  await braveKeyInput.fill('');
  await fetchBaseInput.fill('');
  await fetchKeyInput.fill('');
  await s2KeyInput.fill('');

  const webToolsPanel = braveKeyInput.locator('xpath=ancestor::div[contains(@class, "p-6")][1]');
  await webToolsPanel.locator('button').last().click();

  const updates = await page.evaluate(() => window.__miqiMock.getConfigUpdates());
  expect(updates).toHaveLength(1);
  expect(updates[0]).toEqual({
    tools: {
      web: {
        search: {
          provider: 'auto',
          tavilyApiKey: '',
          braveApiKey: '',
        },
        fetch: {
          provider: 'hybrid',
          ollamaApiBase: '',
          ollamaApiKey: '',
        },
      },
      papers: {
        provider: 'semantic_scholar',
        semanticScholarApiKey: '',
      },
    },
  });
});
