/**
 * E2E tests for exec delta flooding fix.
 *
 * Verifies that exec command output renders inline in terminal blocks
 * without flooding workbench with empty "[outputDelta]" progress rows.
 *
 * Run:
 *   npm run test:e2e -- --project=electron regression-284.spec.ts
 */
import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  sendMessage,
  waitForResponseComplete,
  createNewConversation,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

test.describe.serial('Exec delta flooding fix', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 120_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test(
    'exec output shows inline command output, no empty delta rows',
    { timeout: LLM_TIMEOUT },
    async () => {
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

      await createNewConversation(page);
      const prompt =
        '用 exec 工具执行: echo "DELTA_TEST_OK" && echo "line2"。只输出命令结果，不要解释。';
      await sendMessage(page, prompt);

      try {
        await expect(page.locator('[data-testid="thinking-indicator"]')).toBeHidden({
          timeout: 240_000,
        });
      } catch {
        const mainText = await page.locator('main').textContent();
        console.log('[test] AI stuck — main textContent (last 1000):');
        console.log((mainText || '').slice(-1000));
        throw new Error('Thinking indicator did not hide');
      }

      await page.waitForTimeout(3000);

      const mainText = (await page.locator('main').textContent()) || '';

      expect(mainText).toContain('DELTA_TEST_OK');
      expect(mainText).toContain('line2');
      expect(mainText).not.toContain('outputDelta');
      expect(mainText).not.toContain('item/commandExecution');
      expect(mainText).not.toContain('ExecCommandOutputDeltaEvent');

      console.log('[test] ✅ Exec output clean — no delta flooding');
    }
  );

  test('exec stderr output renders inline without flooding', { timeout: LLM_TIMEOUT }, async () => {
    await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

    await createNewConversation(page);
    const prompt =
      '用 exec 工具执行: echo OK_STDERR >&2 && echo NORMAL_OUTPUT。只输出命令结果，不要解释。';
    await sendMessage(page, prompt);

    try {
      await expect(page.locator('[data-testid="thinking-indicator"]')).toBeHidden({
        timeout: 240_000,
      });
    } catch {
      const mainText = await page.locator('main').textContent();
      console.log('[test] AI stuck — main textContent (last 1000):');
      console.log((mainText || '').slice(-1000));
      throw new Error('Thinking indicator did not hide');
    }

    await page.waitForTimeout(3000);

    const mainText = (await page.locator('main').textContent()) || '';

    expect(mainText).toContain('OK_STDERR');
    expect(mainText).toContain('NORMAL_OUTPUT');
    expect(mainText).not.toContain('outputDelta');
    expect(mainText).not.toContain('item/commandExecution');

    console.log('[test] ✅ stderr output also clean');
  });

  test('multiple exec calls do not accumulate flooding', { timeout: LLM_TIMEOUT }, async () => {
    await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

    await createNewConversation(page);
    const prompt =
      '按顺序执行以下 3 条命令，每条只回复 exec 的实际输出：' +
      '1) echo "ROUND1"' +
      '2) echo "ROUND2"' +
      '3) echo "ROUND3"' +
      '不要加任何解释';
    await sendMessage(page, prompt);

    try {
      await expect(page.locator('[data-testid="thinking-indicator"]')).toBeHidden({
        timeout: 240_000,
      });
    } catch {
      const mainText = await page.locator('main').textContent();
      console.log('[test] AI stuck — main textContent (last 1000):');
      console.log((mainText || '').slice(-1000));
      throw new Error('Thinking indicator did not hide');
    }

    await page.waitForTimeout(3000);

    const mainText = (await page.locator('main').textContent()) || '';

    expect(mainText).toContain('ROUND1');
    expect(mainText).toContain('ROUND2');
    expect(mainText).toContain('ROUND3');
    expect(mainText).not.toContain('outputDelta');

    console.log('[test] ✅ 3 exec rounds — clean output, no flooding');
  });
});
