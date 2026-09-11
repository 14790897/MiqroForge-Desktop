/**
 * E2E: Config hot reload (issue #789)
 *
 * Validates the tier A/B/C behavior after a config save:
 *   1. Tier A save (temperature) → toast「配置已生效，无需重启」+ NO restart banner
 *   2. Tier C save (wsl_distro) → status bar「需要重启」+ reason text + warn toast
 *   3. Tier A save AFTER a tier C save → the pending restart banner must NOT
 *      be cleared (backend-authoritative pending state, 2026-08-31 review)
 *
 * The config saves go through the real bridge (config.update → hot_apply →
 * config_updated broadcast → renderer listener), so this covers the full
 * IPC chain: renderer → main → python bridge → event → renderer.
 *
 * Run: cd apps/desktop && npx electron-vite build &&
 *      PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test --project=electron config-hot-reload.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

test.describe.serial('Config hot reload (#789)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    // Capture bridge stdout for the config.update timeline (diagnostics).
    const proc = electronApp.process();
    (proc.stdout as any)?.on('data', (d: unknown) => {
      const s = String(d ?? '');
      if (s.includes('config.update') || s.includes('hot-apply') || s.includes('config_updated')) {
        console.log('[bridge-out]', s.trim().slice(0, 200));
      }
    });
  }, 120_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('tier A save → toast「配置已生效」 and no restart banner', async () => {
    // Save a hot-applicable setting (temperature) through the real bridge.
    // Compute a value guaranteed to DIFFER from the copied user config, so
    // the diff is never empty on re-runs (#12 review: hard-coded values
    // pollute the config on first run and go stale on the second).
    await page.evaluate(async () => {
      const cfg = await window.miqi.config.get();
      const current = cfg?.agents?.defaults?.temperature ?? 0.1;
      const next = (Math.round((current + 0.37) * 100) / 100) % 1.5;
      return window.miqi.config.update({
        agents: { defaults: { temperature: next } },
      });
    });

    await expect(page.getByTestId('config-updated-toast')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('config-updated-toast')).toContainText('配置已生效');
    // Tier A must NOT raise the restart banner.
    await expect(page.locator('body')).not.toContainText('需要重启');
    await page.screenshot({ path: 'test-results/shot-tier-a-toast.png' });
  });

  test('tier C save → restart banner with reason + warn toast', async () => {
    // Save a process-level setting (wsl_distro) — tier C.  Timestamp-suffixed
    // value so re-runs never produce an empty diff (#12 review).
    const distro = `Ubuntu-E2E-${Date.now()}`;
    await page.evaluate(
      (d) => window.miqi.config.update({ tools: { sandbox: { wsl_distro: d } } }),
      distro
    );

    // Status bar shows「需要重启」with the reason.
    await expect(page.locator('body')).toContainText('需要重启', { timeout: 15_000 });
    await expect(page.locator('body')).toContainText('WSL 发行版');
    await expect(page.locator('body')).toContainText('立即重启');

    // Warn toast mentions restart requirement.
    await expect(page.getByTestId('config-updated-toast')).toContainText('需要重启后生效');
    await page.screenshot({ path: 'test-results/shot-tier-c-banner.png' });
  });

  test('tier A save after tier C → pending restart banner persists', async () => {
    // The previous test's toast lives ~4s — wait for it to detach so this
    // test's assertions can only match the toast produced by THIS save
    // (2026-09-01 review: otherwise the stale toast satisfies the
    // visibility check vacuously, and its text is identical).
    await expect(page.getByTestId('config-updated-toast')).toBeHidden({ timeout: 10_000 });

    // The previous test already saved a tier C change (wsl_distro).  A tier A
    // save must NOT clear the banner — the backend reports the PENDING tier-C
    // state (current value vs startup snapshot), so the warn toast shows
    // again and the banner keeps its reason (2026-08-31 review).
    await page.evaluate(async () => {
      const cfg = await window.miqi.config.get();
      const current = cfg?.agents?.defaults?.temperature ?? 0.1;
      const next = (Math.round((current + 0.37) * 100) / 100) % 1.5;
      return window.miqi.config.update({
        agents: { defaults: { temperature: next } },
      });
    });

    // The new toast proves the listener processed the A save…
    await expect(page.getByTestId('config-updated-toast')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('config-updated-toast')).toContainText('需要重启后生效');
    // …and the pending tier C state keeps the banner + reason alive.
    await expect(page.locator('body')).toContainText('需要重启');
    await expect(page.locator('body')).toContainText('WSL 发行版');
    await page.screenshot({ path: 'test-results/shot-banner-persists-after-a-save.png' });
  });
});
