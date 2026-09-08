/**
 * MiQroForge 平台 OAuth2 登录 — Electron E2E（issue #726）。
 *
 * 覆盖真实主进程链路（qraft IPC → QraftService → QraftStore 落盘）：
 *   1. 登录表单渲染、密码掩码、高级设置折叠
 *   2. 登录失败的错误分类提示（不可路由 baseUrl 强制网络失败，无外部依赖）
 *   3. 预置登录态（MIQI_QRAFT_STORE 指向临时文件）→ 账号信息展示 →
 *      退出登录 → 磁盘文件被清空（验证真实持久化路径）
 *
 * 不依赖 MiQroForge 网络：登录态由测试预置（plain 信封），错误路径用
 * TEST-NET-1（192.0.2.1）强制请求失败，任何平台（含 macOS CI）行为一致。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs';
import {
  launchElectronApp,
  closeElectronApp,
  type ElectronFixture,
} from './helpers/electron-setup';

const STORE_ENV = 'MIQI_QRAFT_STORE';
const TEST_BASE_URL = 'https://192.0.2.1/api'; // TEST-NET-1，永远不可达

/** 构造 plain 信封的预置登录态文件内容（QraftStore 支持无 safeStorage 降级读取）。 */
function buildSeededStoreContent(overrides: { baseUrl?: string; expiresAt?: number } = {}): string {
  const state = {
    version: 1,
    env: 'test',
    baseUrl: overrides.baseUrl ?? 'https://test.forge.miqroera.com/api',
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
      expiresAt: overrides.expiresAt ?? Date.now() + 7_199_000, // 实测 expires_in=7199
    },
  };
  return JSON.stringify({
    v: 1,
    enc: 'plain',
    payload: Buffer.from(JSON.stringify(state), 'utf8').toString('base64'),
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

test.describe('MiQroForge 平台登录 E2E (issue #726)', () => {
  let fixture: ElectronFixture;
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    storePath = join(tmpdir(), `qraft-e2e-store-${process.pid}.json`);
    process.env[STORE_ENV] = storePath;
    fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
  });

  test.afterAll(async () => {
    delete process.env[STORE_ENV];
    if (electronApp) await closeElectronApp(electronApp, fixture?.miqiHome);
    if (existsSync(storePath)) rmSync(storePath, { force: true });
  });

  test('登录表单渲染：手机号/密码（掩码）/高级设置默认折叠', async () => {
    await gotoQraftTab(page);

    const phoneInput = page.getByTestId('qraft-phone-input');
    const passwordInput = page.getByTestId('qraft-password-input');
    await expect(phoneInput).toBeVisible({ timeout: 15_000 });
    await expect(passwordInput).toBeVisible();
    await expect(passwordInput).toHaveAttribute('type', 'password');
    await expect(page.getByTestId('qraft-login-btn')).toBeVisible();
    // 环境选择已隐藏（生产域名未上线，仅测试环境可用）
    await expect(page.getByRole('button', { name: '测试环境' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '生产环境' })).toHaveCount(0);
    // 高级设置默认折叠，展开后出现接入配置输入框
    await expect(page.getByTestId('qraft-baseurl-input')).not.toBeVisible();
    await page.getByText('高级设置（接入配置，默认按环境预填）').click();
    await expect(page.getByTestId('qraft-baseurl-input')).toBeVisible();
    await expect(page.getByTestId('qraft-client-secret-input')).toBeVisible();
  });

  test('登录失败展示分类错误提示（不可达 baseUrl → 网络类错误）', async () => {
    await gotoQraftTab(page);

    await page.getByTestId('qraft-phone-input').fill('18500000000');
    await page.getByTestId('qraft-password-input').fill('not-a-real-password');
    await page.getByText('高级设置（接入配置，默认按环境预填）').click();
    await page.getByTestId('qraft-baseurl-input').fill(TEST_BASE_URL);
    await page.getByTestId('qraft-login-btn').click();

    // 主进程对 192.0.2.1 重试 3 次后失败 → 错误框给出分类提示。
    // 用户可感知结果：错误提示出现且包含网络类文案，而不是空白/崩溃。
    const errorBox = page.getByTestId('qraft-login-error');
    await expect(errorBox).toBeVisible({ timeout: 120_000 });
    const text = (await errorBox.textContent()) ?? '';
    expect(/网络请求失败|网络请求|请检查网络/.test(text)).toBe(true);

    await page.screenshot({
      path: 'test-results/qraft-e2e-login-error.png',
      fullPage: true,
    });
  });

  test('预置登录态展示账号信息，退出登录清空状态与磁盘文件', async () => {
    // 登录态在 service 构造时从磁盘加载 —— 先关掉当前实例，
    // 预置 store 文件后重新启动（走真实持久化读取路径）。
    await closeElectronApp(electronApp, fixture.miqiHome);
    writeFileSync(storePath, buildSeededStoreContent(), 'utf8');

    const f2 = await launchElectronApp();
    electronApp = f2.electronApp;
    page = f2.page;
    fixture = f2;

    await gotoQraftTab(page);

    // 账号信息（nickname/username/脱敏手机号）与 token 到期时间
    await expect(page.getByText('E2E测试账号').first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('已登录')).toBeVisible();
    await expect(page.getByText(/185\*{4}0000/)).toBeVisible();
    await expect(page.getByText('access_token 到期：')).toBeVisible();
    await expect(page.getByTestId('qraft-logout-btn')).toBeVisible();

    // token 文件通道：登录态恢复时同步写入 workspace/.qraft/token.json
    //（供 Skill/agent 读取，仅含 accessToken + expiresAt）
    const tokenFile = join(fixture.miqiHome, 'workspace', '.qraft', 'token.json');
    await expect
      .poll(() => (existsSync(tokenFile) ? readFileSync(tokenFile, 'utf8') : ''), {
        timeout: 10_000,
      })
      .toContain('e2e-fake-access-token');
    expect(JSON.parse(readFileSync(tokenFile, 'utf8'))).not.toHaveProperty('refreshToken');

    // agent 视角：走 agent 文件工具同一条链路（files.read，workspace 相对路径）
    // 读取 token 文件 —— 验证 MiQroForge agent（Python 后端）确实拿得到 access_token。
    const agentRead = await page.evaluate(async () => {
      try {
        const r: { path?: string; content?: string; size?: number } = await (
          window as any
        ).miqi.files.read('.qraft/token.json');
        return { ok: true, content: r?.content ?? '', size: r?.size ?? 0 };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    });
    expect(agentRead.ok, `agent 读取 token 文件失败：${JSON.stringify(agentRead)}`).toBe(true);
    expect(agentRead.content).toContain('e2e-fake-access-token');
    expect(agentRead.content).toContain('expiresAt');

    await page.screenshot({
      path: 'test-results/qraft-e2e-logged-in.png',
      fullPage: true,
    });

    // 退出登录：界面回到登录表单，磁盘凭据清空（store 文件与 token 文件）
    // IPC 返回与磁盘写入存在竞态，轮询文件直到为空。
    await page.getByTestId('qraft-logout-btn').click();
    await expect(page.getByTestId('qraft-phone-input')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('qraft-login-btn')).toBeVisible();
    await expect
      .poll(() => (existsSync(storePath) ? readFileSync(storePath, 'utf8') : ''), {
        timeout: 10_000,
      })
      .toBe('');
    await expect.poll(() => existsSync(tokenFile), { timeout: 10_000 }).toBe(false);
  });

  // macOS CI 的 undici fetch 连不上本地 127.0.0.1 监听（实测 macos-e2e），
  // 与本仓其他本地 mock 用例（confirm-card）同样的裁剪策略：Linux electron-e2e 覆盖。
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  test('refresh_token 已失效（平台作废）→ 停止自动重试并引导重新登录', async () => {
    // 本地 mock 刷新端点：与真实平台一致，返回 Sa-Token 失效响应并
    // 回显请求中实际收到的 refresh_token（HTTP 200 + code 500）。
    // 其他 qraft 请求（设置页会自动拉取积分余额）不参与本用例断言，
    // 返回正常空余额信封即可。
    let refreshCalls = 0;
    const mock = createServer((req, res) => {
      if (!(req.url ?? '').includes('/oauth2/refresh')) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(
          JSON.stringify({
            code: 200,
            msg: 'ok',
            data: { availablePoints: 0, heldPoints: 0, totalEarned: 0, totalSpent: 0 },
          })
        );
        return;
      }
      refreshCalls += 1;
      let body = '';
      req.on('data', (d) => (body += d));
      req.on('end', () => {
        const echoed = new URLSearchParams(body).get('refresh_token') ?? '';
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(
          JSON.stringify({
            code: 500,
            msg: '未知错误',
            data: {
              message: '未知错误',
              originalMessage: `SaOAuth2RefreshTokenException: 无效refresh_token: ${echoed}`,
            },
          })
        );
      });
    });
    await new Promise<void>((resolve) => mock.listen(0, '127.0.0.1', resolve));
    const mockPort = (mock.address() as AddressInfo).port;

    // 无论启动、UI 等待或断言是否失败都关掉 mock：Playwright 不管理该
    // 服务器，close() 是异步的，必须 await 完成避免残留句柄。
    try {
      // 预置登录态：token 已过期 + baseUrl 指向本地 mock。
      // 应用启动时（service 构造）发现已过期 → 立即自动刷新一次 → 平台判定失效。
      await closeElectronApp(electronApp, fixture.miqiHome);
      writeFileSync(
        storePath,
        buildSeededStoreContent({
          baseUrl: `http://127.0.0.1:${mockPort}/api`,
          expiresAt: Date.now() - 1000,
        }),
        'utf8'
      );

      const f2 = await launchElectronApp();
      electronApp = f2.electronApp;
      page = f2.page;
      fixture = f2;

      await gotoQraftTab(page);

      // 引导重新登录的横幅出现（REFRESH_TOKEN_INVALID 置 requiresRelogin）
      await expect(page.getByTestId('qraft-relogin-banner')).toBeVisible({ timeout: 15_000 });
      expect(refreshCalls).toBeGreaterThanOrEqual(1);

      // 永久失败不再排 30 分钟重试：计划自动刷新显示 —，且 3 秒内无新请求
      await expect(page.getByText('计划自动刷新：').locator('..').locator('dd')).toHaveText('—');
      await page.waitForTimeout(3000);
      expect(refreshCalls).toBe(1);

      // 手动刷新：错误框展示新分类文案，且不回显 refresh_token 原文
      //（mock 会回显请求里实际发送的 token，客户端必须脱敏）
      await page.getByTestId('qraft-refresh-btn').click();
      const errorBox = page.getByTestId('qraft-refresh-error');
      await expect(errorBox).toBeVisible({ timeout: 15_000 });
      await expect(errorBox).toContainText('refresh_token 已失效，请重新登录');
      await expect(errorBox).not.toContainText('e2e-fake-refresh-token');

      await page.screenshot({
        path: 'test-results/qraft-e2e-refresh-invalid.png',
        fullPage: true,
      });
    } finally {
      await new Promise<void>((resolve) => mock.close(() => resolve()));
    }
  });
});
