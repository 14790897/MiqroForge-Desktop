/**
 * #1171 — WSL 页面在平台未就绪时给出真实原因。
 *
 * 事件机器（VirtualMachinePlatform 载荷没落地，`wsl --status` 报「WSL2 无法
 * 启动，因为此计算机上未启用虚拟化」）上，页面空状态必须把 WSL 自己的这句原话
 * 显示出来，而不是只写「点击上方『一键安装 WSL2』」。平台正常的机器上只断言页面
 * 能渲染，不制造假失败。
 *
 * 前置：`npm run build`（electron 工程跑的是构建产物）
 * 运行：npx playwright test --config=playwright.config.ts --project=electron issue-1171-wsl-platform-guidance.spec.ts
 */

import { test, expect } from '@playwright/test';
import { waitForInputReady, launchElectronApp, closeElectronApp } from './helpers/electron-setup';

test.skip(process.platform !== 'win32', 'WSL 状态页只在 Windows 上有意义');

test('WSL 页面显示平台未就绪的真实原因（#1171）', { timeout: 120_000 }, async () => {
  const fixture = await launchElectronApp();
  const { electronApp, page, miqiHome } = fixture;

  try {
    await waitForInputReady(page);

    // 设置 → WSL 标签页
    await page.locator('[data-testid="nav-system-settings"]').click();
    await page.waitForTimeout(1500);
    await page.locator('[role="tab"]').filter({ hasText: 'WSL' }).click();
    await page.waitForTimeout(1000);
    await expect(page.getByText('WSL 状态监控').first()).toBeVisible({ timeout: 10_000 });

    const check = await page.evaluate(() => (globalThis as any).miqi.wsl.check());
    const platformIssue: string | null = check?.platformIssue ?? null;
    const distros: string[] = check?.distros ?? [];
    console.log(`[test] platformIssue=${platformIssue} distros=${JSON.stringify(distros)}`);

    if (platformIssue && distros.length === 0) {
      await expect(page.getByText(platformIssue, { exact: false }).first()).toBeVisible({
        timeout: 10_000,
      });
      console.log('[test] ✅ 空状态显示了 WSL 报告的平台故障原因');
    } else {
      console.log('[test] 平台可用或已有发行版，跳过平台故障文案断言');
    }

    await page.screenshot({
      path: 'test-results/issue-1171-wsl-platform-guidance.png',
      fullPage: true,
    });
  } finally {
    await closeElectronApp(electronApp, miqiHome);
  }
});
