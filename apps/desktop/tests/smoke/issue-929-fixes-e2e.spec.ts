import { test, expect } from '@playwright/test';
import { buildMockBridgeScript } from './mocks';

/**
 * #929 修复分支 E2E 评估 —— 验证审查发现修复后的用户可见行为：
 *  1. 激活后默认模型写为带前缀的预设（下拉可显示，不再与页头矛盾）
 *  2. 取消激活后父级状态刷新、默认模型重置，重开弹窗不再显示陈旧的「已激活」
 *  3. custom/* 遗留用户仍有内置激活入口（「激活内置模型」按钮）
 * mock 桥为动态状态（update/activate/deactivate 镜像真实后端行为）。
 */

const DEEPSEEK_PROVIDER = {
  name: 'deepseek',
  display_name: 'DeepSeek',
  env_key: 'DEEPSEEK_API_KEY',
  provider_type: 'openai',
  is_gateway: false,
  is_local: false,
  default_api_base: '',
  configured: false,
  api_key_hint: null,
  api_base: null,
  configured_model: null,
  verification_status: 'missing',
  builtin_available: true,
  builtin_activated: false,
};

test('激活/取消激活流程修复评估（#929 fixes）', async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript({
    content: buildMockBridgeScript({
      activeModel: '',
      activeProvider: 'deepseek',
      providers: [{ ...DEEPSEEK_PROVIDER }],
    }),
  });

  await page.goto('/');
  await page.waitForSelector('#root', { state: 'visible' });
  await page.evaluate(() => (window as any).miqi.qraft.login('18500000000', 'test-password'));
  await page.getByText(/^(System Settings|系统设置)$/).click();
  await page.getByRole('tab', { name: '模型' }).click();

  // ── 1. 未激活：页头「未设置」+ 激活入口 ─────────────────────────────
  await expect(page.getByTestId('providers-active-model')).toHaveText('当前默认模型：未设置');
  await page.getByText('编辑当前模型').click();
  await expect(page.getByPlaceholder('输入激活码')).toBeVisible();
  // 弹窗内下拉显示占位（还没有模型）
  await expect(page.locator('select').last()).toHaveValue('');

  // ── 2. 激活 → 已激活态（弹窗保持打开） ───────────────────────────────
  await page.getByPlaceholder('输入激活码').fill('DEMO-CODE');
  await page.getByRole('button', { name: '激活' }).click();
  await expect(page.getByText('已激活')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('取消激活')).toBeVisible();
  await page.screenshot({ path: 'test-results/929-shots/f01-edit-activated.png' });

  // ── 3. 关闭弹窗：页头默认模型 = 激活写入的带前缀预设 ─────────────────
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.getByTestId('providers-active-model')).toHaveText(
    '当前默认模型：deepseek/deepseek-v4-flash'
  );
  await page.screenshot({ path: 'test-results/929-shots/f02-header-after-activation.png' });

  // ── 4. 重开弹窗：下拉显示带前缀模型（修复前被清空显示占位）───────────
  await page.getByText('编辑当前模型').click();
  await expect(page.getByText('已激活')).toBeVisible({ timeout: 10_000 });
  const sheetSelect = page.locator('select').last();
  await expect(sheetSelect).toHaveValue('deepseek/deepseek-v4-flash');
  await page.screenshot({ path: 'test-results/929-shots/f03-reopen-shows-prefixed-model.png' });

  // ── 5. 取消激活 → 默认模型清空为「未设置」，按钮变为「激活内置模型」 ──
  await page.getByText('取消激活').click();
  await expect(page.getByPlaceholder('输入激活码')).toBeVisible({ timeout: 10_000 });
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.getByTestId('providers-active-model')).toHaveText('当前默认模型：未设置');
  // active_provider 已为空 → 激活入口退到内置 provider（修复 #10）
  await expect(page.getByText('激活内置模型')).toBeVisible();
  await page.screenshot({ path: 'test-results/929-shots/f04-header-after-deactivate.png' });

  // ── 6. 重开弹窗：不再显示陈旧的「已激活」（修复 #7）──────────────────
  await page.getByText('激活内置模型').click();
  await expect(page.getByPlaceholder('输入激活码')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('已激活')).not.toBeVisible();
  await expect(page.getByText('取消激活')).not.toBeVisible();
  await page.screenshot({ path: 'test-results/929-shots/f05-reopen-shows-deactivated.png' });
});

test('custom/* 遗留用户仍有激活入口（#929 fixes）', async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript({
    content: buildMockBridgeScript({
      activeModel: 'custom/my-model',
      activeProvider: null,
      providers: [{ ...DEEPSEEK_PROVIDER }],
    }),
  });

  await page.goto('/');
  await page.waitForSelector('#root', { state: 'visible' });
  await page.evaluate(() => (window as any).miqi.qraft.login('18500000000', 'test-password'));
  await page.getByText(/^(System Settings|系统设置)$/).click();
  await page.getByRole('tab', { name: '模型' }).click();

  // 页头如实显示遗留模型；激活入口不退场（修复 #10）
  await expect(page.getByTestId('providers-active-model')).toHaveText(
    '当前默认模型：custom/my-model'
  );
  await expect(page.getByText('编辑当前模型')).not.toBeVisible();
  await expect(page.getByText('激活内置模型')).toBeVisible();
  await page.screenshot({ path: 'test-results/929-shots/f06-custom-user-activation-entry.png' });

  await page.getByText('激活内置模型').click();
  await expect(page.getByText('API 来源')).toBeVisible();
  await expect(page.getByPlaceholder('输入激活码')).toBeVisible();
  await page.screenshot({ path: 'test-results/929-shots/f07-custom-user-edit-sheet.png' });
});
