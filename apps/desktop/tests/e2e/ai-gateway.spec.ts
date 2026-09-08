/**
 * AI 网关（issue #922）— Electron E2E。
 *
 * 覆盖真实主进程链路（qraft IPC → QraftService → QraftStore → token 文件）：
 *   1. 预置登录态携带 aiGateway（active）→ QraftPage 展示网关状态与配置版本；
 *      token 文件（Python 握手通道）写入 aiGateway 块（含 encryptedApiKey）；
 *      status() IPC 不泄漏密钥、account 不携带网关密钥。
 *   2. 预置登录态 aiGatewayStatus=provisioning → QraftPage 展示"开通中"，
 *      模型 tab 禁用模型下拉并引导查看平台账号。
 *
 * 不依赖 MiQroForge 网络：登录态由测试预置（plain 信封），与 qraft-login 同策略。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs';
import {
  launchElectronApp,
  closeElectronApp,
  type ElectronFixture,
} from './helpers/electron-setup';

const STORE_ENV = 'MIQI_QRAFT_STORE';
const GATEWAY_KEY = 'sk-e2e-gateway-secret-key';

interface SeededAiGateway {
  status: string;
  configVersion?: number;
}

/** 构造 plain 信封的预置登录态（QraftStore 无 safeStorage 降级读取），可携带 aiGateway。 */
function buildSeededStoreContent(aiGateway: SeededAiGateway | null): string {
  const state: Record<string, unknown> = {
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
      username: 'E2E-GATEWAY',
      nickname: 'E2E网关测试',
    },
    tokens: {
      accessToken: 'e2e-fake-access-token',
      refreshToken: 'e2e-fake-refresh-token',
      openid: 'e2e-fake-openid',
      expiresAt: Date.now() + 7_199_000, // 实测 expires_in=7199
    },
  };
  if (aiGateway) {
    state.aiGateway = {
      encryptedApiKey: GATEWAY_KEY,
      status: aiGateway.status,
      configVersion: aiGateway.configVersion ?? 1,
      consumerId: 'C-E2E',
    };
  }
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

test.describe('AI 网关 E2E (issue #922)', () => {
  let fixture: ElectronFixture;
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    storePath = join(tmpdir(), `qraft-gateway-e2e-store-${process.pid}.json`);
    process.env[STORE_ENV] = storePath;
    writeFileSync(storePath, buildSeededStoreContent({ status: 'active' }), 'utf8');
    fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
  });

  test.afterAll(async () => {
    delete process.env[STORE_ENV];
    if (electronApp) await closeElectronApp(electronApp, fixture?.miqiHome);
    if (existsSync(storePath)) rmSync(storePath, { force: true });
  });

  test('active：网关状态展示、token 文件握手、密钥不泄漏渲染进程', async () => {
    await gotoQraftTab(page);

    // QraftPage 网关状态行：可用 + 配置版本
    await expect(page.getByTestId('qraft-ai-gateway')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('qraft-ai-gateway-status')).toHaveText('可用');
    await expect(page.getByTestId('qraft-ai-gateway')).toContainText('配置版本 v1');

    // token 文件握手：登录态恢复时同步写入 aiGateway 块（Python make_provider 读取）
    const tokenFile = join(fixture.miqiHome, 'workspace', '.qraft', 'token.json');
    await expect
      .poll(() => (existsSync(tokenFile) ? readFileSync(tokenFile, 'utf8') : ''), {
        timeout: 10_000,
      })
      .toContain('"aiGateway"');
    const tokenContent = JSON.parse(readFileSync(tokenFile, 'utf8'));
    expect(tokenContent.aiGateway).toMatchObject({
      encryptedApiKey: GATEWAY_KEY,
      status: 'active',
      configVersion: 1,
    });

    // 渲染进程可见的状态：aiGateway 只含 status/configVersion；account 不含密钥。
    //（回归 #922 实现中的 account 展开泄漏 —— encryptedApiKey 绝不能进 renderer）
    const statusJson = await page.evaluate(async () => {
      const s = await (window as any).miqi.qraft.status();
      return JSON.stringify(s);
    });
    expect(statusJson).not.toContain(GATEWAY_KEY);
    expect(statusJson).toContain('"aiGateway":{"status":"active","configVersion":1}');
    expect(statusJson).not.toContain('"encryptedApiKey"');

    await page.screenshot({
      path: 'test-results/ai-gateway-e2e-active.png',
      fullPage: true,
    });
  });

  test('provisioning：平台页展示"开通中"，模型 tab 禁用并引导', async () => {
    // 重新预置非 active 登录态并重启应用（store 只在 service 构造时读取）
    await closeElectronApp(electronApp, fixture.miqiHome);
    writeFileSync(storePath, buildSeededStoreContent({ status: 'provisioning' }), 'utf8');
    const f2 = await launchElectronApp();
    electronApp = f2.electronApp;
    page = f2.page;
    fixture = f2;

    await gotoQraftTab(page);
    await expect(page.getByTestId('qraft-ai-gateway')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('qraft-ai-gateway-status')).toHaveText('开通中');
    await expect(page.getByTestId('qraft-ai-gateway')).toContainText('暂时无法发起会话');

    // 模型 tab：ModelQuickPanel 网关门禁（未就绪 → 禁用下拉 + 平台账号引导）
    await page.getByRole('tab', { name: '模型' }).click();
    await expect(page.getByText('AI 网关未就绪')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('查看平台账号')).toBeVisible();
    await expect(page.getByText('登录后使用平台内置模型')).not.toBeVisible();

    await page.screenshot({
      path: 'test-results/ai-gateway-e2e-provisioning.png',
      fullPage: true,
    });
  });
});
