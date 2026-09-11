import { test, expect } from '@playwright/test';
import { buildMockBridgeScript } from './mocks';

/**
 * #929 模型选择收口 — UI 截图评估。
 * 覆盖：未登录门控（模型/通用 tab）、登录后模型下拉（收口后仅内置 DeepSeek）、
 * 保存流、编辑弹窗激活码 UI（激活/取消激活）。
 * 每步截图存 test-results/929-shots/，供人工与 OCR 评估。
 */
test('模型收口 UI 截图评估（#929）', async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript({
    content: buildMockBridgeScript({
      activeModel: 'deepseek-chat',
      activeProvider: 'deepseek',
      providers: [
        {
          name: 'deepseek',
          display_name: 'DeepSeek',
          env_key: 'DEEPSEEK_API_KEY',
          provider_type: 'openai',
          is_gateway: false,
          is_local: false,
          default_api_base: '',
          configured: true,
          api_key_hint: 'sk-t...seek',
          api_base: null,
          configured_model: 'deepseek-chat',
          verification_status: 'success',
          builtin_available: true,
          builtin_activated: false,
        },
      ],
    }),
  });

  await page.goto('/');
  await page.waitForSelector('#root', { state: 'visible' });
  await page.getByText(/^(System Settings|系统设置)$/).click();

  // ── 1. 未登录 → 模型 tab：登录门控 ────────────────────────────────────
  await page.getByRole('tab', { name: '模型' }).click();
  await expect(page.getByTestId('providers-active-model')).toHaveText(
    '当前默认模型：deepseek-chat'
  );
  await expect(page.getByText('登录后使用平台内置模型')).toBeVisible();
  // #1000：一键浏览器登录按钮（按 testid 定位，页面顶栏/首屏有同名文案）
  await expect(page.getByTestId('model-quickpanel-login-btn')).toBeVisible();
  await expect(page.getByText('编辑当前模型')).toBeVisible();
  // 收口：不再有自配入口 / 状态列表
  await expect(page.getByText('验证成功')).not.toBeVisible();
  await expect(page.getByText(/匹配 Provider/)).not.toBeVisible();
  await page.screenshot({ path: 'test-results/929-shots/01-model-tab-login-gate.png' });

  // ── 2. 未登录 → 通用 tab：登录门控 ────────────────────────────────────
  await page.getByRole('tab', { name: '通用' }).click();
  await expect(page.getByTestId('general-login-btn')).toBeVisible();
  await expect(page.getByText('登录后使用平台内置模型')).toBeVisible();
  await page.screenshot({ path: 'test-results/929-shots/02-general-tab-login-gate.png' });

  // ── 3. 模拟登录 → 模型 tab：下拉出现，收口后仅内置 DeepSeek ────────────
  await page.evaluate(() => (window as any).miqi.qraft.login('18500000000', 'test-password'));
  await page.getByRole('tab', { name: '模型' }).click();
  const select = page.locator('select').first();
  await expect(select).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('登录后使用平台内置模型')).not.toBeVisible();
  // 目录含 openai/custom，但可用 provider 只有内置 deepseek → 过滤后不出现；
  // chat/reasoner 已下线，下拉只保留 deepseek/deepseek-v4-flash
  await expect(select).toContainText('deepseek/deepseek-v4-flash');
  await expect(select).not.toContainText('deepseek/deepseek-chat');
  await expect(select).not.toContainText('deepseek/deepseek-reasoner');
  await expect(select).not.toContainText('openai');
  await expect(select).not.toContainText('custom');
  await expect(select).not.toContainText('自定义模型');
  await page.screenshot({ path: 'test-results/929-shots/03-model-tab-logged-in-dropdown.png' });

  // ── 4. 选择预设模型并保存 ─────────────────────────────────────────────
  await select.selectOption('deepseek/deepseek-v4-flash');
  await expect(select).toHaveValue('deepseek/deepseek-v4-flash');
  await page.screenshot({ path: 'test-results/929-shots/04-model-selected.png' });
  await page.getByRole('button', { name: '保存' }).click();
  await expect(page.getByText('已保存，新会话立即生效')).toBeVisible({ timeout: 10_000 });
  // 保存走 config.update 深合并，只写 agents.defaults.model，不触碰 providers
  const updates = await page.evaluate(() => (window as any).__miqiMock.getConfigUpdates());
  expect(JSON.stringify(updates)).toContain('deepseek/deepseek-v4-flash');
  expect(JSON.stringify(updates)).not.toContain('providers');
  await page.screenshot({ path: 'test-results/929-shots/05-model-saved-flash.png' });

  // ── 5. 编辑当前模型：激活码弹窗（未激活态）────────────────────────────
  await page.getByText('编辑当前模型').click();
  await expect(page.getByText('API 来源')).toBeVisible();
  await expect(page.getByText('推荐（无需API Key）')).toBeVisible();
  await expect(page.getByPlaceholder('输入激活码')).toBeVisible();
  // 收口：弹窗内不再有自配 API Key / Base URL / 自定义模型输入
  await expect(page.locator('input[type="url"]')).toHaveCount(0);
  await expect(page.getByText('API Base URL')).not.toBeVisible();
  await expect(page.getByText('自定义模型')).not.toBeVisible();
  await page.screenshot({ path: 'test-results/929-shots/06-edit-sheet-activation-ui.png' });

  // ── 6. 激活 → 已激活态 + 取消激活入口 ─────────────────────────────────
  await page.getByPlaceholder('输入激活码').fill('DEMO-CODE-123');
  await page.getByRole('button', { name: '激活' }).click();
  await expect(page.getByText('已激活')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('取消激活')).toBeVisible();
  await page.screenshot({ path: 'test-results/929-shots/07-edit-sheet-activated.png' });

  // ── 7. 取消激活 → 回到激活码输入态 ─────────────────────────────────────
  await page.getByText('取消激活').click();
  await expect(page.getByPlaceholder('输入激活码')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('已激活')).not.toBeVisible();
  await page.screenshot({ path: 'test-results/929-shots/08-edit-sheet-deactivated.png' });
});
