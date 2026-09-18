/**
 * E2E: 自动更新横幅（#1124）。
 *
 * 更新链路的真实性由打包环境验证（本地 feed 实测：check → download → pending）；
 * 本用例覆盖渲染契约——主进程 update:changed 事件到达渲染层后：
 *   1. downloading → 横幅显示版本号与百分比；
 *   2. downloaded  → 显示「立即重启」按钮（点击走 update.install IPC）；
 *   3. 关闭后同一阶段不再重复弹出。
 *
 * 注入方式与 tool-error-neutral.spec 一致：从主进程 webContents.send 走真实
 * IPC 通道，不 mock preload。
 *
 * Run:
 *   cd apps/desktop && npm run build && npx playwright test \
 *     --config=playwright.config.ts --project=electron -g "update notifier"
 */

import { test, expect } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

test.describe('update notifier', () => {
  test('下载中/已下载横幅渲染与关闭契约', async () => {
    test.setTimeout(180_000);
    const { electronApp, page } = await launchElectronApp();

    const inject = (snapshot: Record<string, unknown>) =>
      electronApp.evaluate(({ BrowserWindow }, payload) => {
        const win = BrowserWindow.getAllWindows().find(
          (w) => w.getTitle() === 'MiQroForge Desktop'
        );
        if (!win) throw new Error('main window not found');
        win.webContents.send('update:changed', payload);
      }, snapshot);

    const banner = page.locator('[data-testid="update-notify"]');

    // 初始无横幅
    await expect(banner).toHaveCount(0);

    // 1. 下载中：版本号 + 百分比
    await inject({
      state: 'downloading',
      currentVersion: '0.31.0',
      version: '0.31.1',
      percent: 42,
    });
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('v0.31.1');
    await expect(banner).toContainText('42%');
    await expect(page.locator('[data-testid="update-notify-restart"]')).toHaveCount(0);

    // 2. 已下载：出现「立即重启」
    await inject({
      state: 'downloaded',
      currentVersion: '0.31.0',
      version: '0.31.1',
      percent: 100,
    });
    await expect(page.locator('[data-testid="update-notify-restart"]')).toBeVisible();
    await expect(banner).toContainText('重启应用即可完成更新');

    await page.screenshot({ path: 'test-results/update-notifier-downloaded.png' });

    // 3. 关闭后同一状态不再重复弹出（同事件重放）
    await page.locator('[data-testid="update-notify-close"]').click();
    await expect(banner).toHaveCount(0);
    await inject({
      state: 'downloaded',
      currentVersion: '0.31.0',
      version: '0.31.1',
      percent: 100,
    });
    await expect(banner).toHaveCount(0);

    // 4. 下一阶段（新版本下载中）重新出现
    await inject({
      state: 'downloading',
      currentVersion: '0.31.0',
      version: '0.32.0',
      percent: 5,
    });
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('v0.32.0');

    await closeElectronApp(electronApp);
  });
});
