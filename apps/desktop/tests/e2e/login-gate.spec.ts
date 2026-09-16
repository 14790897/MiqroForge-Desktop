/**
 * 登录门 E2E（issue #1095）—— 未登录不得进入主界面。
 *
 * 覆盖四条路径（登录门不绕过：noLoginBypass）：
 *  1. 首次启动：协议门同意后停在登录门（不再有「暂不登录」），点「退出应用」
 *     先给明确提示，确认后进程结束（避免用户误以为崩溃）；
 *  2. 未登录重启：每次启动都停在登录门，不进主界面；
 *  3. 预置登录态（plain 信封，同 qraft-login.spec.ts）：已登录用户正常启动，
 *     登录门放行；
 *  4. 应用内退出登录：立即回到登录门（未登录不能继续使用主界面）。
 *
 * serial 模式：四条用例共享同一个 MIQI_HOME（同意记录、登录态文件、userData），
 * 必须按序执行（playwright.config.ts 全局 fullyParallel，describe.serial 内互斥）。
 *
 * 确定性说明：dev 模式下 userData 按 checkout 共享（main 的 ws-<hash> setPath
 * 覆盖 --user-data-dir），本 checkout 此前运行/重试留下的同意状态会让协议门
 * 被跳过——依赖协议门的用例先清记录、必要时重启一次。
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron -g "登录门"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { existsSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { launchElectronApp, relaunchElectronApp, closeElectronApp } from './helpers/electron-setup';

/** 登录态文件位置（helper 默认把 MIQI_QRAFT_STORE 指向临时 home）。 */
const STORE_ENV = 'MIQI_QRAFT_STORE';

/** 构造 plain 信封的预置登录态文件内容（QraftStore 支持无 safeStorage 降级读取）。 */
function buildSeededStoreContent(): string {
  const state = {
    version: 1,
    env: 'test',
    baseUrl: 'https://test.forge.miqroera.com/api',
    clientId: 'miqi',
    clientSecret: 'test-client-secret',
    redirectUri: 'http://localhost:38000/callback',
    cookie: 'Authorization=e2e-test-cookie',
    account: {
      phone: '18500000000',
      sub: '19',
      username: 'E2E-USER',
      nickname: 'E2E测试账号',
    },
    tokens: {
      accessToken: 'e2e-fake-access-token',
      refreshToken: 'e2e-fake-refresh-token',
      openid: 'e2e-fake-openid',
      expiresAt: Date.now() + 7_199_000,
    },
  };
  return JSON.stringify({
    v: 1,
    enc: 'plain',
    payload: Buffer.from(JSON.stringify(state), 'utf8').toString('base64'),
  });
}

/** 清掉同意记录（localStorage 缓存 + 主进程权威存储，幂等）。 */
async function clearStoredConsent(page: Page) {
  await page.evaluate(async () => {
    try {
      localStorage.removeItem('miqi:privacyConsentVersion');
    } catch {
      /* ignore */
    }
    try {
      await (window as any).miqi?.privacy?.setConsent(null);
    } catch {
      /* ignore */
    }
  });
}

async function gotoQraftTab(page: Page): Promise<void> {
  await page.getByText(/^(System Settings|系统设置)$/).click();
  await page
    .getByRole('tab')
    .filter({ hasText: /MiQroForge/ })
    .click();
}

let storePath: string;

test.describe.serial('登录门（#1095）', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(() => {
    storePath = join(tmpdir(), `login-gate-e2e-store-${process.pid}.json`);
    process.env[STORE_ENV] = storePath;
  });

  test.afterAll(async () => {
    delete process.env[STORE_ENV];
    if (electronApp) await closeElectronApp(electronApp, miqiHome);
    if (existsSync(storePath)) rmSync(storePath, { force: true });
  });

  test(
    '未登录 + 未同意协议：同意后停在登录门，退出应用结束进程',
    { timeout: 300_000 },
    async () => {
      // 两个门都走真实路径：renderer 未设 MIQI_E2E（协议门）也未设
      // MIQI_LOGIN_BYPASS（登录门）。
      const fixture = await launchElectronApp(undefined, {
        noConsentBypass: true,
        noLoginBypass: true,
      });
      electronApp = fixture.electronApp;
      page = fixture.page;
      miqiHome = fixture.miqiHome;

      // 清掉历史运行残留的同意记录；若本次启动已跳过协议门，重启一次。
      await clearStoredConsent(page);
      if ((await page.getByTestId('privacy-consent-gate').count()) === 0) {
        await closeElectronApp(electronApp, miqiHome, true);
        const fresh = await relaunchElectronApp(miqiHome, {
          noConsentBypass: true,
          noLoginBypass: true,
        });
        electronApp = fresh.electronApp;
        page = fresh.page;
      }

      await expect(page.getByTestId('privacy-consent-gate')).toBeVisible({ timeout: 60_000 });
      const agreeBtn = page.getByTestId('privacy-consent-agree');
      await expect(agreeBtn).toBeEnabled({ timeout: 10_000 });
      await agreeBtn.click();

      // #1095：同意协议后未登录 → 停在登录门，不再有「暂不登录」跳过路径
      await expect(page.getByTestId('login-step')).toBeVisible({ timeout: 60_000 });
      await expect(page.getByTestId('login-step-login-btn')).toBeVisible();
      await expect(page.getByTestId('login-step-quit')).toBeVisible();
      await expect(page.getByTestId('login-step-skip')).toHaveCount(0);
      // 未登录不进主界面
      await expect(page.getByTestId('app-title')).toHaveCount(0);
      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-login-gate.png`,
        fullPage: true,
      });

      // 退出前先给明确提示（需求 2：避免用户误以为崩溃）
      await page.getByTestId('login-step-quit').click();
      const quitDialog = page.getByTestId('login-step-quit-dialog');
      await expect(quitDialog).toBeVisible({ timeout: 10_000 });
      await expect(quitDialog).toContainText('需要登录 MiQroForge 账号后才能使用');
      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-quit-dialog.png`,
        fullPage: true,
      });

      // 「返回登录」= 取消退出，仍停在登录门
      await page.getByTestId('login-step-quit-cancel').click();
      await expect(quitDialog).toHaveCount(0);
      await expect(page.getByTestId('login-step')).toBeVisible();

      // 确认退出：走主进程 app.quit()，进程结束（macOS 上 window.close 不退出）
      const closed = electronApp.waitForEvent('close', { timeout: 30_000 }).catch(() => null);
      await page.getByTestId('login-step-quit').click();
      await page.getByTestId('login-step-quit-confirm').click();
      expect(await closed).not.toBeNull();
    }
  );

  test('未登录重启：每次启动都停在登录门', { timeout: 240_000 }, async () => {
    // 上一条已同意协议（记录已持久化）→ 本实例只面对登录门
    const fixture = await relaunchElectronApp(miqiHome, { noLoginBypass: true });
    electronApp = fixture.electronApp;
    page = fixture.page;

    await expect(page.getByTestId('login-step')).toBeVisible({ timeout: 90_000 });
    await expect(page.getByTestId('privacy-consent-gate')).toHaveCount(0);
    await expect(page.getByTestId('app-title')).toHaveCount(0);

    // 退出应用，为下一条（预置登录态）让路
    const closed = electronApp.waitForEvent('close', { timeout: 30_000 }).catch(() => null);
    await page.getByTestId('login-step-quit').click();
    await page.getByTestId('login-step-quit-confirm').click();
    expect(await closed).not.toBeNull();
  });

  test('已登录用户正常启动：登录门放行', { timeout: 240_000 }, async () => {
    // 预置登录态（service 构造时读盘）——登录门应直接放行进主界面
    writeFileSync(storePath, buildSeededStoreContent(), 'utf8');

    const fixture = await relaunchElectronApp(miqiHome, { noLoginBypass: true });
    electronApp = fixture.electronApp;
    page = fixture.page;

    await expect(page.getByTestId('app-title')).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId('login-step')).toHaveCount(0);
    await page.screenshot({
      path: `test-results/${test.info().title.replace(/\s+/g, '-')}-logged-in.png`,
      fullPage: true,
    });
  });

  test('应用内退出登录：立即回到登录门', { timeout: 180_000 }, async () => {
    await gotoQraftTab(page);
    const logoutBtn = page.getByTestId('qraft-logout-btn');
    await expect(logoutBtn).toBeVisible({ timeout: 30_000 });
    await logoutBtn.click();

    // 退出登录即未登录 → 登录门立即接管，主界面卸载
    await expect(page.getByTestId('login-step')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('app-title')).toHaveCount(0);
    await page.screenshot({
      path: `test-results/${test.info().title.replace(/\s+/g, '-')}-after-logout.png`,
      fullPage: true,
    });
  });
});
