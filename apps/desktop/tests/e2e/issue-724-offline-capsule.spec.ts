/**
 * E2E regression spec for issue #724 — [BUG] 顶栏「离线」状态胶囊点击无任何反应。
 *
 * User-facing contract verified (matches the issue's expected behavior):
 *   1. Runtime running → capsule shows「已同步」and is disabled (pure status,
 *      no click handler needed).
 *   2. Runtime stopped → capsule shows「离线」, is enabled, and clicking it
 *      calls RuntimeContext.start() → the bridge comes back up and the
 *      capsule returns to「已同步」.
 *   3. The capsule carries a tooltip (title) explaining its state — the
 *      issue's plan-B ask (hover affordance) is also covered here.
 *
 * The test drives the REAL runtime lifecycle via window.miqi.runtime.stop()
 * (the same IPC the Settings panel uses) instead of mocking, so it exercises
 * the full click → start() → bridge restart path.
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts \
 *        --project=electron issue-724-offline-capsule.spec.ts --workers=1
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

const CAPSULE = '[data-testid="runtime-status-capsule"]';

test.describe('Issue #724: 顶栏「离线」状态胶囊点击重连', () => {
  test.describe.configure({ mode: 'serial' });

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    // Sandbox off keeps the bridge cold-start fast and avoids the
    // 146-169s IPC stall seen on cold e2e runs (see miqi-codebase skill).
    const fixture = await launchElectronApp((config) => {
      config.tools = {
        ...(config.tools ?? {}),
        sandbox: { ...(config.tools?.sandbox ?? {}), enabled: false },
      };
      return config;
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 120_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('1: running 时胶囊显示「已同步」且不可点击', async () => {
    const capsule = page.locator(CAPSULE);
    await expect(capsule).toBeVisible({ timeout: 30_000 });
    await expect(capsule).toContainText('已同步');
    await expect(capsule).toBeDisabled();
    // Plan-B affordance: tooltip explains the state.
    await expect(capsule).toHaveAttribute('title', '运行时已连接');
  });

  test('2: stop 后胶囊显示「离线」可点击，点击触发 start() 恢复 running', async () => {
    // ── Stop the runtime through the real IPC path ────────────────────
    const stopped = await page.evaluate(async () => {
      const s = await (window as any).miqi.runtime.stop();
      return s?.state;
    });
    console.log('[test] runtime.stop() →', stopped);
    expect(['stopped', 'stopping']).toContain(stopped);

    // Capsule flips to 离线 and becomes clickable.
    const capsule = page.locator(CAPSULE);
    await expect(capsule).toContainText('离线', { timeout: 30_000 });
    await expect(capsule).toBeEnabled();
    await expect(capsule).toHaveAttribute('title', '运行时未连接，点击重新连接');

    // ── Click the capsule — this is the #724 fix under test ───────────
    await capsule.click();

    // Bridge comes back → capsule returns to 已同步 and is disabled again.
    await expect(capsule).toContainText('已同步', { timeout: 120_000 });
    await expect(capsule).toBeDisabled();

    const status = await page.evaluate(async () => {
      const s = await (window as any).miqi.runtime.status();
      return s?.state;
    });
    console.log('[test] after capsule click, runtime.status() →', status);
    expect(status).toBe('running');
  });
});
