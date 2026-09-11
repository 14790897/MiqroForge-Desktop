/**
 * E2E: Feedback Page (用户反馈 tab)
 *
 * Validates the in-app feedback submission flow:
 * - Navigate to Settings → 反馈 tab
 * - Verify empty state (no entries yet)
 * - Open submit modal, fill form, submit
 * - Verify the entry appears in the list
 * - Verify success message and modal dismiss
 * - Verify Escape key closes the modal
 *
 * Note: This test does NOT verify the Feishu Bitable API call — that
 * requires real credentials.  We mock feedback.submit() to capture the
 * payload, then verify it has the right shape.
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts --project=electron feedback.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

/** Helper: navigate from current page to Settings → 反馈 tab. */
async function openFeedbackTab(page: Page) {
  const settingsLink = page.getByTestId('nav-system-settings');
  await expect(settingsLink).toBeVisible({ timeout: 10_000 });
  await settingsLink.click();

  // Settings page renders a "通用" tab heading
  await expect(page.getByText('通用').first()).toBeVisible({ timeout: 10_000 });

  const feedbackTab = page.getByRole('tab', { name: '反馈' });
  await expect(feedbackTab).toBeVisible({ timeout: 5_000 });
  await feedbackTab.click();

  // FeedbackPage header is rendered
  await expect(page.getByText('用户反馈')).toBeVisible({ timeout: 5_000 });
}

test.describe('Feedback Page E2E', () => {
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

  // Reset modal between tests so the backdrop doesn't intercept clicks
  // in subsequent tests that call openFeedbackTab().
  test.afterEach(async () => {
    const modalHeading = page.getByRole('heading', { name: '提交反馈' });
    if (await modalHeading.isVisible().catch(() => false)) {
      // Click "取消" to close without triggering the unsaved-content guard.
      const cancelBtn = page.getByRole('button', { name: '取消', exact: true });
      if (await cancelBtn.isVisible().catch(() => false)) {
        await cancelBtn.click();
      } else {
        await page.keyboard.press('Escape');
      }
      await modalHeading.waitFor({ state: 'hidden', timeout: 3_000 });
    }
  });

  test('feedback tab loads with empty state', async () => {
    await openFeedbackTab(page);

    // Empty state should show (button "提交第一条反馈" is visible)
    await expect(page.getByText('提交反馈将自动附加日志并发送到飞书')).toBeVisible({
      timeout: 5_000,
    });
    await expect(page.getByRole('button', { name: /提交第一条反馈/ })).toBeVisible();
  });

  test('submit modal opens and validates form', async () => {
    await openFeedbackTab(page);

    // Click "提交反馈" header button (NOT the "提交第一条反馈" empty-state one)
    // Use exact match to disambiguate.
    const headerBtn = page.locator('div.flex.items-center.gap-4').getByRole('button', {
      name: '提交反馈',
      exact: true,
    });
    await expect(headerBtn).toBeVisible({ timeout: 5_000 });
    await headerBtn.click();

    // Modal heading
    await expect(page.getByRole('heading', { name: '提交反馈' })).toBeVisible();

    // Submit button should be disabled (title/content empty)
    const submitButton = page
      .locator('div.bg-\\[var\\(--surface\\)\\]')
      .getByRole('button', { name: '提交', exact: true });
    await expect(submitButton).toBeDisabled();

    // Fill title and content
    await page.getByPlaceholder('简要描述你的问题或建议').fill('E2E 测试标题');
    await page.getByPlaceholder('请详细描述你的问题或建议...').fill('E2E 测试内容 - 验证表单收集');

    // Submit button should now be enabled
    await expect(submitButton).toBeEnabled();
  });

  test('Escape key closes the submit modal', async () => {
    await openFeedbackTab(page);
    const headerBtn = page
      .locator('div.flex.items-center.gap-4')
      .getByRole('button', { name: '提交反馈', exact: true });
    await headerBtn.click();
    await expect(page.getByRole('heading', { name: '提交反馈' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('heading', { name: '提交反馈' })).not.toBeVisible({
      timeout: 2_000,
    });
  });

  test('unsaved content guard: dismiss keeps modal open, accept closes', async () => {
    await openFeedbackTab(page);
    const headerBtn = page
      .locator('div.flex.items-center.gap-4')
      .getByRole('button', { name: '提交反馈', exact: true });
    await headerBtn.click();
    const modalHeading = page.getByRole('heading', { name: '提交反馈' });
    await expect(modalHeading).toBeVisible();
    await page.getByPlaceholder('简要描述你的问题或建议').fill('测试未保存拦截');

    // Esc: dismiss keeps open, accept closes
    let dismissCalled = false;
    const onDialog = (dialog: any) => {
      if (!dismissCalled) {
        dismissCalled = true;
        dialog.dismiss();
      } else {
        dialog.accept();
      }
    };
    page.on('dialog', onDialog);
    try {
      await page.keyboard.press('Escape');
      await page.waitForTimeout(500);
      expect(dismissCalled).toBe(true);
      await expect(modalHeading).toBeVisible({ timeout: 2_000 });

      await page.getByPlaceholder('简要描述你的问题或建议').click();
      await page.waitForTimeout(300);
      await page.keyboard.press('Escape');
      await expect(modalHeading).not.toBeVisible({ timeout: 5_000 });
    } finally {
      page.off('dialog', onDialog);
    }

    // Overlay click: dismiss keeps open, accept closes
    await headerBtn.click();
    await page.getByPlaceholder('简要描述你的问题或建议').fill('覆盖层点击测试');
    await expect(modalHeading).toBeVisible();

    let overlayDismissed = false;
    const onDialog2 = (dialog: any) => {
      if (!overlayDismissed) {
        overlayDismissed = true;
        dialog.dismiss();
      } else {
        dialog.accept();
      }
    };
    page.on('dialog', onDialog2);
    try {
      // Click outside modal — the overlay is at fixed inset-0
      await page.mouse.click(10, 10);
      await page.waitForTimeout(500);
      expect(overlayDismissed).toBe(true);
      await expect(modalHeading).toBeVisible({ timeout: 2_000 });

      await page.getByPlaceholder('简要描述你的问题或建议').click();
      await page.waitForTimeout(300);
      await page.mouse.click(10, 10);
      await expect(modalHeading).not.toBeVisible({ timeout: 5_000 });
    } finally {
      page.off('dialog', onDialog2);
    }
  });

  test('empty form closes on Escape and overlay click without confirm', async () => {
    await openFeedbackTab(page);
    const headerBtn = page
      .locator('div.flex.items-center.gap-4')
      .getByRole('button', { name: '提交反馈', exact: true });
    const modalHeading = page.getByRole('heading', { name: '提交反馈' });

    // Escape
    await headerBtn.click();
    await expect(modalHeading).toBeVisible();
    let dialogFired = false;
    const onDialog = () => {
      dialogFired = true;
    };
    page.on('dialog', onDialog);
    try {
      await page.keyboard.press('Escape');
      expect(dialogFired).toBe(false);
      await expect(modalHeading).not.toBeVisible({ timeout: 2_000 });
    } finally {
      page.off('dialog', onDialog);
    }

    // Overlay click
    await headerBtn.click();
    await expect(modalHeading).toBeVisible();
    let overlayDialog = false;
    const onDialog2 = () => {
      overlayDialog = true;
    };
    page.on('dialog', onDialog2);
    try {
      await page.mouse.click(10, 10);
      await page.waitForTimeout(500);
      expect(overlayDialog).toBe(false);
      await expect(modalHeading).not.toBeVisible({ timeout: 2_000 });
    } finally {
      page.off('dialog', onDialog2);
    }
  });

  test('hints are visible in the submit modal', async () => {
    await openFeedbackTab(page);
    const headerBtn = page
      .locator('div.flex.items-center.gap-4')
      .getByRole('button', { name: '提交反馈', exact: true });
    await headerBtn.click();
    await expect(page.getByRole('heading', { name: '提交反馈' })).toBeVisible();
    await expect(page.getByText('日志将在提交时自动附加并发送到飞书')).toBeVisible();
    await expect(
      page.getByText('提示：建议先复制已填写的提示词，避免因意外关闭而丢失')
    ).toBeVisible();
  });

  test('submit feedback shows validation error when disabled', async () => {
    await openFeedbackTab(page);
    const headerBtn = page
      .locator('div.flex.items-center.gap-4')
      .getByRole('button', { name: '提交反馈', exact: true });
    await headerBtn.click();

    await page.getByPlaceholder('简要描述你的问题或建议').fill('E2E test title');
    await page.getByPlaceholder('请详细描述你的问题或建议...').fill('E2E test content');

    const submitButton = page
      .locator('div.bg-\\[var\\(--surface\\)\\]')
      .getByRole('button', { name: '提交', exact: true });
    await submitButton.click();

    const errorBox = page.locator('[class*="bg-red-500"]');
    await expect(errorBox).toBeVisible({ timeout: 5_000 });
    await expect(errorBox).toContainText('反馈功能未启用');
    await expect(page.getByText('提交成功！')).not.toBeVisible();
    await expect(page.getByRole('heading', { name: '提交反馈' })).toBeVisible();
    // Close via cancel to bypass the unsaved-content guard
    await page.getByRole('button', { name: '取消', exact: true }).click();
    await expect(page.getByRole('heading', { name: '提交反馈' })).not.toBeVisible();
  });

  test('submit feedback via mocked main-process IPC shows success entry', async () => {
    // contextBridge freezes window.miqi.feedback in the renderer, so
    // addInitScript-based mocks are no-ops.  Patch the IPC handler in
    // the main process instead via electronApp.evaluate — this captures
    // the actual params flowing through the full IPC pipeline and lets
    // us assert against a deterministic success path.
    await electronApp.evaluate(async ({ ipcMain: ipc }) => {
      // Use the IPC channel string literals directly — Playwright's eval
      // context is ESM sandboxed and can't import the compiled CJS IPC
      // module.  These strings are stable and match apps/desktop/src/shared/ipc.ts
      const FEEDBACK_SUBMIT = 'feedback:submit';
      const FEEDBACK_LIST = 'feedback:list';
      const box = ((global as any).__capturedSubmits = []);
      ipc.removeHandler(FEEDBACK_SUBMIT);
      ipc.handle(FEEDBACK_SUBMIT, async (_e: unknown, params: any) => {
        box.push(params);
        return { ok: true, record_id: 'mock_record_xyz' };
      });
      ipc.removeHandler(FEEDBACK_LIST);
      ipc.handle(FEEDBACK_LIST, async () => {
        return {
          entries: box.map((s: any, i: number) => ({
            id: `mock_${i}`,
            category: s.category,
            title: s.title,
            content: s.content,
            contact: s.contact || '',
            app_version: s.app_version || 'dev',
            os: 'Windows 11 (test)',
            python_version: '3.12',
            feishu_record_id: 'mock_record_xyz',
            created_at: new Date().toISOString(),
          })),
        };
      });
    });

    await openFeedbackTab(page);

    const headerBtn = page.locator('div.flex.items-center.gap-4').getByRole('button', {
      name: '提交反馈',
      exact: true,
    });
    await expect(headerBtn).toBeVisible({ timeout: 5_000 });
    await headerBtn.click();
    await expect(page.getByRole('heading', { name: '提交反馈' })).toBeVisible();

    // Fill form
    await page.getByPlaceholder('简要描述你的问题或建议').fill('E2E mock submission');
    await page
      .getByPlaceholder('请详细描述你的问题或建议...')
      .fill('Mocked success-path content for E2E.');
    await page.getByPlaceholder('邮箱或飞书账号，方便我们联系你').fill('e2e@test.com');

    // Submit
    const submitButton = page
      .locator('div.bg-\\[var\\(--surface\\)\\]')
      .getByRole('button', { name: '提交', exact: true });
    await submitButton.click();

    // Deterministic success: success toast appears
    await expect(page.getByText('提交成功！')).toBeVisible({ timeout: 5_000 });

    // After modal auto-closes, list refreshes and shows the new entry
    await expect(page.getByText('E2E mock submission')).toBeVisible({ timeout: 5_000 });

    // Verify the captured payload went through the IPC pipeline
    const captured = await electronApp.evaluate(async () => {
      const box = (global as any).__capturedSubmits ?? [];
      return box.map((s: any) => ({
        title: s.title,
        content: s.content,
        contact: s.contact,
        category: s.category,
      }));
    });
    expect(captured).toHaveLength(1);
    expect(captured[0].title).toBe('E2E mock submission');
    expect(captured[0].content).toContain('Mocked success-path');
    expect(captured[0].contact).toBe('e2e@test.com');
  });

  test('screenshot drop zone accepts files and shows thumbnails', async () => {
    await openFeedbackTab(page);

    const headerBtn = page.locator('div.flex.items-center.gap-4').getByRole('button', {
      name: '提交反馈',
      exact: true,
    });
    await headerBtn.click();
    await expect(page.getByRole('heading', { name: '提交反馈' })).toBeVisible();

    // Drop zone hint visible
    await expect(page.getByText('拖入图片 / 粘贴 (Ctrl+V) / 点击选择')).toBeVisible();

    // Counter starts at 0/5
    await expect(page.getByText('0/5')).toBeVisible();

    // Upload a small PNG via the hidden file input (scoped to the modal)
    const tinyPng = Buffer.from(
      '89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489' +
        '0000000D49444154789C636000000000050001A5F645400000000049454E44AE426082',
      'hex'
    );
    const fileInput = page.locator('div.bg-\\[var\\(--surface\\)\\]').locator('input[type="file"]');
    await fileInput.setInputFiles({
      name: 'test.png',
      mimeType: 'image/png',
      buffer: tinyPng,
    });

    // Counter updates to 1/5
    await expect(page.getByText('1/5')).toBeVisible({ timeout: 3_000 });
    // Thumbnail renders
    await expect(page.locator('img[alt="test.png"]')).toBeVisible();
  });
});
