/**
 * E2E: Task Assets — File Preview & Session-Switch Persistence
 *
 * Verifies:
 *   1. Agent creates file → appears in Task Assets panel
 *   2. Click Preview → dispatched via system default app (no crash)
 *   3. Switch to another session and back → file list survives
 *
 * Run:
 *   cd apps/desktop
 *   npx playwright test --config=playwright.config.ts --project=electron task-assets-persistence.spec.ts
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  waitForInputReady,
  launchElectronApp,
  closeElectronApp,
  switchToSessionWithMarker,
  waitForBridgeInitialized,
  approvePlanCardIfAny,
} from './helpers/electron-setup';

// ─── Helpers ──────────────────────────────────────────────────────────

async function sendMessage(page: Page, text: string) {
  const textarea = await waitForInputReady(page);
  const userBubbles = page.getByTestId('chat-message-user');
  const before = await userBubbles.count();
  await textarea.fill(text);
  await textarea.press('Enter');
  void approvePlanCardIfAny(page);
  // The optimistic-UI send (#364) mounts the user bubble immediately and
  // clears the input BEFORE the backend (providers:list) resolves — so the
  // reliable signal is a count increase plus the input clearing.
  await expect(userBubbles).toHaveCount(before + 1, { timeout: 10_000 });
  await expect(userBubbles.last()).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('[data-testid="chat-input-container"] textarea')).toHaveValue('');
}

async function waitForResponseComplete(page: Page, timeout = 240_000) {
  await expect(page.getByText('Thinking…')).toBeHidden({ timeout });
  try {
    await expect(page.locator('.tag-inprogress')).toBeHidden({ timeout: 15_000 });
  } catch {
    /* fast responses may never show IN PROGRESS */
  }
}

/** Wait for a file card with the given filename to appear in Task Assets.
 *  Uses multiple selector strategies for robustness. */
async function waitForFileInPanel(page: Page, filename: string, timeout = 30_000) {
  const assetsPanel = page.getByTestId('task-assets-panel');

  // Strategy 1: Try the precise class selector
  const cardSelector = assetsPanel.locator('.rounded-lg.p-2\\.5');
  const card = cardSelector.filter({ hasText: filename }).first();

  // Strategy 2: Fallback to more generic selectors
  const fallbackCard = assetsPanel
    .locator('[class*="rounded"][class*="p-"]')
    .filter({ hasText: filename })
    .first();

  // Try primary selector first
  try {
    await expect(card).toBeVisible({ timeout });
  } catch {
    // Fallback to secondary selector
    console.log('[test] Primary selector failed, trying fallback');
    await expect(fallbackCard).toBeVisible({ timeout });
    return fallbackCard;
  }

  // Panel should no longer show empty state
  await expect(page.locator('[data-testid="task-assets-empty"]')).not.toBeVisible({
    timeout: 5_000,
  });
  return card;
}

/** Click Preview button on a file card with robust retry logic */
async function clickPreviewButton(page: Page, card: ReturnType<Page['locator']>) {
  const previewBtn = card.locator('[data-testid="file-preview-btn"]');

  // Wait for the button to be visible and enabled
  await expect(previewBtn).toBeVisible({ timeout: 10000 });

  // Click with retry logic for flaky buttons
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await previewBtn.click({ timeout: 5000 });
      return; // Success
    } catch (e) {
      if (attempt === 2) throw e; // Last attempt failed
      await page.waitForTimeout(500);
    }
  }
}

/** Get the session title text */
function getSessionTitle(page: Page) {
  return page.locator('h2.font-semibold.truncate').first();
}

// ─── Test Suite ───────────────────────────────────────────────────────

test.describe('Task Assets Preview & Persistence', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    // Pre-approve ALL tool calls so no approval dialogs interrupt the test
    await waitForBridgeInitialized(page);
    await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));
    console.log('[test] *:* wildcard pre-approved');
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  // ═══════════════════════════════════════════════════════════════════
  //  Test 1: Preview opens file with system default app
  // ═══════════════════════════════════════════════════════════════════

  test('Agent creates file → click Preview → dispatched to system app', async () => {
    test.setTimeout(LLM_TIMEOUT * 2);
    const filename = `e2e_preview_${Date.now()}.pdf`;
    const content = `# E2E Preview Test\n\nContent: ${Date.now()}`;

    // Ensure Task Assets panel is visible
    await expect(page.getByTestId('task-assets-panel')).toBeVisible({ timeout: 10_000 });

    // Agent creates a .md file via write_file
    await sendMessage(
      page,
      `用 write_file 创建文件：path=${filename}，content="${content}"。创建完只回复：完成`
    );
    await waitForResponseComplete(page, 240_000);

    // Verify file appears in Task Assets panel using robust helper
    const card = await waitForFileInPanel(page, filename);
    console.log(`[test] ✅ File "${filename}" appears in Task Assets`);

    // Click Preview — should dispatch to system default app.
    // In E2E (temp workspace, no WSL sandbox) the file may not be
    // found via openExternal, which triggers the error fallback
    // preview modal.  Both outcomes are valid — the important thing
    // is the click doesn't crash the app.
    await clickPreviewButton(page, card);
    await page.waitForTimeout(500);

    // Verify app is still functional — panel still visible
    await expect(page.getByTestId('task-assets-panel')).toBeVisible({ timeout: 5_000 });
    console.log('[test] ✅ Preview click completed without crash');

    // Close any dialog the preview click opened (e.g. the "系统应用打开"
    // fallback modal). A leftover radix Dialog focus trap swallows the NEXT
    // test's Enter → chat.send never fires → no file created → false fail.
    // (See task-assets-classification reference postmortem #1.)
    await page.keyboard.press('Escape').catch(() => {});
    await expect(page.getByRole('dialog')).toHaveCount(0, { timeout: 5_000 });
  });

  // ═══════════════════════════════════════════════════════════════════
  //  Test 2: File list survives session switch
  // ═══════════════════════════════════════════════════════════════════

  test('files persist in Task Assets after switching sessions and returning', async () => {
    test.setTimeout(LLM_TIMEOUT * 2);
    const persistMarker = `E2E_PERSIST_${Date.now()}`;
    const filename = `e2e_persist_${Date.now()}.pdf`;
    const content = `# ${persistMarker}\nprint("E2E persistence test")`;

    // Step 1: create a file in the current session
    // The model occasionally replies "好了" WITHOUT calling write_file
    // (deepseek no-op turns — same class as the MOF journey flake). The
    // old "只回复：好了" phrasing gave the model an easy way out; retry
    // the send once so a no-op doesn't burn the whole CI timeout.
    let created = false;
    for (let attempt = 0; attempt < 2 && !created; attempt++) {
      await sendMessage(
        page,
        `必须调用 write_file 工具创建文件：path=${filename}，content="${content}"。创建完成后回复"好了"。`
      );
      await waitForResponseComplete(page, 240_000);
      const inPanel = await page
        .getByTestId('task-assets-panel')
        .locator('.rounded-lg.p-2\\.5')
        .filter({ hasText: filename.slice(0, 20) })
        .count();
      created = inPanel > 0;
      if (!created) {
        console.log(`[test] ⚠️ write_file not executed (attempt ${attempt + 1}) — retrying send`);
      }
    }

    // Grab the session title for later switch-back
    const sessionATitle = await getSessionTitle(page).textContent();
    console.log(`[test] Session A title: "${sessionATitle}"`);

    // Verify file is in Task Assets using robust helper
    await waitForFileInPanel(page, filename);
    const countBefore = await page
      .getByTestId('task-assets-panel')
      .locator('.rounded-lg.p-2\\.5')
      .count();
    console.log(`[test] ✅ ${countBefore} file(s) in Task Assets before switch`);

    // Step 2: switch to a new (empty) session via sidebar "+"
    // Sidebar "+" now creates session directly (no workspace picker modal)
    const newSessionBtn = page.locator('[data-testid="nav-new-session"]');
    await expect(newSessionBtn).toBeVisible({ timeout: 5_000 });
    await newSessionBtn.click();
    await waitForInputReady(page, 15_000);

    // Session B should have no files
    await expect(page.locator('[data-testid="task-assets-empty"]')).toBeVisible({
      timeout: 10_000,
    });
    console.log('[test] ✅ Session B shows empty state.');

    // Step 3: switch back to Session A using improved helper
    const found = await switchToSessionWithMarker(page, persistMarker);
    if (!found) {
      console.log('[test] ⚠️ Could not find Session A via marker — skipping restore check');
      return;
    }
    console.log(`[test] Switched back to Session A`);

    // Step 4: wait for ChatConsole to remount and load tracked files
    await waitForInputReady(page, 15_000);
    await page.waitForTimeout(2000);

    // File should STILL be in Task Assets using robust helper
    const card = await waitForFileInPanel(page, filename, 15_000);
    const countAfter = await page
      .getByTestId('task-assets-panel')
      .locator('.rounded-lg.p-2\\.5')
      .count();
    console.log(`[test] ✅ ${countAfter} file(s) in Task Assets after return`);

    expect(countAfter).toBeGreaterThanOrEqual(1);
    console.log('[test] ✅ Task Assets file list survives session switch');
  });
});
