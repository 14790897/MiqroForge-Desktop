/**
 * 反馈平台通道 E2E（issue #1054）。
 *
 * 覆盖真实主进程链路（IPC → QraftService → QraftClient → 本地 mock 平台服务）
 * 与 Python 桥（feedback:submit 收集日志 → skip_feishu 跳过飞书 → 本地备份）：
 *   1. 已登录提交：平台 POST /oauth2/feedback 携带 Bearer access_token 与
 *      {type, content, contact} → 成功视图不出现「平台归属未同步」提示；
 *   2. access_token 失效（业务码 40102）且 refresh_token 被平台作废（40102）：
 *      飞书通道已兜底记录，提交仍成功，但展示「平台归属未同步」，并触发
 *      登录失效三件套的全局横幅（QraftReloginNotifier）。
 *
 * 平台服务用本地 http mock（不依赖 MiQroForge 网络）：登录态由测试预置
 * （MIQI_QRAFT_STORE，plain 信封，与 ai-gateway.spec.ts 同策略），
 * baseUrl 指向 mock；飞书通道经 config.channels.feedback.skipFeishu 跳过，
 * 使平台通道成为被测对象。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { createServer, type Server } from 'node:http';
import { join } from 'node:path';
import { writeFileSync } from 'node:fs';
import {
  launchElectronApp,
  closeElectronApp,
  type ElectronFixture,
} from './helpers/electron-setup';

const STORE_ENV = 'MIQI_QRAFT_STORE';

interface RecordedCall {
  path: string;
  authorization: string | null;
  body: Record<string, unknown>;
}

/** 本地 mock 平台服务：记录请求，按 path 返回注入的业务信封。 */
function startMockPlatform(
  baseUrl: string,
  responses: Map<string, { status?: number; body: string; contentType?: string }>
): Promise<{ server: Server; calls: RecordedCall[] }> {
  const calls: RecordedCall[] = [];
  const server = createServer((req, res) => {
    let raw = '';
    req.on('data', (chunk) => {
      raw += chunk;
    });
    req.on('end', () => {
      const url = new URL(req.url ?? '/', 'http://localhost');
      let body: Record<string, unknown> = {};
      try {
        body = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
      } catch {
        body = { __raw: raw };
      }
      calls.push({
        path: url.pathname,
        authorization: (req.headers['authorization'] as string | undefined) ?? null,
        body,
      });
      const hit = responses.get(url.pathname);
      if (!hit) {
        res.writeHead(404, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ code: 404, message: 'not mocked' }));
        return;
      }
      res.writeHead(hit.status ?? 200, {
        'content-type': hit.contentType ?? 'application/json',
      });
      res.end(hit.body);
    });
  });
  return new Promise((resolve) => {
    // 端口由 baseUrl 决定（spec 先固定端口再指向 mock）。
    const port = Number(new URL(baseUrl).port);
    server.listen(port, '127.0.0.1', () => resolve({ server, calls }));
  });
}

function closeServer(server: Server): Promise<void> {
  return new Promise((resolve) => server.close(() => resolve()));
}

/** 预置登录态：plain 信封（QraftStore 无 safeStorage 时按 Base64 读取）。 */
function seedQraftStore(storePath: string, baseUrl: string): void {
  writeFileSync(
    storePath,
    JSON.stringify({
      v: 1,
      enc: 'plain',
      payload: Buffer.from(
        JSON.stringify({
          version: 1,
          env: 'test',
          baseUrl,
          clientId: 'miqi',
          clientSecret: 'test-client-secret',
          redirectUri: 'http://localhost:38000/callback',
          cookie: 'Authorization=e2e-test-cookie',
          account: {
            phone: '18500000000',
            sub: '19',
            username: 'E2E-FEEDBACK',
            nickname: 'E2E反馈测试',
          },
          tokens: {
            accessToken: 'e2e-fake-access-token',
            refreshToken: 'e2e-fake-refresh-token',
            openid: 'e2e-fake-openid',
            // 远未到期：提交时不会先走自动刷新，token 失效路径由测试显式触发。
            expiresAt: Date.now() + 7_199_000,
          },
        }),
        'utf8'
      ).toString('base64'),
    }),
    'utf-8'
  );
}

/**
 * 打开反馈通道并跳过飞书写入（须在 launchElectronApp 之后调用）。
 *
 * helper 会在 patchConfig 之后整体重建 channels（反馈默认关闭），patchConfig
 * 里改的任何 channels 值都会被覆盖 —— 因此改走在用的配置写入通道
 * （config.update → 桥），并轮询 config.get 确认桥已读到新值再提交。
 */
async function enableFeedbackChannel(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await (window as any).miqi.config.update({
      channels: { feedback: { enabled: true, skipFeishu: true } },
    });
  });

  const deadline = Date.now() + 40_000;
  let lastSeen: unknown = null;
  while (Date.now() < deadline) {
    lastSeen = await page.evaluate(async () => {
      const cfg = await (window as any).miqi.config.get();
      return cfg?.channels?.feedback ?? null;
    });
    if ((lastSeen as { enabled?: boolean } | null)?.enabled === true) return;
    await page.waitForTimeout(1_000);
  }
  throw new Error(`enableFeedbackChannel: 桥未读到 enabled=true，最后一次读到 ${JSON.stringify(lastSeen)}`);
}

async function openFeedbackTab(page: Page): Promise<void> {
  const settingsLink = page.getByTestId('nav-system-settings');
  await expect(settingsLink).toBeVisible({ timeout: 15_000 });
  await settingsLink.click();
  await expect(page.getByText('通用').first()).toBeVisible({ timeout: 15_000 });
  const feedbackTab = page.getByRole('tab', { name: '反馈' });
  await expect(feedbackTab).toBeVisible({ timeout: 5_000 });
  await feedbackTab.click();
  await expect(page.getByText('用户反馈')).toBeVisible({ timeout: 5_000 });
}

async function submitFeedback(page: Page, content: string): Promise<void> {
  const headerBtn = page
    .locator('div.flex.items-center.gap-4')
    .getByRole('button', { name: '提交反馈', exact: true });
  await expect(headerBtn).toBeVisible({ timeout: 5_000 });
  await headerBtn.click();
  await expect(page.getByRole('heading', { name: '提交反馈' })).toBeVisible();
  await page.getByPlaceholder(/简要描述你的问题或建议/).fill(content);
  await page
    .locator('div.bg-\\[var\\(--surface\\)\\]')
    .getByRole('button', { name: '提交', exact: true })
    .click();
}

// 串行执行：两个用例各自启动一个 Electron 实例，并行启动会互相拖垮
// （Windows 上 userData 缓存争用 → 桥迟迟不 ready，600s 用例超时）。
test.describe.configure({ mode: 'serial' });

test.describe('Feedback platform channel E2E（issue #1054）', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let fixture: ElectronFixture;
  let server: Server | null = null;

  test.afterEach(async () => {
    if (electronApp) await closeElectronApp(electronApp, fixture?.miqiHome);
    if (server) {
      await closeServer(server);
      server = null;
    }
    delete process.env[STORE_ENV];
  });

  test('登录态提交成功：平台收到 Bearer + 字段，UI 不提示未同步', async () => {
    const port = 38931;
    const baseUrl = `http://127.0.0.1:${port}/api`;
    const started = await startMockPlatform(
      baseUrl,
      new Map([
        [
          '/api/oauth2/feedback',
          { body: JSON.stringify({ code: 200, message: 'ok', data: null }) },
        ],
      ])
    );
    server = started.server;

    const storePath = join(process.env.TEMP ?? '/tmp', 'qraft-e2e-feedback-ok.json');
    seedQraftStore(storePath, baseUrl);
    process.env[STORE_ENV] = storePath;

    fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    await enableFeedbackChannel(page);

    await openFeedbackTab(page);
    await submitFeedback(page, 'E2E 平台通道提交');

    await expect(page.getByText('提交成功！')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('feedback-platform-unsynced')).toHaveCount(0);

    const calls = started.calls.filter((c) => c.path === '/api/oauth2/feedback');
    expect(calls).toHaveLength(1);
    expect(calls[0].authorization).toBe('Bearer e2e-fake-access-token');
    expect(calls[0].body).toMatchObject({
      type: 'bug',
      content: 'E2E 平台通道提交',
    });
  });

  test('access_token 失效且 refresh 作废：提交仍成功 + 未同步提示 + 重登横幅', async () => {
    const port = 38932;
    const baseUrl = `http://127.0.0.1:${port}/api`;
    const started = await startMockPlatform(
      baseUrl,
      new Map([
        [
          '/api/oauth2/feedback',
          { body: JSON.stringify({ code: 40102, message: 'access_token 无效或已过期' }) },
        ],
        [
          '/api/oauth2/refresh',
          {
            body: JSON.stringify({
              code: 40102,
              message: 'refresh_token 无效',
              data: { originalMessage: 'RefreshTokenException: 无效refresh_token' },
            }),
          },
        ],
      ])
    );
    server = started.server;

    const storePath = join(process.env.TEMP ?? '/tmp', 'qraft-e2e-feedback-expired.json');
    seedQraftStore(storePath, baseUrl);
    process.env[STORE_ENV] = storePath;

    fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    await enableFeedbackChannel(page);

    await openFeedbackTab(page);
    await submitFeedback(page, 'E2E 失效态提交');

    // 飞书通道已兜底记录 → 提交成功，但提示平台归属未同步。
    await expect(page.getByText('提交成功！')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('feedback-platform-unsynced')).toBeVisible();

    // 登录失效全局横幅（三件套之一）；文案指向重新登录。
    await expect(page.getByTestId('qraft-relogin-notify')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('qraft-relogin-notify')).toContainText('重新登录');

    // 刷新与重试路径：先 40102 提交失败 → refresh 作废 → 不重试提交。
    const feedbackCalls = started.calls.filter((c) => c.path === '/api/oauth2/feedback');
    expect(feedbackCalls).toHaveLength(1);
    expect(started.calls.some((c) => c.path === '/api/oauth2/refresh')).toBe(true);
  });
});
