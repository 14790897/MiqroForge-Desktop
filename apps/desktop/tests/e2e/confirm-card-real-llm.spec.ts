/**
 * Confirm Card E2E — REAL LLM path (issue #646 真机验证)。
 *
 * 与 confirm-card.spec.ts（mock 状态机）互补：本 spec 不 patch provider，
 * 使用配置中的真实模型（本地 deepseek / CI siliconflow），显式指令模型
 * 调用 ask_user_confirm_card，验证真实模型 + 真实 HTTP 请求下卡片渲染、
 * 阻塞、用户选择回传、回合完成的完整链路。
 *
 * 断言刻意收敛：真实模型回复文案不可控，只断言卡片出现、决议回传、
 * 回合正常收尾（有 assistant 回复且流式结束）。
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron -g "real LLM"
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  waitForResponseComplete,
  sendUntilDoneOrProviderDown,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

test.describe('Confirm Card (real LLM)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    // 真实 provider（不 patch 配置）——本地走 deepseek，CI 走 siliconflow
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test(
    '真实模型调用 ask_user_confirm_card — 弹卡、点击确认、tool result 回传、回合完成',
    { timeout: LLM_TIMEOUT * 2 },
    async () => {
      const cardArea = page.getByTestId('confirm-card-area');
      const resolvedArea = page.getByTestId('confirm-card-resolved');

      // 显式指令模型调用工具（真实 HTTP 请求到 provider）。macos-e2e 上共享
      // CI key 限流会让回合报「模型服务暂时不可用」——重发一次；全失败则跳过
      // （卡片从未出现，fail 是噪音而非回归）。
      const cardAppeared = await sendUntilDoneOrProviderDown(
        page,
        '请立即调用 ask_user_confirm_card 工具弹出确认卡片：' +
          'title 用「确认执行方案？」，message 用「开始前需要你确认以下计划」。' +
          '调用后收到结果时直接回复 OK。',
        async () => (await cardArea.count()) > 0
      );
      test.skip(
        !cardAppeared,
        'no AI reply on every attempt (provider unavailable or too slow) — no confirm card to verify'
      );

      // 真实模型往返（本地 deepseek / CI siliconflow）——给足超时
      await expect(cardArea).toBeVisible({ timeout: 30_000 });
      await expect(cardArea.getByText('确认执行方案？')).toBeVisible();
      await expect(cardArea.getByRole('button', { name: '确认执行' })).toBeVisible();

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-real-card.png`,
      });

      // 点击确认 → 选择回传模型 → 模型继续完成回合
      await cardArea.getByRole('button', { name: '确认执行' }).click();
      await expect(resolvedArea.getByText('已选择「确认执行」')).toBeVisible({
        timeout: 30_000,
      });

      await waitForResponseComplete(page, LLM_TIMEOUT);
      // 回合正常收尾：至少有一条 assistant 回复（内容由真实模型生成，不断言文案）
      const assistantBubbles = page.getByTestId('chat-message-assistant');
      await expect(assistantBubbles.first()).toBeVisible({ timeout: 30_000 });

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-real-final.png`,
        fullPage: true,
      });
    }
  );
});
