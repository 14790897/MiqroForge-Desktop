/**
 * E2E: Reasoning Mode (Fast/Think) — issue #680
 *
 * Validates (welcome-page EB-1 redesign 之后):
 * 1. 空状态（未开始对话）渲染 EB-1 模式卡：⚡极速问答 / 🧠深度研究 / 💻代码任务，
 *    默认选中极速问答（fast）；点击卡切换模式并持久化到 sessionStorage；
 *    代码任务卡映射 think（与深度研究同一档）。
 * 2. 发送消息后进入对话窗口，输入条里的 ReasoningModeSwitch 接管模式显示。
 * 3. Sending a message stamps the user bubble with the mode tag.
 * 4. Default mode is fast (user decision: 默认极速版).
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts --project=electron reasoning-mode.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  launchElectronApp,
  closeElectronApp,
  waitForBridgeInitialized,
  waitForInputReady,
  userMessage,
} from './helpers/electron-setup';

test.describe('Reasoning Mode E2E', () => {
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    await waitForBridgeInitialized(page);
  }, 60_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp);
  });

  test('welcome hero shows three mode cards with fast selected by default', async () => {
    const fastCard = page.getByRole('button', { name: /极速问答/ }).first();
    await expect(fastCard).toBeVisible({ timeout: 15_000 });
    await expect(fastCard).toContainText('✓ 已选择');

    await expect(page.getByRole('button', { name: /深度研究/ }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /代码任务/ }).first()).toBeVisible();

    // Default fast persisted (fresh app → sessionStorage empty → fast)
    await expect
      .poll(async () => page.evaluate(() => sessionStorage.getItem('miqi-reasoning-mode')))
      .toBe('fast');
  });

  test('clicking 深度研究 / 代码任务 cards switches to think and persists', async () => {
    const thinkCard = page.getByRole('button', { name: /深度研究/ }).first();
    await thinkCard.click();
    await expect(thinkCard).toContainText('✓ 已选择');
    await expect
      .poll(async () => page.evaluate(() => sessionStorage.getItem('miqi-reasoning-mode')))
      .toBe('think');

    // 代码任务卡映射 think：仅它高亮，深度研究卡取消选中，持久化仍为 think
    const codeCard = page.getByRole('button', { name: /代码任务/ }).first();
    await codeCard.click();
    await expect(codeCard).toContainText('✓ 已选择');
    await expect(thinkCard).not.toContainText('✓ 已选择');
    await expect
      .poll(async () => page.evaluate(() => sessionStorage.getItem('miqi-reasoning-mode')))
      .toBe('think');

    // 回到 fast 供后续用例使用
    const fastCard = page.getByRole('button', { name: /极速问答/ }).first();
    await fastCard.click();
    await expect(fastCard).toContainText('✓ 已选择');
    await expect
      .poll(async () => page.evaluate(() => sessionStorage.getItem('miqi-reasoning-mode')))
      .toBe('fast');
  });

  test('fast-mode send completes and assistant answer carries 🚀 icon', async () => {
    // Ensure fast active (default) — 在空状态用模式卡兜底
    const fastCard = page.getByRole('button', { name: /极速问答/ }).first();
    if (!(await fastCard.textContent())?.includes('已选择')) {
      await fastCard.click();
    }
    await waitForInputReady(page);

    const input = page.locator('textarea').first();
    await input.fill('只回答"好的"两个字');
    await input.press('Enter');

    // User bubble appears
    const userBubble = userMessage(page, '只回答');
    await expect(userBubble).toBeVisible({ timeout: 15_000 });

    // No text label on the user bubble anymore (removed #680 跟进) —
    // the assistant answer carries the 🚀 fast-mode icon instead.
    await expect(userBubble.getByText('极速回答')).toHaveCount(0);

    // Assistant answer appears (fast mode).  Scope the locator to the
    // ASSISTANT bubble (CodeRabbit #783): the user input itself contains
    // 好的, so a global match could pass before any answer renders.
    const answer = page
      .locator('[data-testid="chat-message-assistant"]')
      .filter({ hasText: '好的' })
      .last();
    await expect(answer).toBeVisible({ timeout: 60_000 });

    // Fast-mode 🚀 badge — EXACTLY ONE on screen, never duplicated (#905):
    // when a thinking block rendered above, its header icon column carries
    // 🚀 and the reply bubble must NOT repeat it; without thinking the
    // inline badge on the bubble is the single marker.
    const thinkHeader = page.getByText(/快速思考/).first();
    if (await thinkHeader.isVisible().catch(() => false)) {
      await expect(answer).not.toContainText('🚀');
      await expect(page.getByText('🚀', { exact: true }).first()).toBeVisible();
    } else {
      await expect(answer).toContainText('🚀');
    }
  });
});
