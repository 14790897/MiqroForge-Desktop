/**
 * MiQroForge 平台 OAuth2 登录 — Electron E2E（issue #726）。
 *
 * 覆盖真实主进程链路（qraft IPC → QraftService → QraftStore 落盘）：
 *   1. 登录页只渲染浏览器登录（OAuth）入口（手机号表单/高级设置已隐藏）
 *   2. 预置登录态（MIQI_QRAFT_STORE 指向临时文件）→ 账号信息展示 →
 *      退出登录 → 磁盘文件被清空（验证真实持久化路径）
 *
 * 不依赖 MiQroForge 网络：登录态由测试预置（plain 信封），行为在任何
 * 平台（含 macOS CI）一致。
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
  getAccountWorkspaceDir,
  type ElectronFixture,
} from './helpers/electron-setup';

const STORE_ENV = 'MIQI_QRAFT_STORE';

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

/**
 * 本地 mock 刷新端点：与真实平台一致，返回 Sa-Token 失效响应并
 * 回显请求中实际收到的 refresh_token（HTTP 200 + code 500）。
 * 其他 qraft 请求（设置页会自动拉取积分余额）不参与失效断言，
 * 返回正常空余额信封即可。
 */
async function startInvalidRefreshMock(onRefresh?: () => void): Promise<{
  port: number;
  close: () => Promise<void>;
}> {
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
    onRefresh?.();
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
  return {
    port: (mock.address() as AddressInfo).port,
    close: () => new Promise<void>((resolve) => mock.close(() => resolve())),
  };
}

/** 预置「已过期 token + baseUrl 指向本地 mock」的登录态（重启后应用立即自动刷新失败）。 */
function seedExpiredStore(mockPort: number): void {
  writeFileSync(
    storePath,
    buildSeededStoreContent({
      baseUrl: `http://127.0.0.1:${mockPort}/api`,
      expiresAt: Date.now() - 1000,
    }),
    'utf8'
  );
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

  test('登录页只渲染浏览器登录（OAuth）入口', async () => {
    await gotoQraftTab(page);

    // 浏览器登录入口可见；手机号/密码表单、提交按钮与高级设置均不渲染
    await expect(page.getByTestId('qraft-browser-login-btn')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('qraft-phone-input')).toHaveCount(0);
    await expect(page.getByTestId('qraft-password-input')).toHaveCount(0);
    await expect(page.getByTestId('qraft-login-btn')).toHaveCount(0);
    await expect(page.getByTestId('qraft-baseurl-input')).toHaveCount(0);
    await expect(page.getByText('高级设置（接入配置，默认按环境预填）')).toHaveCount(0);
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
    // #1185：预置的是「已登录」态，工作区按账号收口，落点在 accounts/<sub>/ 下。
    const tokenFile = join(getAccountWorkspaceDir(fixture.miqiHome, '19'), '.qraft', 'token.json');
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

    // 退出登录：界面回到登录入口，磁盘凭据清空（store 文件与 token 文件）
    // IPC 返回与磁盘写入存在竞态，轮询文件直到为空。
    await page.getByTestId('qraft-logout-btn').click();
    await expect(page.getByTestId('qraft-browser-login-btn')).toBeVisible({ timeout: 15_000 });
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

  test('refresh_token 已失效（平台作废）→ 自动退出登录，登录页说明「已自动退出」', async () => {
    let refreshCalls = 0;
    const mockServer = await startInvalidRefreshMock(() => {
      refreshCalls += 1;
    });
    const mockPort = mockServer.port;

    // 无论启动、UI 等待或断言是否失败都关掉 mock：Playwright 不管理该
    // 服务器，close() 是异步的，必须 await 完成避免残留句柄。
    try {
      // 预置登录态：token 已过期 + baseUrl 指向本地 mock。
      // 应用启动时（service 构造）发现已过期 → 立即自动刷新一次 → 平台判定失效。
      await closeElectronApp(electronApp, fixture.miqiHome);
      seedExpiredStore(mockPort);

      // 登录门**不绕过**：要看真实用户看到的登录页（#1095）与「已自动退出」提示。
      const f2 = await launchElectronApp(undefined, { noLoginBypass: true });
      electronApp = f2.electronApp;
      page = f2.page;
      fixture = f2;

      // 平台判定 refresh_token 作废 → 直接自动退出登录，人停在登录页
      await expect(page.getByTestId('login-step')).toBeVisible({ timeout: 60_000 });
      const notice = page.getByTestId('login-step-session-expired');
      await expect(notice).toBeVisible({ timeout: 30_000 });
      await expect(notice).toContainText('登录已失效');
      await expect(notice).toContainText('已自动退出登录');

      // 状态：已登出 + 带失效标记（登录页据此说明，而不是让人面对无解释的登录页）
      const status = await page.evaluate(async () => await (window as any).miqi.qraft.status());
      expect(status.loggedIn).toBe(false);
      expect(status.sessionExpired).toBe(true);

      // 凭据已清干净：加密登录态文件被清空
      await expect
        .poll(() => (existsSync(storePath) ? readFileSync(storePath, 'utf8') : ''), {
          timeout: 10_000,
        })
        .toBe('');

      await page.screenshot({
        path: 'test-results/qraft-e2e-auto-logout.png',
        fullPage: true,
      });

      // 永久失效不再自动重试：观察窗口内没有新的刷新请求
      expect(refreshCalls).toBeGreaterThanOrEqual(1);
      const callsAfterLogout = refreshCalls;
      await page.waitForTimeout(3000);
      expect(refreshCalls).toBe(callsAfterLogout);
    } finally {
      await mockServer.close();
    }
  });

  test('自动退出登录后：发送消息仍给出一键登录引导气泡', async () => {
    const mockServer = await startInvalidRefreshMock();
    const mockPort = mockServer.port;

    try {
      await closeElectronApp(electronApp, fixture.miqiHome);
      seedExpiredStore(mockPort);

      const f2 = await launchElectronApp();
      electronApp = f2.electronApp;
      page = f2.page;
      fixture = f2;

      // 平台判定失效 → 自动退出登录。本用例走 E2E 的登录门绕过（主界面仍在），
      // 所以这里断言的是「退出后仍能发消息时，给出的是登录引导而非模型配置指引」。
      await expect
        .poll(
          async () =>
            await page.evaluate(async () => (await (window as any).miqi.qraft.status()).loggedIn),
          { timeout: 30_000 }
        )
        .toBe(false);

      const textarea = page.locator('[data-testid="chat-input-container"] textarea');
      await textarea.fill('继续之前的工作');
      await page.evaluate(() => {
        const ta = document.querySelector<HTMLTextAreaElement>(
          '[data-testid="chat-input-container"] textarea'
        );
        if (!ta) throw new Error('textarea not found');
        ta.dispatchEvent(
          new KeyboardEvent('keydown', {
            key: 'Enter',
            code: 'Enter',
            keyCode: 13,
            bubbles: true,
            cancelable: true,
          })
        );
      });

      await expect(page.getByTestId('chat-error-login-btn')).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId('chat-error-login-btn')).toContainText('登录 MiQroForge 账号');

      await page.screenshot({
        path: 'test-results/qraft-e2e-auto-logout-send-intercept.png',
        fullPage: true,
      });
    } finally {
      await mockServer.close();
    }
  });
});
