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
const GATEWAY_KEY = 'sk-e2e-gateway-secret-key';

interface SeededAiGateway {
  status: string;
  configVersion?: number;
}

/** 构造 plain 信封的预置登录态（QraftStore 无 safeStorage 降级读取），可携带 aiGateway。 */
function buildSeededStoreContent(
  aiGateway: SeededAiGateway | null,
  overrides: { baseUrl?: string } = {}
): string {
  const state: Record<string, unknown> = {
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
    // #1185：预置了登录态 → 工作区按账号收口，落点是 accounts/<sub>/workspace。
    const tokenFile = join(getAccountWorkspaceDir(fixture.miqiHome, '19'), '.qraft', 'token.json');
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

  test('active + 存量 gateway 型 provider 旧 key：默认模型仍自动就绪（#1172）', async () => {
    // #1172 的主复现场景：全新安装的 agents.defaults.model 是 schema 默认值
    // anthropic/claude-opus-4-5（config.get 把默认值带出来，永远非空）。
    // 关键差异：这里额外给 siliconflow（is_gateway，按名字路由任意模型）留一把
    // 旧 key —— 真实后端的 _model_provider_resolvable 会经「已配置 gateway 兜底」
    // 把 anthropic 默认值判成可发起会话（active_model_resolvable=true），
    // 若自动就绪只看该宽口径判据就会跳过写入，用户仍要手动选模型。
    // 本用例断言严格判据下依然自动落盘为网关模型。
    test.setTimeout(240_000);
    await closeElectronApp(electronApp, fixture.miqiHome);
    writeFileSync(storePath, buildSeededStoreContent({ status: 'active' }), 'utf8');
    const f2 = await launchElectronApp((config) => {
      config.agents = config.agents ?? {};
      config.agents.defaults = config.agents.defaults ?? {};
      config.agents.defaults.model = 'anthropic/claude-opus-4-5';
      // 存量凭据：gateway 型 provider 的遗留 key（#835 收口前配置的）。
      config.providers = config.providers ?? {};
      config.providers.siliconflow = {
        ...(config.providers.siliconflow ?? {}),
        api_key: 'sk-e2e-legacy-siliconflow',
      };
    });
    electronApp = f2.electronApp;
    page = f2.page;
    fixture = f2;

    // 自动落盘：比较并设置（expectModel=遗留默认值）把默认模型换成网关模型
    const configPath = join(fixture.miqiHome, 'config.json');
    await expect
      .poll(() => JSON.parse(readFileSync(configPath, 'utf8')).agents?.defaults?.model, {
        // CI 冷启动 + 桥握手可能很慢，自动就绪的重试窗口本身最长约 26s，留足余量
        timeout: 120_000,
      })
      .toBe('deepseek/deepseek-v4-flash');

    // 模型 tab：没有任何手动选择，「当前默认模型」已是网关模型
    await gotoQraftTab(page);
    await page.getByRole('tab', { name: '模型' }).click();
    await expect(page.getByTestId('providers-active-model')).toHaveText(
      '当前默认模型：deepseek/deepseek-v4-flash',
      { timeout: 15_000 }
    );

    await page.screenshot({
      path: 'test-results/gateway-model-autoready-1172.png',
      fullPage: true,
    });
  });

  test('active + 全新安装（无任何用户配置）：默认模型自动就绪（#1172）', async () => {
    // #1172 的另一个复现前提：MIQI_HOME 里**没有**用户配置 —— 既不拷贝开发者
    // 本机的 provider 凭据，也没有显式配置过 agents.defaults.model。
    // noUserConfig 关掉 launchElectronApp 的本机配置拷贝，临时 home 只剩启动
    // 时写入的 approvals/channels，agents.defaults.model 完全是 schema 默认值。
    test.setTimeout(240_000);
    await closeElectronApp(electronApp, fixture.miqiHome);
    writeFileSync(storePath, buildSeededStoreContent({ status: 'active' }), 'utf8');
    const f2 = await launchElectronApp(undefined, { noUserConfig: true });
    electronApp = f2.electronApp;
    page = f2.page;
    fixture = f2;

    // 自动落盘：默认模型从 schema 默认值换成网关模型，全程无手动选择
    const configPath = join(fixture.miqiHome, 'config.json');
    await expect
      .poll(
        () => {
          try {
            return JSON.parse(readFileSync(configPath, 'utf8')).agents?.defaults?.model ?? '';
          } catch {
            return '';
          }
        },
        { timeout: 120_000 }
      )
      .toBe('deepseek/deepseek-v4-flash');

    await gotoQraftTab(page);
    await page.getByRole('tab', { name: '模型' }).click();
    await expect(page.getByTestId('providers-active-model')).toHaveText(
      '当前默认模型：deepseek/deepseek-v4-flash',
      { timeout: 15_000 }
    );

    await page.screenshot({
      path: 'test-results/gateway-model-autoready-1172-no-config.png',
      fullPage: true,
    });
  });

  // macOS CI 的 undici fetch 连不上本地 127.0.0.1 监听（同 qraft-login.spec.ts）。
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  test('登录态缺 aiGateway（登录时未下发）：启动补拉失败后，点「立即刷新」补拉 userinfo 并生效（#1251）', async () => {
    // #1251 的现场：平台在用户登录**之后**才开通网关，或登录那次 userinfo
    // 失败（登录流程只记警告、照常成功）—— 登录态里没有 aiGateway 块。
    // 旧行为只在登录那一刻拉一次 userinfo，这份「未下发」会一直留着，
    // 用户只能退出重登。本用例用本地 mock 平台覆盖两条补拉路径。
    test.setTimeout(180_000);
    let userInfoCalls = 0;
    const mock = createServer((req, res) => {
      const url = req.url ?? '';
      if (url.includes('/oauth2/userinfo')) {
        userInfoCalls += 1;
        if (userInfoCalls === 1) {
          // 启动那一次失败：模拟平台瞬时不可用 / 下发失败（非 JSON → USERINFO_FAILED）
          res.writeHead(500, { 'Content-Type': 'text/plain' });
          res.end('platform unavailable');
          return;
        }
        // 真实平台实测：字段平铺在顶层（无 data 嵌套）
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(
          JSON.stringify({
            code: 200,
            msg: 'ok',
            sub: '19',
            username: 'E2E-GATEWAY',
            nickname: 'E2E网关测试',
            encryptedApiKey: GATEWAY_KEY,
            aiGatewayStatus: 'active',
            configVersion: 1,
            consumerId: 'C-E2E',
          })
        );
        return;
      }
      if (url.includes('/oauth2/refresh')) {
        req.resume();
        // 刷新成功响应同样是平铺结构（见 QraftClient.refreshTokens）
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(
          JSON.stringify({
            code: 200,
            msg: 'ok',
            token_type: 'bearer',
            access_token: 'e2e-refreshed-access-token',
            refresh_token: 'e2e-fake-refresh-token',
            expires_in: '7199',
          })
        );
        return;
      }
      // 其余（设置页会自动拉积分余额）：正常空余额信封，避免干扰断言
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          code: 200,
          msg: 'ok',
          data: { availablePoints: 0, heldPoints: 0, totalEarned: 0, totalSpent: 0 },
        })
      );
    });
    await new Promise<void>((resolve) => mock.listen(0, '127.0.0.1', resolve));
    const mockPort = (mock.address() as AddressInfo).port;

    try {
      await closeElectronApp(electronApp, fixture.miqiHome);
      writeFileSync(
        storePath,
        buildSeededStoreContent(null, { baseUrl: `http://127.0.0.1:${mockPort}/api` }),
        'utf8'
      );
      const f2 = await launchElectronApp();
      electronApp = f2.electronApp;
      page = f2.page;
      fixture = f2;

      await gotoQraftTab(page);
      await expect(page.getByText('已登录')).toBeVisible({ timeout: 15_000 });

      // 启动补拉失败 → 平台账号页没有网关行（用户看到的就是「没有网关状态」）。
      // 等应用真正发过一次 userinfo 再断言，避免抢在补拉发起之前。
      await expect.poll(() => userInfoCalls, { timeout: 15_000 }).toBeGreaterThanOrEqual(1);
      await expect(page.getByTestId('qraft-ai-gateway')).toHaveCount(0);

      // 「立即刷新」= 用户的补拉入口：刷新 token 后重新取 userinfo
      await page.getByTestId('qraft-refresh-btn').click();
      await expect(page.getByTestId('qraft-ai-gateway')).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId('qraft-ai-gateway-status')).toHaveText('可用');
      await expect(page.getByTestId('qraft-ai-gateway')).toContainText('配置版本 v1');

      // Python 握手通道：token 文件补上 aiGateway 块（make_provider 据此走网关）
      const tokenFile = JSON.parse(
        readFileSync(
          join(getAccountWorkspaceDir(fixture.miqiHome, '19'), '.qraft', 'token.json'),
          'utf8'
        )
      );
      expect(tokenFile.aiGateway).toMatchObject({
        encryptedApiKey: GATEWAY_KEY,
        status: 'active',
        configVersion: 1,
      });

      // 密钥仍然不进渲染进程
      const statusJson = await page.evaluate(async () =>
        JSON.stringify(await (window as any).miqi.qraft.status())
      );
      expect(statusJson).not.toContain(GATEWAY_KEY);
      expect(statusJson).toContain('"aiGateway":{"status":"active","configVersion":1}');

      await page.screenshot({
        path: 'test-results/ai-gateway-e2e-late-delivery.png',
        fullPage: true,
      });
    } finally {
      await mock.close();
    }
  });
});
