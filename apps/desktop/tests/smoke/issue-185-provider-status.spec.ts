import { test, expect } from '@playwright/test';
import { buildMockBridgeScript } from './mocks';

// 原 issue #185 测试 provider 状态列表；#835 合规收口后该列表已移除，
// 改为验证「模型」tab 只剩「默认模型」下拉 + 登录门控。
test('模型 tab 收口后显示默认模型下拉与登录门控（#835）', async ({ page }) => {
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
  await page.getByRole('tab', { name: '模型' }).click();

  // 用 data-testid 定位唯一的头部「当前默认模型」，避免与 ModelQuickPanel 里的同名字段重复匹配
  await expect(page.getByTestId('providers-active-model')).toHaveText(
    '当前默认模型：deepseek-chat'
  );
  // 未登录（mock 默认 loggedIn:false）→ 显示登录门控而非模型下拉
  await expect(page.getByText('登录后使用平台内置模型')).toBeVisible();
  // #1000：一键浏览器登录按钮（按 testid 定位，页面顶栏/首屏有同名文案）
  await expect(page.getByTestId('model-quickpanel-login-btn')).toBeVisible();
  // 内置 DeepSeek 激活入口仍保留
  await expect(page.getByText('编辑当前模型')).toBeVisible();
  // 收口后不再显示 provider 状态列表
  await expect(page.getByText('验证成功')).not.toBeVisible();
  await expect(page.getByText(/匹配 Provider/)).not.toBeVisible();
});
