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
  sendMessage,
  waitForResponseComplete,
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
    { timeout: LLM_TIMEOUT },
    async () => {
      // 显式、强约束地要求模型先调用工具，再输出最终答复。
      await sendMessage(
        page,
        '不要直接回答，也不要解释。必须先调用 ask_user_confirm_card 工具弹出确认卡，' +
          '参数 title 必须是「确认执行方案？」，message 必须是「开始前需要你确认以下计划」。' +
          '只有在收到该工具结果后，才能回复 OK。',
      );

      const confirmCard = page.getByTestId('confirm-card').first();
      await expect(confirmCard.getByText('确认执行方案？', { exact: true })).toBeVisible({
        timeout: 120_000,
      });
      await expect(confirmCard.getByTestId('confirm-run')).toBeVisible();

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-real-card.png`,
      });

      // 点击确认 → tool result 回传模型 → 模型继续完成回合
      await confirmCard.getByTestId('confirm-run').click();
      await expect(page.getByText('已确认执行方案', { exact: true })).toBeVisible({
        timeout: 30_000,
      });

      await waitForResponseComplete(page, LLM_TIMEOUT);
      const assistantBubbles = page.getByTestId('chat-message-assistant');
      await expect(assistantBubbles.first()).toBeVisible({ timeout: 30_000 });

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-real-final.png`,
        fullPage: true,
      });
    },
  );
});
