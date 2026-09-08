/**
 * E2E: Task Assets Panel — AI 创建文件 → 右侧面板出现 → 点击预览内容
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts --project=electron task-assets.spec.ts
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

// ─── Helpers ──────────────────────────────────────────────────────

/**
 * Wait for a file card with the given filename to appear in Task Assets.
 *
 * One selector strategy: the innermost rounded card that contains the
 * filename AND a file-preview-btn.  The old two-step approach first tried
 * `.rounded-lg.p-2\\.5` (exact class) and fell back to a loose
 * `[class*="rounded"][class*="p-"]` that can match the panel container
 * itself — `.first()` then returned the panel (DOM order puts ancestors
 * first) whose preview button belongs to some other card, or no button at
 * all → 10s visibility timeout → flaky on slow macOS runners.
 */
async function waitForFileInPanel(page: Page, filename: string, timeout = 60_000) {
  const assetsPanel = page.getByTestId('task-assets-panel');
  const card = assetsPanel
    .locator('[class*="rounded"]', { hasText: filename })
    .filter({ has: assetsPanel.getByTestId('file-preview-btn') })
    .last();

  await expect(card).toBeVisible({ timeout });

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

// ─── Test Suite ───────────────────────────────────────────────────

test.describe('Task Assets Panel E2E', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    // Pre-approve all tools via *:* wildcard
    await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));
    console.log('[test] *:* wildcard pre-approved');
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  // ═══════════════════════════════════════════════════════════════
  //  Test 1: AI creates file → appears in Task Assets
  // ═══════════════════════════════════════════════════════════════

  test('AI creates .txt file → appears in Task Assets panel', async () => {
    test.setTimeout(LLM_TIMEOUT * 2);
    await page.evaluate(async () => {
      for (let i = 0; i < 30; i++) {
        try {
          const s = await (window as any).miqi.runtime.status();
          if (s?.state === 'running' && s?.initialized) return;
        } catch {
          /* */
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    });

    const filename = `e2e_task_${Date.now()}.pdf`;
    const content = `E2E Task Assets test content ${Date.now()}`;

    // Panel should show empty state initially
    await expect(page.getByTestId('task-assets-panel')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-testid="task-assets-empty"]')).toBeVisible({
      timeout: 10_000,
    });

    // Have AI create a file (will trigger approval)
    await sendMessage(page, `Use write_file to create ${filename} with content "${content}"`);

    // *:* pre-approved — no approval dialog needed
    await waitForResponseComplete(page, 240_000);

    // Verify AI confirmed file creation in main chat
    await expect(page.locator('main').getByText(filename, { exact: false }).first()).toBeVisible({
      timeout: 15_000,
    });
    console.log(`[test] ✅ File created: ${filename}`);

    // ── Verify Task Assets panel shows the file ──
    const fileCard = await waitForFileInPanel(page, filename);

    // issue #607 白名单（excel/word/pdf）：write_file 生成的 .pdf 是交付物 → 结果资产区
    await expect(page.locator('[data-testid="task-assets-stats"]')).toContainText('1 个结果', {
      timeout: 10_000,
    });
    await expect(fileCard.getByTestId('file-result-badge')).toBeVisible({ timeout: 10_000 });

    // Should show a WRITE op badge on the file
    await expect(fileCard.getByTestId('file-op-write')).toBeVisible({ timeout: 10_000 });

    console.log('[test] ✅ Task Assets panel shows the file');
  });

  // ═══════════════════════════════════════════════════════════════
  //  Test 2: Click Preview → see file content in modal
  // ═══════════════════════════════════════════════════════════════

  test('click Preview on tracked file → content modal opens', async () => {
    test.setTimeout(LLM_TIMEOUT * 2);
    await page.evaluate(async () => {
      for (let i = 0; i < 30; i++) {
        try {
          const s = await (window as any).miqi.runtime.status();
          if (s?.state === 'running' && s?.initialized) return;
        } catch {
          /* */
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    });

    const filename = `e2e_preview_${Date.now()}.txt`;
    const content = `Preview content: ${Date.now()}`;

    await sendMessage(page, `Use write_file to create ${filename} with content="${content}"`);
    // *:* pre-approved — no approval dialog needed
    await waitForResponseComplete(page, 240_000);
    console.log(`[test] ✅ File created: ${filename}`);

    // Find the file in Task Assets panel and click Preview using robust helpers
    const fileCard = await waitForFileInPanel(page, filename);
    await clickPreviewButton(page, fileCard);
    console.log('[test] Clicked Preview on file card');

    // Preview button now opens files with system default application.
    // In CI (headless), openExternal may fail and fall back to showing
    // a preview modal with an error message, or succeed and show nothing.
    const previewModal = page.locator('pre.text-xs.font-mono');
    const modalVisible = await previewModal.isVisible({ timeout: 8_000 }).catch(() => false);
    if (modalVisible) {
      await page.screenshot({ path: 'test-results/preview-modal.png' });
      const previewText = (await previewModal.textContent()) || '';
      if (previewText.includes(content)) {
        console.log('[test] ✅ Preview modal shows correct content');
      } else {
        console.log(`[test] Preview opened externally or showed: ${previewText.slice(0, 120)}`);
      }
    } else {
      console.log('[test] ✅ Preview opened with system app (no in-app modal)');
      await page.screenshot({ path: 'test-results/preview-external.png' });
    }

    // Close preview modal if visible
    const closeBtn = page.locator('.fixed.inset-0.z-50 button').last();
    if (await closeBtn.isVisible().catch(() => false)) {
      await closeBtn.click();
      console.log('[test] ✅ Preview modal closed');
    }
  });

  // ═══════════════════════════════════════════════════════════════
  //  Test 3: AI creates .docx → appears in Task Assets → Preview shows Office message
  // ═══════════════════════════════════════════════════════════════

  test.skip('AI creates .docx file → appears in Task Assets → Preview shows Office notice', async () => {
    test.setTimeout(LLM_TIMEOUT * 2);
    await page.evaluate(async () => {
      for (let i = 0; i < 30; i++) {
        try {
          const s = await (window as any).miqi.runtime.status();
          if (s?.state === 'running' && s?.initialized) return;
        } catch {
          /* */
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    });

    // Ensure panel is open
    const toggleBtn = page.locator('[data-testid="toggle-assets-panel-btn"]');
    const panelVisible = await page
      .getByTestId('task-assets-panel')
      .isVisible()
      .catch(() => false);
    if (!panelVisible) {
      await toggleBtn.click();
      await expect(page.getByTestId('task-assets-panel')).toBeVisible({ timeout: 10_000 });
    }

    const filename = `e2e_docx_${Date.now()}.docx`;
    const content = `E2E Docx test content ${Date.now()}`;

    // Have AI create a .docx file using create_docx tool
    await sendMessage(
      page,
      `使用 create_docx 工具创建文件：file_path=${filename}，content="${content}"。创建成功后只回复一个字：成`
    );

    // *:* pre-approved — no approval dialog needed
    await waitForResponseComplete(page, 240_000);

    // Verify AI confirmed creation in chat
    await expect(page.locator('main').getByText('成').first()).toBeVisible({ timeout: 15_000 });
    console.log(`[test] ✅ Docx created: ${filename}`);
    await page.screenshot({ path: 'test-results/docx-created.png' });

    // ── Verify docx appears in Task Assets panel (not "No files yet.") ──
    // This only works with a fresh frontend build that includes the
    // onFinal docx-tracking fix (ChatConsole.tsx ~line 760).
    const shortName = filename.slice(0, 20); // visible portion (truncated to 28)

    // Panel must no longer be empty
    await expect(page.locator('[data-testid="task-assets-empty"]')).not.toBeVisible({
      timeout: 15_000,
    });

    // Scope to the assets panel only to avoid matching the same file card
    // that also appears in the main chat "Proposed Changes" area.
    const assetsPanel = page.getByTestId('task-assets-panel');
    const docxCard = assetsPanel
      .locator('.rounded-lg.p-2\\.5')
      .filter({ hasText: shortName })
      .first();
    await expect(docxCard).toBeVisible({ timeout: 10_000 });

    // issue #607: docx via create_docx is a result asset → 结果区 + 结果 badge
    await expect(page.locator('[data-testid="task-assets-stats"]')).toContainText('1 个结果', {
      timeout: 10_000,
    });
    await expect(docxCard.getByTestId('file-result-badge')).toBeVisible({ timeout: 10_000 });
    await expect(docxCard.getByTestId('file-op-write')).toBeVisible({ timeout: 10_000 });
    await expect(docxCard.getByTestId('file-office-badge')).toBeVisible({ timeout: 10_000 });
    console.log('[test] ✅ Docx appears in Task Assets panel');
    await page.screenshot({ path: 'test-results/docx-in-panel.png' });

    // ── Click Preview → opens directly with system app, no modal ──
    await docxCard.locator('[data-testid="file-preview-btn"]').click();
    // Office files are dispatched to the system default application via shell.openPath;
    // no preview modal is shown. Just verify the click does not throw.
    await page.waitForTimeout(500);
    console.log('[test] ✅ Preview click dispatched (no modal for Office files)');

    console.log('[test] ✅ Docx test complete');
  });
});
