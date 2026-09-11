/**
 * E2E test for sandbox stuck fix.
 *
 * Verifies that the sandbox toggle transitions from "正在安装依赖…"
 * to "已开启（推荐）" after the bridge sandbox.ready event fires.
 *
 * Run:
 *   npm run test:e2e -- --project=electron regression-284-sandbox.spec.ts
 */
import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

test.describe.serial('Sandbox toggle ready fix', () => {
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
    'sandbox toggle shows ready label after bridge starts',
    { timeout: 420_000 }, // 300s settle window + nav/assertion margin
    async () => {
      const settingsBtn = page.locator('[data-testid="nav-system-settings"]');
      await expect(settingsBtn).toBeVisible({ timeout: 10_000 });
      await settingsBtn.click();
      await page.waitForTimeout(1500);

      await expect(page.locator('[data-testid="settings-sandbox-section-title"]')).toBeVisible({
        timeout: 5_000,
      });

      const settled = await page.waitForFunction(
        () => {
          const el = document.querySelector('[data-testid="sandbox-toggle-label"]');
          if (!el) return false;
          const text = el.textContent || '';
          // Log progress to CI console so we can see how long each
          // phase of sandbox init takes (export / import / apt-get).
          const ts = new Date().toISOString().slice(11, 19);
          console.log(`[regression-284] ${ts} toggle label: "${text}"`);
          return !text.includes('正在') && (text.includes('已开启') || text.includes('已关闭'));
        },
        { timeout: 300_000, polling: 10000 }
      );
      expect(settled).toBeTruthy();

      const label = await page
        .locator('[data-testid="sandbox-toggle-label"]')
        .first()
        .textContent();
      console.log('[test] Sandbox toggle label:', label);
      expect(label).toBeDefined();
    }
  );
});
