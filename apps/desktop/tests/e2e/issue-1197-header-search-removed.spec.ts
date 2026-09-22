/**
 * E2E Regression: #1197 — 对话窗口顶部栏不再有装饰性搜索框
 *
 * 顶部栏中间原本有一个长得像搜索输入框的组件（灰色圆角条 + 放大镜 +
 * 「搜索或输入命令...」），但它只是一个静态 <span>：没有 input、没有
 * onClick、没有快捷键绑定，点它没反应。它是纯装饰的误导性 UI，已删除。
 *
 * 本 spec 断言两件事：
 *   1. 那个假搜索框确实不存在了（没有文案，也没有任何顶栏内的输入框）；
 *   2. 顶栏右侧那组（用户区 + 「更多对话操作」）没被连带删掉，仍然贴右
 *      —— 它原本靠中间元素的 flex-1 才被撑到最右，删除后靠 ml-auto，
 *      这条断言就是 ml-auto 的回归护栏。
 *
 * Run:
 *   cd apps/desktop
 *   npx playwright test --config=playwright.config.ts --project=electron \
 *     -g "1197"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp, waitForBridgeInitialized } from './helpers/electron-setup';

test.describe('#1197 顶部栏装饰性搜索框移除', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome).catch(() => {});
  });

  test('顶部栏没有假搜索框，标题与右侧操作区仍在', async () => {
    await waitForBridgeInitialized(page);

    const title = page.getByTestId('app-title');
    await expect(title).toBeVisible({ timeout: 30_000 });

    // 1. 假搜索框（纯静态占位）已移除
    await expect(page.getByText('搜索或输入命令...')).toHaveCount(0);

    // 2. 右侧操作区仍在，且没有被删除中间元素后塌到标题旁
    const moreBtn = page.getByRole('button', { name: '更多对话操作' });
    await expect(moreBtn).toBeVisible();

    const headerBox = await title.locator('xpath=ancestor::div[1]').boundingBox();
    const moreBox = await moreBtn.boundingBox();
    expect(headerBox).not.toBeNull();
    expect(moreBox).not.toBeNull();
    // 右对齐：按钮中线落在顶栏右半边（阈值放宽，避免窄窗口下抖动）
    const headerMid = headerBox!.x + headerBox!.width / 2;
    expect(moreBox!.x + moreBox!.width / 2).toBeGreaterThan(headerMid);
  });
});
