/**
 * MiQroForge Desktop — Playwright Smoke QA Tests
 *
 * Covers the core renderer flows with a mock bridge backend.
 * Run: npx playwright test --config=playwright.config.ts
 *
 * Test coverage:
 *  1. App load — preload bridge check, UI renders
 *  2. Sidebar — navigation buttons, session list
 *  3. Chat — session title header, message sanitization
 *  4. StatusBar — runtime status visible + 登录态积分余额
 */

import { test, expect } from '@playwright/test';
import { buildMockBridgeScript, type MockBridgeOptions } from './mocks';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function injectMockAndGoto(page: import('@playwright/test').Page, opts?: MockBridgeOptions) {
  await page.addInitScript({ content: buildMockBridgeScript(opts) });
  await page.goto('/');
  // Wait for React to render
  await page.waitForSelector('#root', { state: 'visible' });
}

// ---------------------------------------------------------------------------
// Suite 1: App Load & Bridge
// ---------------------------------------------------------------------------

test.describe('App Load & Bridge', () => {
  test('renders the application shell when preload is available', async ({ page }) => {
    await injectMockAndGoto(page);

    // App shell should render (not the "预加载桥接不可用" error page)
    const preloadError = page.locator('h2', { hasText: '预加载桥接不可用' });
    await expect(preloadError).toHaveCount(0);
  });

  test('shows preload bridge error when window.miqi is missing', async ({ page }) => {
    await injectMockAndGoto(page, { preloadOk: false });

    // Should show the error message
    const errorHeading = page.locator('h2', { hasText: '预加载桥接不可用' });
    await expect(errorHeading).toBeVisible();

    // Should show restart instructions
    await expect(page.getByText('应用预加载脚本注入失败')).toBeVisible();
  });

  test('renders MiQroForge branding', async ({ page }) => {
    await injectMockAndGoto(page);

    // "MiQroForge Desktop" is the app-title in the ChatConsole header
    await expect(page.getByTestId('app-title')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Suite 2: Sidebar Navigation
// ---------------------------------------------------------------------------

test.describe('Sidebar Navigation', () => {
  test('sidebar renders with core session layout', async ({ page }) => {
    await injectMockAndGoto(page);

    // Tasks section header — stable text across redesigns
    await expect(page.getByText(/^(Tasks|任务)$/)).toBeVisible({ timeout: 3000 });

    // Plus button for new session — stable title attribute
    await expect(page.locator('[title="New Session"], [title="新建会话"]')).toBeVisible();

    // At least one session card should render
    const session1 = page.getByText('Test conversation 1');
    await expect(session1.first()).toBeVisible({ timeout: 5000 });
  });

  test('sessions list shows mock sessions', async ({ page }) => {
    await injectMockAndGoto(page);

    // Sessions should be loaded from mock
    const session1 = page.getByText('Test conversation 1');
    const session2 = page.getByText('Test conversation 2');

    // At least one session should be visible after loading
    await expect(session1.first()).toBeVisible({ timeout: 5000 });
    await expect(session2.first()).toBeVisible({ timeout: 5000 });
  });

  test('new session button is present', async ({ page }) => {
    await injectMockAndGoto(page);

    const newSessionBtn = page.locator('[title="New Session"], [title="新建会话"]');
    await expect(newSessionBtn).toBeVisible();
  });

  test('Tasks section header is visible', async ({ page }) => {
    await injectMockAndGoto(page);

    // The sessions section header should say "Tasks"
    await expect(page.getByText(/^(Tasks|任务)$/)).toBeVisible({ timeout: 3000 });
  });
});

// ---------------------------------------------------------------------------
// Suite 3: Chat Console
// ---------------------------------------------------------------------------

test.describe('Chat Console', () => {
  test('renders session title in header', async ({ page }) => {
    await injectMockAndGoto(page);

    // Session title (h2.font-semibold.truncate) renders in both old and new UI
    const title = page.locator('h2.font-semibold.truncate').first();
    await expect(title).toBeVisible({ timeout: 5000 });
  });
});

// ---------------------------------------------------------------------------
// Suite 4: Status Bar
// ---------------------------------------------------------------------------

test.describe('Status Bar', () => {
  test('shows runtime status indicator', async ({ page }) => {
    await injectMockAndGoto(page);

    // The status bar at the bottom should render
    // When runtime status is "running", the app shows "运行中"
    await expect(page.getByText('运行中')).toBeVisible({ timeout: 5000 });
  });

  test('shows stopped status when runtime is down', async ({ page }) => {
    await injectMockAndGoto(page, { runtimeStatus: 'stopped' });

    await expect(page.getByText('已停止')).toBeVisible({ timeout: 5000 });
  });

  test('shows points balance in status bar when logged in', async ({ page }) => {
    await injectMockAndGoto(page, {
      qraftStatus: {
        loggedIn: true,
        account: { phone: '18500000000', sub: '19', nickname: 'MiQi测试' },
      },
    });

    // 状态栏在登录后拉取余额（mock 默认 270 可用积分）并展示
    await expect(page.getByTestId('statusbar-points')).toHaveText(/积分 270/);
  });

  test('hides points balance in status bar when logged out', async ({ page }) => {
    await injectMockAndGoto(page);

    await expect(page.getByTestId('statusbar-points')).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------
// Suite 5: Error Sanitization (sanitizeUiMessage)
// ---------------------------------------------------------------------------

test.describe('Error Sanitization', () => {
  test('renderer loads sanitizeUiMessage module without error', async ({ page }) => {
    await injectMockAndGoto(page);

    // Verify the page loaded without JavaScript errors
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    // Reload to trigger a fresh render
    await page.reload();
    await page.waitForSelector('#root', { state: 'visible' });

    // Filter out expected errors (CSP, missing icons, etc.)
    const unexpectedErrors = errors.filter((e) => !e.includes('Failed to load resource'));

    expect(unexpectedErrors).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Suite 6: Responsive Layout
// ---------------------------------------------------------------------------

test.describe('Layout', () => {
  test('page title is set correctly', async ({ page }) => {
    await injectMockAndGoto(page);

    await expect(page).toHaveTitle(/MiQroForge/i);
  });
});
