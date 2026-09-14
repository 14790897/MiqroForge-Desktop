/**
 * MiQroForge 扣费历史跨登出/重启留存 — Electron E2E。
 *
 * 覆盖真实主进程链路（qraft IPC → QraftService → 扣费历史文件）：
 *   1. 预置登录态 + 历史文件 → 设置页只展示当前账号的记录（他账号记录不展示）
 *   2. 退出登录 → 历史文件不删除（回归：初版在 logout 里 rmSync，平台轮换
 *      refresh_token 迫使重新登录时用户历史整份消失），未登录界面不外发记录
 *   3. 重启应用 + 重新登录同一账号 → 记录仍在，状态栏积分弹层可见
 *
 * 不依赖 MiQroForge 网络：登录态与历史均由测试预置（plain 信封 + JSON 文件，
 * 经 MIQI_QRAFT_STORE / MIQI_QRAFT_BILLING_DIR 改道临时目录），余额查询指向
 * 本地 mock（状态栏积分按钮只在有余额缓存时渲染）。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import {
  launchElectronApp,
  relaunchElectronApp,
  closeElectronApp,
  type ElectronFixture,
} from './helpers/electron-setup';

const STORE_ENV = 'MIQI_QRAFT_STORE';
const BILLING_ENV = 'MIQI_QRAFT_BILLING_DIR';

const ACCOUNT_SUB = '19';
const OTHER_SUB = '77';

let tmpDir: string;
let storePath: string;
let billingDir: string;
let historyPath: string;

/** 预置登录态（plain 信封，QraftStore 支持无 safeStorage 降级读取）。 */
function seedStore(baseUrl: string): void {
  const state = {
    version: 1,
    env: 'test',
    baseUrl,
    clientId: 'miqi',
    clientSecret: 'test-client-secret',
    redirectUri: 'http://localhost:38000/callback',
    cookie: 'Authorization=e2e-test-cookie',
    account: {
      phone: '18500000000',
      sub: ACCOUNT_SUB,
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
  writeFileSync(
    storePath,
    JSON.stringify({
      v: 1,
      enc: 'plain',
      payload: Buffer.from(JSON.stringify(state), 'utf8').toString('base64'),
    }),
    'utf8'
  );
}

/** 预置扣费历史：两条当前账号（sub 19）+ 一条其他账号（sub 77，不应展示）。 */
function seedHistory(): void {
  const entry = (over: Record<string, unknown>) => ({
    cost: 10,
    status: 'billed',
    serverName: 'miqroforge-slurm',
    toolName: 'check_job_status',
    sessionKey: 'desktop:default',
    ...over,
  });
  const entries = [
    entry({
      chargeId: 'e2e-charge-2',
      jobId: '12137708',
      deductedAt: '2026-09-11T08:19:14.574Z',
      balanceAfter: 170,
      argsSummary: '{"job_id": "12137708"}',
      accountSub: ACCOUNT_SUB,
    }),
    entry({
      chargeId: 'e2e-charge-1',
      jobId: '12137427',
      deductedAt: '2026-09-11T07:21:56.488Z',
      balanceAfter: 180,
      argsSummary: '{"job_id": "12137427"}',
      accountSub: ACCOUNT_SUB,
    }),
    entry({
      chargeId: 'e2e-charge-other',
      jobId: '99999999',
      deductedAt: '2026-09-11T06:00:00.000Z',
      balanceAfter: 500,
      argsSummary: '{"job_id": "99999999"}',
      accountSub: OTHER_SUB,
    }),
  ];
  mkdirSync(billingDir, { recursive: true });
  writeFileSync(historyPath, JSON.stringify(entries, null, 2), 'utf8');
}

/** 本地 mock：积分余额查询返回正常信封（其余请求同样返回空余额，不参与断言）。 */
async function startBalanceMock(): Promise<{ port: number; close: () => Promise<void> }> {
  const mock = createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(
      JSON.stringify({
        code: 200,
        msg: 'ok',
        data: { availablePoints: 1234, heldPoints: 0, totalEarned: 0, totalSpent: 60 },
      })
    );
  });
  await new Promise<void>((resolve) => mock.listen(0, '127.0.0.1', resolve));
  return {
    port: (mock.address() as AddressInfo).port,
    close: () => new Promise<void>((resolve) => mock.close(() => resolve())),
  };
}

async function gotoQraftTab(page: Page): Promise<void> {
  await page.getByText(/^(System Settings|系统设置)$/).click();
  await page
    .getByRole('tab')
    .filter({ hasText: /MiQroForge/ })
    .click();
}

test.describe('MiQroForge 扣费历史跨登出/重启留存 E2E', () => {
  let fixture: ElectronFixture;
  let electronApp: ElectronApplication;
  let page: Page;
  let mock: { port: number; close: () => Promise<void> };

  test.beforeAll(async () => {
    tmpDir = mkdtempSync(join(tmpdir(), 'qraft-billing-e2e-'));
    storePath = join(tmpDir, 'qraft-auth.json');
    billingDir = join(tmpDir, 'billing');
    historyPath = join(billingDir, 'qraft-billing-history.json');
    mock = await startBalanceMock();
    process.env[STORE_ENV] = storePath;
    process.env[BILLING_ENV] = billingDir;
    seedStore(`http://127.0.0.1:${mock.port}/api`);
    seedHistory();

    fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
  });

  test.afterAll(async () => {
    delete process.env[STORE_ENV];
    delete process.env[BILLING_ENV];
    if (electronApp) await closeElectronApp(electronApp, fixture?.miqiHome);
    await mock?.close();
    if (tmpDir) rmSync(tmpDir, { recursive: true, force: true });
  });

  test('设置页只展示当前登录账号的扣费记录', async () => {
    await gotoQraftTab(page);

    const block = page.getByTestId('qraft-billing-history');
    await expect(block).toBeVisible({ timeout: 15_000 });
    await expect(block.getByText('作业 12137708')).toBeVisible();
    await expect(block.getByText('作业 12137427')).toBeVisible();
    // 其他账号（sub 77）的记录不展示
    await expect(block.getByText('作业 99999999')).toHaveCount(0);

    await page.screenshot({
      path: 'test-results/qraft-billing-history-settings.png',
      fullPage: false,
    });
  });

  test('退出登录不删除历史文件；重启重新登录同一账号后记录仍在', async () => {
    await gotoQraftTab(page);
    await expect(page.getByTestId('qraft-billing-history')).toBeVisible({ timeout: 15_000 });

    // 退出登录：界面回到登录入口，历史区块消失（未登录不外发记录）
    await page.getByTestId('qraft-logout-btn').click();
    await expect(page.getByTestId('qraft-browser-login-btn')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('qraft-billing-history')).toHaveCount(0);

    // 历史文件仍在磁盘上（回归点：#927 初版 logout 里 rmSync 整份文件）
    expect(existsSync(historyPath)).toBe(true);
    expect(JSON.parse(readFileSync(historyPath, 'utf8'))).toHaveLength(3);

    // 关闭软件 → 重新登录同一账号（重新预置登录态）→ 记录仍在
    await closeElectronApp(electronApp, fixture.miqiHome);
    seedStore(`http://127.0.0.1:${mock.port}/api`);
    const f2 = await relaunchElectronApp(fixture.miqiHome);
    electronApp = f2.electronApp;
    page = f2.page;
    fixture = f2;

    await gotoQraftTab(page);
    await expect(page.getByTestId('qraft-billing-history')).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByTestId('qraft-billing-history').getByText('作业 12137708')
    ).toBeVisible();

    // 状态栏积分弹层（明细入口）同样展示记录。先把弹层压到 240px：行宽受
    // 平台字体度量影响（CI Linux 字体更宽时曾把作业 ID 挤成 0 宽、整条不可见），
    // 固定窄宽 + 宽度下限让该断言与平台无关。
    await page.getByTestId('statusbar-points').click();
    await expect(page.getByTestId('statusbar-points-popover')).toBeVisible({ timeout: 15_000 });
    await page.evaluate(() => {
      const el = document.querySelector<HTMLElement>('[data-testid="statusbar-points-popover"]');
      if (el) el.style.width = '220px';
    });
    const jobLabel = page.getByTestId('statusbar-billing-history').getByText('作业 12137708');
    await expect(jobLabel).toBeVisible();
    const jobBox = await jobLabel.boundingBox();
    expect(jobBox?.width ?? 0).toBeGreaterThan(40);

    // 截图用正常宽度（断言用的窄宽已生效过，这里还原后再截图）
    await page.evaluate(() => {
      const el = document.querySelector<HTMLElement>('[data-testid="statusbar-points-popover"]');
      if (el) el.style.width = '';
    });
    await page.screenshot({
      path: 'test-results/qraft-billing-history-popover.png',
      fullPage: false,
    });
  });
});
