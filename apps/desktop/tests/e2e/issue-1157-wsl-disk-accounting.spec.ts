/**
 * Issue #1157 — WSL 状态监控「磁盘」三项必须对得上账
 *
 * 现象：详细信息里 磁盘总量 − 磁盘已用 ≠ 磁盘可用（ext4 为 root 预留约 5%
 * 空间，`df` 的 Avail 已扣除保留块），用户看到三个自相矛盾的数字。
 *
 * 断言的是用户能感知的结果：界面上展示的磁盘数字满足
 *     磁盘总量 = 磁盘已用 + 磁盘可用 + 系统保留
 * （文件系统有保留块时，界面必须把「系统保留」这一行也显示出来）。
 *
 * WSL 是 Windows-only 功能，其他平台跳过；Windows 上无可用发行版时跳过。
 *
 * Run:
 *   npx playwright test --config=playwright.config.ts --project=electron \
 *     issue-1157-wsl-disk-accounting.spec.ts --workers=1
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp, waitForInputReady } from './helpers/electron-setup';

test.skip(process.platform !== 'win32', 'WSL status monitoring is Windows-only');

// ─── Helpers ────────────────────────────────────────────────────────

/** Navigate to Settings → WSL tab */
async function navigateToWslPage(page: Page): Promise<void> {
  const settingsBtn = page.locator('[data-testid="nav-system-settings"]');
  await expect(settingsBtn).toBeVisible({ timeout: 15_000 });
  await settingsBtn.click();
  await page.waitForTimeout(1500);

  const wslTab = page.locator('[role="tab"]').filter({ hasText: 'WSL' });
  await expect(wslTab).toBeVisible({ timeout: 15_000 });
  await wslTab.click();
  await page.waitForTimeout(1000);

  await expect(page.getByText('WSL 状态监控').first()).toBeVisible({ timeout: 15_000 });
}

/** 读取「详细信息」表里的磁盘行：{ 磁盘总量: '1006.9 GB', 系统保留: '51.2 GB', ... } */
async function readDiskRows(page: Page): Promise<Record<string, string>> {
  return page.evaluate(() => {
    const rows: Record<string, string> = {};
    document.querySelectorAll('table tr').forEach((tr) => {
      const tds = tr.querySelectorAll('td');
      if (tds.length < 2) return;
      const key = (tds[0].textContent ?? '').trim();
      if (!key.startsWith('磁盘') && key !== '系统保留') return;
      rows[key] = (tds[1].textContent ?? '').trim();
    });
    return rows;
  });
}

/** "1006.9 GB" / "1.8 GB (0%)" → 1006.9 / 1.8 */
function num(text: string | undefined): number {
  const m = (text ?? '').match(/-?\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : NaN;
}

/** 轮询等待磁盘行出现（实时监控数据来自一次 WSL 内采集，需要几秒） */
async function waitForDiskRows(page: Page, timeoutMs = 60_000): Promise<Record<string, string>> {
  const deadline = Date.now() + timeoutMs;
  let last: Record<string, string> = {};
  while (Date.now() < deadline) {
    last = await readDiskRows(page);
    // 采集失败时后端给的是 0 GB，不是可断言的数据
    if (num(last['磁盘总量']) > 0) return last;
    await page.waitForTimeout(1000);
  }
  return last;
}

// ─── Test Suite ─────────────────────────────────────────────────────

test.describe('Issue #1157 — WSL 磁盘三项对账', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await waitForInputReady(page);
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('磁盘总量 = 已用 + 可用 + 系统保留', { timeout: 180_000 }, async () => {
    await navigateToWslPage(page);

    // 无 WSL 发行版时页面只有引导语，没有数据可断言 —— 跳过而不是假失败
    const distros = await page.evaluate(async () => {
      const r = await (window as any).miqi.wsl.check();
      return (r?.distros ?? []) as string[];
    });
    if (!distros.length) {
      console.log('[test] 本机无 WSL 发行版，跳过磁盘对账断言');
      test.skip(true, 'no WSL distro available');
      return;
    }

    const rows = await waitForDiskRows(page);
    console.log(`[test] 磁盘行=${JSON.stringify(rows)}`);

    const total = num(rows['磁盘总量']);
    const used = num(rows['磁盘已用']);
    const avail = num(rows['磁盘可用']);
    const reserved = num(rows['系统保留']);

    if (!(total > 0)) {
      console.log('[test] 未取到磁盘数据（total=0），跳过对账断言');
      test.skip(true, 'no disk stats from WSL');
      return;
    }

    expect(Number.isNaN(total), '磁盘总量应为数字').toBe(false);
    expect(Number.isNaN(used), '磁盘已用应为数字').toBe(false);
    expect(Number.isNaN(avail), '磁盘可用应为数字').toBe(false);

    // 用户能直接看到的算术：总量 − 已用 − 可用 = 差额
    const gap = Math.round((total - used - avail) * 10) / 10;
    console.log(
      `[test] 总量=${total} 已用=${used} 可用=${avail} 差额=${gap} 系统保留=${rows['系统保留'] ?? '(缺)'}`
    );

    // 有可见差额时（文件系统保留了块），界面必须给出「系统保留」行来解释它
    if (gap >= 0.5) {
      expect(rows['系统保留'], '差额存在时必须展示「系统保留」行').toBeTruthy();
      expect(Number.isNaN(reserved), '系统保留应为数字').toBe(false);
      expect(Math.abs(reserved - gap), '「系统保留」应等于总量−已用−可用').toBeLessThanOrEqual(0.2);
    }

    // 对账：三项加保留（无保留时为 0）必须回到总量
    const sum = used + avail + (Number.isNaN(reserved) ? 0 : reserved);
    expect(
      Math.abs(sum - total),
      `已用+可用+系统保留 应等于总量（${sum} vs ${total}）`
    ).toBeLessThanOrEqual(0.3);

    await page.screenshot({
      path: `test-results/${test.info().title.replace(/[^a-zA-Z0-9]+/g, '-')}.png`,
      fullPage: true,
    });
  });
});
