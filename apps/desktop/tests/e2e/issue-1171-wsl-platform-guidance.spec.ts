/**
 * #1171 — WSL 页面在平台未就绪时给出真实原因。
 *
 * 宿主机状态不可控（健康机器上根本没有 `platformIssue`），所以断言不能依赖真机：
 * 打开页面之前先把主进程的 `wsl:check` 换成确定性结果（空发行版 + WSL 报出的平台
 * 故障），这样无论如何都在真的跑这条渲染路径。故障文案取自 #1171 报告机器的
 * `wsl --status` 原话。
 *
 * 前置：`npm run build`（electron 工程跑的是构建产物）
 * 运行：PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test --config=playwright.config.ts --project=electron issue-1171-wsl-platform-guidance.spec.ts
 */

import { test, expect } from '@playwright/test';
import { waitForInputReady, launchElectronApp, closeElectronApp } from './helpers/electron-setup';

test.skip(process.platform !== 'win32', 'WSL 状态页只在 Windows 上有意义');

const PLATFORM_ISSUE = 'WSL2 无法启动，因为此计算机上未启用虚拟化。';

/** 平台未就绪机器的确定性 WSL 检查结果。 */
const STUB_WSL_CHECK = {
  isWindows: true,
  installed: true,
  version: '2',
  distros: [],
  defaultDistro: null,
  running: false,
  featureState: 'installed-but-not-initialized',
  rebootRequired: true,
  platformIssue: PLATFORM_ISSUE,
  pendingInstall: null,
};

test('WSL 页面显示平台未就绪的真实原因（#1171）', { timeout: 120_000 }, async () => {
  const fixture = await launchElectronApp();
  const { electronApp, page, miqiHome } = fixture;

  try {
    await waitForInputReady(page);

    await electronApp.evaluate(({ ipcMain }, stub) => {
      ipcMain.removeHandler('wsl:check');
      ipcMain.handle('wsl:check', () => stub);
    }, STUB_WSL_CHECK);

    // 设置 → WSL 标签页
    await page.locator('[data-testid="nav-system-settings"]').click();
    await page.waitForTimeout(1500);
    await page.locator('[role="tab"]').filter({ hasText: 'WSL' }).click();
    await page.waitForTimeout(1000);
    await expect(page.getByText('WSL 状态监控').first()).toBeVisible({ timeout: 10_000 });

    // 空状态必须显示 WSL 报出的原因，而不是只写「点击上方『一键安装 WSL2』」
    await expect(page.getByText(PLATFORM_ISSUE, { exact: false }).first()).toBeVisible({
      timeout: 10_000,
    });
    console.log('[test] ✅ 空状态显示了 WSL 报告的平台故障原因');

    await page.screenshot({
      path: 'test-results/issue-1171-wsl-platform-guidance.png',
      fullPage: true,
    });
  } finally {
    await closeElectronApp(electronApp, miqiHome);
  }
});
