/**
 * Auto Timeline E2E（#646-v2 GPT P0-3）— 必测 2 的 E2E 版。
 *
 * mock auto 分支（用户消息含"自动"）：web_search → write_file →
 * request_action_confirmation（ActionCard）→ 完成（不弹 PlanCard）。
 *
 * 断言：
 * 1. Auto 模式无 PlanCard（data-testid=plan-card 不出现）
 * 2. Timeline 出现（data-testid=timeline，非阻塞展示）
 * 3. 危险动作仍弹 ActionCard（确认）→ 完成后回合结束
 *
 * Run: cd apps/desktop && npx electron-vite build &&
 *      PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test \
 *      --config=playwright.config.ts --project=electron -g "auto timeline"
 */
import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { createConnection } from 'node:net';
import { join } from 'node:path';
import {
  LLM_TIMEOUT,
  sendMessage,
  waitForResponseComplete,
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
  APPS_DESKTOP,
} from './helpers/electron-setup';
import { startMockOpenAI, patchConfigForMock } from './helpers/mock-openai';

test.describe('Auto Timeline (#646-v2)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let mockServer: ChildProcess;
  let miqiHome: string;

  test.beforeAll(async () => {
    const mock = await startMockOpenAI();
    mockServer = mock.proc;
    const fixture = await launchElectronApp((config: any) =>
      patchConfigForMock(config, mock.mockUrl)
    );
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    page.on('pageerror', (err) => console.log(`[renderer-pageerror] ${String(err).slice(0, 300)}`));
    page.on('console', (msg) => {
      if (msg.type() === 'error') console.log(`[renderer-error] ${msg.text().slice(0, 300)}`);
    });
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mockServer?.kill();
  });

  test(
    'Auto 模式：无 PlanCard + Timeline 出现 + ActionCard 危险确认',
    { timeout: LLM_TIMEOUT },
    async () => {
      await createNewConversation(page);

      const modeBtn = page.getByRole('button', { name: /允许编辑/ }).first();
      await modeBtn.click();
      const autoOpt = page.getByRole('button', { name: /自动.*完全自主执行/ }).first();
      await expect(autoOpt).toBeVisible({ timeout: 10_000 });
      await autoOpt.click();
      const confirmBtn = page.getByRole('button', { name: /^确认$/ }).first();
      await expect(confirmBtn).toBeVisible({ timeout: 10_000 });
      await confirmBtn.click();
      await expect(page.getByText(/✓ 自主 已启用/)).toBeVisible({ timeout: 10_000 });
      await page.waitForTimeout(600);

      await sendMessage(page, '自动：生成 MOF-5 实验报告并上传');

      const autoApprove = async () => {
        try {
          for (let i = 0; i < 60; i++) {
            const dialog = page.getByRole('alertdialog').first();
            if (await dialog.isVisible().catch(() => false)) {
              const allow = dialog.getByRole('button', { name: /允许一次|允许/ }).first();
              if (await allow.isVisible().catch(() => false)) {
                await allow.click();
              }
            }
            await page.waitForTimeout(500);
          }
        } catch {
          // App closed: nothing left to approve.
        }
      };
      const approveTask = autoApprove();

      const actionCard = page.getByTestId('action-card').first();
      await expect(actionCard).toBeVisible({ timeout: 60_000 });
      await expect(actionCard.getByText('☁ 上传').first()).toBeVisible();
      await expect(actionCard.getByText('Qraft').first()).toBeVisible();
      await expect(actionCard.getByText('mof-report.json').first()).toBeVisible();
      await expect(actionCard.getByText(/23\.0 KB/)).toBeVisible();

      await page.screenshot({ path: 'test-results/auto-timeline-action-card.png' });
      await actionCard.getByRole('button', { name: '确认上传' }).click();
      await waitForResponseComplete(page, LLM_TIMEOUT);
      await expect(page.getByText(/已完成：MOF-5 实验报告/)).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId('plan-card')).toHaveCount(0);
      await expect(page.getByTestId('timeline')).toBeVisible();

      await approveTask;
    }
  );
});
