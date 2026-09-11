/**
 * MiQroForge Desktop — Logs Tab Smoke Tests
 *
 * Covers the Settings → Logs tab renderer flows with a mock bridge backend.
 * Validates: navigation, table rendering, filter controls, sub-tabs,
 * real-time log streaming, row expansion, and export button presence.
 *
 * Run: npx playwright test --config=playwright.config.ts --project=smoke logs.spec.ts
 */

import { test, expect } from '@playwright/test';
import { buildMockBridgeScript, type MockBridgeOptions } from './mocks';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function injectMockAndGoto(page: import('@playwright/test').Page, opts?: MockBridgeOptions) {
  await page.addInitScript({ content: buildMockBridgeScript(opts) });
  await page.goto('/');
  await page.waitForSelector('#root', { state: 'visible' });
}

/** Click "System Settings" in the sidebar bottom bar to open SettingsPage */
async function navigateToSettings(page: import('@playwright/test').Page) {
  await page.getByText(/^(System Settings|系统设置)$/).click();
  await expect(page.getByRole('heading', { name: '设置' })).toBeVisible({ timeout: 3000 });
}

/** Navigate to Settings → Logs tab, wait for it to render */
async function navigateToLogsTab(page: import('@playwright/test').Page) {
  await navigateToSettings(page);
  await page.getByRole('tab', { name: '日志' }).click();
  // Wait for the filter toolbar — indicates LogsTab has mounted
  await expect(page.getByText('自动刷新')).toBeVisible({ timeout: 5000 });
}

/** Click the refresh button in the Logs tab filter toolbar */
async function clickRefreshButton(page: import('@playwright/test').Page) {
  await page.getByTestId('refresh-logs').click();
}

// ---------------------------------------------------------------------------
// Suite 1: Logs Tab Navigation
// ---------------------------------------------------------------------------

test.describe('Logs Tab — Navigation', () => {
  test('navigates from sidebar to Settings → Logs tab', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    // The "日志" tab trigger should now be in active state
    const logsTab = page.getByRole('tab', { name: '日志' });
    await expect(logsTab).toHaveAttribute('data-state', 'active');
  });

  test('renders all log filter toolbar controls', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    // Auto-refresh checkbox
    await expect(page.getByText('自动刷新')).toBeVisible();

    // Level dropdown — "全部级别"
    await expect(page.locator('select').filter({ hasText: '全部级别' })).toBeVisible();

    // Source dropdown — "全部来源"
    await expect(page.locator('select').filter({ hasText: '全部来源' })).toBeVisible();

    // Session key input
    await expect(page.getByPlaceholder('session')).toBeVisible();

    // Keyword search input
    await expect(page.getByPlaceholder('关键字')).toBeVisible();
  });

  test('renders all export and action buttons', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    // Copy button
    await expect(page.getByRole('button', { name: /复制日志/ })).toBeVisible();

    // Export TXT button
    await expect(page.getByRole('button', { name: /导出 TXT/ })).toBeVisible();

    // Export JSON button
    await expect(page.getByRole('button', { name: /导出 JSON/ })).toBeVisible();
  });

  test('renders table headers with correct columns', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await expect(page.getByRole('columnheader', { name: '时间' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: '级别' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: '来源' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: '消息' })).toBeVisible();
  });

  test('renders all three sub-tab view buttons', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await expect(page.getByRole('button', { name: '全部' })).toBeVisible();
    await expect(page.getByRole('button', { name: '前端日志' })).toBeVisible();
    await expect(page.getByRole('button', { name: '后端日志' })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Suite 2: Log Entry Display
// ---------------------------------------------------------------------------

test.describe('Logs Tab — Entry Display', () => {
  test('table populates with entries after clicking refresh', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);

    // After refresh, mock logs (5 entries) should replace current entries
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });
  });

  test('log entries display all four columns correctly', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Check specific content from mock data
    // Entry: [2026-07-07T10:00:00.000Z] [INFO] [bridge] Bridge process started
    await expect(page.getByText('Bridge process started').first()).toBeVisible();

    // Entry: [2026-07-07T10:00:10.000Z] [ERROR] [sandbox] Sandbox timeout after 30s
    await expect(page.getByText('Sandbox timeout after 30s')).toBeVisible();

    // Source labels should appear in cells
    const rows = page.locator('table tbody');
    await expect(rows.getByRole('cell', { name: 'bridge', exact: true }).first()).toBeVisible();
    await expect(rows.getByRole('cell', { name: 'sandbox', exact: true })).toBeVisible();
  });

  test('level badges render with correct level text', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // All three levels should be represented in mock data
    const rows = page.locator('table tbody');
    await expect(rows.getByText('INFO').first()).toBeVisible();
    await expect(rows.getByText('WARN')).toBeVisible();
    await expect(rows.getByText('ERROR')).toBeVisible();
  });

  test('ERROR rows have red background tint', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // The ERROR row (sandbox timeout) should have bg-red-500/5 class
    const errorRow = page.locator('table tbody tr').filter({ hasText: 'Sandbox timeout' });
    await expect(errorRow).toHaveClass(/bg-red-500/);
  });

  test('WARN rows have amber background tint', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // The WARN row (Slow IPC) should have bg-amber-500/5 class
    const warnRow = page.locator('table tbody tr').filter({ hasText: 'Slow IPC' });
    await expect(warnRow).toHaveClass(/bg-amber-500/);
  });
});

// ---------------------------------------------------------------------------
// Suite 3: Log Filtering
// ---------------------------------------------------------------------------

test.describe('Logs Tab — Filtering', () => {
  test('level filter: ERROR shows only ERROR rows', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Select ERROR level
    await page.locator('select').filter({ hasText: '全部级别' }).selectOption('ERROR');

    // Only the sandbox timeout ERROR entry should remain
    await expect(page.locator('table tbody tr')).toHaveCount(1);
    await expect(page.getByText('Sandbox timeout after 30s')).toBeVisible();

    // Other entries should be filtered out
    await expect(page.getByText('Bridge process started')).not.toBeVisible();
    await expect(page.getByText('Slow IPC')).not.toBeVisible();
  });

  test('level filter: WARN shows only WARN rows', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Select WARN level
    await page.locator('select').filter({ hasText: '全部级别' }).selectOption('WARN');

    // Only the slow IPC WARN entry should remain
    await expect(page.locator('table tbody tr')).toHaveCount(1);
    await expect(page.getByText('Slow IPC')).toBeVisible();
  });

  test('source filter: bridge shows only bridge-sourced rows', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Select bridge source
    await page.locator('select').filter({ hasText: '全部来源' }).selectOption('bridge');

    // Mock data has 3 bridge entries: "Bridge process started", "Agent ready", "Slow IPC"
    await expect(page.locator('table tbody tr')).toHaveCount(3);

    // Sandbox entry should be hidden
    await expect(page.getByText('Sandbox timeout after 30s')).not.toBeVisible();
    // Renderer entry should be hidden
    await expect(page.getByText('Runtime context initialized')).not.toBeVisible();
  });

  test('keyword filter performs case-insensitive substring match', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Type keyword (case-insensitive — "sandbox" matches "Sandbox timeout")
    await page.getByPlaceholder('关键字').fill('sandbox');

    // Only the sandbox entry should match
    await expect(page.locator('table tbody tr')).toHaveCount(1);
    await expect(page.getByText('Sandbox timeout after 30s')).toBeVisible();
  });

  test('keyword filter: no match shows empty state message', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Type a keyword that matches nothing
    await page.getByPlaceholder('关键字').fill('xyznonexistent');

    // Empty state message should appear, table should be hidden
    await expect(page.getByText('暂无匹配日志')).toBeVisible({ timeout: 3000 });
    await expect(page.locator('table')).not.toBeVisible();
  });

  test('combined level + keyword filter narrows results', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Set level to INFO and keyword to "agent"
    await page.locator('select').filter({ hasText: '全部级别' }).selectOption('INFO');
    await page.getByPlaceholder('关键字').fill('agent');

    // "Agent ready" is INFO + contains "Agent" (case-insensitive match on "agent")
    await expect(page.locator('table tbody tr')).toHaveCount(1);
    await expect(page.getByText('Agent ready')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Suite 4: Sub-tabs (Frontend / Backend / All)
// ---------------------------------------------------------------------------

test.describe('Logs Tab — Sub-tabs', () => {
  test('"前端日志" shows only renderer and main sources', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Click "前端日志" sub-tab button
    await page.getByRole('button', { name: '前端日志' }).click();

    // Mock data: only 1 entry has source=renderer ("Runtime context initialized")
    await expect(page.locator('table tbody tr')).toHaveCount(1);
    await expect(page.getByText('Runtime context initialized')).toBeVisible();
  });

  test('"后端日志" shows only bridge, sandbox, and tool sources', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Click "后端日志" sub-tab button
    await page.getByRole('button', { name: '后端日志' }).click();

    // Mock data: 3 bridge + 1 sandbox = 4 backend entries
    await expect(page.locator('table tbody tr')).toHaveCount(4);

    // Renderer entry should be filtered out
    await expect(page.getByText('Runtime context initialized')).not.toBeVisible();
  });

  test('switching back to "全部" restores all entries', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Switch to frontend, then back to all
    await page.getByRole('button', { name: '前端日志' }).click();
    await expect(page.locator('table tbody tr')).toHaveCount(1);

    await page.getByRole('button', { name: '全部' }).click();
    await expect(page.locator('table tbody tr')).toHaveCount(5);
  });
});

// ---------------------------------------------------------------------------
// Suite 5: Real-time Log Streaming
// ---------------------------------------------------------------------------

test.describe('Logs Tab — Real-time Streaming', () => {
  test('triggerLog adds new entries to the table in real time', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Fire a real-time log event via the mock trigger API
    await page.evaluate(() => {
      (window as any).__miqiMock.triggerLog('Real-time test message', 'ERROR', 'tool');
    });

    // Table should now have 6 rows (5 original + 1 streamed)
    await expect(page.locator('table tbody tr')).toHaveCount(6, { timeout: 5000 });
    await expect(page.getByText('Real-time test message')).toBeVisible({ timeout: 3000 });
  });

  test('multiple triggerLog calls accumulate entries', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Fire 3 real-time log events
    await page.evaluate(() => {
      (window as any).__miqiMock.triggerLog('Event A', 'INFO', 'bridge');
      (window as any).__miqiMock.triggerLog('Event B', 'WARN', 'renderer');
      (window as any).__miqiMock.triggerLog('Event C', 'ERROR', 'sandbox');
    });

    // 5 original + 3 streamed = 8
    await expect(page.locator('table tbody tr')).toHaveCount(8, { timeout: 5000 });
  });
});

// ---------------------------------------------------------------------------
// Suite 6: Row Interaction
// ---------------------------------------------------------------------------

test.describe('Logs Tab — Row Interaction', () => {
  test('clicking a row toggles message expansion (removes line-clamp)', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Click the first row
    const firstRow = page.locator('table tbody tr').first();
    const messageCell = firstRow.locator('td').last();

    // Before click: message span should have line-clamp-1 class
    await expect(messageCell.locator('span').first()).toHaveClass(/line-clamp-1/);

    // Click to expand
    await firstRow.click();

    // After click: line-clamp-1 should be removed
    await expect(messageCell.locator('span').first()).not.toHaveClass(/line-clamp-1/);

    // Click again to collapse
    await firstRow.click();

    // line-clamp-1 should be restored
    await expect(messageCell.locator('span').first()).toHaveClass(/line-clamp-1/);
  });

  test('filter change resets expanded rows', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    await clickRefreshButton(page);
    await expect(page.locator('table tbody tr')).toHaveCount(5, { timeout: 5000 });

    // Expand first row
    const firstRow = page.locator('table tbody tr').first();
    await firstRow.click();
    const messageCell = firstRow.locator('td').last();
    await expect(messageCell.locator('span').first()).not.toHaveClass(/line-clamp-1/);

    // Change a filter (should reset expanded state)
    await page.locator('select').filter({ hasText: '全部级别' }).selectOption('INFO');

    // After filter change, all remaining rows should be collapsed (line-clamp-1 restored)
    const rows = page.locator('table tbody tr');
    const count = await rows.count();
    for (let i = 0; i < count; i++) {
      const span = rows.nth(i).locator('td').last().locator('span').first();
      await expect(span).toHaveClass(/line-clamp-1/);
    }
  });
});

// ---------------------------------------------------------------------------
// Suite 7: Edge Cases
// ---------------------------------------------------------------------------

test.describe('Logs Tab — Edge Cases', () => {
  test('preload bridge error page does not show logs', async ({ page }) => {
    await injectMockAndGoto(page, { preloadOk: false });

    // Should show the error page, not the app shell
    const errorHeading = page.locator('h2', { hasText: '预加载桥接不可用' });
    await expect(errorHeading).toBeVisible();

    // Settings should not be accessible
    await expect(page.getByText(/^(System Settings|系统设置)$/)).not.toBeVisible();
  });

  test('auto-refresh checkbox can be toggled', async ({ page }) => {
    await injectMockAndGoto(page);
    await navigateToLogsTab(page);

    const checkbox = page.locator('input[type="checkbox"]').first();

    // Default is checked (autoRefresh starts as true)
    await expect(checkbox).toBeChecked();

    // Toggle off
    await checkbox.click();
    await expect(checkbox).not.toBeChecked();

    // Toggle on
    await checkbox.click();
    await expect(checkbox).toBeChecked();
  });
});
