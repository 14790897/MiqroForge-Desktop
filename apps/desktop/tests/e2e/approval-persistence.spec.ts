/**
 * E2E: Approval Persistence (永久允许 → next call auto-approves)
 *
 * Validates that after a user clicks "永久允许" for a tool operation,
 * subsequent identical operations are automatically approved without
 * showing the approval dialog again.
 *
 * KEY: write_file approval is path-specific, not tool-wide.  The
 * permanent allowlist pattern is "write_file:<path>".  To test
 * persistence, both calls MUST target the same file path.
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts --project=electron approval-persistence.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  sendMessage,
  waitForResponseComplete,
  createNewConversation,
  waitForBridgeInitialized,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

// ─── Test Suite ───────────────────────────────────────────────────

const SKIP_APPROVAL_ON_CI = !!process.env.CI;

test.describe('Approval Persistence E2E', () => {
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

  // ═══════════════════════════════════════════════════════════════
  //  Test 1: 永久允许 persists for same file path
  // ═══════════════════════════════════════════════════════════════

  test(
    '永久允许: same file path twice, second call auto-approves',
    { timeout: LLM_TIMEOUT * 3 },
    async () => {
      test.skip(SKIP_APPROVAL_ON_CI, 'CI disables commandApproval — approval dialog never appears');
      // Ensure bridge ready, clear existing approvals
      await waitForBridgeInitialized(page);
      try {
        await page.evaluate(() => (window as any).miqi.approvals.clearPermanent());
      } catch {
        /* fine */
      }

      await createNewConversation(page);

      // Use the SAME file path for both calls so permanent allowlist matches
      const filepath = `e2e_persist_${Date.now()}.txt`;

      // ── First call: same file path → shows approval dialog ──
      await sendMessage(page, `Use write_file to create ${filepath} with content "first write"`);

      await expect(page.getByTestId('approval-title')).toBeVisible({
        timeout: 60_000,
      });
      console.log('[test] ✅ First call: approval dialog appeared');

      await page.getByTestId('approval-allow-permanent').click();
      console.log('[test] Clicked 永久允许');

      await waitForResponseComplete(page, 240_000);
      await expect(page.locator('main').getByText(filepath, { exact: false }).first()).toBeVisible({
        timeout: 15_000,
      });
      console.log(`[test] ✅ First file created: ${filepath}`);

      // ── Second call: SAME file path → should auto-approve ──
      await sendMessage(
        page,
        `Use write_file to overwrite ${filepath} with content "second write — auto-approved"`
      );

      await waitForResponseComplete(page, 240_000);

      // Must NOT show approval dialog again
      await expect(page.getByTestId('approval-title')).not.toBeVisible({
        timeout: 5_000,
      });
      console.log('[test] ✅ Second call: no approval dialog (auto-approved)');

      await expect(page.locator('main').getByText(filepath, { exact: false }).first()).toBeVisible({
        timeout: 15_000,
      });
      console.log(`[test] ✅ Second write auto-approved: ${filepath}`);
    }
  );

  // ═══════════════════════════════════════════════════════════════
  //  Test 2: Cross-session auto-approval
  // ═══════════════════════════════════════════════════════════════

  test(
    '永久允许: persists across new conversations (same path)',
    { timeout: LLM_TIMEOUT * 3 },
    async () => {
      test.skip(SKIP_APPROVAL_ON_CI, 'CI disables commandApproval — approval dialog never appears');
      try {
        await page.evaluate(() => (window as any).miqi.approvals.clearPermanent());
      } catch {
        /* ok */
      }

      await createNewConversation(page);
      const filepath = `e2e_cross_${Date.now()}.txt`;

      // ── Conv 1: approve permanently ──
      await sendMessage(
        page,
        `Use write_file to create ${filepath} with content "cross-session test"`
      );
      await expect(page.getByTestId('approval-title')).toBeVisible({ timeout: 60_000 });
      await page.getByTestId('approval-allow-permanent').click();
      await waitForResponseComplete(page, 240_000);
      console.log(`[test] ✅ Conv 1: approved permanently for ${filepath}`);

      // ── Conv 2: new session, same path → auto-approve ──
      await createNewConversation(page);
      await sendMessage(
        page,
        `Use write_file to overwrite ${filepath} with content "cross-session overwrite"`
      );
      await waitForResponseComplete(page, 240_000);

      await expect(page.getByTestId('approval-title')).not.toBeVisible({ timeout: 5_000 });
      console.log('[test] ✅ Conv 2: no approval dialog (cross-session permanent allowlist)');
    }
  );
});
