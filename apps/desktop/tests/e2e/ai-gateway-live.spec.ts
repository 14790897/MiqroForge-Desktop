/**
 * AI 网关真实账号 live E2E（opt-in，默认跳过，不入 CI 常规执行）。
 *
 * 与 billing-live.spec.ts 同策略：凭据仅经环境变量注入，登录态、密钥
 * 均落在 launchElectronApp 的临时 MIQI_HOME，测试结束随临时目录清理。
 *
 * 用法：
 *   QRAFT_LIVE=1 QRAFT_PHONE=<测试账号> QRAFT_PASSWORD=<密码> \
 *   PLAYWRIGHT_SKIP_WEB_SERVER=1 \
 *   npx playwright test --config=playwright.config.ts --project=electron ai-gateway-live.spec.ts
 *
 * 覆盖真实全链路（issue #922 / PR #946）：
 *   真实 Electron 应用 → QraftPage 手机号登录 → /oauth2/userinfo 下发
 *   encryptedApiKey（网关状态行"可用"+ 配置版本）→ 聊天发消息
 *   → 主进程写 token.json → Python make_provider 路由 AnthropicProvider
 *   → 平台 AI 网关真实回复。
 */

import { test, expect } from '@playwright/test';
import {
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
  sendMessage,
  type ElectronFixture,
} from './helpers/electron-setup';

const LIVE = process.env.QRAFT_LIVE === '1';
const PHONE = process.env.QRAFT_PHONE ?? '';
const PASSWORD = process.env.QRAFT_PASSWORD ?? '';
const READY = LIVE && PHONE !== '' && PASSWORD !== '';
const LLM_TIMEOUT = 300_000;

const describeFn = READY ? test.describe : test.describe.skip;

describeFn('AI 网关真实账号 live E2E (opt-in)', () => {
  let fixture: ElectronFixture;

  test.beforeAll(async () => {
    fixture = await launchElectronApp();
  }, 180_000);

  test.afterAll(async () => {
    if (fixture?.electronApp) await closeElectronApp(fixture.electronApp, fixture.miqiHome);
  });

  test('真实登录 → 网关可用 → 新会话消息经网关真实回复', { timeout: LLM_TIMEOUT }, async () => {
    const page = fixture.page;

    // 1. 设置页真实登录（幂等：dev userData 可能残留上次登录态）
    await page.getByText(/^(System Settings|系统设置)$/).click();
    await page
      .getByRole('tab')
      .filter({ hasText: /MiQroForge/ })
      .first()
      .click();
    const loggedInBadge = page.getByText('已登录');
    if (!(await loggedInBadge.isVisible({ timeout: 5000 }).catch(() => false))) {
      await page.getByTestId('qraft-phone-input').fill(PHONE);
      await page.getByTestId('qraft-password-input').fill(PASSWORD);
      await page.getByTestId('qraft-login-btn').click();
    }
    await expect(loggedInBadge).toBeVisible({ timeout: 90_000 });

    // 2. 网关状态：真实 userinfo 下发 encryptedApiKey → 状态行"可用" + 配置版本
    await expect(page.getByTestId('qraft-ai-gateway')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('qraft-ai-gateway-status')).toHaveText('可用');
    await expect(page.getByTestId('qraft-ai-gateway')).toContainText('配置版本 v1');

    // 3. 新会话发消息（默认模型 deepseek/deepseek-v4-flash 即网关模型）
    await createNewConversation(page);
    await sendMessage(page, 'ping');

    // 4. 真实回复经网关流式渲染（assistant 气泡出现 pong）
    await expect(page.getByTestId('chat-message-assistant').getByText(/pong/i).first()).toBeVisible(
      { timeout: 180_000 }
    );

    await page.screenshot({
      path: 'test-results/ai-gateway-live-reply.png',
      fullPage: true,
    });
  });
});
