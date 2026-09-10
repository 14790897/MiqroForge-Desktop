/**
 * Chat disclaimer E2E (issue #836) — 每条 AI 回答底部常驻免责声明。
 *
 * 走真实 LLM（沿用用户/CI 的 provider 配置）：发送一条消息 →
 * 等待 AI 回复完成 → 断言回复正文下方出现免责声明
 * （data-testid="chat-disclaimer"，文案为 CHAT_DISCLAIMER_ZH）。
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron -g "disclaimer"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  waitForResponseComplete,
  sendUntilDoneOrProviderDown,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

test.describe('Chat disclaimer (#836)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('AI 回复底部显示免责声明', { timeout: LLM_TIMEOUT * 2 }, async () => {
    const disclaimer = page.getByTestId('chat-disclaimer');

    // 真实 provider：共享 CI key 在并行 job 下会限流，回合报「模型服务暂时
    // 不可用或过载」→ 没有 AI 回复，免责声明永远渲染不出来。重发一次通常能
    // 落在限流窗口外；全部失败则跳过（被测功能从未拿到回复，fail 是噪音）。
    const replied = await sendUntilDoneOrProviderDown(
      page,
      '你好',
      async () => (await disclaimer.count()) > 0
    );
    test.skip(!replied, 'no AI reply on every attempt (provider unavailable or too slow)');

    // 等待 AI 流式回复完成
    await waitForResponseComplete(page, LLM_TIMEOUT);

    // 免责声明应出现在 AI 回复正文下方
    await expect(disclaimer.first()).toBeVisible({ timeout: 30_000 });
    await expect(disclaimer.first()).toContainText(
      'AI 生成内容仅供参考，可能存在错误，请自行核实关键信息'
    );

    await page.screenshot({
      path: `test-results/${test.info().title.replace(/\s+/g, '-')}-disclaimer.png`,
      fullPage: true,
    });
  });
});
